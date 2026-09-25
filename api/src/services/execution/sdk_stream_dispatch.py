"""
Parent-side pump for the engine-local streaming transport.

Serves one child's dedicated stream channel: the child opens a named
operation stream, spends one-event credits (``pull``), and receives ordered
event frames plus an explicit outer terminal frame (``end``) or an
HTTP-style terminal error. A ``cancel`` control frame races even a blocked
source read; the parent stops/closes its source and acknowledges with
``cancelled``, leaving the channel reusable for a later stream.

The production operation registry (:data:`STREAM_OPS`) holds the real
``ai.stream`` source (:func:`ai_stream_source`), which runs the shared
``shared.sdk_ai.stream_sdk_ai`` generator — the same operation the HTTP
SSE handler consumes — for parent-derived principals. Tests inject a
fake operation and async source through the ``registry`` pump seam for
transport-only coverage; the AI binding itself is covered through the
production registry.

Gripes this pump obeys from the handoff:

- One active stream per child; the parent advances its source at most once
  per ``pull`` credit.
- Bounded JSON frames (the same 64 KiB invariant as the unary/import
  transports), chunking for large event payloads, pipe backpressure (every
  send is sequential), a bounded deadline covering setup and idle periods,
  no pickle, no unbounded queue.
- The outer terminal frame is distinct from a business ``done`` event: the
  source generator runs to completion (including post-``done`` work) before
  the pump sends ``end``.
- The pump itself never opens a database session or holds a connection
  while awaiting an external source: sources own their short sessions
  (setup first, release before provider latency, reacquire for terminal
  bookkeeping — like ``shared.sdk_ai.stream_sdk_ai``). The pump closes the
  source generator on child EOF, parent shutdown, protocol error, idle
  timeout, terminal delivery, or cancellation.
- Size/order/version validation applies to every open, credit, event,
  chunk, and cancel frame. A graceful cancel/ack failure fails closed and
  breaks only this channel — never HTTP (this pump has no HTTP path).
- The stream descriptors and this pump are independent of the unary SDK
  and sync import channels.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
from collections.abc import AsyncGenerator, Callable, Mapping
from contextlib import suppress
from typing import Any

from bifrost._stream_transport import (
    _CHUNK_RAW_BYTES,
    MAX_FRAME_BYTES,
    OP_AI_STREAM,
    TRANSPORT_VERSION,
    decode_frame,
)

logger = logging.getLogger(__name__)

# Wall-clock bound covering one stream's setup (open to first credit) and
# each idle period between credits, plus any single source read. A stall
# fails that stream loudly with a terminal error; the channel itself stays
# usable for a later stream. The child deadline adds a margin on top so a
# parent timeout still arrives as an error frame first.
STREAM_IDLE_TIMEOUT_SECONDS = 30.0

# Slice for readability polls while a source read is outstanding. Polling
# consumes no frame, so cancelling a poll between slices is always safe —
# unlike cancelling a blocked ``recv_bytes``, which could steal the next
# control frame for a discarded task.
_POLL_SLICE_SECONDS = 0.2


class StreamSourceError(Exception):
    """One stream source failed with an HTTP-style terminal status.

    Sources raise this (instead of leaking provider internals) so the pump
    maps a provider failure to exactly one terminal error frame after the
    already delivered events — never silently dropping them, never sending
    a second terminal.
    """

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


# A named stream source: yields JSON-serializable event-payload dicts in
# stream order, then returns. The factory receives the validated open
# params and the parent-derived dispatch principal (never child claims).
# It owns any sessions it needs — the pump holds none.
StreamSourceFactory = Callable[
    [dict[str, Any], Any], AsyncGenerator[dict[str, Any], None]
]

# Production stream operations. ``ai.stream`` is bound here: the source
# runs the shared ``shared.sdk_ai.stream_sdk_ai`` generator for the
# validated open params and the parent-derived principal. Tests pass
# their own registry to :func:`serve_stream_channel` for transport-only
# coverage instead of touching this mapping.
STREAM_OPS: dict[str, StreamSourceFactory] = {}


async def ai_stream_source(
    params: dict[str, Any],
    principal: Any,
    *,
    session_factory: Any = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """Serve ``ai.stream`` through the shared AI service.

    Validates the open params with the same ``CLIAICompleteRequest`` DTO
    as the HTTP handler (422), then consumes the exact
    ``shared.sdk_ai.stream_sdk_ai`` generator the handler consumes —
    provider selection, chunk construction, error text, and usage
    attribution are identical by construction. The child sends the
    already-composed messages (knowledge context applied) and the
    already-encoded input files (large payloads arrive via the chunked
    open frames); the public ``ai.stream`` takes no profile, so
    ``profile=None`` always, like the HTTP path.

    The token-equivalent caller comes from the parent-derived principal
    (never child claims), the usage ``execution_id`` is the parent's own
    (any frame-supplied id is validated by the DTO but ignored), and the
    requested ``org_id`` rides as an untrusted scope resolved here —
    before the generator starts — so authorization failures stay
    terminal stream errors (HTTP status), never stream events. Scope
    failures raise :class:`StreamSourceError` with their own status
    (403/422), never a generic 500.

    The shared generator yields its business ``done`` payload before
    resuming to record usage. Only that single payload is buffered: every
    earlier delta is yielded incrementally, then the generator is
    exhausted (recording usage), its transaction committed, and the
    buffered ``done`` yielded last — so a caller that stops on ``done``
    never loses usage attribution. Provider ``{"error": ...}`` events
    stream through like any other event (the facade maps them to one
    empty non-done chunk, then EOF). On early child close the shared
    generator is closed and no partial usage is recorded or committed.
    Usage commit itself is best-effort: a failed commit never fails the
    already-delivered stream.
    """
    from pydantic import ValidationError

    from shared.sdk_ai import stream_sdk_ai
    from shared.sdk_config import ScopeResolutionError, resolve_sdk_scope
    from src.models.contracts.cli import CLIAICompleteRequest

    try:
        request = CLIAICompleteRequest.model_validate(
            {
                "messages": params.get("messages"),
                "max_tokens": params.get("max_tokens"),
                "org_id": params.get("org_id"),
                "model": params.get("model"),
                "execution_id": params.get("execution_id"),
                "input_files": params.get("input_files", []),
            }
        )
    except ValidationError as e:
        raise StreamSourceError(
            422, f"invalid {OP_AI_STREAM} request: {e}"
        ) from None

    from src.services.execution.sdk_local_dispatch import _ai_user_for_principal

    user = _ai_user_for_principal(principal)
    parent_execution_id = getattr(principal, "execution_id", None)

    if session_factory is None:
        from src.core.database import get_session_factory

        session_factory = get_session_factory()
    async with session_factory() as session:
        try:
            resolved_org_id = await resolve_sdk_scope(
                request.org_id,
                caller_org_id=user.organization_id,
                is_platform_admin=user.is_superuser,
                session=session,
            )
        except ScopeResolutionError as e:
            raise StreamSourceError(e.status_code, e.detail) from None

        gen = stream_sdk_ai(
            session,
            user,
            messages=[dict(m) for m in request.messages],
            max_tokens=request.max_tokens,
            model=request.model,
            profile=None,
            execution_id=parent_execution_id,
            resolved_org_id=resolved_org_id,
            input_files=list(request.input_files),
        )
        pending_done: dict[str, Any] | None = None
        completed = False
        try:
            async for event in gen:
                if isinstance(event, dict) and event.get("done") is True:
                    pending_done = event
                    continue
                yield event
            completed = True
        finally:
            with suppress(Exception):
                await gen.aclose()
        if completed:
            try:
                await session.commit()
            except Exception as e:
                from src.core.log_safety import log_safe

                logger.warning(f"AI stream usage commit failed: {log_safe(e)}")
                with suppress(Exception):
                    await session.rollback()
            if pending_done is not None:
                yield pending_done


STREAM_OPS[OP_AI_STREAM] = ai_stream_source


class _FrameOversized(Exception):
    """A received frame exceeds the wire-size bound."""


class _IdleTimeout(Exception):
    """No credit/control frame arrived before the stream deadline."""


class _Malformed(Exception):
    """A frame violated the stream wire contract."""


class _ChildGone(Exception):
    """The child went away (EOF/pipe error)."""


def _error_frame(stream_id: str | None, status: int, detail: str) -> dict[str, Any]:
    return {
        "v": TRANSPORT_VERSION,
        "id": stream_id,
        "type": "error",
        "status": status,
        "detail": detail,
    }


def _encode_outgoing(frame: dict[str, Any]) -> bytes:
    """Serialize one outgoing frame, enforcing the byte bound before sending."""
    raw = json.dumps(frame, separators=(",", ":")).encode("utf-8")
    if len(raw) > MAX_FRAME_BYTES:
        raise _Malformed(
            f"outgoing stream frame is {len(raw)} bytes (limit {MAX_FRAME_BYTES})"
        )
    return raw


def _check_chunk_claim(total: Any, parts: Any) -> tuple[int, int]:
    """Validate an event chunk header's ``total``/``parts`` claim.

    No total cap is enforced — the HTTP path has none. Raises
    :class:`_Malformed` on any inconsistency before a single part is sent.
    """
    if (
        not isinstance(total, int)
        or isinstance(total, bool)
        or total <= 0
        or not isinstance(parts, int)
        or isinstance(parts, bool)
        or parts < 2
        or parts != -(-total // _CHUNK_RAW_BYTES)
    ):
        raise _Malformed("invalid stream chunk header")
    return total, parts


async def serve_stream_channel(
    *,
    recv_conn: Any,
    send_conn: Any,
    principal: Any,
    registry: Mapping[str, StreamSourceFactory] | None = None,
    idle_timeout: float = STREAM_IDLE_TIMEOUT_SECONDS,
    executor: Any = None,
) -> str:
    """Pump one child stream channel until EOF, protocol violation, or cancel.

    At most one stream is active at a time. Each ``open`` names an operation
    from ``registry`` (default :data:`STREAM_OPS`, empty in production) and
    carries small params in a single bounded frame; the source generator is
    created from the parent-derived ``principal`` and advanced at most once
    per ``pull`` credit. ``cancel`` races even a blocked source read: the
    pump drops the pending read, closes the source, and acknowledges with
    ``cancelled`` so the child may open a later stream.

    Blocking pipe IO runs in ``executor`` (or ``asyncio.to_thread``) so the
    pool event loop stays responsive; readability polls consume no frame, so
    a cancelled wait never steals a control frame. No unbounded queues are
    used at any point.

    Returns a short reason string: ``"eof"`` (child exited/crashed),
    ``"oversized"`` (a frame exceeded the byte bound), ``"malformed"``
    (version/id/order/type violation), or ``"child-gone"`` (a response
    write failed). Cancellation propagates for pool shutdown after closing
    any active source.
    """
    ops = STREAM_OPS if registry is None else registry
    loop = asyncio.get_running_loop()

    async def _poll_ready(timeout: float) -> bool:
        if executor is not None:
            return await loop.run_in_executor(executor, recv_conn.poll, timeout)
        return await asyncio.to_thread(recv_conn.poll, timeout)

    async def _recv_frame(deadline: float | None) -> dict[str, Any]:
        """Read and parse one control frame, with an optional idle deadline."""
        while True:
            remaining = None if deadline is None else deadline - loop.time()
            if remaining is not None and remaining <= 0:
                raise _IdleTimeout
            try:
                if remaining is None:
                    ready = await _poll_ready(_POLL_SLICE_SECONDS)
                else:
                    ready = await _poll_ready(min(_POLL_SLICE_SECONDS, remaining))
            except OSError as e:
                raise _ChildGone(f"child channel poll failed: {e}") from e
            if ready:
                break
        raw = await _recv_raw_tail()
        try:
            return decode_frame(raw)
        except Exception as e:
            raise _Malformed(f"unparseable stream frame: {e}") from e

    async def _recv_raw_tail() -> bytes:
        try:
            if executor is not None:
                raw: bytes = await loop.run_in_executor(
                    executor, recv_conn.recv_bytes, MAX_FRAME_BYTES + 1
                )
            else:
                raw = await asyncio.to_thread(recv_conn.recv_bytes, MAX_FRAME_BYTES + 1)
        except EOFError as e:
            raise _ChildGone("child closed the stream channel") from e
        except OSError:
            # Connection has no portable exception subtype for an
            # over-bound frame. The descriptor cannot be safely reused, and
            # every OSError on this bounded receive has the same close path
            # (matches the unary SDK pump).
            raise _FrameOversized from None
        if len(raw) > MAX_FRAME_BYTES:
            raise _FrameOversized
        return raw

    async def _send(frame: dict[str, Any]) -> None:
        try:
            raw = _encode_outgoing(frame)
        except _Malformed:
            logger.error("local stream outgoing frame exceeded byte bound; closing")
            raise
        try:
            if executor is not None:
                await loop.run_in_executor(executor, send_conn.send_bytes, raw)
            else:
                await asyncio.to_thread(send_conn.send_bytes, raw)
        except (EOFError, OSError) as e:
            raise _ChildGone(f"stream response write failed: {e}") from e

    async def _send_event(
        stream_id: str, seq: int, event: dict[str, Any]
    ) -> None:
        """Send one ordered event, chunked when its payload is large."""
        single = {
            "v": TRANSPORT_VERSION,
            "id": stream_id,
            "type": "event",
            "seq": seq,
            "event": event,
        }
        single_raw = json.dumps(single, separators=(",", ":")).encode("utf-8")
        if len(single_raw) <= MAX_FRAME_BYTES:
            await _send(single)
            return
        raw_event = json.dumps(event, separators=(",", ":")).encode("utf-8")
        total = len(raw_event)
        parts = -(-total // _CHUNK_RAW_BYTES)
        _check_chunk_claim(total, parts)
        await _send(
            {
                "v": TRANSPORT_VERSION,
                "id": stream_id,
                "type": "event",
                "seq": seq,
                "chunked": True,
                "total": total,
                "parts": parts,
            }
        )
        for i in range(parts):
            chunk = raw_event[i * _CHUNK_RAW_BYTES : (i + 1) * _CHUNK_RAW_BYTES]
            await _send(
                {
                    "v": TRANSPORT_VERSION,
                    "id": stream_id,
                    "type": "chunk",
                    "seq": seq,
                    "part": i,
                    "data": base64.b64encode(chunk).decode("ascii"),
                }
            )

    def _validate_open(frame: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
        if frame.get("v") != TRANSPORT_VERSION or not isinstance(frame.get("id"), str):
            raise _Malformed("stream open version/id mismatch")
        if frame.get("type") != "open":
            raise _Malformed(f"expected stream open, got {frame.get('type')!r}")
        op = frame.get("op")
        if not isinstance(op, str) or not op:
            raise _Malformed("stream open carries no operation name")
        params = frame.get("params", {})
        if not isinstance(params, dict):
            raise _Malformed("stream open params must be an object")
        return frame["id"], op, params

    async def _read_open(frame: dict[str, Any]) -> dict[str, Any]:
        """Reassemble a large opening request from bounded ordered frames."""
        if frame.get("type") != "open_chunked":
            return frame
        if frame.get("v") != TRANSPORT_VERSION or not isinstance(frame.get("id"), str):
            raise _Malformed("chunked stream open version/id mismatch")
        total, parts = _check_chunk_claim(frame.get("total"), frame.get("parts"))
        stream_id = frame["id"]
        buf = bytearray()
        for i in range(parts):
            part = await _recv_frame(loop.time() + idle_timeout)
            if (
                part.get("v") != TRANSPORT_VERSION
                or part.get("id") != stream_id
                or part.get("type") != "open_chunk"
                or part.get("part") != i
                or isinstance(part.get("part"), bool)
            ):
                raise _Malformed("stream open chunk out of order")
            encoded = part.get("data")
            if not isinstance(encoded, str):
                raise _Malformed("invalid stream open chunk encoding")
            try:
                chunk = base64.b64decode(encoded.encode("ascii"), validate=True)
            except (UnicodeEncodeError, binascii.Error) as e:
                raise _Malformed("invalid stream open chunk payload") from e
            buf.extend(chunk)
            if len(buf) > total:
                raise _Malformed("stream open chunk exceeded declared total")
        if len(buf) != total:
            raise _Malformed("stream open chunk length mismatch")
        try:
            request = json.loads(buf.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as e:
            raise _Malformed("invalid chunked stream open") from e
        if not isinstance(request, dict) or request.get("id") != stream_id:
            raise _Malformed("chunked stream open id mismatch")
        return request

    active_id: str | None = None
    source: AsyncGenerator[dict[str, Any], None] | None = None
    seq = 0
    # Ids of opens rejected with a terminal error (unknown operation or
    # factory failure). The child's credit for such an open is already in
    # flight when the rejection is sent, so the stale ``pull``/``cancel``
    # must be ignored — its terminal was already delivered. Anything else
    # arriving without an active stream stays a protocol violation.
    # Bounded: ids are uuids, consumed on use, oldest evicted past the cap.
    rejected: dict[str, None] = {}

    def _remember_rejected(stream_id: str) -> None:
        rejected[stream_id] = None
        while len(rejected) > 16:
            rejected.pop(next(iter(rejected)))

    async def _close_source() -> None:
        nonlocal source
        if source is not None:
            with suppress(Exception):
                await source.aclose()
            source = None

    async def _advance_once(stream_id: str) -> str | None:
        """Advance the active source once, racing a ``cancel`` control frame.

        Returns None when the stream continues (event sent — the caller
        waits for the next credit), ``"done"`` when a terminal frame ended
        the stream normally, or a channel-close reason.
        """
        nonlocal seq, active_id, source
        assert source is not None
        read_task: asyncio.Task[Any] = asyncio.create_task(source.__anext__())
        deadline = loop.time() + idle_timeout

        async def _abandon_read() -> None:
            """Drop a still-pending source read and retrieve its outcome.

            Every exit before the read completes lands here so a blocked
            external source can never leak a task or keep the generator
            open. Retrieving the task avoids "exception never retrieved"
            noise when the source had already failed.
            """
            if not read_task.done():
                read_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await read_task

        try:
            while True:
                if read_task.done():
                    break
                remaining = deadline - loop.time()
                if remaining <= 0:
                    await _abandon_read()
                    await _close_source()
                    active_id = None
                    with suppress(_ChildGone, _Malformed):
                        await _send(
                            _error_frame(stream_id, 504, "local stream idle timeout")
                        )
                    return "done"
                try:
                    ready = await _poll_ready(min(_POLL_SLICE_SECONDS, remaining))
                except OSError as e:
                    raise _ChildGone(f"child channel poll failed: {e}") from e
                if read_task.done():
                    break
                if not ready:
                    continue
                # A control frame arrived while the source was blocked.
                # Only ``cancel`` for this stream is legal here; anything
                # else is an order violation. The poll consumed nothing, so
                # this read cannot steal a frame.
                raw = await _recv_raw_tail()
                try:
                    ctrl = decode_frame(raw)
                except Exception as e:
                    raise _Malformed(f"unparseable stream frame: {e}") from e
                if (
                    ctrl.get("v") == TRANSPORT_VERSION
                    and ctrl.get("id") == stream_id
                    and ctrl.get("type") == "cancel"
                ):
                    read_task.cancel()
                    with suppress(asyncio.CancelledError, Exception):
                        await read_task
                    await _close_source()
                    active_id = None
                    await _send(
                        {"v": TRANSPORT_VERSION, "id": stream_id, "type": "cancelled"}
                    )
                    return "done"
                raise _Malformed(
                    f"expected stream cancel, got {ctrl.get('type')!r}"
                )
        except _ChildGone:
            await _abandon_read()
            await _close_source()
            return "eof"
        except _FrameOversized:
            await _abandon_read()
            await _close_source()
            raise
        except _Malformed:
            await _abandon_read()
            await _close_source()
            raise
        except asyncio.CancelledError:
            await _abandon_read()
            await _close_source()
            raise
        try:
            event = await read_task
        except StopAsyncIteration:
            # The source returned (after any post-``done`` work): only now
            # send the outer terminal, distinct from a business done event.
            await _close_source()
            active_id = None
            try:
                await _send({"v": TRANSPORT_VERSION, "id": stream_id, "type": "end"})
            except _ChildGone:
                return "child-gone"
            return "done"
        except StreamSourceError as e:
            await _close_source()
            active_id = None
            try:
                await _send(_error_frame(stream_id, e.status_code, e.detail))
            except _ChildGone:
                return "child-gone"
            return "done"
        except asyncio.CancelledError:
            await _abandon_read()
            await _close_source()
            raise
        except Exception as e:  # noqa: BLE001 - streams must end in one terminal error
            logger.exception("local stream source failed")
            await _close_source()
            active_id = None
            try:
                await _send(
                    _error_frame(
                        stream_id, 500, f"local stream failed: {type(e).__name__}"
                    )
                )
            except _ChildGone:
                return "child-gone"
            return "done"
        if not isinstance(event, dict):
            logger.error("local stream source yielded a non-object event; ending stream")
            await _close_source()
            active_id = None
            try:
                await _send(_error_frame(stream_id, 500, "local stream failed"))
            except _ChildGone:
                return "child-gone"
            return "done"
        try:
            await _send_event(stream_id, seq, event)
        except (_ChildGone, _Malformed):
            await _close_source()
            active_id = None
            raise
        seq += 1
        return None

    try:
        while True:
            if active_id is None:
                try:
                    frame = await _read_open(await _recv_frame(None))
                except _FrameOversized:
                    logger.warning("local stream frame exceeded byte bound; closing")
                    await _close_source()
                    return "oversized"
                except (_Malformed, _IdleTimeout) as e:
                    logger.warning("local stream open rejected: %s", e)
                    await _close_source()
                    return "malformed"
                except _ChildGone:
                    await _close_source()
                    return "eof"
                try:
                    stream_id, op, params = _validate_open(frame)
                except _Malformed:
                    # A stale credit for a just-rejected open is benign:
                    # its terminal was already delivered, so ignore it. Any
                    # other out-of-order frame closes the channel.
                    if (
                        frame.get("v") == TRANSPORT_VERSION
                        and isinstance(frame.get("id"), str)
                        and frame.get("type") in ("pull", "cancel")
                        and frame["id"] in rejected
                    ):
                        rejected.pop(frame["id"], None)
                        continue
                    logger.warning("local stream open rejected: %s", frame)
                    await _close_source()
                    return "malformed"
                factory = ops.get(op)
                if factory is None:
                    with suppress(_ChildGone, _Malformed):
                        await _send(
                            _error_frame(
                                frame.get("id")
                                if isinstance(frame.get("id"), str)
                                else None,
                                404,
                                f"local stream operation not allowed: {op!r}",
                            )
                        )
                    _remember_rejected(frame["id"])
                    continue
                try:
                    source = factory(params, principal)
                except Exception as e:  # noqa: BLE001 - factory failure is one terminal error
                    logger.exception("local stream source factory failed")
                    with suppress(_ChildGone, _Malformed):
                        await _send(
                            _error_frame(
                                frame["id"], 500, f"local stream failed: {type(e).__name__}"
                            )
                        )
                    _remember_rejected(frame["id"])
                    continue
                active_id = stream_id
                seq = 0
                continue

            assert source is not None
            try:
                ctrl = await _recv_frame(loop.time() + idle_timeout)
            except _IdleTimeout:
                timed_out_id = active_id
                await _close_source()
                active_id = None
                with suppress(_ChildGone, _Malformed):
                    await _send(_error_frame(timed_out_id, 504, "local stream idle timeout"))
                continue
            except _FrameOversized:
                logger.warning("local stream frame exceeded byte bound; closing")
                await _close_source()
                return "oversized"
            except _Malformed as e:
                logger.warning("local stream frame rejected: %s", e)
                await _close_source()
                return "malformed"
            except _ChildGone:
                await _close_source()
                return "eof"
            if ctrl.get("v") != TRANSPORT_VERSION or ctrl.get("id") != active_id:
                logger.warning("local stream frame version/id mismatch; closing")
                await _close_source()
                return "malformed"
            kind = ctrl.get("type")
            if kind == "cancel":
                await _close_source()
                active_id = None
                try:
                    await _send(
                        {"v": TRANSPORT_VERSION, "id": ctrl["id"], "type": "cancelled"}
                    )
                except (_ChildGone, _Malformed):
                    return "child-gone"
                continue
            if kind != "pull":
                logger.warning("local stream expected pull, got %r; closing", kind)
                await _close_source()
                return "malformed"
            try:
                outcome = await _advance_once(active_id)
            except _FrameOversized:
                logger.warning("local stream frame exceeded byte bound; closing")
                await _close_source()
                return "oversized"
            except _Malformed:
                await _close_source()
                return "malformed"
            except _ChildGone:
                await _close_source()
                return "eof"
            if outcome is not None and outcome != "done":
                await _close_source()
                return outcome
    except asyncio.CancelledError:
        await _close_source()
        raise
    finally:
        await _close_source()
        for conn in (recv_conn, send_conn):
            with suppress(OSError, ValueError):
                conn.close()
