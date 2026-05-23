"""
WebSocket Router

Provides real-time updates via WebSocket connections.
Replaces Azure Web PubSub with native FastAPI WebSockets.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from shared.policies.probe import is_subscribe_authorized
from shared.policies.subscription import decide_visibility_change
from shared.role_cache import get_user_roles
from src.core.auth import UserPrincipal, get_current_user_ws
from src.core.database import get_db_context
from src.core.log_safety import log_safe
from src.core.pubsub import manager
from src.models import Conversation, Execution
from src.models.contracts.policies import Expr, TablePolicies
from src.models.orm import Agent
from src.models.orm.applications import Application
from src.models.orm.tables import Table as TableOrm
from src.services.agent_run_access import load_agent_run_for_user

logger = logging.getLogger(__name__)

_AGENT_RUNS_CHANNEL = "agent-runs"
_AGENT_RUNS_CHANNEL_PREFIX = "agent-runs:"


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
            out.append(ChannelSpec(name=ch["name"], filter=filter_expr))
        else:
            raise WSError("channel must be a string or {name, filter} object")
    return out


async def _resolve_table_id(name_or_id: str, user: UserPrincipal) -> str | None:
    """Resolve a table reference (UUID string or name) to its canonical UUID,
    enforcing the same org gate as the REST `get_table_or_404` helper:
    a non-superuser may only resolve to tables in their own org or global.

    Returns None if no table matches OR the resolved table is outside the
    user's org reach (404-style — callers translate to the user-visible
    "Table not found" error).
    """
    async with get_db_context() as db:
        try:
            table_uuid = UUID(name_or_id)
            stmt = select(TableOrm.id, TableOrm.organization_id).where(
                TableOrm.id == table_uuid,
            )
        except ValueError:
            stmt = select(TableOrm.id, TableOrm.organization_id).where(
                TableOrm.name == name_or_id,
            )
            # Non-superusers' name lookups are restricted to their own org
            # (cascade with global) — superusers see all orgs.
            if not user.is_superuser:
                stmt = stmt.where(
                    (TableOrm.organization_id == user.organization_id)
                    | (TableOrm.organization_id.is_(None))
                )
        result = await db.execute(stmt)
        row = result.one_or_none()

    if row is None:
        return None

    table_id, table_org = row[0], row[1]

    # Org gate for UUID lookups (name lookups already constrained above).
    if not user.is_superuser:
        if table_org is not None and table_org != user.organization_id:
            return None

    return str(table_id)


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
_table_policy_cache: dict[str, TablePolicies | None] = {}
# Bounded to keep memory predictable under high table churn. Eviction is
# FIFO — for a hot working set of <_POLICY_CACHE_MAX tables, every read is
# a hit; cold reads pay one DB load.
_POLICY_CACHE_MAX = 256


def _invalidate_table_policy_cache(table_id: str) -> None:
    """Drop the cached policies for a table; next read reloads from DB."""
    _table_policy_cache.pop(table_id, None)


async def _load_policies_for_table(table_id: str) -> TablePolicies | None:
    """Load policies for a table by id (UUID) or name. Returns None if missing.

    Cache-first for UUID lookups (the hot path during `document_change`
    fanout). Name lookups bypass the cache because the same name can resolve
    to different tables in different orgs; UUIDs are unambiguous.
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

    if is_uuid and table_id in _table_policy_cache:
        return _table_policy_cache[table_id]

    async with get_db_context() as db:
        if is_uuid:
            stmt = select(TableOrm.access).where(TableOrm.id == UUID(table_id))
        else:
            stmt = select(TableOrm.access).where(TableOrm.name == table_id)
        result = await db.execute(stmt)
        row = result.one_or_none()

    if row is None:
        policies: TablePolicies | None = None
    elif row[0] is None:
        policies = TablePolicies()
    else:
        policies = TablePolicies.model_validate(row[0])

    if is_uuid:
        # Bounded FIFO eviction — pop arbitrary entry when full.
        if len(_table_policy_cache) >= _POLICY_CACHE_MAX:
            _table_policy_cache.pop(next(iter(_table_policy_cache)), None)
        _table_policy_cache[table_id] = policies

    return policies


async def _populate_user_roles(user: UserPrincipal) -> None:
    """Hydrate `role_ids` / `role_names` on the principal if not already loaded.

    `get_current_user_ws` does not hit the DB for roles (the JWT carries only
    the `roles` claim, not the `user_roles` rows the policy evaluator's
    `has_role` reads). Call this once per connection before the first
    table-subscription policy check. Reads go through the per-user role cache
    (Redis); DB is fallback on miss.
    """
    if user.role_ids or user.role_names:
        return

    async with get_db_context() as db:
        role_ids, role_names = await get_user_roles(user.user_id, db)
        user.role_ids = role_ids
        user.role_names = role_names


def _make_table_dispatcher(websocket: WebSocket, user: UserPrincipal) -> Any:
    """Return an async dispatcher that filters table-channel messages for this WS.

    Attached to the WebSocket as `_table_dispatcher` and invoked by the pubsub
    manager's `_send_local` for every `table:` message destined for this
    connection. The dispatcher consults the per-connection
    `table_subscriptions` state to decide what (if anything) to forward.
    """

    async def dispatcher(channel_name: str, payload: dict[str, Any]) -> None:
        await _handle_table_message(websocket, user, channel_name, payload)

    return dispatcher


async def _handle_table_message(
    websocket: WebSocket,
    user: UserPrincipal,
    channel_name: str,
    payload: dict[str, Any],
) -> None:
    """Apply the four-way visibility decision and emit the right action.

    `policy_changed` triggers a re-evaluation; if the user no longer satisfies
    `is_subscribe_authorized`, a `subscription_revoked` notice is sent and the
    subscription state is cleared.
    """
    table_id = channel_name.split(":", 1)[1]
    table_subs: dict[str, dict[str, Any]] = getattr(websocket.state, "table_subscriptions", {})
    sub = table_subs.get(table_id)
    if sub is None:
        return  # no longer subscribed

    msg_type = payload.get("type")

    if msg_type == "policy_changed":
        # Drop the in-process policy cache for this table so the
        # re-evaluation (and every subsequent document_change for this
        # table on this process) sees the fresh policies.
        _invalidate_table_policy_cache(table_id)
        await _re_evaluate_subscription(websocket, user, table_id)
        return

    if msg_type != "document_change":
        return

    policies = await _load_policies_for_table(table_id)
    if policies is None:
        return

    decision = decide_visibility_change(
        old_row=payload.get("old_row"),
        new_row=payload.get("new_row"),
        policies=policies,
        user=user,
        user_filter=sub.get("filter"),
    )
    if decision is None:
        return

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


async def _re_evaluate_subscription(
    websocket: WebSocket,
    user: UserPrincipal,
    table_id: str,
) -> None:
    """Re-run subscribe-time authorization after a policy edit; revoke if no."""
    policies = await _load_policies_for_table(table_id)
    if policies is None or not is_subscribe_authorized(policies, user):
        await websocket.send_json({
            "type": "subscription_revoked",
            "channel": f"table:{table_id}",
        })
        table_subs: dict[str, dict[str, Any]] = getattr(websocket.state, "table_subscriptions", {})
        table_subs.pop(table_id, None)
        # The pubsub manager unsubscribes on disconnect; for a partial revoke,
        # we just stop processing future messages on this table by clearing
        # the per-connection subscription state. The dispatcher early-exits
        # for unknown table_ids.


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
    policies = await _load_policies_for_table(canonical_id)
    if policies is None:
        await websocket.send_json({
            "type": "error",
            "channel": spec.name,
            "message": "Table not found",
        })
        return None

    await _populate_user_roles(user)
    if not is_subscribe_authorized(policies, user):
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

    if not hasattr(websocket, "_table_dispatcher"):
        websocket._table_dispatcher = _make_table_dispatcher(websocket, user)  # type: ignore[attr-defined]
    return canonical_channel


async def can_access_conversation(user: UserPrincipal, conversation_id: str) -> tuple[bool, Conversation | None]:
    """
    Check if user can access a conversation.

    Args:
        user: The authenticated user
        conversation_id: The conversation ID to check access for

    Returns:
        Tuple of (has_access, conversation_object)
    """
    try:
        conv_uuid = UUID(conversation_id)
    except ValueError:
        return False, None

    async with get_db_context() as db:
        result = await db.execute(
            select(Conversation)
            .options(
                selectinload(Conversation.agent).selectinload(Agent.tools),
                selectinload(Conversation.agent).selectinload(Agent.delegated_agents),
                selectinload(Conversation.user),
            )
            .where(Conversation.id == conv_uuid)
            .where(Conversation.user_id == user.user_id)
            .where(Conversation.is_active.is_(True))
        )
        conversation = result.scalar_one_or_none()

        if conversation is None:
            return False, None

        return True, conversation


async def can_access_execution(user: UserPrincipal, execution_id: str) -> bool:
    """
    Check if user can access an execution (owner or superuser).

    Args:
        user: The authenticated user
        execution_id: The execution ID to check access for

    Returns:
        True if user can access, False otherwise
    """
    # Superusers can access any execution
    if user.is_superuser:
        return True

    # Embed users: check Redis key linking their session (jti) to the execution
    if user.embed and user.jti:
        from src.core.cache.keys import embed_execution_key
        from src.core.cache.redis_client import get_redis

        async with get_redis() as r:
            return bool(await r.exists(embed_execution_key(user.jti, execution_id)))

    try:
        execution_uuid = UUID(execution_id)
    except ValueError:
        return False

    async with get_db_context() as db:
        result = await db.execute(
            select(Execution.executed_by).where(Execution.id == execution_uuid)
        )
        row = result.scalar_one_or_none()

        if row is None:
            # Execution doesn't exist - allow subscription anyway
            # (they won't receive anything, and this avoids timing attacks)
            return True

        return row == user.user_id


async def can_access_app(user: UserPrincipal, app_id: str) -> bool:
    """
    Check if user can access an application.

    Access is granted if:
    - User is a superuser (platform admin)
    - App is global (organization_id is NULL)
    - App belongs to user's organization

    Args:
        user: The authenticated user
        app_id: The application ID to check access for

    Returns:
        True if user can access, False otherwise
    """
    # Superusers can access any app
    if user.is_superuser:
        return True

    try:
        app_uuid = UUID(app_id)
    except ValueError:
        return False

    async with get_db_context() as db:
        result = await db.execute(
            select(Application.organization_id).where(Application.id == app_uuid)
        )
        # Note: scalar_one_or_none returns None if no row, or the column value (which may also be None for global apps)
        # We need to check if the row exists first
        row_result = result.one_or_none()

        if row_result is None:
            # App doesn't exist - allow subscription anyway
            # (they won't receive anything, and this avoids timing attacks)
            return True

        org_id = row_result[0]

        # Global app (organization_id is NULL) - accessible to all authenticated users
        if org_id is None:
            return True

        # Org-scoped app - check if user is in the same org
        return org_id == user.organization_id


def _agent_runs_channel_for_user(user: UserPrincipal) -> str | None:
    if user.is_superuser:
        return f"{_AGENT_RUNS_CHANNEL_PREFIX}all"
    if user.organization_id:
        return f"{_AGENT_RUNS_CHANNEL_PREFIX}org:{user.organization_id}"
    return None


def _resolve_agent_runs_channel(user: UserPrincipal, channel: str) -> str | None:
    if channel == _AGENT_RUNS_CHANNEL:
        return _agent_runs_channel_for_user(user)
    if (
        channel == f"{_AGENT_RUNS_CHANNEL_PREFIX}all"
        or channel == f"{_AGENT_RUNS_CHANNEL_PREFIX}global"
    ):
        return channel if user.is_superuser else None
    if channel.startswith(f"{_AGENT_RUNS_CHANNEL_PREFIX}org:"):
        org_id = channel.split(":", 2)[2]
        if user.is_superuser:
            return channel
        return channel if str(user.organization_id) == org_id else None
    return None


async def can_access_agent_run(user: UserPrincipal, run_id: str) -> bool:
    try:
        run_uuid = UUID(run_id)
    except ValueError:
        return False

    async with get_db_context() as db:
        run = await load_agent_run_for_user(db, run_uuid, user)
        return run is not None


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
    - user:{user_id} - User notifications
    - system - System broadcasts

    Query params:
        channels: List of channels to subscribe to

    Example:
        ws://localhost:8000/ws/connect?channels=execution:abc-123&channels=user:user-456

    Messages are JSON with structure:
        {
            "type": "execution_update" | "execution_log" | "notification" | "system_event",
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
        elif channel == "package:install":
            # Package installation channel - shared, superusers only
            if user.is_superuser:
                allowed_channels.append(channel)
        elif channel.startswith("git:"):
            # Git job channels - ephemeral, job-specific UUIDs
            # Authorization: any authenticated user can subscribe
            # The job_id is a one-time UUID that only the requester knows
            # (returned by the API after queueing the job)
            allowed_channels.append(channel)
        elif channel.startswith("notification:"):
            # Notification channels - users can subscribe to their own
            if channel == f"notification:{user.user_id}":
                allowed_channels.append(channel)
            # Platform admins can subscribe to admin notifications
            elif channel == "notification:admins" and user.is_superuser:
                allowed_channels.append(channel)
        elif channel.startswith("chat:"):
            # Chat conversation channels - validate user owns the conversation
            conversation_id = channel.split(":", 1)[1]
            has_access, _ = await can_access_conversation(user, conversation_id)
            if has_access:
                allowed_channels.append(channel)
        elif channel.startswith("history:"):
            # History channels for real-time updates
            # history:user:{user_id} - Allow only for the user's own channel
            # history:GLOBAL - Allow only for platform admins
            if channel == f"history:user:{user.user_id}":
                allowed_channels.append(channel)
            elif channel == "history:GLOBAL" and user.is_superuser:
                allowed_channels.append(channel)
        elif channel.startswith("local-runner:"):
            # Local runner channels - users can subscribe to their own
            if channel == f"local-runner:{user.user_id}":
                allowed_channels.append(channel)
        elif channel.startswith("devrun:"):
            # Legacy dev run channels - users can subscribe to their own
            if channel == f"devrun:{user.user_id}":
                allowed_channels.append(channel)
        elif channel.startswith("cli-session:"):
            # CLI session channels - allow all (session ownership validated elsewhere)
            allowed_channels.append(channel)
        elif channel.startswith("cli-sessions:"):
            # CLI sessions list channel - users can subscribe to their own
            if channel == f"cli-sessions:{user.user_id}":
                allowed_channels.append(channel)
        elif channel.startswith("event-source:"):
            # Event source channels for real-time event updates
            # Platform admins can view all, org users can view their org's sources
            # Access is validated on event delivery, so we allow subscription
            allowed_channels.append(channel)
        elif channel.startswith("reindex:"):
            # Reindex job progress channels - platform admins only
            if user.is_superuser:
                allowed_channels.append(channel)
        elif channel.startswith("app:draft:"):
            # App Builder draft channels - validate user has access to the app
            app_id = channel.split(":", 2)[2]
            if await can_access_app(user, app_id):
                allowed_channels.append(channel)
        elif channel.startswith("app:live:"):
            # App Builder live channels - validate user has access to the app
            app_id = channel.split(":", 2)[2]
            if await can_access_app(user, app_id):
                allowed_channels.append(channel)
        elif channel == "file-activity":
            # File activity channel - platform admins only
            if user.is_superuser:
                allowed_channels.append(channel)
        elif channel.startswith("table:"):
            # Table channels carry a per-connection user filter that cannot be
            # expressed in a query string. Require runtime `subscribe` messages
            # for table channels — query-string subscription is rejected.
            continue
        elif channel == "system":
            allowed_channels.append(channel)
        elif channel.startswith("agent-run:"):
            # Agent run detail channels carry per-run metadata/steps.
            run_id = channel.split(":", 1)[1]
            if await can_access_agent_run(user, run_id):
                allowed_channels.append(channel)
        elif channel == _AGENT_RUNS_CHANNEL or channel.startswith(
            _AGENT_RUNS_CHANNEL_PREFIX
        ):
            scoped_channel = _resolve_agent_runs_channel(user, channel)
            if scoped_channel:
                allowed_channels.append(scoped_channel)
        elif channel.startswith("summary-backfill:"):
            # Summary backfill job progress — platform admins only
            if user.is_superuser:
                allowed_channels.append(channel)
        elif channel == "platform_workers":
            # Platform workers channel - diagnostics, platform admins only
            if user.is_superuser:
                allowed_channels.append(channel)

    # Always subscribe to user's own channel
    user_channel = f"user:{user.user_id}"
    if user_channel not in allowed_channels:
        allowed_channels.append(user_channel)

    # Track active chat tasks per conversation so they can be cancelled
    active_chat_tasks: dict[str, asyncio.Task] = {}
    pending_messages: dict[str, tuple[str, str | None]] = {}  # conversation_id -> (message, local_id)

    # Per-connection state for policy-driven table subscriptions.
    # Populated by `_authorize_table_subscribe`; consulted by the dispatcher.
    websocket.state.table_subscriptions = {}

    try:
        await manager.connect(websocket, allowed_channels)
        logger.info(f"WebSocket connected for user {user.user_id}, channels: {log_safe(allowed_channels)}")

        # Send connection confirmation
        await websocket.send_json({
            "type": "connected",
            "channels": allowed_channels,
            "userId": str(user.user_id)
        })

        # Keep connection alive and handle incoming messages
        while True:
            data = await websocket.receive_json()

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
                    elif channel.startswith("cli-session:"):
                        if channel not in manager.connections:
                            manager.connections[channel] = set()
                        manager.connections[channel].add(websocket)
                        await websocket.send_json({
                            "type": "subscribed",
                            "channel": channel
                        })
                    elif channel.startswith("event-source:"):
                        # Event source channels for real-time event updates
                        if channel not in manager.connections:
                            manager.connections[channel] = set()
                        manager.connections[channel].add(websocket)
                        await websocket.send_json({
                            "type": "subscribed",
                            "channel": channel
                        })
                    elif channel.startswith("history:"):
                        # History channels for real-time execution updates
                        # history:user:{user_id} - Allow only for the user's own channel
                        # history:GLOBAL - Allow only for platform admins
                        if channel == f"history:user:{user.user_id}" or (channel == "history:GLOBAL" and user.is_superuser):
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
                    elif channel.startswith("app:draft:") or channel.startswith("app:live:"):
                        # App Builder channels - validate user has access to the app
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
                        if user.is_superuser:
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
                        # Git sync job channels - ephemeral, job-specific UUIDs
                        # Any authenticated user can subscribe (job ID is a secret token)
                        if channel not in manager.connections:
                            manager.connections[channel] = set()
                        manager.connections[channel].add(websocket)
                        await websocket.send_json({
                            "type": "subscribed",
                            "channel": channel
                        })
                    elif channel.startswith("agent-run:"):
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
                    elif channel == _AGENT_RUNS_CHANNEL or channel.startswith(
                        _AGENT_RUNS_CHANNEL_PREFIX
                    ):
                        scoped_channel = _resolve_agent_runs_channel(user, channel)
                        if scoped_channel:
                            if scoped_channel not in manager.connections:
                                manager.connections[scoped_channel] = set()
                            manager.connections[scoped_channel].add(websocket)
                            await websocket.send_json({
                                "type": "subscribed",
                                "channel": scoped_channel
                            })
                        else:
                            await websocket.send_json({
                                "type": "error",
                                "channel": channel,
                                "message": "Access denied"
                            })
                    elif channel.startswith("summary-backfill:"):
                        # Summary backfill job progress — platform admins only.
                        # Mirrors the initial-connect whitelist above; without this
                        # branch, late subscriptions from SummaryBackfillProgress
                        # silently no-op and no broadcast ever reaches the client
                        # (that's why cancel didn't dismiss and counters didn't tick).
                        if user.is_superuser:
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
                        if user.is_superuser:
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

            elif data.get("type") == "unsubscribe":
                channel = data.get("channel")
                if channel:
                    if channel == _AGENT_RUNS_CHANNEL or channel.startswith(
                        _AGENT_RUNS_CHANNEL_PREFIX
                    ):
                        channel = _resolve_agent_runs_channel(user, channel) or channel
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
                    elif channel in manager.connections:
                        manager.connections[channel].discard(websocket)
                        await websocket.send_json({
                            "type": "unsubscribed",
                            "channel": channel
                        })

            elif data.get("type") == "ping":
                await websocket.send_json({"type": "pong"})

            elif data.get("type") == "chat":
                # Handle chat message - process and stream response
                conversation_id = data.get("conversation_id")
                message_text = data.get("message", "")
                local_id = data.get("local_id")  # Client-generated ID for dedup

                if not conversation_id or not message_text:
                    await websocket.send_json({
                        "type": "error",
                        "error": "Missing conversation_id or message"
                    })
                    continue

                # Validate access and get conversation
                has_access, conversation = await can_access_conversation(user, conversation_id)
                if not has_access or not conversation:
                    await websocket.send_json({
                        "type": "error",
                        "error": "Conversation not found or access denied"
                    })
                    continue

                # If a task is already running for this conversation, queue the
                # message instead of cancelling.  Cancelling mid-tool-call causes
                # interleaved messages that break the Anthropic API contract.
                existing_task = active_chat_tasks.get(conversation_id)
                if existing_task and not existing_task.done():
                    pending_messages[conversation_id] = (message_text, local_id)
                    continue

                # No running task — process immediately
                def _start_chat_task(cid: str, msg: str, lid: str | None) -> asyncio.Task:
                    t = asyncio.create_task(
                        _process_chat_message(
                            websocket=websocket,
                            user=user,
                            conversation_id=cid,
                            message=msg,
                            local_id=lid,
                        )
                    )
                    active_chat_tasks[cid] = t

                    def _on_task_done(_t: asyncio.Task, _cid: str = cid) -> None:
                        active_chat_tasks.pop(_cid, None)
                        queued = pending_messages.pop(_cid, None)
                        if queued:
                            q_msg, q_lid = queued
                            _start_chat_task(_cid, q_msg, q_lid)

                    t.add_done_callback(_on_task_done)
                    return t

                _start_chat_task(conversation_id, message_text, local_id)

            elif data.get("type") == "chat_stop":
                conversation_id = data.get("conversation_id")
                if conversation_id:
                    pending_messages.pop(conversation_id, None)
                    task = active_chat_tasks.pop(conversation_id, None)
                    if task and not task.done():
                        task.cancel()

    except WebSocketDisconnect:
        # Cancel all active chat tasks for this connection
        pending_messages.clear()
        for task in active_chat_tasks.values():
            if not task.done():
                task.cancel()
        active_chat_tasks.clear()
        manager.disconnect(websocket)
        logger.info(f"WebSocket disconnected for user {user.user_id}")
    except Exception as e:
        # Cancel all active chat tasks for this connection
        pending_messages.clear()
        for task in active_chat_tasks.values():
            if not task.done():
                task.cancel()
        active_chat_tasks.clear()
        logger.error(f"WebSocket error: {e}")
        manager.disconnect(websocket)


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

    # Validate user has access to this execution
    if not await can_access_execution(user, execution_id):
        await websocket.close(code=4003, reason="Access denied")
        return

    channel = f"execution:{execution_id}"

    try:
        await manager.connect(websocket, [channel])
        logger.info(f"WebSocket connected to execution {log_safe(execution_id)}")

        await websocket.send_json({
            "type": "connected",
            "executionId": execution_id
        })

        while True:
            data = await websocket.receive_json()
            if data.get("type") == "ping":
                await websocket.send_json({"type": "pong"})

    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        manager.disconnect(websocket)


async def _generate_conversation_title(
    db,
    conversation: Conversation,
    user_message: str,
) -> str | None:
    """
    Generate a concise title for a conversation using LLM.

    Returns the generated title or None if generation fails.
    """
    from src.services.llm import get_llm_client, LLMMessage

    try:
        llm_client = await get_llm_client(db)

        # Use a simple prompt to generate a title
        response = await llm_client.complete(
            messages=[
                LLMMessage(
                    role="system",
                    content="Generate a very short, concise title (3-6 words max) for a conversation that starts with the following message. Respond with ONLY the title, no quotes or punctuation at the end.",
                ),
                LLMMessage(
                    role="user",
                    content=user_message,
                ),
            ],
            max_tokens=1024,
        )

        if response.content:
            # Clean up the title - remove quotes, limit length
            title = response.content.strip().strip('"\'')
            # Truncate if too long (max 100 chars)
            if len(title) > 100:
                title = title[:97] + "..."
            return title

    except Exception as e:
        logger.warning(f"Failed to generate conversation title: {e}")

    return None


async def _process_chat_message(
    websocket: WebSocket,
    user: UserPrincipal,
    conversation_id: str,
    message: str,
    local_id: str | None = None,
) -> None:
    """
    Process a chat message and stream the response.

    Sends streaming chunks directly to the WebSocket, then broadcasts
    the final message to the chat channel for any other subscribers.

    DB connections are only held for short discrete operations (loading
    conversation, saving messages, etc.) — never during LLM streaming.

    Args:
        websocket: The WebSocket connection
        user: The authenticated user
        conversation_id: The conversation ID
        message: The user's message
    """
    from src.core.database import get_session_factory
    from src.services.agent_executor import AgentExecutor

    try:
        session_factory = get_session_factory()
        conv_uuid = UUID(conversation_id)

        # Load conversation in a short-lived session (released before streaming)
        async with session_factory() as db:
            result = await db.execute(
                select(Conversation)
                .options(
                    selectinload(Conversation.agent).selectinload(Agent.tools),
                    selectinload(Conversation.agent).selectinload(Agent.delegated_agents),
                    selectinload(Conversation.user),
                )
                .where(Conversation.id == conv_uuid)
            )
            conversation = result.scalar_one_or_none()

        if not conversation:
            await websocket.send_json({
                "type": "error",
                "conversation_id": conversation_id,
                "error": "Conversation not found"
            })
            return

        # Check if conversation needs a title (no title set yet)
        needs_title = conversation.title is None

        # Execute chat — executor manages its own short-lived sessions
        executor = AgentExecutor(session_factory)

        # Track streamed content so we can persist partial responses on cancellation
        streamed_content = ""
        assistant_message_id: str | None = None

        try:
            async for chunk in executor.chat(
                agent=conversation.agent,
                conversation=conversation,
                user_message=message,
                stream=True,
                local_id=local_id,
            ):
                # Track partial content from deltas
                if chunk.type == "delta" and chunk.content:
                    streamed_content += chunk.content
                elif chunk.type == "message_start" and chunk.assistant_message_id:
                    assistant_message_id = chunk.assistant_message_id
                elif chunk.type == "assistant_message_end":
                    # Text segment was saved by executor; reset for next segment
                    streamed_content = ""
                    assistant_message_id = None

                # Send chunk to WebSocket with conversation_id for client routing
                chunk_data = chunk.model_dump(exclude_none=True)
                chunk_data["conversation_id"] = conversation_id
                await websocket.send_json(chunk_data)
        except asyncio.CancelledError:
            logger.info(f"Chat processing cancelled for conversation {log_safe(conversation_id)}")

            # Save partial assistant response if we have streamed content
            # that hasn't been saved yet (no assistant_message_end was received)
            if streamed_content:
                from src.models.enums import MessageRole

                await executor._save_message(
                    conversation_id=UUID(conversation_id),
                    role=MessageRole.ASSISTANT,
                    content=streamed_content,
                    message_id=UUID(assistant_message_id) if assistant_message_id else None,
                )

            try:
                await websocket.send_json({
                    "type": "done",
                    "conversation_id": conversation_id,
                })
            except Exception:
                pass  # WebSocket may already be closed
            return

        # Generate title if this is a new conversation (no title yet)
        if needs_title:
            async with session_factory() as db:
                title = await _generate_conversation_title(db, conversation, message)
                if title:
                    conv = await db.get(Conversation, conv_uuid)
                    if conv:
                        conv.title = title
                    await db.commit()

            # Send title update to client AFTER commit
            if title:
                await websocket.send_json({
                    "type": "title_update",
                    "conversation_id": conversation_id,
                    "title": title,
                })

    except Exception as e:
        logger.error(f"Chat processing error: {e}", exc_info=True)
        try:
            await websocket.send_json({
                "type": "error",
                "conversation_id": conversation_id,
                "error": str(e)
            })
        except Exception:
            pass  # WebSocket may be closed
