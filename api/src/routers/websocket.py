"""
WebSocket Router

Provides real-time updates via WebSocket connections.
Replaces Azure Web PubSub with native FastAPI WebSockets.
"""

import asyncio
import copy
import logging
from dataclasses import dataclass
from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from pydantic import ValidationError
from sqlalchemy import select

from shared import access_checks
from shared.browser_impersonation import (
    BrowserImpersonationError,
    is_browser_impersonation,
    validate_browser_impersonation,
)
from shared.claims.preresolve import preresolve_for_policies
from shared.policies.probe import is_subscribe_authorized
from shared.policy_rules import PolicyRuleDomainMismatch, PolicyRuleNotFound, resolve_policy_refs
from src.repositories.policy_rule import PolicyRuleRepository
from shared.policies.subscription import decide_visibility_change
from src.core.auth import get_current_user_ws
from src.core.exceptions import AccessDeniedError
from src.core.principal import UserPrincipal
from src.core.database import get_db_context
from src.core.log_safety import log_safe
from src.core.pubsub import manager
from src.models import Conversation, Execution
from src.models.contracts.policies import Expr, TablePolicies
from src.models.contracts.policies import FileAction
from src.models.orm.agent_runs import AgentRun
from src.models.orm.platform_jobs import PlatformJob
from src.models.orm.services import ServiceDefinition
from src.models.orm.events import EventSource
from src.models.orm.tables import Table as TableOrm
from src.models.orm.users import Role, UserRole
from src.repositories.applications import ApplicationRepository
from src.services.access_check_writer import flush_detached
from src.services.audit import emit_file_policy_deny, emit_table_policy_deny
from src.services.audit_context import ActorContext
from src.services.authorization.callers import CallerUnavailable, refresh_caller
from src.services.authorization.enforce import allows_permission, load_caller, user_reach
from src.services.authorization.execution_access import (
    current_execution_read_access,
    execution_read_access,
)
from src.services.execution.agent_run_access import agent_run_visibility_conditions
from src.services.notification_access import can_access_notification, can_read_admin_notifications
from src.services.notification_service import get_notification_service
from src.services.platform_job_access import (
    PlatformJobAccessDenied,
    require_platform_job_access,
)
from src.services.service_access import can_access_service as service_access_allowed


logger = logging.getLogger(__name__)


class WSError(Exception):
    """Subscribe-protocol error surfaced as an `error` ack to the client."""


@dataclass
class ChannelSpec:
    """Parsed channel-subscription request.

    `name` is the channel name (e.g. `table:<uuid>`). `filter` is an optional
    user-supplied filter expression that further narrows visibility on `table:`
    channels; it is ignored on other channels.
    """

    name: str
    filter: Expr | None
    scope: str | None = None


def _parse_channels(channels_raw: list) -> list[ChannelSpec]:
    """Accept either string or {name, filter} channel specs."""
    out: list[ChannelSpec] = []
    for ch in channels_raw:
        if isinstance(ch, str):
            out.append(ChannelSpec(name=ch, filter=None))
        elif isinstance(ch, dict) and "name" in ch:
            filter_dict = ch.get("filter")
            filter_expr: Expr | None = None
            if filter_dict is not None:
                try:
                    filter_expr = Expr.model_validate(filter_dict)
                except ValidationError as e:
                    raise WSError(f"invalid filter: {e}")
            scope = ch.get("scope")
            out.append(
                ChannelSpec(
                    name=ch["name"],
                    filter=filter_expr,
                    scope=scope if isinstance(scope, str) else None,
                )
            )
        else:
            raise WSError("channel must be a string or {name, filter} object")
    return out


async def _resolve_table_id(name_or_id: str, user: UserPrincipal) -> str | None:
    """Resolve a table in the current original caller's selected scope."""
    async with get_db_context() as db:
        caller = await _current_ws_caller(db, user)
        if caller is None or caller.ctx is None:
            return None
        try:
            table_uuid = UUID(name_or_id)
            row = await db.execute(select(TableOrm.id, TableOrm.organization_id).where(TableOrm.id == table_uuid))
        except ValueError:
            row = await db.execute(
                select(TableOrm.id, TableOrm.organization_id)
                .where(TableOrm.name == name_or_id, TableOrm.solution_id.is_(None))
                .where((TableOrm.organization_id == caller.ctx.home_organization_id) | TableOrm.organization_id.is_(None))
            )
        resolved = row.one_or_none()
        if resolved is None or not user_reach(caller).covers(resolved[1]):
            return None
        return str(resolved[0])


# In-process policy cache for the document_change fanout hot path.
#
# Each `document_change` event reaches every WS subscriber's local handler,
# which previously did a fresh DB load + Pydantic validate per event per
# subscriber. For high-fanout tables (many subscribers + frequent updates)
# that's O(subs × updates) DB queries. The cache collapses it to one load
# per (table, generation) pair.
#
# Invalidation is event-driven: `policy_changed` messages bump the table's
# generation in `_invalidate_table_policy_cache`, forcing a reload on next
# read. Worker processes that don't subscribe to `policy_changed` for a
# given table will hold a stale entry until the table is re-resolved — but
# they also won't be evaluating that table's policies, so the staleness is
# moot. (When a new subscriber arrives, the subscribe path goes through
# `is_subscribe_authorized` with a fresh load, sidestepping the cache.)
#
# We cache the RAW (access, org_id, solution_id) triple rather than a resolved
# TablePolicies. Resolution happens on every read (one DB lookup per $ref).
# This means a policy-RULE edit is reflected on the next `document_change`
# without requiring explicit cache-busting of the rule layer — only the table
# row itself (access JSONB) needs a `policy_changed` invalidation.
@dataclass
class _RawTableEntry:
    """Raw table data cached for resolution at read time."""
    access: dict | None
    org_id: UUID | None
    solution_id: UUID | None


_table_policy_cache: dict[str, _RawTableEntry | None] = {}
# Bounded to keep memory predictable under high table churn. Eviction is
# FIFO — for a hot working set of <_POLICY_CACHE_MAX tables, every read is
# a hit; cold reads pay one DB load.
_POLICY_CACHE_MAX = 256


def _invalidate_table_policy_cache(table_id: str) -> None:
    """Drop the cached policies for a table; next read reloads from DB."""
    _table_policy_cache.pop(table_id, None)


async def _load_policies_for_table(
    table_id: str,
    user: UserPrincipal,
) -> TablePolicies | None:
    """Load policies and safely pre-resolve their custom claims.

    Returns None if the table doesn't exist.

    Cache-first for UUID lookups (the hot path during `document_change`
    fanout). The cache stores the raw (access, org_id, solution_id) triple;
    resolution of $ref entries happens on every read so that rule edits are
    reflected without a separate cache-bust on the rule layer.
    Name lookups bypass the cache because the same name can resolve to
    different tables in different orgs; UUIDs are unambiguous.
    """
    # UUID lookups go through the cache. Name lookups bypass it because the
    # same name can resolve to different tables in different orgs.
    is_uuid = False
    try:
        UUID(table_id)
        is_uuid = True
    except ValueError:
        # Not a UUID — `table_id` is a name lookup (handled below). The
        # exception itself carries no information we need; the boolean is
        # the signal.
        pass

    raw: _RawTableEntry | None
    if is_uuid and table_id in _table_policy_cache:
        raw = _table_policy_cache[table_id]
    else:
        async with get_db_context() as db:
            if is_uuid:
                stmt = select(
                    TableOrm.access, TableOrm.organization_id, TableOrm.solution_id
                ).where(TableOrm.id == UUID(table_id))
            else:
                stmt = select(
                    TableOrm.access, TableOrm.organization_id, TableOrm.solution_id
                ).where(
                    TableOrm.name == table_id,
                    # By-name resolution is the live _repo/ namespace — mirror
                    # OrgScopedRepository.get(): solution-managed rows resolve by
                    # id (or ?solution=).
                    TableOrm.solution_id.is_(None),
                )
            result = await db.execute(stmt)
            row = result.one_or_none()

        raw = (
            None
            if row is None
            else _RawTableEntry(access=row[0], org_id=row[1], solution_id=row[2])
        )

        if is_uuid:
            # Bounded FIFO eviction — pop arbitrary entry when full.
            if len(_table_policy_cache) >= _POLICY_CACHE_MAX:
                _table_policy_cache.pop(next(iter(_table_policy_cache)), None)
            _table_policy_cache[table_id] = raw

    if raw is None:
        return None
    if not raw.access:
        return TablePolicies()

    try:
        policies = TablePolicies.model_validate(raw.access)
    except ValidationError:
        return TablePolicies()

    # Resolve $ref entries using a fresh DB session. Resolution is cheap
    # (one DB lookup per distinct ref name) and must happen after every cache
    # hit so that rule edits are visible without a cache invalidation.
    async with get_db_context() as db:
        repo = PolicyRuleRepository(db, org_id=raw.org_id, is_superuser=True)
        try:
            await resolve_policy_refs(
                policies,
                repo=repo,
                action_domain="table",
                solution_id=raw.solution_id,
            )
        except (PolicyRuleNotFound, PolicyRuleDomainMismatch) as exc:
            logger.warning(
                "unresolvable policy ref on table %s; denying: %s", table_id, exc
            )
            return TablePolicies()

        await preresolve_for_policies(
            user,
            policies,
            db,
            raw.org_id,
            raw.solution_id,
        )

    return policies


def _fresh_table_policy_user(user: UserPrincipal) -> UserPrincipal:
    """Copy a websocket principal without reusing custom-claim values.

    Websocket principals live across tables and policy changes. Custom claims
    can be solution-scoped and mutable, so each evaluation must resolve them
    afresh rather than sharing a name-keyed cache across subscriptions.
    """
    policy_user = copy.copy(user)
    policy_user.claims = {}  # type: ignore[attr-defined]
    return policy_user


async def _revoke_live_subscription(
    websocket: WebSocket,
    channel: str,
    *,
    notice_channel: str | None = None,
    remove_connection: bool = True,
) -> None:
    """Tell the client that a subscription was revoked and optionally detach it."""
    if remove_connection:
        sockets = manager.connections.get(channel)
        if sockets is not None:
            sockets.discard(websocket)
            if not sockets:
                manager.connections.pop(channel, None)
    await websocket.send_json({
        "type": "subscription_revoked",
        "channel": notice_channel or channel,
    })


async def _current_table_policy_user(user: UserPrincipal, table_id: str) -> UserPrincipal | None:
    try:
        table_uuid = UUID(table_id)
    except ValueError:
        return None
    async with get_db_context() as db:
        caller = await _current_ws_caller(db, user)
        table = await db.get(TableOrm, table_uuid)
        if caller is None or table is None or not user_reach(caller).covers(table.organization_id):
            return None
        role_rows = (
            await db.execute(
                select(Role.id, Role.name)
                .join(UserRole, UserRole.role_id == Role.id)
                .where(UserRole.user_id == caller.ctx.user_id)
            )
        ).all()
        policy_user = _fresh_table_policy_user(caller.principal)
        policy_user.role_ids = [row.id for row in role_rows]
        policy_user.role_names = [row.name for row in role_rows]
        policy_user.roles = list(policy_user.role_names)
        return policy_user


async def _handle_table_message(
    websocket: WebSocket,
    user: UserPrincipal,
    channel_name: str,
    payload: dict[str, Any],
) -> bool:
    """Apply table visibility and report whether its subscription remains active.

    `policy_changed` triggers a re-evaluation; if the user no longer satisfies
    `is_subscribe_authorized`, a `subscription_revoked` notice is sent and the
    subscription state is cleared. A row omitted by a policy or user filter
    keeps the subscription active for later visible rows.
    """
    table_id = channel_name.split(":", 1)[1]
    table_subs: dict[str, dict[str, Any]] = getattr(websocket.state, "table_subscriptions", {})
    sub = table_subs.get(table_id)
    if sub is None:
        return False  # no longer subscribed

    msg_type = payload.get("type")

    if msg_type == "policy_changed":
        _invalidate_table_policy_cache(table_id)

    policy_user = await _current_table_policy_user(user, table_id)
    policies = None if policy_user is None else await _load_policies_for_table(table_id, policy_user)
    if policies is None or policy_user is None or not is_subscribe_authorized(policies, policy_user):
        table_subs.pop(table_id, None)
        await _revoke_live_subscription(websocket, channel_name)
        return False

    if msg_type == "policy_changed":
        return True

    if msg_type == "table_invalidated":
        await websocket.send_json({
            "type": "table_invalidated",
            "table_id": table_id,
        })
        return True

    if msg_type != "document_change":
        return True

    decision = decide_visibility_change(
        old_row=payload.get("old_row"),
        new_row=payload.get("new_row"),
        policies=policies,
        user=policy_user,
        user_filter=sub.get("filter"),
    )
    if decision is None:
        return True

    action, body = decision
    if action == "delete":
        await websocket.send_json({
            "type": "document_change",
            "action": "delete",
            "table_id": table_id,
            "row_id": body,
        })
    else:
        await websocket.send_json({
            "type": "document_change",
            "action": action,
            "table_id": table_id,
            "row": body,
        })
    return True


async def _re_evaluate_subscription(
    websocket: WebSocket,
    user: UserPrincipal,
    table_id: str,
) -> None:
    """Re-run subscribe-time authorization after a policy edit; revoke if no."""
    policy_user = await _current_table_policy_user(user, table_id)
    policies = None if policy_user is None else await _load_policies_for_table(table_id, policy_user)
    if policies is None or policy_user is None or not is_subscribe_authorized(policies, policy_user):
        table_subs: dict[str, dict[str, Any]] = getattr(websocket.state, "table_subscriptions", {})
        table_subs.pop(table_id, None)
        await _revoke_live_subscription(websocket, f"table:{table_id}")


async def _authorize_table_subscribe(
    websocket: WebSocket,
    user: UserPrincipal,
    spec: ChannelSpec,
) -> str | None:
    """Run the subscribe-time policy probe and register per-connection state.

    Resolves the user-supplied `table:<name-or-id>` to the canonical
    `table:<uuid>` channel — the publisher only ever emits on UUID-keyed
    channels, so subscriptions registered under a name would never receive
    messages. On success, returns the canonical channel string and populates
    `websocket.state.table_subscriptions[uuid]`. On failure, sends an `error`
    ack and returns None.
    """
    name_or_id = spec.name.split(":", 1)[1]
    canonical_id = await _resolve_table_id(name_or_id, user)
    if canonical_id is None:
        await websocket.send_json({
            "type": "error",
            "channel": spec.name,
            "message": "Table not found",
        })
        return None

    # Drop any stale cache entry: between this process's last sub
    # disconnecting and now, a `policy_changed` event for this table
    # would NOT have reached us (no subscriber to fan out to). Force a
    # fresh load on subscribe to close that staleness window.
    _invalidate_table_policy_cache(canonical_id)
    policy_user = await _current_table_policy_user(user, canonical_id)
    if policy_user is None:
        return None
    policies = await _load_policies_for_table(canonical_id, policy_user)
    if policies is None:
        await websocket.send_json({
            "type": "error",
            "channel": spec.name,
            "message": "Table not found",
        })
        return None

    if not is_subscribe_authorized(policies, policy_user):
        await _emit_table_subscribe_denial(
            websocket=websocket,
            user=user,
            table_id=UUID(canonical_id),
        )
        await websocket.send_json({
            "type": "error",
            "channel": spec.name,
            "message": "Access denied",
        })
        return None

    canonical_channel = f"table:{canonical_id}"
    table_subs: dict[str, dict[str, Any]] = getattr(websocket.state, "table_subscriptions", None) or {}
    table_subs[canonical_id] = {"filter": spec.filter, "channel_name": canonical_channel}
    websocket.state.table_subscriptions = table_subs

    return canonical_channel


def _parse_file_channel(name: str) -> tuple[str, str] | None:
    parts = name.split(":", 2)
    if len(parts) != 3 or parts[0] != "files":
        return None
    return parts[1], parts[2].strip("/")


def _path_matches(prefix: str, path: str) -> bool:
    normalized_prefix = prefix.strip("/")
    normalized_path = path.strip("/")
    if normalized_prefix == "":
        return True
    return (
        normalized_path == normalized_prefix
        or normalized_path.startswith(f"{normalized_prefix}/")
    )


async def _file_org_and_scope(
    *, user: UserPrincipal, location: str, requested_scope: str | None
) -> tuple[UUID | None, str | None] | None:
    """Resolve a file scope from fresh original-user reach, never flags."""
    if location == "workspace":
        return None, None
    async with get_db_context() as db:
        caller = await _current_ws_caller(db, user)
        if caller is None or caller.ctx is None:
            return None
        scope = requested_scope or (str(caller.ctx.home_organization_id) if caller.ctx.home_organization_id else None)
        if scope is None:
            return None
        if scope == "global":
            return (None, scope) if user_reach(caller).covers(None) else None
        try:
            org_id = UUID(scope)
        except ValueError:
            return None
        return (org_id, scope) if user_reach(caller).covers(org_id) else None


def _file_channel(location: str, scope: str | None) -> str:
    return f"files:{location}:{scope or 'GLOBAL'}"


async def _file_allowed(
    *,
    user: UserPrincipal,
    action: str,
    organization_id: UUID | None,
    location: str,
    path: str,
) -> bool:
    from src.services.file_policy_service import FilePolicyService

    async with get_db_context() as db:
        caller = await _current_ws_caller(db, user)
        if caller is None or not user_reach(caller).covers(organization_id):
            return False
        service = FilePolicyService(db)
        return await service.is_allowed(
            cast(FileAction, action),
            organization_id=organization_id,
            location=location,
            path=path,
            user=caller.principal,
        )


async def _file_has_applicable_policy(
    *,
    organization_id: UUID | None,
    location: str,
    path: str,
) -> bool:
    from src.services.file_policy_service import FilePolicyService

    async with get_db_context() as db:
        service = FilePolicyService(db)
        return (
            await service.load_policy(
                organization_id=organization_id,
                location=location,
                path=path,
            )
        ) is not None


async def _authorize_file_subscribe(
    websocket: WebSocket,
    user: UserPrincipal,
    spec: ChannelSpec,
) -> str | None:
    parsed = _parse_file_channel(spec.name)
    if parsed is None:
        await websocket.send_json({
            "type": "error",
            "channel": spec.name,
            "message": "Invalid file channel",
        })
        return None

    location, prefix = parsed
    scope_result = await _file_org_and_scope(
        user=user,
        location=location,
        requested_scope=spec.scope,
    )
    if scope_result is None:
        await _emit_file_subscribe_denial(
            websocket=websocket,
            user=user,
            location=location,
            path=prefix,
            scope=spec.scope,
        )
        await websocket.send_json({
            "type": "error",
            "channel": spec.name,
            "message": "Access denied",
        })
        return None
    organization_id, scope = scope_result

    if not await _file_has_applicable_policy(
        organization_id=organization_id,
        location=location,
        path=prefix,
    ):
        await _emit_file_subscribe_denial(
            websocket=websocket,
            user=user,
            location=location,
            path=prefix,
            scope=scope,
        )
        await websocket.send_json({
            "type": "error",
            "channel": spec.name,
            "message": "Access denied",
        })
        return None

    canonical_channel = _file_channel(location, scope)
    sub_key = f"{canonical_channel}:{prefix}"
    file_subs: dict[str, dict[str, Any]] = getattr(websocket.state, "file_subscriptions", None) or {}
    file_subs[sub_key] = {
        "channel_name": canonical_channel,
        "requested_channel": spec.name,
        "location": location,
        "scope": scope,
        "organization_id": organization_id,
        "prefix": prefix,
    }
    websocket.state.file_subscriptions = file_subs

    return canonical_channel


async def _emit_file_subscribe_denial(
    *,
    websocket: WebSocket,
    user: UserPrincipal,
    location: str,
    path: str,
    scope: str | None,
) -> None:
    """Persist a WebSocket denial through the same Event Log path as REST."""
    actor = _websocket_actor(websocket, user)
    async with get_db_context() as db:
        await emit_file_policy_deny(
            db,
            policy_action="subscribe",
            location=location,
            path=path,
            scope=scope,
            solution_id=None,
            actor_override=actor,
        )


def _websocket_actor(websocket: WebSocket, user: UserPrincipal) -> ActorContext:
    """Build the HTTP-equivalent audit actor for an upgraded connection."""
    forwarded = websocket.headers.get("x-forwarded-for")
    ip_address = (
        forwarded.split(",", 1)[0].strip()
        if forwarded
        else websocket.client.host if websocket.client else "unknown"
    )
    return ActorContext(
        user_id=user.user_id,
        organization_id=user.organization_id,
        email=user.email,
        name=user.name,
        ip_address=ip_address,
        user_agent=websocket.headers.get("user-agent"),
        impersonated_by_user_id=user.impersonated_by_user_id,
        impersonation_session_id=user.impersonation_session_id,
    )


def _collect_websocket_checks(user: UserPrincipal):
    return access_checks.start_collecting({
        "sub": str(user.user_id),
        "impersonated_by_user_id": user.impersonated_by_user_id,
        "impersonation_session_id": user.impersonation_session_id,
    }, surface="web", principal=user)


async def _emit_table_subscribe_denial(
    *,
    websocket: WebSocket,
    user: UserPrincipal,
    table_id: UUID,
) -> None:
    """Persist a table subscribe denial through the canonical Event Log path."""
    async with get_db_context() as db:
        table_name = await db.scalar(
            select(TableOrm.name).where(TableOrm.id == table_id)
        )
        if table_name is None:
            return
        await emit_table_policy_deny(
            db,
            policy_action="subscribe",
            table_id=table_id,
            table_name=table_name,
            actor_override=_websocket_actor(websocket, user),
        )


async def _handle_file_message(
    websocket: WebSocket,
    user: UserPrincipal,
    channel_name: str,
    payload: dict[str, Any],
) -> bool:
    file_subs: dict[str, dict[str, Any]] = getattr(websocket.state, "file_subscriptions", {})
    matching = [
        (sub_key, sub)
        for sub_key, sub in file_subs.items()
        if sub.get("channel_name") == channel_name
    ]
    if not matching:
        return False

    def subscription_active() -> bool:
        return any(
            sub.get("channel_name") == channel_name for sub in file_subs.values()
        )

    msg_type = payload.get("type")
    if msg_type == "file_policy_changed":
        for sub_key, sub in list(matching):
            has_policy = await _file_has_applicable_policy(
                organization_id=sub["organization_id"],
                location=sub["location"],
                path=sub["prefix"],
            )
            allowed = has_policy and await _file_allowed(
                user=user,
                action="read",
                organization_id=sub["organization_id"],
                location=sub["location"],
                path=sub["prefix"],
            )
            if not allowed:
                file_subs.pop(sub_key, None)
                await _revoke_live_subscription(
                    websocket,
                    sub["channel_name"],
                    notice_channel=sub["requested_channel"],
                    remove_connection=False,
                )
        return subscription_active()

    if msg_type != "file_change":
        return subscription_active()

    changed_path = str(payload.get("path") or "").strip("/")
    if not changed_path:
        return subscription_active()

    delivered_prefixes: set[str] = set()
    for sub_key, sub in matching:
        prefix = sub["prefix"]
        if prefix in delivered_prefixes:
            continue
        if not _path_matches(prefix, changed_path):
            continue
        if not await _file_allowed(
            user=user,
            action="read",
            organization_id=sub["organization_id"],
            location=sub["location"],
            path=changed_path,
        ):
            file_subs.pop(sub_key, None)
            await _revoke_live_subscription(
                websocket,
                sub["channel_name"],
                notice_channel=sub["requested_channel"],
                remove_connection=False,
            )
            continue
        delivered_prefixes.add(prefix)
        await websocket.send_json({
            "type": "file_change",
            "channel": sub["requested_channel"],
            "location": payload.get("location"),
            "scope": payload.get("scope"),
            "path": changed_path,
            "action": payload.get("action"),
        })
    return subscription_active()


async def _current_ws_caller(db, user: UserPrincipal):
    """Reload the original caller and current role state before a live read."""
    try:
        return await refresh_caller(db, await load_caller(db, user))
    except CallerUnavailable:
        return None


async def can_access_execution(user: UserPrincipal, execution_id: str) -> bool:
    """Authorize one execution with the same current Caller policy as REST."""
    if user.embed:
        if not (
            user.is_verified
            and user.jti
            and (
                user.embed_kind == "app"
                or (user.embed_kind == "form" and user.grant == "hmac")
            )
        ):
            return False
        from src.core.embed_middleware import embed_session_owns_execution

        if not await embed_session_owns_execution(user.jti, execution_id):
            return False
        try:
            execution_uuid = UUID(execution_id)
        except ValueError:
            return False
        async with get_db_context() as db:
            execution = await db.get(Execution, execution_uuid)
            # A mapped execution may still be Redis-pending. Once persisted,
            # bind the read to the row's exact organization.
            return execution is None or execution.organization_id == user.organization_id
    try:
        execution_uuid = UUID(execution_id)
    except ValueError:
        return False
    async with get_db_context() as db:
        caller = await _current_ws_caller(db, user)
        execution = await db.get(Execution, execution_uuid)
        return caller is not None and execution is not None and execution_read_access(caller, execution).visible


async def can_read_execution_diagnostics(user: UserPrincipal, execution_id: str) -> bool:
    """Return current diagnostic access for a visible execution subscription."""
    if user.embed:
        return False
    try:
        execution_uuid = UUID(execution_id)
    except ValueError:
        return False
    async with get_db_context() as db:
        caller = await _current_ws_caller(db, user)
        execution = await db.get(Execution, execution_uuid)
        if caller is None or execution is None:
            return False
        return (await current_execution_read_access(caller, execution)).diagnostics


def _is_diagnostic_execution_log(channel: str, payload: dict[str, Any]) -> bool:
    return (
        channel.startswith("execution:")
        and payload.get("type") == "execution_log"
        and str(payload.get("level") or "").upper() in {"DEBUG", "TRACEBACK"}
    )


async def can_access_service(user: UserPrincipal, service_id: str) -> bool:
    """Authorize service logs at their durable service organization."""
    try:
        service_uuid = UUID(service_id)
    except ValueError:
        return False
    async with get_db_context() as db:
        caller = await _current_ws_caller(db, user)
        definition = await db.get(ServiceDefinition, service_uuid)
        return caller is not None and definition is not None and service_access_allowed(caller, definition)


async def can_access_app(user: UserPrincipal, app_id: str, *, write: bool = False) -> bool:
    """Recheck App ACL and named authority from the current original caller."""
    try:
        app_uuid = UUID(app_id)
    except ValueError:
        return False
    async with get_db_context() as db:
        caller = await _current_ws_caller(db, user)
        if caller is None:
            return False
        repo = ApplicationRepository(
            session=db,
            org_id=caller.ctx.home_organization_id if caller.ctx is not None else None,
            caller=caller,
            access_mode="write" if write else "read",
        )
        try:
            app = await repo.can_access(id=app_uuid)
        except AccessDeniedError:
            return False
        return app is not None


async def can_access_conversation_current(user: UserPrincipal, conversation_id: str) -> bool:
    try:
        conversation_uuid = UUID(conversation_id)
    except ValueError:
        return False
    async with get_db_context() as db:
        caller = await _current_ws_caller(db, user)
        conversation = await db.get(Conversation, conversation_uuid)
        return bool(
            caller is not None and caller.ctx is not None and conversation is not None
            and conversation.is_active and conversation.user_id == caller.ctx.user_id
        )


async def can_access_event_source(user: UserPrincipal, source_id: str) -> bool:
    try:
        source_uuid = UUID(source_id)
    except ValueError:
        return False
    async with get_db_context() as db:
        caller = await _current_ws_caller(db, user)
        source = await db.get(EventSource, source_uuid)
        return bool(
            caller is not None and source is not None
            and user_reach(caller).covers(source.organization_id)
            and allows_permission(caller, "events.read", source.organization_id)
        )


async def can_access_global_platform(user: UserPrincipal) -> bool:
    async with get_db_context() as db:
        caller = await _current_ws_caller(db, user)
        return caller is not None and allows_permission(caller, "platform.read", None)


async def can_access_agent_run(user: UserPrincipal, run_id: str) -> bool:
    """Authorize an agent-run live update from current original authority."""
    try:
        run_uuid = UUID(run_id)
    except ValueError:
        return False
    async with get_db_context() as db:
        caller = await _current_ws_caller(db, user)
        if caller is None:
            return False
        return bool(await db.scalar(
            select(AgentRun.id).where(AgentRun.id == run_uuid, *agent_run_visibility_conditions(caller))
        ))


async def can_access_platform_job(user: UserPrincipal, job_id: str) -> bool:
    try:
        job_uuid = UUID(job_id)
    except ValueError:
        return False
    async with get_db_context() as db:
        caller = await _current_ws_caller(db, user)
        job = await db.get(PlatformJob, job_uuid)
        if caller is None or job is None:
            return False
        try:
            require_platform_job_access(caller, job)
        except PlatformJobAccessDenied:
            return False
        return True


async def can_access_admin_notifications(user: UserPrincipal) -> bool:
    async with get_db_context() as db:
        caller = await _current_ws_caller(db, user)
        return caller is not None and can_read_admin_notifications(caller)


async def _delivery_allowed(user: UserPrincipal, channel: str, payload: dict[str, Any]) -> bool:
    """Return whether a raw live delivery is currently authorized."""
    if channel.startswith("user:"):
        async with get_db_context() as db:
            caller = await _current_ws_caller(db, user)
            return caller is not None and caller.ctx is not None and channel == f"user:{caller.ctx.user_id}"
    if channel.startswith("execution:"):
        return await can_access_execution(user, channel.split(":", 1)[1])
    if channel.startswith("history:"):
        execution_id = payload.get("execution_id")
        return isinstance(execution_id, str) and await can_access_execution(user, execution_id)
    if channel.startswith("app:live:"):
        return await can_access_app(user, channel.split(":", 2)[2])
    if channel.startswith("app:draft:"):
        return await can_access_app(user, channel.split(":", 2)[2], write=True)
    if channel.startswith("chat:"):
        return await can_access_conversation_current(user, channel.split(":", 1)[1])
    if channel.startswith("event-source:"):
        return await can_access_event_source(user, channel.split(":", 1)[1])
    if channel in {"package:install", "file-activity", "platform_workers"} or channel.startswith(("git:", "reindex:", "summary-backfill:")):
        return await can_access_global_platform(user)
    if channel.startswith("agent-run:"):
        return await can_access_agent_run(user, channel.split(":", 1)[1])
    if channel == "agent-runs":
        run_id = payload.get("agent_run_id") or payload.get("run_id") or payload.get("id")
        return isinstance(run_id, str) and await can_access_agent_run(user, run_id)
    if channel.startswith("service:"):
        return await can_access_service(user, channel.split(":", 1)[1])
    if channel.startswith("platform-job:"):
        return await can_access_platform_job(user, channel.split(":", 1)[1])
    if channel.startswith("notification:"):
        if payload.get("type") == "platform_job_updated":
            job = payload.get("job")
            job_id = job.get("id") if isinstance(job, dict) else None
            return isinstance(job_id, str) and await can_access_platform_job(user, job_id)
        if channel != "notification:admins":
            async with get_db_context() as db:
                caller = await _current_ws_caller(db, user)
                return caller is not None and caller.ctx is not None and channel == f"notification:{caller.ctx.user_id}"
        notification = payload.get("notification")
        if not isinstance(notification, dict):
            return False
        async with get_db_context() as db:
            caller = await _current_ws_caller(db, user)
            return caller is not None and await can_access_notification(
                get_notification_service(), caller, str(notification.get("id", "")), notification
            )
    return True


async def _require_live_impersonation(websocket: WebSocket, user: UserPrincipal) -> None:
    """Recheck support-session authority before receiving or delivering data."""
    if not is_browser_impersonation(user):
        return
    try:
        async with get_db_context() as db:
            await validate_browser_impersonation(db, user)
    except BrowserImpersonationError:
        await websocket.close(code=4001, reason="Impersonation session ended")
        raise WebSocketDisconnect(code=4001)


def _make_delivery_dispatcher(websocket: WebSocket, user: UserPrincipal):
    def remove_connection(channel: str) -> None:
        if channel in manager.connections:
            manager.connections[channel].discard(websocket)
            if not manager.connections[channel]:
                manager.connections.pop(channel, None)

    async def dispatch(channel: str, payload: dict[str, Any]) -> None:
        await _require_live_impersonation(websocket, user)
        # Table and file handlers own their visibility filtering and delivery.
        # A filtered message is not an authorization revocation, so it must not
        # remove the socket from the channel before a later visible message.
        if channel.startswith("table:"):
            if not await _handle_table_message(websocket, user, channel, payload):
                remove_connection(channel)
            return
        if channel.startswith("files:"):
            if not await _handle_file_message(websocket, user, channel, payload):
                remove_connection(channel)
            return

        if await _delivery_allowed(user, channel, payload):
            if _is_diagnostic_execution_log(channel, payload):
                execution_id = channel.split(":", 1)[1]
                if not await can_read_execution_diagnostics(user, execution_id):
                    return
            await websocket.send_json(payload)
        else:
            remove_connection(channel)
    return dispatch


# Background judgements in flight, held so they are not collected mid-run.
_judging: set[asyncio.Task[None]] = set()


def _judged(task: asyncio.Task[None]) -> None:
    _judging.discard(task)
    if not task.cancelled() and task.exception() is not None:
        logger.warning("access checks not judged: %s", type(task.exception()).__name__)


def _judge_access_checks(path: str) -> None:
    """Judge what the last subscriptions on WebSocket ``path`` noted, in the
    background (report-only; never raises into the connection), and collect
    afresh for the next message."""
    collector = access_checks.renew()
    if collector is None or not collector.notes:
        return
    task = asyncio.create_task(flush_detached(collector, operation=f"WS {path}", route=("WS", path)))
    _judging.add(task)
    task.add_done_callback(_judged)


router = APIRouter(prefix="/ws", tags=["WebSocket"])


@router.websocket("/connect")
async def websocket_connect(
    websocket: WebSocket,
    channels: Annotated[list[str], Query()] = [],
):
    """
    WebSocket endpoint for real-time updates.

    Connect and subscribe to channels:
    - execution:{execution_id} - Execution updates and logs
    - service:{service_id} - Supervised service log streaming (platform admins)
    - user:{user_id} - User notifications
    - system - System broadcasts

    Query params:
        channels: List of channels to subscribe to

    Example:
        ws://localhost:8000/ws/connect?channels=execution:abc-123&channels=user:user-456

    Messages are JSON with structure:
        {
            "type": "execution_update" | "execution_log" | "service_log" | "notification",
            ...payload
        }
    """
    # Authenticate via header (query params not supported for security)
    user = await get_current_user_ws(websocket)

    if not user:
        # Must accept before closing, otherwise client sees HTTP 403
        await websocket.accept()
        await websocket.close(code=4001, reason="Unauthorized")
        return

    if user.embed and user.embed_kind == "form":
        await websocket.accept()
        await websocket.close(code=4003, reason="Form sessions cannot use WebSockets")
        return

    checks = _collect_websocket_checks(user)

    # Filter channels - users can only subscribe to their own user channel
    # and execution channels (we'll validate execution access separately)
    allowed_channels = []
    for channel in channels:
        if channel.startswith("user:"):
            # Users can only subscribe to their own notifications
            if channel == f"user:{user.user_id}":
                allowed_channels.append(channel)
        elif channel.startswith("execution:"):
            # Validate user has access to this execution
            execution_id = channel.split(":", 1)[1]
            if await can_access_execution(user, execution_id):
                allowed_channels.append(channel)
        elif channel.startswith("service:"):
            # Service log streaming - platform admins only (the services
            # surface is admin-only; the pubsub bridge fans attempt logs
            # out to this channel)
            service_id = channel.split(":", 1)[1]
            if await can_access_service(user, service_id):
                allowed_channels.append(channel)
        elif channel == "package:install":
            # Package installation channel - shared, superusers only
            if await can_access_global_platform(user):
                access_checks.note_power("platform.read", None, subject=f"channel:{channel}")
                allowed_channels.append(channel)
        elif channel.startswith("git:"):
            # Git job channels have no owning-user record to check against;
            # gate to platform admins until they carry real job ownership.
            if await can_access_global_platform(user):
                access_checks.note_power("platform.read", None, subject=f"channel:{channel}")
                allowed_channels.append(channel)
        elif channel.startswith("notification:"):
            # Notification channels - users can subscribe to their own
            if channel == f"notification:{user.user_id}":
                allowed_channels.append(channel)
            # Platform admins can subscribe to admin notifications
            elif channel == "notification:admins" and await can_access_admin_notifications(user):
                allowed_channels.append(channel)
        elif channel.startswith("chat:"):
            if await can_access_conversation_current(user, channel.split(":", 1)[1]):
                allowed_channels.append(channel)
        elif channel.startswith("history:"):
            # History channels for real-time updates
            # history:user:{user_id} - Allow only for the user's own channel
            # history:GLOBAL - Allow only for platform admins
            if channel == f"history:user:{user.user_id}":
                allowed_channels.append(channel)
            elif channel == "history:GLOBAL" and await can_access_global_platform(user):
                allowed_channels.append(channel)
        elif channel.startswith("event-source:"):
            if await can_access_event_source(user, channel.split(":", 1)[1]):
                allowed_channels.append(channel)
        elif channel.startswith("reindex:"):
            # Reindex job progress channels - platform admins only
            if await can_access_global_platform(user):
                access_checks.note_power("platform.read", None, subject=f"channel:{channel}")
                allowed_channels.append(channel)
        elif channel.startswith("app:draft:"):
            app_id = channel.split(":", 2)[2]
            if await can_access_app(user, app_id, write=True):
                allowed_channels.append(channel)
        elif channel.startswith("app:live:"):
            # App Builder live channels - same access check as the REST app read.
            app_id = channel.split(":", 2)[2]
            if await can_access_app(user, app_id):
                allowed_channels.append(channel)
        elif channel == "file-activity":
            # File activity channel - platform admins only
            if await can_access_global_platform(user):
                access_checks.note_power("platform.read", None, subject=f"channel:{channel}")
                allowed_channels.append(channel)
        elif channel.startswith("files:"):
            # File channels need runtime subscribe metadata (scope) and
            # per-connection policy filtering. Query-string subscription is
            # intentionally not supported.
            continue
        elif channel.startswith("table:"):
            # Table channels carry a per-connection user filter that cannot be
            # expressed in a query string. Require runtime `subscribe` messages
            # for table channels — query-string subscription is rejected.
            continue
        elif channel.startswith("agent-run:"):
            # Agent run detail channels - only the run's own caller, or bypass
            run_id = channel.split(":", 1)[1]
            if await can_access_agent_run(user, run_id):
                allowed_channels.append(channel)
        elif channel == "agent-runs":
            # Per-message visibility is enforced by the shared delivery guard.
            async with get_db_context() as db:
                if await _current_ws_caller(db, user) is not None:
                    allowed_channels.append(channel)
        elif channel.startswith("summary-backfill:"):
            # Summary backfill job progress — platform admins only
            if await can_access_global_platform(user):
                access_checks.note_power("platform.read", None, subject=f"channel:{channel}")
                allowed_channels.append(channel)
        elif channel == "platform_workers":
            # Platform workers channel - diagnostics, platform admins only
            if await can_access_global_platform(user):
                access_checks.note_power("platform.read", None, subject=f"channel:{channel}")
                allowed_channels.append(channel)

    # Always subscribe to user's own channel
    user_channel = f"user:{user.user_id}"
    if user_channel not in allowed_channels:
        allowed_channels.append(user_channel)

    # Per-connection state for policy-driven table subscriptions.
    # Populated by `_authorize_table_subscribe`; consulted by the dispatcher.
    websocket.state.table_subscriptions = {}
    websocket.state.file_subscriptions = {}
    websocket._delivery_dispatcher = _make_delivery_dispatcher(websocket, user)  # type: ignore[attr-defined]

    try:
        await manager.connect(websocket, allowed_channels)
        logger.info(f"WebSocket connected for user {user.user_id}, channels: {log_safe(allowed_channels)}")

        # Send connection confirmation
        await websocket.send_json({
            "type": "connected",
            "channels": allowed_channels,
            "userId": str(user.user_id)
        })
        _judge_access_checks("/ws/connect")

        # Keep connection alive and handle incoming messages
        while True:
            data = await websocket.receive_json()
            await _require_live_impersonation(websocket, user)

            # Handle subscription changes
            if data.get("type") == "subscribe":
                new_channels_raw = data.get("channels", [])
                try:
                    parsed_specs = _parse_channels(new_channels_raw)
                except WSError as e:
                    await websocket.send_json({
                        "type": "error",
                        "message": str(e),
                    })
                    continue
                for spec in parsed_specs:
                    channel = spec.name
                    # Validate and add subscription
                    if channel.startswith("execution:"):
                        # Validate execution access before subscribing
                        execution_id = channel.split(":", 1)[1]
                        if not await can_access_execution(user, execution_id):
                            await websocket.send_json({
                                "type": "error",
                                "channel": channel,
                                "message": "Access denied"
                            })
                            continue
                        if channel not in manager.connections:
                            manager.connections[channel] = set()
                        manager.connections[channel].add(websocket)
                        await websocket.send_json({
                            "type": "subscribed",
                            "channel": channel
                        })
                    elif channel.startswith("service:"):
                        # Service log streaming - platform admins only
                        service_id = channel.split(":", 1)[1]
                        if not await can_access_service(user, service_id):
                            await websocket.send_json({
                                "type": "error",
                                "channel": channel,
                                "message": "Access denied"
                            })
                            continue
                        if channel not in manager.connections:
                            manager.connections[channel] = set()
                        manager.connections[channel].add(websocket)
                        await websocket.send_json({
                            "type": "subscribed",
                            "channel": channel
                        })
                    elif channel.startswith("event-source:"):
                        # Event source channels - bypass-only, matching the
                        # REST events router.
                        if await can_access_event_source(user, channel.split(":", 1)[1]):
                            access_checks.note_power("platform.read", None, subject=f"channel:{channel}")
                            if channel not in manager.connections:
                                manager.connections[channel] = set()
                            manager.connections[channel].add(websocket)
                            await websocket.send_json({
                                "type": "subscribed",
                                "channel": channel
                            })
                        else:
                            await websocket.send_json({
                                "type": "error",
                                "channel": channel,
                                "message": "Access denied"
                            })
                    elif channel.startswith("history:"):
                        # History channels for real-time execution updates
                        # history:user:{user_id} - Allow only for the user's own channel
                        # history:GLOBAL - Allow only for platform admins
                        if channel == f"history:user:{user.user_id}" or (channel == "history:GLOBAL" and await can_access_global_platform(user)):
                            if channel == "history:GLOBAL":
                                access_checks.note_power("platform.read", None, subject=f"channel:{channel}")
                            if channel not in manager.connections:
                                manager.connections[channel] = set()
                            manager.connections[channel].add(websocket)
                            await websocket.send_json({
                                "type": "subscribed",
                                "channel": channel
                            })
                        else:
                            await websocket.send_json({
                                "type": "error",
                                "channel": channel,
                                "message": "Access denied"
                            })
                    elif channel.startswith("app:draft:"):
                        # App Builder draft channels are an authoring surface - bypass-only.
                        if await can_access_app(user, channel.split(":", 2)[2], write=True):
                            access_checks.note_power("platform.read", None, subject=f"channel:{channel}")
                            if channel not in manager.connections:
                                manager.connections[channel] = set()
                            manager.connections[channel].add(websocket)
                            await websocket.send_json({
                                "type": "subscribed",
                                "channel": channel
                            })
                        else:
                            await websocket.send_json({
                                "type": "error",
                                "channel": channel,
                                "message": "Access denied"
                            })
                    elif channel.startswith("app:live:"):
                        # App Builder live channels - same access check as the REST app read.
                        app_id = channel.split(":", 2)[2]
                        if await can_access_app(user, app_id):
                            if channel not in manager.connections:
                                manager.connections[channel] = set()
                            manager.connections[channel].add(websocket)
                            await websocket.send_json({
                                "type": "subscribed",
                                "channel": channel
                            })
                        else:
                            await websocket.send_json({
                                "type": "error",
                                "channel": channel,
                                "message": "Access denied"
                            })
                    elif channel == "package:install":
                        # Package installation channel - shared, superusers only
                        if await can_access_global_platform(user):
                            access_checks.note_power("platform.read", None, subject=f"channel:{channel}")
                            if channel not in manager.connections:
                                manager.connections[channel] = set()
                            manager.connections[channel].add(websocket)
                            await websocket.send_json({
                                "type": "subscribed",
                                "channel": channel
                            })
                        else:
                            await websocket.send_json({
                                "type": "error",
                                "channel": channel,
                                "message": "Access denied"
                            })
                    elif channel.startswith("git:"):
                        # Git job channels have no owning-user record to check
                        # against; gate to platform admins until they carry
                        # real job ownership.
                        if await can_access_global_platform(user):
                            access_checks.note_power("platform.read", None, subject=f"channel:{channel}")
                            if channel not in manager.connections:
                                manager.connections[channel] = set()
                            manager.connections[channel].add(websocket)
                            await websocket.send_json({
                                "type": "subscribed",
                                "channel": channel
                            })
                        else:
                            await websocket.send_json({
                                "type": "error",
                                "channel": channel,
                                "message": "Access denied"
                            })
                    elif channel.startswith("agent-run:"):
                        # Agent run detail channels - only the run's own caller, or bypass
                        run_id = channel.split(":", 1)[1]
                        if await can_access_agent_run(user, run_id):
                            if channel not in manager.connections:
                                manager.connections[channel] = set()
                            manager.connections[channel].add(websocket)
                            await websocket.send_json({
                                "type": "subscribed",
                                "channel": channel
                            })
                        else:
                            await websocket.send_json({
                                "type": "error",
                                "channel": channel,
                                "message": "Access denied"
                            })
                    elif channel == "agent-runs":
                        async with get_db_context() as db:
                            allowed = await _current_ws_caller(db, user) is not None
                        if allowed:
                            manager.connections.setdefault(channel, set()).add(websocket)
                            await websocket.send_json({"type": "subscribed", "channel": channel})
                        else:
                            await websocket.send_json({"type": "error", "channel": channel, "message": "Access denied"})
                    elif channel.startswith("notification:"):
                        allowed = channel == f"notification:{user.user_id}" or (
                            channel == "notification:admins" and await can_access_admin_notifications(user)
                        )
                        if allowed:
                            manager.connections.setdefault(channel, set()).add(websocket)
                            await websocket.send_json({"type": "subscribed", "channel": channel})
                        else:
                            await websocket.send_json({"type": "error", "channel": channel, "message": "Access denied"})
                    elif channel.startswith("chat:"):
                        conversation_id = channel.split(":", 1)[1]
                        if await can_access_conversation_current(user, conversation_id):
                            if channel not in manager.connections:
                                manager.connections[channel] = set()
                            manager.connections[channel].add(websocket)
                            await websocket.send_json({
                                "type": "subscribed",
                                "channel": channel,
                            })
                        else:
                            await websocket.send_json({
                                "type": "error",
                                "channel": channel,
                                "message": "Access denied",
                            })
                    elif channel.startswith("summary-backfill:"):
                        # Summary backfill job progress — platform admins only.
                        # Mirrors the initial-connect whitelist above; without this
                        # branch, late subscriptions from SummaryBackfillProgress
                        # silently no-op and no broadcast ever reaches the client
                        # (that's why cancel didn't dismiss and counters didn't tick).
                        if await can_access_global_platform(user):
                            access_checks.note_power("platform.read", None, subject=f"channel:{channel}")
                            if channel not in manager.connections:
                                manager.connections[channel] = set()
                            manager.connections[channel].add(websocket)
                            await websocket.send_json({
                                "type": "subscribed",
                                "channel": channel
                            })
                        else:
                            await websocket.send_json({
                                "type": "error",
                                "channel": channel,
                                "message": "Access denied"
                            })
                    elif channel == "platform_workers":
                        # Platform workers channel - diagnostics, platform admins only
                        if await can_access_global_platform(user):
                            access_checks.note_power("platform.read", None, subject=f"channel:{channel}")
                            if channel not in manager.connections:
                                manager.connections[channel] = set()
                            manager.connections[channel].add(websocket)
                            await websocket.send_json({
                                "type": "subscribed",
                                "channel": channel
                            })
                        else:
                            await websocket.send_json({
                                "type": "error",
                                "channel": channel,
                                "message": "Access denied"
                            })
                    elif channel.startswith("table:"):
                        # Policy-driven subscribe: probe authorization, resolve
                        # the user-supplied name-or-id to the canonical UUID
                        # channel (publisher always emits on `table:{uuid}`),
                        # then register per-connection state for the four-way
                        # fanout filter under that canonical key.
                        canonical_channel = await _authorize_table_subscribe(
                            websocket, user, spec
                        )
                        if canonical_channel is None:
                            continue
                        if canonical_channel not in manager.connections:
                            manager.connections[canonical_channel] = set()
                        manager.connections[canonical_channel].add(websocket)
                        # Echo the canonical channel back so client + server
                        # agree on a single name for subsequent unsubscribe
                        # / revocation messages.
                        await websocket.send_json({
                            "type": "subscribed",
                            "channel": canonical_channel
                        })
                    elif channel.startswith("files:"):
                        canonical_channel = await _authorize_file_subscribe(
                            websocket, user, spec
                        )
                        if canonical_channel is None:
                            continue
                        if canonical_channel not in manager.connections:
                            manager.connections[canonical_channel] = set()
                        manager.connections[canonical_channel].add(websocket)
                        await websocket.send_json({
                            "type": "subscribed",
                            "channel": spec.name
                        })
                _judge_access_checks("/ws/connect")

            elif data.get("type") == "unsubscribe":
                channel = data.get("channel")
                if channel:
                    # Table channels may be subscribed by name but registered
                    # under the canonical UUID channel. Resolve before pop.
                    if channel.startswith("table:"):
                        name_or_id = channel.split(":", 1)[1]
                        canonical_id = await _resolve_table_id(name_or_id, user)
                        canonical_channel = (
                            f"table:{canonical_id}"
                            if canonical_id is not None
                            else channel
                        )
                        if canonical_channel in manager.connections:
                            manager.connections[canonical_channel].discard(websocket)
                        if canonical_id is not None:
                            table_subs = getattr(websocket.state, "table_subscriptions", None)
                            if table_subs is not None:
                                table_subs.pop(canonical_id, None)
                        await websocket.send_json({
                            "type": "unsubscribed",
                            "channel": canonical_channel,
                        })
                    elif channel.startswith("files:"):
                        parsed = _parse_file_channel(channel)
                        if parsed is not None:
                            location, prefix = parsed
                            file_subs = getattr(websocket.state, "file_subscriptions", None)
                            if file_subs is not None:
                                for sub_key, sub in list(file_subs.items()):
                                    if (
                                        sub.get("requested_channel") == channel
                                        or (
                                            sub.get("location") == location
                                            and sub.get("prefix") == prefix
                                        )
                                    ):
                                        canonical_channel = sub.get("channel_name")
                                        if canonical_channel in manager.connections:
                                            manager.connections[canonical_channel].discard(websocket)
                                        file_subs.pop(sub_key, None)
                        await websocket.send_json({
                            "type": "unsubscribed",
                            "channel": channel,
                        })
                    elif channel in manager.connections:
                        manager.connections[channel].discard(websocket)
                        await websocket.send_json({
                            "type": "unsubscribed",
                            "channel": channel
                        })

            elif data.get("type") == "ping":
                await websocket.send_json({"type": "pong"})

    except WebSocketDisconnect:
        manager.disconnect(websocket)
        logger.info(f"WebSocket disconnected for user {user.user_id}")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        manager.disconnect(websocket)
    finally:
        access_checks.stop_collecting(checks)


@router.websocket("/execution/{execution_id}")
async def websocket_execution(
    websocket: WebSocket,
    execution_id: str,
):
    """
    Convenience endpoint for subscribing to a single execution.

    Equivalent to connecting with channels=execution:{execution_id}
    """
    user = await get_current_user_ws(websocket)

    if not user:
        await websocket.close(code=4001, reason="Unauthorized")
        return
    if user.embed and user.embed_kind == "form":
        await websocket.close(code=4003, reason="Form sessions cannot use WebSockets")
        return

    # Validate user has access to this execution
    checks = _collect_websocket_checks(user)
    try:
        allowed = await can_access_execution(user, execution_id)
        _judge_access_checks("/ws/execution/{execution_id}")
    finally:
        access_checks.stop_collecting(checks)
    if not allowed:
        await websocket.close(code=4003, reason="Access denied")
        return

    channel = f"execution:{execution_id}"

    websocket._delivery_dispatcher = _make_delivery_dispatcher(websocket, user)  # type: ignore[attr-defined]
    try:
        await manager.connect(websocket, [channel])
        logger.info(f"WebSocket connected to execution {log_safe(execution_id)}")

        await websocket.send_json({
            "type": "connected",
            "executionId": execution_id
        })

        while True:
            data = await websocket.receive_json()
            await _require_live_impersonation(websocket, user)
            if data.get("type") == "ping":
                await websocket.send_json({"type": "pong"})

    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        manager.disconnect(websocket)
