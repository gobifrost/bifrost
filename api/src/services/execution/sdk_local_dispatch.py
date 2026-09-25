"""
Parent-side dispatcher for the engine-local SDK operation transport.

The worker parent (``ProcessPoolManager``) serves ``config`` requests
(get, set, list, delete) arriving on each child's dedicated SDK channel.
Identity and scope come exclusively from the parent's own dispatch
context (:func:`principal_from_context`) — child-supplied scope strings
are treated as untrusted requests and re-validated through the same
``resolve_effective_scope`` rule table the HTTP path uses, and the actor
email for mutation audit is the effective SDK actor (engine sentinel for
workflows, service identity for ``@service`` children), never the
initiating user's ``caller.email`` and never child frames.

Each operation runs on the parent's pooled database engine with one short
session, and calls the exact shared business service
(``shared.sdk_config``) the HTTP handler calls, so cascade,
external-user behavior, secret handling, type coercion, audit
attribution, commit/cache ordering, and missing-key mapping are identical
by construction. Large payloads in either direction travel as bounded
chunked frames (header plus ordered parts, every frame within the wire
bound); small payloads use a single frame.

Allowlist (stage 2a): ``config.get/set/list/delete``. Unknown operations
or wire versions get an error response — never silent acceptance, never
arbitrary route forwarding. The existing engine token path is untouched
for every operation not yet migrated.
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
    TRANSPORT_VERSION,
    decode_frame,
)
from shared.sdk_config import ScopeResolutionError, resolve_sdk_scope

logger = logging.getLogger(__name__)

# Operations this dispatcher will serve. Stage 2a: the config facade.
ALLOWLIST = frozenset({OP_CONFIG_GET, OP_CONFIG_SET, OP_CONFIG_LIST, OP_CONFIG_DELETE})

# Wall-clock bound for one parent-side operation (short session + indexed
# read). A stall fails that request loudly; the child has its own timeout
# and treats a missing response as fatal (no HTTP fallback).
DISPATCH_TIMEOUT_SECONDS = 25.0

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


class LocalPrincipalError(ValueError):
    """Parent dispatch context carries an unusable identity.

    Raised before any local pump starts — the pool fails the dispatch
    loudly instead of serving SDK calls under a downgraded (e.g. global)
    scope.
    """


def principal_from_context(context_data: Mapping[str, Any]) -> LocalDispatchPrincipal:
    """Derive the dispatch principal from parent-owned execution context.

    Reads only ``organization`` (id/is_provider), ``is_platform_admin``,
    and the parent-owned ``service`` block assembled by the parent consumer.
    Never reads child frames, and never reads ``caller.email``: the
    effective SDK actor is the engine sentinel for workflows
    (``engine@bifrost.internal``, the ``mint_engine_token()`` address HTTP
    workflow calls authenticate as) and the shared
    ``service_sdk_actor_email`` derivation for services (the
    ``mint_service_token()`` address). A missing or empty org id means a
    genuinely global execution; a malformed non-empty id fails closed. A
    present-but-malformed ``service`` block (non-mapping, missing or
    non-UUID ``service_id``) fails closed rather than attributing service
    writes to a forged or blank value.
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
    return LocalDispatchPrincipal(
        caller_org_id=caller_org_id,
        # Service tokens are never superuser by construction — force False
        # rather than trusting any flag the context might carry.
        is_platform_admin=False,
        is_provider_org=bool(org.get("is_provider", False)),
        is_external=False,
        actor_email=actor_email,
        is_service=True,
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
) -> tuple[Any, dict[str, Any] | None]:
    """Run one service coroutine on a short pooled session with a deadline.

    Returns ``(result, None)`` on success, or ``(None, error_frame)`` for
    the timeout/service failures the child maps to transport errors.
    """
    try:
        async def _run() -> Any:
            async with session_factory() as session:
                return await coro_factory(session)

        result = await asyncio.wait_for(_run(), DISPATCH_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        logger.warning("local %s dispatch timed out for key=%r", op, log_key)
        return None, _error(None, 503, f"local {op} dispatch timed out")
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
        except OSError as e:
            if "bad message length" in str(e):
                raise _RequestMalformed("request part exceeded frame bound") from e
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
            return await loop.run_in_executor(
                executor, recv_conn.recv_bytes, MAX_FRAME_BYTES + 1
            )
        return await asyncio.to_thread(recv_conn.recv_bytes, MAX_FRAME_BYTES + 1)

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
        except OSError as e:
            # recv_bytes enforces the maxlength without allocating the
            # announced size; an oversized peer breaks the channel here.
            if "bad message length" in str(e):
                logger.warning("local SDK frame exceeded byte bound; closing channel")
                return "oversized"
            return "eof"
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
            except OSError as e:
                if "bad message length" in str(e):
                    logger.warning(
                        "local SDK frame exceeded byte bound; closing channel"
                    )
                    return "oversized"
                return "eof"
        if not isinstance(frame.get("id"), str):
            return "malformed"
        for response in await dispatch_frames(session_factory, principal, frame):
            reason = await _send_frame(response)
            if reason is not None:
                return reason
