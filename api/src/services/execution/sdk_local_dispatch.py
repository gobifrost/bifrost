"""
Parent-side dispatcher for the engine-local SDK operation transport.

The worker parent (``ProcessPoolManager``) serves ``config`` requests
(get, set, list, delete) and ``integrations`` requests (get,
list_mappings, get_mapping, upsert_mapping, delete_mapping,
refresh_token) arriving on each child's dedicated SDK channel.
Identity, scope, and Solution install id come exclusively from the parent's
own dispatch context (:func:`principal_from_context`) — child-supplied
scope strings are treated as untrusted requests and re-validated through
the same ``resolve_effective_scope`` rule table the HTTP path uses, a
child-supplied Solution id is never read, and the actor
email for mutation audit is the effective SDK actor (engine sentinel for
workflows, service identity for ``@service`` children), never the
initiating user's ``caller.email`` and never child frames.

Each operation runs on the parent's pooled database engine with one short
session, and calls the exact shared business service
(``shared.sdk_config``, ``shared.sdk_integrations``) the HTTP handler calls, so cascade,
external-user behavior, secret handling, type coercion, audit
attribution, commit/cache ordering, declared-Solution behavior, OAuth
token cascade, and missing-key mapping are identical
by construction. Large payloads in either direction travel as bounded
chunked frames (header plus ordered parts, every frame within the wire
bound); small payloads use a single frame.

Allowlist (stage 3a): ``config.get/set/list/delete``, the full
``integrations`` facade (``get/list_mappings/get_mapping/upsert_mapping/
delete_mapping/refresh_token``), and the table document reads
(``tables.get/query/count``). Engine import fast path: ``modules.resolve``
and ``modules.fetch`` (served on the dedicated import channel through the
shared ``sdk_modules`` service, scoped by the parent-derived principal).
Unknown operations or wire versions get an error response — never silent
acceptance, never arbitrary route forwarding. The existing engine token
path is untouched for every operation not yet migrated.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Callable, Mapping
from uuid import UUID

from bifrost._local_transport import (
    _CHUNK_RAW_BYTES,
    MAX_FRAME_BYTES,
    OP_CONFIG_DELETE,
    OP_CONFIG_GET,
    OP_CONFIG_LIST,
    OP_CONFIG_SET,
    OP_INTEGRATIONS_DELETE_MAPPING,
    OP_INTEGRATIONS_GET,
    OP_INTEGRATIONS_GET_MAPPING,
    OP_INTEGRATIONS_LIST_MAPPINGS,
    OP_INTEGRATIONS_REFRESH_TOKEN,
    OP_INTEGRATIONS_UPSERT_MAPPING,
    OP_TABLES_COUNT,
    OP_TABLES_BATCH_DELETE,
    OP_TABLES_BATCH,
    OP_TABLES_DELETE_DOCUMENT,
    OP_TABLES_UPDATE,
    OP_TABLES_UPSERT,
    OP_TABLES_INSERT,
    OP_TABLES_DELETE,
    OP_TABLES_LIST,
    OP_TABLES_CREATE,
    OP_TABLES_GET,
    OP_TABLES_QUERY,
    TRANSPORT_VERSION,
    decode_frame,
)
from bifrost._import_transport import (
    OP_MODULES_FETCH,
    OP_MODULES_RESOLVE,
)
from shared.sdk_config import ScopeResolutionError, resolve_sdk_scope

logger = logging.getLogger(__name__)

# Operations this dispatcher can dispatch. Stage 3a: the config facade, the
# full integrations facade (reads, mapping mutations, token refresh), and
# the table document reads (get/query/unfiltered count). Engine import
# fast path: cold module-name resolution and candidate source fetch
# (served on the dedicated import channel through the shared sdk_modules
# service).
SDK_CHANNEL_ALLOWED_OPS = frozenset(
    {
        OP_CONFIG_GET,
        OP_CONFIG_SET,
        OP_CONFIG_LIST,
        OP_CONFIG_DELETE,
        OP_INTEGRATIONS_GET,
        OP_INTEGRATIONS_LIST_MAPPINGS,
        OP_INTEGRATIONS_GET_MAPPING,
        OP_INTEGRATIONS_UPSERT_MAPPING,
        OP_INTEGRATIONS_DELETE_MAPPING,
        OP_INTEGRATIONS_REFRESH_TOKEN,
        OP_TABLES_GET,
        OP_TABLES_QUERY,
        OP_TABLES_COUNT,
        OP_TABLES_BATCH_DELETE,
        OP_TABLES_BATCH,
        OP_TABLES_DELETE_DOCUMENT,
        OP_TABLES_UPDATE,
        OP_TABLES_UPSERT,
        OP_TABLES_INSERT,
        OP_TABLES_DELETE,
        OP_TABLES_LIST,
        OP_TABLES_CREATE,
    }
)
IMPORT_CHANNEL_ALLOWED_OPS = frozenset({OP_MODULES_RESOLVE, OP_MODULES_FETCH})
ALLOWLIST = SDK_CHANNEL_ALLOWED_OPS | IMPORT_CHANNEL_ALLOWED_OPS

# Wall-clock bound for one parent-side operation (short session + indexed
# read). A stall fails that request loudly; the child has its own timeout
# and treats a missing response as fatal (no HTTP fallback).
DISPATCH_TIMEOUT_SECONDS = 25.0
OAUTH_REFRESH_DISPATCH_TIMEOUT_SECONDS = 30.0

# Type alias for a zero-argument factory returning short sessions on the
# parent's pooled engine (e.g. ``src.core.database.get_session_factory``).
SessionFactory = Callable[[], Any]


@dataclass(frozen=True)
class LocalDispatchPrincipal:
    """Parent-derived identity for one child channel.

    Built once per fork from the parent-owned dispatch context — never from
    child claims.

    ``actor_email`` is the effective SDK actor: ``engine@bifrost.internal``
    for workflow executions (the ``mint_engine_token()`` address HTTP
    workflow calls authenticate as) or ``service-<id>@bifrost.internal``
    for supervised services (the ``mint_service_token()`` address, via the
    shared ``service_sdk_actor_email`` helper). It never comes from
    ``caller.email`` (the initiating user) or from child frames.
    """

    caller_org_id: UUID | None
    is_platform_admin: bool = False
    is_provider_org: bool = False
    # Engine executions always run under the engine sentinel, never as a
    # direct EXTERNAL portal caller, so the full org+global merge applies
    # (matches the HTTP engine path where the sentinel is non-external).
    is_external: bool = False
    # Parent-derived effective SDK actor for mutation audit
    # (``updated_by``). Reads never need it; set/delete fail closed without
    # it rather than attributing the write to a child claim or blank value.
    actor_email: str | None = None
    # True for supervised ``@service`` children (service identity preserved
    # from the parent-owned ``service`` block, never from child frames).
    # Services resolve provider bypass live (see ``_resolve_frame_scope``);
    # workflows keep engine-token semantics.
    is_service: bool = False
    # Solution install id this execution belongs to (None for plain _repo/
    # executions). Derived from the parent-owned dispatch context — never
    # from child frames — so a child cannot forge another install's
    # declared-connection 424. A malformed value fails dispatch closed
    # (see ``principal_from_context``) rather than silently downgrading to
    # the loose (silent-None) behavior.
    solution_id: UUID | None = None
    # Workflow execution id (``context_data["execution_id"]``) or, for
    # services, the attempt id. Carried onto the token-equivalent table
    # principal as the signed ``engine_execution_id`` claim so
    # ``resolve_trustworthy_caller`` attests the caller's own install
    # exactly like the HTTP engine-token path. None degrades safely to
    # "outside any install" (inbound gate denies) rather than forging.
    execution_id: str | None = None
    # Service definition id for supervised ``@service`` children, from the
    # parent-owned ``service`` block. Carried onto the token-equivalent
    # table principal as the ``service_id`` claim (with
    # ``service_attempt_id`` below) so service table access matches the
    # ``mint_service_token`` HTTP path: system-user non-superuser, org
    # confinement, no initiator-admin bypass.
    service_id: str | None = None
    # Service attempt id for supervised ``@service`` children, from the
    # parent-owned ``service`` block. See ``service_id``.
    service_attempt_id: str | None = None
    # Whether Solution-managed code in this execution may import from the
    # bare workspace repository (the install's ``global_repo_access``
    # flag). Derived from the parent-owned
    # ``context_data["solution_global_repo_access"]`` — never from child
    # frames — and combined with ``solution_id`` into the authoritative
    # ``ModuleSourceScope`` for the ``modules.*`` import operations.
    solution_global_repo_access: bool = False


class LocalPrincipalError(ValueError):
    """Parent dispatch context carries an unusable identity.

    Raised before any local pump starts — the pool fails the dispatch
    loudly instead of serving SDK calls under a downgraded (e.g. global)
    scope.
    """


def _solution_id_from_context(context_data: Mapping[str, Any]) -> UUID | None:
    """Derive the Solution install id from parent-owned dispatch context.

    Missing or empty means a plain (non-solution) execution. A malformed
    non-empty value fails closed — declared-connection behavior must not
    silently downgrade to the loose silent-None path.
    """
    raw = context_data.get("solution_id")
    if raw is None or raw == "":
        return None
    if isinstance(raw, UUID):
        return raw
    if isinstance(raw, str):
        try:
            return UUID(raw)
        except ValueError:
            raise LocalPrincipalError(
                f"local dispatch: solution id {raw!r} is not a "
                "valid UUID; refusing to serve local SDK calls (no silent "
                "declared-connection downgrade)"
            ) from None
    raise LocalPrincipalError(
        f"local dispatch: solution id {raw!r} is not a "
        "string; refusing to serve local SDK calls (no silent "
        "declared-connection downgrade)"
    )


def _execution_id_from_context(context_data: Mapping[str, Any]) -> str | None:
    """Execution/attempt id for the token-equivalent table principal.

    A non-empty string rides onto the ``engine_execution_id`` claim so
    ``resolve_trustworthy_caller`` attests the caller's own install exactly
    like the HTTP engine-token path. Anything else degrades to None
    (outside any install — the inbound gate then denies rather than forges).
    """
    raw = context_data.get("execution_id")
    if isinstance(raw, str) and raw:
        return raw
    return None


def _global_repo_access_from_context(context_data: Mapping[str, Any]) -> bool:
    """Whether Solution code may import from the bare workspace repository.

    Reads only the parent-owned ``solution_global_repo_access`` (set by the
    workflow and service producers from the install's
    ``global_repo_access`` flag). A missing value defaults to False (no
    fallback imports — the sealed-Solution posture). A present-but-malformed
    (non-bool) value fails closed: silently coercing truthy junk would widen
    a sealed install's import surface.
    """
    raw = context_data.get("solution_global_repo_access", False)
    if isinstance(raw, bool):
        return raw
    raise LocalPrincipalError(
        f"local dispatch: solution_global_repo_access {raw!r} is not a "
        "bool; refusing to serve local SDK calls (no silent import-scope "
        "downgrade)"
    )


def principal_from_context(context_data: Mapping[str, Any]) -> LocalDispatchPrincipal:
    """Derive the dispatch principal from parent-owned execution context.

    Reads only ``organization`` (id/is_provider), ``is_platform_admin``,
    ``solution_id``, and the parent-owned ``service`` block assembled by the
    parent consumer. Never reads child frames, and never reads
    ``caller.email``: the effective SDK actor is the engine sentinel for
    workflows (``engine@bifrost.internal``, the ``mint_engine_token()`` address HTTP
    workflow calls authenticate as) and the shared
    ``service_sdk_actor_email`` derivation for services (the
    ``mint_service_token()`` address). A missing or empty org id means a
    genuinely global execution; a malformed non-empty id fails closed. A
    present-but-malformed ``service`` block (non-mapping, missing or
    non-UUID ``service_id``) fails closed rather than attributing service
    writes to a forged or blank value. A missing or empty ``solution_id``
    means a plain (non-solution) execution; a malformed non-empty value
    fails closed rather than silently downgrading declared-connection
    behavior to silent-None. ``execution_id`` and the service
    ``service_id``/``attempt_id`` ride along for the token-equivalent table
    principal (signed engine/service claims) — non-string values degrade to
    None (outside any install) rather than failing the whole dispatch.
    ``solution_global_repo_access`` rides along for the authoritative
    module-source scope — missing defaults to False (sealed), malformed
    fails closed.
    """
    from src.core.security import ENGINE_SDK_ACTOR_EMAIL, service_sdk_actor_email

    org = context_data.get("organization") or {}
    raw_org_id = org.get("id")
    caller_org_id: UUID | None = None
    if raw_org_id is None or raw_org_id == "":
        caller_org_id = None
    elif isinstance(raw_org_id, str):
        try:
            caller_org_id = UUID(raw_org_id)
        except ValueError:
            raise LocalPrincipalError(
                f"local dispatch: organization id {raw_org_id!r} is not a "
                "valid UUID; refusing to serve local SDK calls (no silent "
                "global downgrade)"
            ) from None
    else:
        raise LocalPrincipalError(
            f"local dispatch: organization id {raw_org_id!r} is not a "
            "string; refusing to serve local SDK calls (no silent global "
            "downgrade)"
        )
    service_raw = context_data.get("service")
    if service_raw is None:
        return LocalDispatchPrincipal(
            caller_org_id=caller_org_id,
            is_platform_admin=bool(context_data.get("is_platform_admin", False)),
            is_provider_org=bool(org.get("is_provider", False)),
            is_external=False,
            actor_email=ENGINE_SDK_ACTOR_EMAIL,
            is_service=False,
            solution_id=_solution_id_from_context(context_data),
            execution_id=_execution_id_from_context(context_data),
            solution_global_repo_access=_global_repo_access_from_context(
                context_data
            ),
        )
    if not isinstance(service_raw, Mapping):
        raise LocalPrincipalError(
            "local dispatch: service identity is not a mapping; refusing "
            "to serve local SDK calls"
        )
    raw_service_id = service_raw.get("service_id")
    try:
        actor_email = service_sdk_actor_email(raw_service_id)
    except ValueError as e:
        raise LocalPrincipalError(
            f"local dispatch: malformed service identity ({e}); refusing "
            "to serve local SDK calls"
        ) from None
    raw_attempt_id = service_raw.get("attempt_id")
    service_attempt_id = (
        raw_attempt_id
        if isinstance(raw_attempt_id, str) and raw_attempt_id
        else None
    )
    return LocalDispatchPrincipal(
        caller_org_id=caller_org_id,
        # Service tokens are never superuser by construction — force False
        # rather than trusting any flag the context might carry.
        is_platform_admin=False,
        is_provider_org=bool(org.get("is_provider", False)),
        is_external=False,
        actor_email=actor_email,
        is_service=True,
        solution_id=_solution_id_from_context(context_data),
        execution_id=_execution_id_from_context(context_data),
        service_id=raw_service_id if isinstance(raw_service_id, str) else None,
        service_attempt_id=service_attempt_id,
        solution_global_repo_access=_global_repo_access_from_context(
            context_data
        ),
    )


def _error(frame_id: str | None, status: int, detail: str) -> dict[str, Any]:
    return {
        "v": TRANSPORT_VERSION,
        "id": frame_id,
        "ok": False,
        "status": status,
        "detail": detail,
    }


async def dispatch_frames(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve one validated request frame; always returns response frames.

    Never raises except on cancellation: every validation, scope, and
    service failure maps to an ``ok: false`` response carrying an HTTP-style
    status the child maps back to the public exception. Large results are
    returned as a lazy iterable yielding a header frame plus ordered part
    frames (bounded chunked transfer); everything else is a single frame.

    The chunked iterable is lazy: each part is base64-encoded only as the
    caller iterates, so ``serve_channel`` generates one part, sends it,
    and only then generates the next (sequential backpressure). The single
    ``raw_result`` bytes buffer is shared across yields, as the HTTP path
    materializes one body.
    """
    frame_id = frame.get("id") if isinstance(frame.get("id"), str) else None
    if frame.get("v") != TRANSPORT_VERSION:
        return [_error(frame_id, 400, f"unsupported local transport version: {frame.get('v')!r}")]
    op = frame.get("op")
    if op not in ALLOWLIST:
        return [_error(frame_id, 404, f"local SDK operation not allowed: {op!r}")]
    if op == OP_CONFIG_GET:
        return await _dispatch_config_frames(session_factory, principal, frame_id, frame)
    if op == OP_CONFIG_SET:
        return await _dispatch_config_set(session_factory, principal, frame_id, frame)
    if op == OP_CONFIG_LIST:
        return await _dispatch_config_list(session_factory, principal, frame_id, frame)
    if op == OP_CONFIG_DELETE:
        return await _dispatch_config_delete(session_factory, principal, frame_id, frame)
    if op == OP_INTEGRATIONS_GET:
        return await _dispatch_integrations_get(session_factory, principal, frame_id, frame)
    if op == OP_INTEGRATIONS_LIST_MAPPINGS:
        return await _dispatch_integrations_list_mappings(session_factory, principal, frame_id, frame)
    if op == OP_INTEGRATIONS_GET_MAPPING:
        return await _dispatch_integrations_get_mapping(session_factory, principal, frame_id, frame)
    if op == OP_INTEGRATIONS_UPSERT_MAPPING:
        return await _dispatch_integrations_upsert_mapping(session_factory, principal, frame_id, frame)
    if op == OP_INTEGRATIONS_DELETE_MAPPING:
        return await _dispatch_integrations_delete_mapping(session_factory, principal, frame_id, frame)
    if op == OP_INTEGRATIONS_REFRESH_TOKEN:
        return await _dispatch_integrations_refresh_token(session_factory, principal, frame_id, frame)
    if op == OP_TABLES_CREATE:
        return await _dispatch_tables_create(session_factory, principal, frame_id, frame)
    if op == OP_TABLES_LIST:
        return await _dispatch_tables_list(session_factory, principal, frame_id, frame)
    if op == OP_TABLES_DELETE:
        return await _dispatch_tables_delete(session_factory, principal, frame_id, frame)
    if op == OP_TABLES_INSERT:
        return await _dispatch_tables_insert(session_factory, principal, frame_id, frame)
    if op == OP_TABLES_UPSERT:
        return await _dispatch_tables_upsert(session_factory, principal, frame_id, frame)
    if op == OP_TABLES_UPDATE:
        return await _dispatch_tables_update(session_factory, principal, frame_id, frame)
    if op == OP_TABLES_DELETE_DOCUMENT:
        return await _dispatch_tables_delete_document(session_factory, principal, frame_id, frame)
    if op == OP_TABLES_BATCH:
        return await _dispatch_tables_batch(session_factory, principal, frame_id, frame)
    if op == OP_TABLES_BATCH_DELETE:
        return await _dispatch_tables_batch_delete(session_factory, principal, frame_id, frame)
    if op == OP_TABLES_GET:
        return await _dispatch_tables_get(session_factory, principal, frame_id, frame)
    if op == OP_TABLES_QUERY:
        return await _dispatch_tables_query(session_factory, principal, frame_id, frame)
    if op == OP_TABLES_COUNT:
        return await _dispatch_tables_count(session_factory, principal, frame_id, frame)
    if op == OP_MODULES_RESOLVE:
        return await _dispatch_modules_resolve(session_factory, principal, frame_id, frame)
    if op == OP_MODULES_FETCH:
        return await _dispatch_modules_fetch(session_factory, principal, frame_id, frame)
    return [_error(frame_id, 404, f"local SDK operation not allowed: {op!r}")]


async def dispatch_frame(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame: dict[str, Any],
) -> dict[str, Any]:
    """First response frame for one request (the full response when single-frame)."""
    return next(iter(await dispatch_frames(session_factory, principal, frame)))


class _OutgoingTooLarge(Exception):
    """A frame the parent built exceeds the wire bound (internal bug guard)."""


def _encode_outgoing(frame: dict[str, Any]) -> bytes:
    """Serialize one outgoing frame, enforcing the byte bound before sending."""
    raw = json.dumps(frame, separators=(",", ":")).encode("utf-8")
    if len(raw) > MAX_FRAME_BYTES:
        raise _OutgoingTooLarge(
            f"outgoing local SDK frame is {len(raw)} bytes "
            f"(limit {MAX_FRAME_BYTES})"
        )
    return raw


def _validate_request(
    model: Any, data: dict[str, Any], frame_id: str | None, op: str
) -> tuple[Any, dict[str, Any] | None]:
    """Validate one local frame with the same Pydantic DTO as its HTTP handler.

    Gives local requests the HTTP request's coercion (e.g. ``is_secret``
    bool parsing) and required-field behavior without changing public SDK
    signatures. Returns ``(request, None)`` on success, or
    ``(None, error_frame)`` with an HTTP-style 422 the child maps to the
    public exception.
    """
    from pydantic import ValidationError

    try:
        return model.model_validate(data), None
    except ValidationError as e:
        return None, _error(frame_id, 422, f"invalid {op} request: {e}")


def _scope_needs_bypass_check(
    principal: LocalDispatchPrincipal, scope: Any
) -> bool:
    """True when resolving ``scope`` would consult the bypass gate.

    Mirrors the ``needs_bypass_check`` rule the HTTP path applies: UNSET
    and the caller's own org resolve without consulting provider/admin
    membership; anything else (explicit global, another org) needs the
    gate. Malformed scopes return False here — the resolver itself 422s
    them without any membership lookup.
    """
    if scope is None or scope == "":
        return False
    if scope == "global":
        return not principal.is_platform_admin
    if not isinstance(scope, str):
        return False
    try:
        requested = UUID(scope)
    except ValueError:
        return False
    if requested == principal.caller_org_id:
        return False
    return not principal.is_platform_admin


async def _live_provider_membership(
    session_factory: SessionFactory | None,
    caller_org_id: UUID | None,
) -> bool | None:
    """Live ``is_provider`` for the caller's org, or None when unknowable.

    Returns None when there is no session factory or no caller org (the
    caller resolves the failure closed). DB errors propagate to the caller,
    which maps them to a 500 transport error — never to silent bypass.
    """
    if session_factory is None or caller_org_id is None:
        return None
    from src.models import Organization

    async def _lookup(session: Any) -> bool:
        from sqlalchemy import select

        row = await session.execute(
            select(Organization.is_provider).where(
                Organization.id == caller_org_id
            )
        )
        return bool(row.scalar_one_or_none())

    async with session_factory() as session:
        return await asyncio.wait_for(_lookup(session), DISPATCH_TIMEOUT_SECONDS)


async def _resolve_frame_scope(
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
    session_factory: SessionFactory | None = None,
) -> tuple[UUID | None, dict[str, Any] | None]:
    """Resolve one frame's untrusted scope string against the principal.

    Returns ``(org_id, None)`` on success, or ``(None, error_frame)`` with
    the HTTP-style status the child maps to the public exception.

    For supervised services, a cross-org/global request re-checks
    provider membership live in a short parent session — the snapshot
    captured when the service started must not survive a revocation.
    Own-org calls need no extra lookup. Workflow engine-token semantics
    are unchanged (superuser token path keeps its snapshot behavior).
    """
    if (
        principal.is_service
        and _scope_needs_bypass_check(principal, frame.get("scope"))
    ):
        try:
            live_provider = await _live_provider_membership(
                session_factory, principal.caller_org_id
            )
        except asyncio.TimeoutError:
            logger.warning("local scope check timed out; refusing bypass")
            return None, _error(frame_id, 500, "local scope check timed out")
        except Exception as e:  # noqa: BLE001 - transport must return errors, not raise
            logger.exception("local scope check failed")
            return None, _error(
                frame_id, 500, f"local scope check failed: {type(e).__name__}"
            )
        if live_provider is None:
            return None, _error(
                frame_id, 500, "local scope check failed: no caller organization"
            )
        try:
            resolved_org_id = await resolve_sdk_scope(
                frame.get("scope"),
                caller_org_id=principal.caller_org_id,
                is_platform_admin=principal.is_platform_admin,
                is_provider_org=live_provider,
            )
        except ScopeResolutionError as e:
            return None, _error(frame_id, e.status_code, e.detail)
        return resolved_org_id, None
    try:
        resolved_org_id = await resolve_sdk_scope(
            frame.get("scope"),
            caller_org_id=principal.caller_org_id,
            is_platform_admin=principal.is_platform_admin,
            is_provider_org=principal.is_provider_org,
        )
    except ScopeResolutionError as e:
        return None, _error(frame_id, e.status_code, e.detail)
    return resolved_org_id, None


async def _run_short(
    session_factory: SessionFactory,
    coro_factory: Callable[[], Any],
    *,
    op: str,
    log_key: str,
    status_errors: tuple[type[Exception], ...] = (),
    timeout_seconds: float = DISPATCH_TIMEOUT_SECONDS,
) -> tuple[Any, dict[str, Any] | None]:
    """Run one service coroutine on a short pooled session with a deadline.

    Returns ``(result, None)`` on success, or ``(None, error_frame)`` for
    the timeout/service failures the child maps to transport errors.
    Exceptions listed in ``status_errors`` carry their own HTTP-style
    status (e.g. the integrations service 424) instead of the generic
    500; everything else unexpected stays a 500.
    """
    try:
        async def _run() -> Any:
            async with session_factory() as session:
                return await coro_factory(session)

        result = await asyncio.wait_for(_run(), timeout_seconds)
    except asyncio.TimeoutError:
        logger.warning("local %s dispatch timed out for key=%r", op, log_key)
        return None, _error(None, 503, f"local {op} dispatch timed out")
    except status_errors as e:
        status = getattr(e, "status_code", 500)
        detail = getattr(e, "detail", str(e))
        try:
            status = int(status)
        except (TypeError, ValueError):
            status = 500
        if not isinstance(detail, str):
            detail = str(detail)
        return None, _error(None, status, detail)
    except Exception as e:  # noqa: BLE001 - transport must return errors, not raise
        logger.exception("local %s dispatch failed for key=%r", op, log_key)
        return None, _error(None, 500, f"local {op} failed: {type(e).__name__}")
    return result, None


def _single_ok(frame_id: str | None, result: Any) -> list[dict[str, Any]]:
    """One small ``ok`` frame (set/delete and small get/list results)."""
    return [
        {
            "v": TRANSPORT_VERSION,
            "id": frame_id,
            "ok": True,
            "result": result,
        }
    ]


def _ok_frames(
    frame_id: str | None, result: Any
) -> Iterable[dict[str, Any]]:
    """Single frame, or lazy bounded chunked frames for a large result.

    Shares the response-chunking contract with ``config.get``: the single
    ``raw_result`` buffer is shared and each part base64-encodes only as
    the caller iterates, so ``serve_channel`` sends sequentially with pipe
    backpressure. No total result cap (the HTTP path has none).
    """
    single = {
        "v": TRANSPORT_VERSION,
        "id": frame_id,
        "ok": True,
        "result": result,
    }
    if result is not None:
        raw_result = json.dumps(result, separators=(",", ":")).encode("utf-8")
        single_raw = json.dumps(single, separators=(",", ":")).encode("utf-8")
        if len(single_raw) > MAX_FRAME_BYTES:
            return _chunked_frames(frame_id, raw_result)
    return [single]


def _require_actor(
    principal: LocalDispatchPrincipal, frame_id: str | None, op: str
) -> dict[str, Any] | None:
    """Fail a mutation closed when the parent identity lacks an actor email.

    The email comes only from the parent-owned dispatch context — never
    from child frames — so a missing value means the write cannot be
    audited and must not proceed.
    """
    if not principal.actor_email:
        logger.warning(
            "local %s refused: parent dispatch context has no caller email", op
        )
        return _error(
            frame_id,
            500,
            f"local {op} failed: parent dispatch identity has no caller email",
        )
    return None


async def _dispatch_config_set(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    from shared.sdk_config import set_sdk_config_value
    from src.models.contracts.cli import CLIConfigSetRequest

    request, invalid = _validate_request(
        CLIConfigSetRequest,
        {
            "key": frame.get("key"),
            "value": frame.get("value"),
            "is_secret": frame.get("is_secret", False),
            "scope": frame.get("scope"),
        },
        frame_id,
        OP_CONFIG_SET,
    )
    if "value" not in frame:
        return [_error(frame_id, 422, "config set requires a value")]
    if invalid is not None:
        return [invalid]
    key, is_secret = request.key, bool(request.is_secret)
    resolved_org_id, scope_error = await _resolve_frame_scope(
        principal, frame_id, {"scope": request.scope}, session_factory
    )
    if scope_error is not None:
        return [scope_error]
    actor_error = _require_actor(principal, frame_id, OP_CONFIG_SET)
    if actor_error is not None:
        return [actor_error]
    assert principal.actor_email is not None

    async def _set(session: Any) -> None:
        await set_sdk_config_value(
            session,
            key=key,
            value=request.value,
            is_secret=is_secret,
            org_id=resolved_org_id,
            actor_email=principal.actor_email,
        )

    _, error = await _run_short(session_factory, _set, op=OP_CONFIG_SET, log_key=key)
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _single_ok(frame_id, None)


async def _dispatch_config_list(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    from shared.sdk_config import list_sdk_config_values
    from src.models.contracts.cli import CLIConfigListRequest

    request, invalid = _validate_request(
        CLIConfigListRequest,
        {"scope": frame.get("scope")},
        frame_id,
        OP_CONFIG_LIST,
    )
    if invalid is not None:
        return [invalid]
    resolved_org_id, scope_error = await _resolve_frame_scope(
        principal, frame_id, {"scope": request.scope}, session_factory
    )
    if scope_error is not None:
        return [scope_error]

    async def _list(session: Any) -> dict[str, Any]:
        return await list_sdk_config_values(
            session,
            org_id=resolved_org_id,
            external=principal.is_external,
        )

    result, error = await _run_short(
        session_factory, _list, op=OP_CONFIG_LIST, log_key=""
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


async def _dispatch_config_delete(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    from shared.sdk_config import delete_sdk_config_value
    from src.models.contracts.cli import CLIConfigDeleteRequest

    request, invalid = _validate_request(
        CLIConfigDeleteRequest,
        {"key": frame.get("key"), "scope": frame.get("scope")},
        frame_id,
        OP_CONFIG_DELETE,
    )
    if invalid is not None:
        return [invalid]
    key = request.key
    resolved_org_id, scope_error = await _resolve_frame_scope(
        principal, frame_id, {"scope": request.scope}, session_factory
    )
    if scope_error is not None:
        return [scope_error]
    actor_error = _require_actor(principal, frame_id, OP_CONFIG_DELETE)
    if actor_error is not None:
        return [actor_error]
    assert principal.actor_email is not None

    async def _delete(session: Any) -> bool:
        return await delete_sdk_config_value(
            session,
            key=key,
            org_id=resolved_org_id,
            actor_email=principal.actor_email,
        )

    result, error = await _run_short(
        session_factory, _delete, op=OP_CONFIG_DELETE, log_key=key
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _single_ok(frame_id, result)


async def _dispatch_config_frames(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``config.get`` through the common scope/session/chunk path.

    Shares ``_validate_request`` (HTTP DTO), ``_resolve_frame_scope``,
    ``_run_short``, and ``_ok_frames`` with set/list/delete, so there is
    one local scope/error/chunk behavior across the facade.
    """
    from shared.sdk_config import get_sdk_config_dict
    from src.models.contracts.cli import CLIConfigGetRequest

    request, invalid = _validate_request(
        CLIConfigGetRequest,
        {"key": frame.get("key"), "scope": frame.get("scope")},
        frame_id,
        OP_CONFIG_GET,
    )
    if invalid is not None:
        return [invalid]
    resolved_org_id, scope_error = await _resolve_frame_scope(
        principal, frame_id, {"scope": request.scope}, session_factory
    )
    if scope_error is not None:
        return [scope_error]

    async def _get(session: Any) -> dict[str, Any] | None:
        return await get_sdk_config_dict(
            session,
            key=request.key,
            org_id=resolved_org_id,
            external=principal.is_external,
        )

    result, error = await _run_short(
        session_factory, _get, op=OP_CONFIG_GET, log_key=request.key
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


async def _dispatch_integrations_get(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``integrations.get`` through the shared integrations service.

    Validates with the same HTTP DTO, resolves the untrusted scope string
    against the parent-derived principal, and calls the same service the
    HTTP handler calls. The Solution install id comes ONLY from the
    principal: a child ``"solution"`` frame field is never read, so a
    child cannot forge another install's declared-connection 424.
    """
    from shared.sdk_integrations import (
        IntegrationServiceError,
        get_sdk_integration_dict,
    )
    from src.models.contracts.cli import SDKIntegrationsGetRequest

    request, invalid = _validate_request(
        SDKIntegrationsGetRequest,
        {
            "name": frame.get("name"),
            "scope": frame.get("scope"),
            "oauth_scope": frame.get("oauth_scope"),
        },
        frame_id,
        OP_INTEGRATIONS_GET,
    )
    if invalid is not None:
        return [invalid]
    resolved_org_id, scope_error = await _resolve_frame_scope(
        principal, frame_id, {"scope": request.scope}, session_factory
    )
    if scope_error is not None:
        return [scope_error]

    async def _get(session: Any) -> dict[str, Any] | None:
        return await get_sdk_integration_dict(
            session,
            name=request.name,
            org_id=resolved_org_id,
            oauth_scope=request.oauth_scope,
            solution_id=principal.solution_id,
            external=principal.is_external,
        )

    result, error = await _run_short(
        session_factory,
        _get,
        op=OP_INTEGRATIONS_GET,
        log_key=request.name,
        status_errors=(IntegrationServiceError,),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


async def _dispatch_integrations_list_mappings(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``integrations.list_mappings`` through the shared service.

    A missing integration returns null (like HTTP); otherwise the
    ``{"items": [...]}`` envelope rides single or chunked frames.
    """
    from shared.sdk_integrations import list_sdk_integration_mappings
    from src.models.contracts.cli import SDKIntegrationsListMappingsRequest

    request, invalid = _validate_request(
        SDKIntegrationsListMappingsRequest,
        {"name": frame.get("name"), "scope": frame.get("scope")},
        frame_id,
        OP_INTEGRATIONS_LIST_MAPPINGS,
    )
    if invalid is not None:
        return [invalid]
    async def _list(session: Any) -> dict[str, Any] | None:
        items = await list_sdk_integration_mappings(
            session,
            name=request.name,
            scope=request.scope,
            caller_org_id=principal.caller_org_id,
            is_platform_admin=principal.is_platform_admin,
            is_provider_org=None if principal.is_service else principal.is_provider_org,
            external=principal.is_external,
        )
        if items is None:
            return None
        return {"items": items}

    result, error = await _run_short(
        session_factory,
        _list,
        op=OP_INTEGRATIONS_LIST_MAPPINGS,
        log_key=request.name,
        status_errors=(ScopeResolutionError,),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


async def _dispatch_integrations_get_mapping(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``integrations.get_mapping`` through the shared service.

    Entity-ID fallback lookups stay scoped by the resolved org exactly
    like the HTTP path, so non-bypass callers cannot probe other orgs.
    """
    from shared.sdk_integrations import get_sdk_integration_mapping_dict
    from src.models.contracts.cli import SDKIntegrationsGetMappingRequest

    request, invalid = _validate_request(
        SDKIntegrationsGetMappingRequest,
        {
            "name": frame.get("name"),
            "scope": frame.get("scope"),
            "entity_id": frame.get("entity_id"),
        },
        frame_id,
        OP_INTEGRATIONS_GET_MAPPING,
    )
    if invalid is not None:
        return [invalid]
    async def _get_mapping(session: Any) -> dict[str, Any] | None:
        return await get_sdk_integration_mapping_dict(
            session,
            name=request.name,
            scope=request.scope,
            caller_org_id=principal.caller_org_id,
            is_platform_admin=principal.is_platform_admin,
            is_provider_org=None if principal.is_service else principal.is_provider_org,
            entity_id=request.entity_id,
            external=principal.is_external,
        )

    result, error = await _run_short(
        session_factory,
        _get_mapping,
        op=OP_INTEGRATIONS_GET_MAPPING,
        log_key=request.name,
        status_errors=(ScopeResolutionError,),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


async def _dispatch_integrations_upsert_mapping(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``integrations.upsert_mapping`` through the shared service.

    Validates with the same HTTP DTO, requires the parent-derived actor
    email (writes cannot be audited off a child claim), and calls the
    same service the HTTP handler calls: missing integration 404s before
    scope validation, global scope is a 400, the existing row keeps its
    OAuth link, and the echo carries the merged config.
    """
    from shared.sdk_integrations import (
        IntegrationServiceError,
        upsert_sdk_integration_mapping,
    )
    from src.models.contracts.cli import SDKIntegrationsUpsertMappingRequest

    request, invalid = _validate_request(
        SDKIntegrationsUpsertMappingRequest,
        {
            "name": frame.get("name"),
            "scope": frame.get("scope"),
            "entity_id": frame.get("entity_id"),
            "entity_name": frame.get("entity_name"),
            "config": frame.get("config"),
        },
        frame_id,
        OP_INTEGRATIONS_UPSERT_MAPPING,
    )
    if invalid is not None:
        return [invalid]
    actor_error = _require_actor(principal, frame_id, OP_INTEGRATIONS_UPSERT_MAPPING)
    if actor_error is not None:
        return [actor_error]
    assert principal.actor_email is not None

    async def _upsert(session: Any) -> dict[str, Any]:
        return await upsert_sdk_integration_mapping(
            session,
            name=request.name,
            scope=request.scope,
            caller_org_id=principal.caller_org_id,
            is_platform_admin=principal.is_platform_admin,
            is_provider_org=None if principal.is_service else principal.is_provider_org,
            external=principal.is_external,
            entity_id=request.entity_id,
            entity_name=request.entity_name,
            config=request.config,
            actor_email=principal.actor_email,
        )

    result, error = await _run_short(
        session_factory,
        _upsert,
        op=OP_INTEGRATIONS_UPSERT_MAPPING,
        log_key=request.name,
        status_errors=(IntegrationServiceError, ScopeResolutionError),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


async def _dispatch_integrations_delete_mapping(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``integrations.delete_mapping`` through the shared service.

    A missing integration, a global scope, or a missing mapping returns
    ``{"deleted": False}`` (like HTTP); scope denials still 403.
    """
    from shared.sdk_integrations import delete_sdk_integration_mapping
    from src.models.contracts.cli import SDKIntegrationsDeleteMappingRequest

    request, invalid = _validate_request(
        SDKIntegrationsDeleteMappingRequest,
        {"name": frame.get("name"), "scope": frame.get("scope")},
        frame_id,
        OP_INTEGRATIONS_DELETE_MAPPING,
    )
    if invalid is not None:
        return [invalid]
    actor_error = _require_actor(principal, frame_id, OP_INTEGRATIONS_DELETE_MAPPING)
    if actor_error is not None:
        return [actor_error]

    async def _delete(session: Any) -> dict[str, bool]:
        return await delete_sdk_integration_mapping(
            session,
            name=request.name,
            scope=request.scope,
            caller_org_id=principal.caller_org_id,
            is_platform_admin=principal.is_platform_admin,
            is_provider_org=None if principal.is_service else principal.is_provider_org,
        )

    result, error = await _run_short(
        session_factory,
        _delete,
        op=OP_INTEGRATIONS_DELETE_MAPPING,
        log_key=request.name,
        status_errors=(ScopeResolutionError,),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _single_ok(frame_id, result)


async def _dispatch_integrations_refresh_token(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``integrations.refresh_token`` through the shared service.

    The SDK ``refresh()`` call passes no scope, so a missing scope
    resolves to the caller's own org — exactly like the HTTP handler.
    The locked token lookup, refresh context, rotation, persistence,
    and external-caller restrictions are the shared service's, so both
    paths commit the same state. The child registers the fresh token
    with its own secret scrubber (like the HTTP SDK facade does).
    """
    from shared.sdk_integrations import (
        IntegrationServiceError,
        refresh_sdk_oauth_token,
    )
    from src.models.contracts.cli import SDKIntegrationsRefreshTokenRequest

    request, invalid = _validate_request(
        SDKIntegrationsRefreshTokenRequest,
        {
            "connection_name": frame.get("connection_name"),
            "scope": frame.get("scope"),
        },
        frame_id,
        OP_INTEGRATIONS_REFRESH_TOKEN,
    )
    if invalid is not None:
        return [invalid]

    async def _refresh(session: Any) -> dict[str, Any]:
        return await refresh_sdk_oauth_token(
            session,
            connection_name=request.connection_name,
            scope=request.scope,
            caller_org_id=principal.caller_org_id,
            is_platform_admin=principal.is_platform_admin,
            is_provider_org=None if principal.is_service else principal.is_provider_org,
            external=principal.is_external,
        )

    result, error = await _run_short(
        session_factory,
        _refresh,
        op=OP_INTEGRATIONS_REFRESH_TOKEN,
        log_key=request.connection_name,
        status_errors=(IntegrationServiceError, ScopeResolutionError),
        timeout_seconds=OAUTH_REFRESH_DISPATCH_TIMEOUT_SECONDS,
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _single_ok(frame_id, result)


def _table_user_for_principal(principal: LocalDispatchPrincipal) -> Any:
    """Token-equivalent ``UserPrincipal`` for table resolution and policy checks.

    Mirrors the minted engine/service tokens the HTTP table path
    authenticates, so org gates, install resolution, and row policies decide
    identically on both transports:

    - workflows: system-user superuser with the parent execution and Solution
      ids as the signed ``engine_execution_id``/``engine_solution_id``
      claims (the ``mint_engine_token()`` shape);
    - services: system-user non-superuser with the parent service, attempt,
      and Solution ids (the ``mint_service_token()`` shape), org-confined.

    The config-oriented ``principal.is_platform_admin`` tracks the
    *initiating* user and must NOT stand in here: a platform-admin
    initiator's service child is still a non-superuser service caller, and a
    non-admin initiator's workflow child is still the superuser engine.
    """
    from src.core.constants import SYSTEM_USER_UUID
    from src.core.principal import UserPrincipal
    from src.core.security import ENGINE_SDK_ACTOR_EMAIL

    solution_id = (
        str(principal.solution_id) if principal.solution_id is not None else None
    )
    if principal.is_service:
        return UserPrincipal(
            user_id=SYSTEM_USER_UUID,
            email=principal.actor_email or ENGINE_SDK_ACTOR_EMAIL,
            organization_id=principal.caller_org_id,
            is_superuser=False,
            engine_execution_id=principal.service_attempt_id
            or principal.execution_id,
            engine_solution_id=solution_id,
            service_id=principal.service_id,
            service_attempt_id=principal.service_attempt_id
            or principal.execution_id,
        )
    return UserPrincipal(
        user_id=SYSTEM_USER_UUID,
        email=ENGINE_SDK_ACTOR_EMAIL,
        organization_id=None,
        is_superuser=True,
        engine_execution_id=principal.execution_id,
        engine_solution_id=solution_id,
    )


def _tables_target_solution_id(
    frame_solution: Any, principal: LocalDispatchPrincipal
) -> str | None:
    """Per-call target install ref for one table frame.

    The child-supplied ``solution`` is only ever a *target* (UUID or
    slug/name, resolved inside the target org downstream). Unset or blank
    inherits the parent-owned own install. The caller's install identity
    itself always comes from the principal — never from the frame.
    """
    if isinstance(frame_solution, str) and frame_solution.strip():
        return frame_solution
    if principal.solution_id is not None:
        return str(principal.solution_id)
    return None


def _required_tables_field(
    frame: dict[str, Any], field: str, frame_id: str | None, op: str
) -> tuple[str | None, dict[str, Any] | None]:
    """One required non-empty string frame field, else a 422 error frame."""
    value = frame.get(field)
    if isinstance(value, str) and value.strip():
        return value, None
    return None, _error(frame_id, 422, f"invalid {op} request: {field!r} is required")


def _optional_tables_field(
    frame: dict[str, Any], field: str, frame_id: str | None, op: str
) -> tuple[str | None, dict[str, Any] | None]:
    """One optional string frame field (None when absent), else a 422."""
    value = frame.get(field)
    if value is None:
        return None, None
    if isinstance(value, str):
        return value, None
    return None, _error(frame_id, 422, f"invalid {op} request: {field!r} must be a string")


async def _dispatch_tables_get(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``tables.get`` through the shared resolution + read services.

    The parent resolves the table and enforces the read policy with the
    token-equivalent principal on one short session — the same
    ``shared.table_resolution`` + ``shared.table_documents`` calls the HTTP
    handlers make. The service's actual 404/403 rides the error frame; the
    SDK facade maps a 404 to ``None``.
    """
    from fastapi import HTTPException

    table_ref, invalid = _required_tables_field(frame, "table", frame_id, OP_TABLES_GET)
    if invalid is not None:
        return [invalid]
    doc_id, invalid = _required_tables_field(frame, "doc_id", frame_id, OP_TABLES_GET)
    if invalid is not None:
        return [invalid]
    scope, invalid = _optional_tables_field(frame, "scope", frame_id, OP_TABLES_GET)
    if invalid is not None:
        return [invalid]
    solution, invalid = _optional_tables_field(
        frame, "solution", frame_id, OP_TABLES_GET
    )
    if invalid is not None:
        return [invalid]
    assert table_ref is not None and doc_id is not None

    async def _get(session: Any) -> dict[str, Any]:
        from shared.table_documents import get_table_document
        from shared.table_resolution import LocalTableContext, get_table_or_404

        user = _table_user_for_principal(principal)
        ctx = LocalTableContext(
            db=session,
            user=user,
            org_id=principal.caller_org_id,
            solution_id=_tables_target_solution_id(solution, principal),
        )
        table = await get_table_or_404(ctx, table_ref, scope=scope)
        doc = await get_table_document(session, table, doc_id, user)
        return doc.model_dump(mode="json")

    result, error = await _run_short(
        session_factory,
        _get,
        op=OP_TABLES_GET,
        log_key=table_ref,
        status_errors=(HTTPException,),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


async def _dispatch_tables_query(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``tables.query`` through the shared resolution + read services.

    The query payload validates with the same ``DocumentQuery`` DTO as the
    HTTP handler, so coercion, defaults, and pagination limits match. The
    service's actual 404/403 rides the error frame; the SDK facade maps a
    404 to an empty ``DocumentList``.
    """
    from fastapi import HTTPException

    from src.models.contracts.tables import DocumentQuery

    table_ref, invalid = _required_tables_field(
        frame, "table", frame_id, OP_TABLES_QUERY
    )
    if invalid is not None:
        return [invalid]
    scope, invalid = _optional_tables_field(frame, "scope", frame_id, OP_TABLES_QUERY)
    if invalid is not None:
        return [invalid]
    solution, invalid = _optional_tables_field(
        frame, "solution", frame_id, OP_TABLES_QUERY
    )
    if invalid is not None:
        return [invalid]
    raw_query = frame.get("query")
    if raw_query is None:
        raw_query = {}
    if not isinstance(raw_query, dict):
        return [_error(frame_id, 422, f"invalid {OP_TABLES_QUERY} request: 'query' must be an object")]
    request, invalid = _validate_request(
        DocumentQuery, raw_query, frame_id, OP_TABLES_QUERY
    )
    if invalid is not None:
        return [invalid]
    assert table_ref is not None and request is not None

    async def _query(session: Any) -> dict[str, Any]:
        from shared.table_documents import query_table_documents
        from shared.table_resolution import LocalTableContext, get_table_or_404

        user = _table_user_for_principal(principal)
        ctx = LocalTableContext(
            db=session,
            user=user,
            org_id=principal.caller_org_id,
            solution_id=_tables_target_solution_id(solution, principal),
        )
        table = await get_table_or_404(ctx, table_ref, scope=scope)
        response = await query_table_documents(session, table, request, user)
        return response.model_dump(mode="json")

    result, error = await _run_short(
        session_factory,
        _query,
        op=OP_TABLES_QUERY,
        log_key=table_ref,
        status_errors=(HTTPException,),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


async def _dispatch_tables_count(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve the unfiltered ``tables.count`` through the shared services.

    Only the unfiltered count rides this operation (returned as
    ``{"count": n}`` — the transport result contract does not carry bare
    integers). A filtered count stays composed through the public
    ``tables.query(limit=1)`` in the SDK facade, exactly like the HTTP path.
    """
    from fastapi import HTTPException

    table_ref, invalid = _required_tables_field(
        frame, "table", frame_id, OP_TABLES_COUNT
    )
    if invalid is not None:
        return [invalid]
    scope, invalid = _optional_tables_field(frame, "scope", frame_id, OP_TABLES_COUNT)
    if invalid is not None:
        return [invalid]
    solution, invalid = _optional_tables_field(
        frame, "solution", frame_id, OP_TABLES_COUNT
    )
    if invalid is not None:
        return [invalid]
    assert table_ref is not None

    async def _count(session: Any) -> dict[str, Any]:
        from shared.table_documents import count_table_documents
        from shared.table_resolution import LocalTableContext, get_table_or_404

        user = _table_user_for_principal(principal)
        ctx = LocalTableContext(
            db=session,
            user=user,
            org_id=principal.caller_org_id,
            solution_id=_tables_target_solution_id(solution, principal),
        )
        table = await get_table_or_404(ctx, table_ref, scope=scope)
        response = await count_table_documents(session, table, user)
        return response.model_dump(mode="json")

    result, error = await _run_short(
        session_factory,
        _count,
        op=OP_TABLES_COUNT,
        log_key=table_ref,
        status_errors=(HTTPException,),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


async def _dispatch_tables_create(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``tables.create`` through the shared metadata service.

    Validates with the same ``SDKTableCreateRequest`` DTO as the HTTP
    handler, keeps the Solution-restriction-before-scope ordering, and
    calls the same ``create_sdk_table`` service. ``solution_present``
    comes only from the principal — a Solution execution cannot conjure
    tables ad hoc on either transport. The facade's per-call ``app``
    argument is not read: the HTTP DTO has no such field, so the server
    ignores it there too.
    """
    from shared.sdk_table_metadata import (
        SDKTableMetadataError,
        create_sdk_table,
        ensure_sdk_table_create_allowed,
    )
    from src.models.contracts.cli import SDKTableCreateRequest, SDKTableInfo

    request, invalid = _validate_request(
        SDKTableCreateRequest,
        {
            "name": frame.get("name"),
            "table_schema": frame.get("table_schema"),
            "description": frame.get("description"),
            "scope": frame.get("scope"),
        },
        frame_id,
        OP_TABLES_CREATE,
    )
    if invalid is not None:
        return [invalid]
    actor_error = _require_actor(principal, frame_id, OP_TABLES_CREATE)
    if actor_error is not None:
        return [actor_error]
    assert principal.actor_email is not None and request is not None
    solution_present = principal.solution_id is not None
    try:
        ensure_sdk_table_create_allowed(solution_present)
    except SDKTableMetadataError as e:
        return [_error(frame_id, e.status_code, e.detail)]
    resolved_org_id, scope_error = await _resolve_frame_scope(
        principal, frame_id, {"scope": request.scope}, session_factory
    )
    if scope_error is not None:
        return [scope_error]

    async def _create(session: Any) -> dict[str, Any]:
        result = await create_sdk_table(
            session,
            name=request.name,
            table_schema=request.table_schema,
            description=request.description,
            org_id=resolved_org_id,
            actor_email=principal.actor_email,
            solution_present=solution_present,
        )
        return SDKTableInfo(**result).model_dump(mode="json")

    result, error = await _run_short(
        session_factory,
        _create,
        op=OP_TABLES_CREATE,
        log_key=request.name,
        status_errors=(SDKTableMetadataError,),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


async def _dispatch_tables_list(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``tables.list`` through the shared metadata service.

    Same DTO, same scope resolution, same external-sentinel handling as
    the HTTP handler. The items ride an ``{"items"}`` envelope — the
    transport result contract does not carry bare lists.
    """
    from shared.sdk_table_metadata import list_sdk_tables
    from src.models.contracts.cli import SDKTableInfo, SDKTableListRequest

    request, invalid = _validate_request(
        SDKTableListRequest,
        {"scope": frame.get("scope")},
        frame_id,
        OP_TABLES_LIST,
    )
    if invalid is not None:
        return [invalid]
    assert request is not None
    resolved_org_id, scope_error = await _resolve_frame_scope(
        principal, frame_id, {"scope": request.scope}, session_factory
    )
    if scope_error is not None:
        return [scope_error]

    async def _list(session: Any) -> dict[str, Any]:
        items = await list_sdk_tables(
            session,
            org_id=resolved_org_id,
            external=principal.is_external,
        )
        return {
            "items": [
                SDKTableInfo(**item).model_dump(mode="json") for item in items
            ]
        }

    result, error = await _run_short(
        session_factory, _list, op=OP_TABLES_LIST, log_key=""
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


async def _dispatch_tables_delete(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``tables.delete`` through the shared metadata service.

    The HTTP route requires a platform-admin principal; the local
    equivalent is the workflow engine identity — supervised service
    children (system-user non-superuser) get a 403, exactly like their
    HTTP DELETE would. A missing table is a 404 error frame (the facade
    raises); a Solution-managed table is a 409.
    """
    from shared.sdk_table_metadata import SDKTableMetadataError, delete_sdk_table

    raw_id = frame.get("table_id")
    if not isinstance(raw_id, str) or not raw_id.strip():
        return [
            _error(
                frame_id,
                422,
                f"invalid {OP_TABLES_DELETE} request: 'table_id' is required",
            )
        ]
    try:
        table_uuid = UUID(raw_id)
    except ValueError:
        return [
            _error(
                frame_id,
                422,
                f"invalid {OP_TABLES_DELETE} request: 'table_id' must be a UUID",
            )
        ]
    if principal.is_service:
        return [_error(frame_id, 403, "Only platform admins can delete tables")]
    actor_error = _require_actor(principal, frame_id, OP_TABLES_DELETE)
    if actor_error is not None:
        return [actor_error]

    async def _delete(session: Any) -> bool:
        return await delete_sdk_table(
            session, table_id=table_uuid, org_id=principal.caller_org_id
        )

    result, error = await _run_short(
        session_factory,
        _delete,
        op=OP_TABLES_DELETE,
        log_key=raw_id,
        status_errors=(SDKTableMetadataError,),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    if not result:
        return [_error(frame_id, 404, f"Table '{table_uuid}' not found")]
    return _single_ok(frame_id, True)


async def _resolve_tables_write_target(
    session: Any,
    principal: LocalDispatchPrincipal,
    table_ref: str,
    *,
    scope: str | None,
    solution: str | None,
    require_explicit_scope_gate: bool = False,
) -> tuple[Any, Any, Any]:
    """Resolve and gate one write's target table on an open session.

    Builds the parent-owned ``LocalTableContext`` (token-equivalent
    user, caller org, per-call target install) and resolves through the
    shared ``get_table_or_404`` — org gating, Solution install fallback,
    and the inbound gate stay identical to the HTTP path. Then applies
    the same Solution write-target gate (and, for batch writes, the
    explicit-scope exact-table gate) the HTTP handlers apply.

    Returns ``(table, user, ctx)``. Resolution and gate failures raise
    ``HTTPException`` for the caller to map to error frames.
    """
    from shared.table_resolution import LocalTableContext, get_table_or_404

    user = _table_user_for_principal(principal)
    ctx = LocalTableContext(
        db=session,
        user=user,
        org_id=principal.caller_org_id,
        solution_id=_tables_target_solution_id(solution, principal),
    )
    table = await get_table_or_404(ctx, table_ref, scope=scope)
    from src.routers.tables import (
        _assert_explicit_scope_targets_table,
        _assert_solution_write_targets_owned_table,
    )

    await _assert_solution_write_targets_owned_table(ctx, table)
    if require_explicit_scope_gate:
        await _assert_explicit_scope_targets_table(ctx, table, scope)
    return table, user, ctx


async def _dispatch_tables_insert(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``tables.insert`` through the shared write service.

    The body validates with the same ``DocumentCreate`` DTO as the HTTP
    handler (plain insert — no upsert branch). A missing table is a 404
    error frame; the facade auto-creates outside a Solution and retries
    once, exactly like the HTTP path.
    """
    from fastapi import HTTPException

    from shared.table_document_writes import TableWriteError
    from src.models.contracts.tables import DocumentCreate, DocumentPublic

    table_ref, invalid = _required_tables_field(
        frame, "table", frame_id, OP_TABLES_INSERT
    )
    if invalid is not None:
        return [invalid]
    scope, invalid = _optional_tables_field(frame, "scope", frame_id, OP_TABLES_INSERT)
    if invalid is not None:
        return [invalid]
    solution, invalid = _optional_tables_field(
        frame, "solution", frame_id, OP_TABLES_INSERT
    )
    if invalid is not None:
        return [invalid]
    doc_id = frame.get("doc_id")
    if doc_id is not None and not isinstance(doc_id, str):
        return [
            _error(
                frame_id,
                422,
                f"invalid {OP_TABLES_INSERT} request: 'doc_id' must be a string",
            )
        ]
    request, invalid = _validate_request(
        DocumentCreate,
        {
            "id": doc_id,
            "data": frame.get("data"),
            "created_by": frame.get("created_by"),
            "updated_by": frame.get("updated_by"),
        },
        frame_id,
        OP_TABLES_INSERT,
    )
    if invalid is not None:
        return [invalid]
    assert table_ref is not None and request is not None
    actor_error = _require_actor(principal, frame_id, OP_TABLES_INSERT)
    if actor_error is not None:
        return [actor_error]

    async def _insert(session: Any) -> dict[str, Any]:
        from shared.table_document_writes import insert_table_document

        table, user, _ctx = await _resolve_tables_write_target(
            session, principal, table_ref, scope=scope, solution=solution
        )
        doc = await insert_table_document(
            session,
            table,
            user,
            doc_id=request.id,
            data=request.data,
            created_by=request.created_by,
            updated_by=request.updated_by,
            upsert=False,
        )
        return DocumentPublic.model_validate(doc).model_dump(mode="json")

    result, error = await _run_short(
        session_factory,
        _insert,
        op=OP_TABLES_INSERT,
        log_key=table_ref,
        status_errors=(HTTPException, TableWriteError),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


async def _dispatch_tables_upsert(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``tables.upsert`` through the shared write service.

    Atomic replace-upsert by id (the JSONB ``data`` column is replaced,
    not merged), validated with the same ``DocumentUpsert`` DTO as the
    HTTP handler. A missing table is a 404 error frame for the facade's
    auto-create retry.
    """
    from fastapi import HTTPException

    from shared.table_document_writes import TableWriteError
    from src.models.contracts.tables import DocumentPublic, DocumentUpsert

    table_ref, invalid = _required_tables_field(
        frame, "table", frame_id, OP_TABLES_UPSERT
    )
    if invalid is not None:
        return [invalid]
    scope, invalid = _optional_tables_field(frame, "scope", frame_id, OP_TABLES_UPSERT)
    if invalid is not None:
        return [invalid]
    doc_id, invalid = _required_tables_field(
        frame, "doc_id", frame_id, OP_TABLES_UPSERT
    )
    if invalid is not None:
        return [invalid]
    request, invalid = _validate_request(
        DocumentUpsert,
        {
            "id": doc_id,
            "data": frame.get("data"),
            "created_by": frame.get("created_by"),
            "updated_by": frame.get("updated_by"),
        },
        frame_id,
        OP_TABLES_UPSERT,
    )
    if invalid is not None:
        return [invalid]
    assert table_ref is not None and doc_id is not None and request is not None
    actor_error = _require_actor(principal, frame_id, OP_TABLES_UPSERT)
    if actor_error is not None:
        return [actor_error]

    async def _upsert(session: Any) -> dict[str, Any]:
        from shared.table_document_writes import upsert_table_document

        table, user, _ctx = await _resolve_tables_write_target(
            session, principal, table_ref, scope=scope, solution=None
        )
        doc = await upsert_table_document(
            session,
            table,
            user,
            doc_id=request.id,
            data=request.data,
            created_by=request.created_by,
            updated_by=request.updated_by,
        )
        return DocumentPublic.model_validate(doc).model_dump(mode="json")

    result, error = await _run_short(
        session_factory,
        _upsert,
        op=OP_TABLES_UPSERT,
        log_key=table_ref,
        status_errors=(HTTPException, TableWriteError),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


async def _dispatch_tables_update(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``tables.update`` through the shared write service.

    Partial merge-update validated with the same ``DocumentUpdate`` DTO
    as the HTTP handler. A missing table or row is a 404 error frame
    (the facade maps it to ``None``).
    """
    from fastapi import HTTPException

    from shared.table_document_writes import TableWriteError
    from src.models.contracts.tables import DocumentPublic, DocumentUpdate

    table_ref, invalid = _required_tables_field(
        frame, "table", frame_id, OP_TABLES_UPDATE
    )
    if invalid is not None:
        return [invalid]
    doc_id, invalid = _required_tables_field(
        frame, "doc_id", frame_id, OP_TABLES_UPDATE
    )
    if invalid is not None:
        return [invalid]
    scope, invalid = _optional_tables_field(frame, "scope", frame_id, OP_TABLES_UPDATE)
    if invalid is not None:
        return [invalid]
    request, invalid = _validate_request(
        DocumentUpdate,
        {"data": frame.get("data"), "updated_by": frame.get("updated_by")},
        frame_id,
        OP_TABLES_UPDATE,
    )
    if invalid is not None:
        return [invalid]
    assert table_ref is not None and doc_id is not None and request is not None
    actor_error = _require_actor(principal, frame_id, OP_TABLES_UPDATE)
    if actor_error is not None:
        return [actor_error]

    async def _update(session: Any) -> dict[str, Any]:
        from shared.table_document_writes import update_table_document

        table, user, _ctx = await _resolve_tables_write_target(
            session, principal, table_ref, scope=scope, solution=None
        )
        doc = await update_table_document(
            session,
            table,
            user,
            doc_id=doc_id,
            data=request.data,
            updated_by=request.updated_by,
        )
        return DocumentPublic.model_validate(doc).model_dump(mode="json")

    result, error = await _run_short(
        session_factory,
        _update,
        op=OP_TABLES_UPDATE,
        log_key=table_ref,
        status_errors=(HTTPException, TableWriteError),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


async def _dispatch_tables_delete_document(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``tables.delete_document`` through the shared write service.

    A missing table or row is a 404 error frame (the facade maps it to
    ``False``).
    """
    from fastapi import HTTPException

    from shared.table_document_writes import TableWriteError

    table_ref, invalid = _required_tables_field(
        frame, "table", frame_id, OP_TABLES_DELETE_DOCUMENT
    )
    if invalid is not None:
        return [invalid]
    doc_id, invalid = _required_tables_field(
        frame, "doc_id", frame_id, OP_TABLES_DELETE_DOCUMENT
    )
    if invalid is not None:
        return [invalid]
    scope, invalid = _optional_tables_field(
        frame, "scope", frame_id, OP_TABLES_DELETE_DOCUMENT
    )
    if invalid is not None:
        return [invalid]
    assert table_ref is not None and doc_id is not None
    actor_error = _require_actor(principal, frame_id, OP_TABLES_DELETE_DOCUMENT)
    if actor_error is not None:
        return [actor_error]

    async def _delete(session: Any) -> bool:
        from shared.table_document_writes import delete_table_document

        table, user, _ctx = await _resolve_tables_write_target(
            session, principal, table_ref, scope=scope, solution=None
        )
        return await delete_table_document(session, table, user, doc_id=doc_id)

    result, error = await _run_short(
        session_factory,
        _delete,
        op=OP_TABLES_DELETE_DOCUMENT,
        log_key=table_ref,
        status_errors=(HTTPException, TableWriteError),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    assert result is True
    return _single_ok(frame_id, True)


async def _dispatch_tables_batch(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``tables.batch`` through the shared batch write service.

    One operation covers the facade's ``insert_batch`` (plain insert),
    ``upsert_batch`` (legacy merge upsert), and ``bulk_upsert``
    (privileged replace upsert): the body validates with the same
    ``DocumentBatchCreate`` DTO as the HTTP handler, so the 1000-row
    limit, explicit-id requirements, and write-mode compatibility match.
    The response carries the same ``DocumentBatchCreateResponse`` shape
    (insert conflicts listed, documents only when requested). A missing
    table is a 404 error frame for the facade's auto-create retry; a
    concurrent-write conflict is a 409 the facade retries boundedly.
    """
    from fastapi import HTTPException

    from shared.table_document_writes import TableWriteError
    from src.models.contracts.tables import DocumentBatchCreate, DocumentPublic

    table_ref, invalid = _required_tables_field(
        frame, "table", frame_id, OP_TABLES_BATCH
    )
    if invalid is not None:
        return [invalid]
    scope, invalid = _optional_tables_field(frame, "scope", frame_id, OP_TABLES_BATCH)
    if invalid is not None:
        return [invalid]
    raw_body: dict[str, Any] = {"documents": frame.get("documents")}
    for key in ("upsert", "write_mode", "return_documents"):
        if frame.get(key) is not None:
            raw_body[key] = frame[key]
    request, invalid = _validate_request(
        DocumentBatchCreate, raw_body, frame_id, OP_TABLES_BATCH
    )
    if invalid is not None:
        return [invalid]
    assert table_ref is not None and request is not None
    actor_error = _require_actor(principal, frame_id, OP_TABLES_BATCH)
    if actor_error is not None:
        return [actor_error]

    async def _batch(session: Any) -> dict[str, Any]:
        from shared.table_document_writes import (
            BatchDocumentInput,
            batch_write_table_documents,
        )

        table, user, _ctx = await _resolve_tables_write_target(
            session,
            principal,
            table_ref,
            scope=scope,
            solution=None,
            require_explicit_scope_gate=True,
        )
        outcome = await batch_write_table_documents(
            session,
            table,
            user,
            items=[
                BatchDocumentInput(
                    id=item.id,
                    data=item.data,
                    created_by=item.created_by,
                    updated_by=item.updated_by,
                )
                for item in request.documents
            ],
            mode=request.effective_write_mode,
        )
        return {
            "inserted": outcome.inserted,
            "errors": [
                {"id": conflict.id, "error": "Document already exists"}
                for conflict in outcome.insert_conflicts
            ],
            "documents": (
                [
                    DocumentPublic.model_validate(doc).model_dump(mode="json")
                    for doc in outcome.ordered_documents
                ]
                if request.return_documents
                else []
            ),
        }

    result, error = await _run_short(
        session_factory,
        _batch,
        op=OP_TABLES_BATCH,
        log_key=table_ref,
        status_errors=(HTTPException, TableWriteError),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


async def _dispatch_tables_batch_delete(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``tables.batch_delete`` through the shared write service.

    Same ``DocumentBatchDeleteRequest`` DTO and all-or-nothing policy
    behavior as the HTTP handler. A missing table is a 404 error frame
    (the facade maps it to an empty result).
    """
    from fastapi import HTTPException

    from shared.table_document_writes import TableWriteError
    from src.models.contracts.tables import DocumentBatchDeleteRequest

    table_ref, invalid = _required_tables_field(
        frame, "table", frame_id, OP_TABLES_BATCH_DELETE
    )
    if invalid is not None:
        return [invalid]
    scope, invalid = _optional_tables_field(
        frame, "scope", frame_id, OP_TABLES_BATCH_DELETE
    )
    if invalid is not None:
        return [invalid]
    request, invalid = _validate_request(
        DocumentBatchDeleteRequest,
        {"ids": frame.get("ids")},
        frame_id,
        OP_TABLES_BATCH_DELETE,
    )
    if invalid is not None:
        return [invalid]
    assert table_ref is not None and request is not None
    actor_error = _require_actor(principal, frame_id, OP_TABLES_BATCH_DELETE)
    if actor_error is not None:
        return [actor_error]

    async def _batch_delete(session: Any) -> dict[str, Any]:
        from shared.table_document_writes import batch_delete_table_documents

        table, user, _ctx = await _resolve_tables_write_target(
            session, principal, table_ref, scope=scope, solution=None
        )
        outcome = await batch_delete_table_documents(
            session, table, user, ids=list(request.ids)
        )
        return {"deleted": outcome.deleted, "deleted_ids": outcome.deleted_ids}

    result, error = await _run_short(
        session_factory,
        _batch_delete,
        op=OP_TABLES_BATCH_DELETE,
        log_key=table_ref,
        status_errors=(HTTPException, TableWriteError),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


def _required_str_field(
    frame: dict[str, Any], field: str, frame_id: str | None, op: str
) -> tuple[str | None, dict[str, Any] | None]:
    """One required non-empty string frame field, else a 422 error frame."""
    value = frame.get(field)
    if isinstance(value, str) and value:
        return value, None
    return None, _error(frame_id, 422, f"invalid {op} request: {field!r} is required")


def _module_source_scope(
    principal: LocalDispatchPrincipal,
) -> Any:
    """Authoritative source scope for one child's ``modules.*`` calls.

    Built only from the parent-derived principal (its Solution install id
    plus the install's global-repo flag) — child frames are never read
    for scope, so a child cannot forge another install's sources or widen
    a sealed Solution's import surface.
    """
    from shared.sdk_modules import ModuleSourceScope

    return ModuleSourceScope(
        solution_id=(
            str(principal.solution_id) if principal.solution_id is not None else None
        ),
        global_repo_access=principal.solution_global_repo_access,
    )


async def _dispatch_modules_resolve(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``modules.resolve`` through the shared module-source service.

    The child supplies only the logical import ``name``; the scope comes
    exclusively from the parent-derived principal. Calls the exact shared
    ``shared.sdk_modules.resolve_module_name`` the HTTP handler calls, so
    Solution-first ordering, sealed-Solution restrictions, cache keys/TTLs,
    and namespace handling are identical by construction. Large resolved
    sources ride bounded chunked frames via ``_ok_frames``.
    """
    from shared.sdk_modules import ModuleSourceError, resolve_module_name

    name, invalid = _required_str_field(
        frame, "name", frame_id, OP_MODULES_RESOLVE
    )
    if invalid is not None:
        return [invalid]
    assert name is not None
    scope = _module_source_scope(principal)

    async def _resolve(session: Any) -> dict[str, object]:
        return await resolve_module_name(name, scope=scope)

    result, error = await _run_short(
        session_factory,
        _resolve,
        op=OP_MODULES_RESOLVE,
        log_key=name,
        status_errors=(ModuleSourceError,),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


async def _dispatch_modules_fetch(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``modules.fetch`` through the shared module-source service.

    The child supplies only the candidate storage ``path``; the scope
    comes exclusively from the parent-derived principal, which also
    enforces the Solution-path access rules (a sealed install's bare
    workspace fetch 403s; an out-of-install solution path 403s; a miss
    404s so the child advances to the next candidate). Large sources ride
    bounded chunked frames via ``_ok_frames``.
    """
    from shared.sdk_modules import ModuleSourceError, fetch_module_source

    path, invalid = _required_str_field(
        frame, "path", frame_id, OP_MODULES_FETCH
    )
    if invalid is not None:
        return [invalid]
    assert path is not None
    scope = _module_source_scope(principal)

    async def _fetch(session: Any) -> dict:
        return await fetch_module_source(path, scope=scope)

    result, error = await _run_short(
        session_factory,
        _fetch,
        op=OP_MODULES_FETCH,
        log_key=path,
        status_errors=(ModuleSourceError,),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


def _chunked_frames(
    frame_id: str | None, raw_result: bytes
) -> Iterable[dict[str, Any]]:
    """Yield a header plus ordered part frames for a large result.

    Lazy generator: the single ``raw_result`` buffer is shared and each
    part is base64-encoded only when the caller iterates to it, so
    ``serve_channel`` sends sequentially with pipe backpressure instead of
    materializing every part up front. Each frame is size-validated at
    send time by ``serve_channel``; there is no total result cap (the
    HTTP path has none).
    """
    import base64

    total = len(raw_result)
    parts = -(-total // _CHUNK_RAW_BYTES)
    yield {
        "v": TRANSPORT_VERSION,
        "id": frame_id,
        "ok": True,
        "chunked": True,
        "total": total,
        "parts": parts,
    }
    for i in range(parts):
        chunk = raw_result[i * _CHUNK_RAW_BYTES : (i + 1) * _CHUNK_RAW_BYTES]
        yield {
            "v": TRANSPORT_VERSION,
            "id": frame_id,
            "part": i,
            "data": base64.b64encode(chunk).decode("ascii"),
        }


class _RequestGone(Exception):
    """The child went away mid-request (EOF/pipe error during reassembly)."""


class _FrameOversized(Exception):
    """A received frame exceeds the wire-size bound."""


class _RequestMalformed(Exception):
    """A chunked request violated the wire contract."""


def _check_chunk_claim(total: Any, parts: Any) -> tuple[int, int]:
    """Validate a chunk header's ``total``/``parts`` claim (either direction).

    No total cap is enforced — the HTTP path has none. Raises
    :class:`_RequestMalformed` on any inconsistency before a single part
    is read.
    """
    if (
        not isinstance(total, int)
        or isinstance(total, bool)
        or total <= 0
        or not isinstance(parts, int)
        or isinstance(parts, bool)
        or parts < 1
        or parts != -(-total // _CHUNK_RAW_BYTES)
    ):
        raise _RequestMalformed("invalid chunk header")
    return total, parts


async def _reassemble_request(header: dict[str, Any], recv: Callable[[], Any]) -> dict[str, Any]:
    """Rebuild one chunked child request (e.g. large ``config.set`` value).

    Reads exactly the announced parts with bounded per-frame reads; the
    pipe itself provides sequential backpressure. There is no total cap.
    Any deviation — wrong order, wrong id, bad encoding, length mismatch,
    child gone mid-stream — raises instead of returning a partial request.
    """
    request_id = header.get("id")
    op = header.get("op")
    total, parts = _check_chunk_claim(header.get("total"), header.get("parts"))
    buf = bytearray()
    for i in range(parts):
        try:
            raw = await recv()
        except EOFError as e:
            raise _RequestGone("child went away mid-request") from e
        except _FrameOversized as e:
            raise _RequestMalformed("request part exceeded frame bound") from e
        except OSError as e:
            raise _RequestGone(f"child channel failed mid-request: {e}") from e
        try:
            part = decode_frame(raw)
        except Exception as e:
            raise _RequestMalformed(f"unparseable request part: {e}") from e
        if part.get("id") != request_id or part.get("part") != i:
            raise _RequestMalformed("request chunk stream desynchronized")
        data = part.get("data")
        if not isinstance(data, str):
            raise _RequestMalformed("invalid request chunk encoding")
        try:
            chunk = base64.b64decode(data.encode("ascii"), validate=True)
        except Exception as e:
            raise _RequestMalformed(f"invalid request chunk payload: {e}") from e
        buf.extend(chunk)
        if len(buf) > total:
            raise _RequestMalformed("request chunk stream exceeded declared total")
    if len(buf) != total:
        raise _RequestMalformed("request chunk stream length mismatch")
    try:
        request = json.loads(bytes(buf).decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise _RequestMalformed(f"invalid chunked request: {e}") from e
    if (
        not isinstance(request, dict)
        or request.get("id") != request_id
        or request.get("op") != op
    ):
        raise _RequestMalformed("reassembled request id/op mismatch")
    return request


async def serve_channel(
    *,
    recv_conn: Any,
    send_conn: Any,
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    executor: Any = None,
    allowed_ops: frozenset[str] = SDK_CHANNEL_ALLOWED_OPS,
) -> str:
    """Pump one child SDK channel until EOF, protocol violation, or cancel.

    Reads exactly one frame at a time (the child serializes requests, so at
    most one is ever outstanding; a chunked request header is followed by
    exactly its announced parts, read sequentially with bounded frames and
    pipe backpressure), dispatches it on a short session, and writes the
    response frame-by-frame as the dispatch iterable yields (large results
    encode one part per send, preserving pipe backpressure). Blocking pipe
    IO runs in ``executor`` (or ``asyncio.to_thread``) so the pool event
    loop stays responsive; no unbounded queues are used at any point, and
    there is no total size cap in either direction (the HTTP path has none).

    Returns a short reason string: ``"eof"`` (child exited/crashed),
    ``"oversized"`` (a frame exceeded the byte bound — the pipe is left
    unreadable by design, so the channel closes rather than risk unbounded
    allocation), ``"malformed"`` (unparseable or id-less frame — channel
    closed rather than risk desync), ``"oversize-out"`` (a frame the parent
    built exceeded the bound — internal bug guard, channel closed), or
    ``"child-gone"`` (response write failed). Cancellation propagates for
    pool shutdown.
    """
    loop = asyncio.get_running_loop()

    async def _recv() -> bytes:
        if executor is not None:
            raw = await loop.run_in_executor(
                executor, recv_conn.recv_bytes, MAX_FRAME_BYTES + 1
            )
        else:
            raw = await asyncio.to_thread(recv_conn.recv_bytes, MAX_FRAME_BYTES + 1)
        if len(raw) > MAX_FRAME_BYTES:
            raise _FrameOversized
        return raw

    async def _send(raw: bytes) -> None:
        if executor is not None:
            await loop.run_in_executor(executor, send_conn.send_bytes, raw)
        else:
            await asyncio.to_thread(send_conn.send_bytes, raw)

    async def _send_frame(frame: dict[str, Any]) -> str | None:
        """Validate one outgoing frame's size at send time, then send it."""
        try:
            raw = _encode_outgoing(frame)
        except _OutgoingTooLarge:
            logger.error(
                "local SDK outgoing frame exceeded byte bound; closing channel"
            )
            return "oversize-out"
        try:
            await _send(raw)
        except (EOFError, OSError):
            return "child-gone"
        return None

    while True:
        try:
            raw = await _recv()
        except EOFError:
            return "eof"
        except _FrameOversized:
            logger.warning("local SDK frame exceeded byte bound; closing channel")
            return "oversized"
        except OSError:
            # Connection has no portable exception subtype for an
            # over-bound frame. The descriptor cannot be safely reused, and
            # every OSError on this bounded receive has the same close path.
            return "oversized"
        try:
            frame = decode_frame(raw)
        except Exception:
            return "malformed"
        if frame.get("chunked"):
            # A large child request (config.set with a big JSON value):
            # reassemble before dispatch. Any violation closes the channel
            # rather than risk desync; a child gone mid-stream reads as EOF.
            if frame.get("v") != TRANSPORT_VERSION or not isinstance(
                frame.get("id"), str
            ):
                return "malformed"
            try:
                frame = await _reassemble_request(frame, _recv)
            except _RequestGone:
                return "eof"
            except _RequestMalformed as e:
                logger.warning("local SDK chunked request rejected: %s", e)
                return "malformed"
            except _FrameOversized:
                logger.warning("local SDK frame exceeded byte bound; closing channel")
                return "oversized"
            except OSError:
                return "oversized"
        if not isinstance(frame.get("id"), str):
            return "malformed"
        if frame.get("op") not in allowed_ops:
            reason = await _send_frame(
                _error(
                    frame["id"],
                    404,
                    f"local channel operation not allowed: {frame.get('op')!r}",
                )
            )
            if reason is not None:
                return reason
            continue
        for response in await dispatch_frames(session_factory, principal, frame):
            reason = await _send_frame(response)
            if reason is not None:
                return reason
