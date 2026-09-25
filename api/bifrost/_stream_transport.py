"""
Child-side endpoint of the engine-local streaming transport.

This module is intentionally stdlib-only at import time: forked execution
children must never import PostgreSQL drivers, the ORM, or the API server
stack. (The HTTP-style error mapping imports ``httpx``/``bifrost.client``
lazily, inside the raising function, long after the child runtime is loaded.)

Protocol (generic named-operation streams; the parent registers the
production ``ai.stream`` operation):

- One stream at a time per child over a dedicated child<->parent channel
  pair, separate from the unary SDK pipes and the synchronous import pipes.
  JSON over ``multiprocessing.Connection.send_bytes`` / ``recv_bytes``.
- The child opens a stream with
  ``{"v": 1, "id": <correlation>, "type": "open", "op": <name>,
  "params": {...}}``. ``op`` names an allowlisted operation the parent
  registry knows; the parent derives identity/scope from its own dispatch
  context, never from child claims. Large opens (AI input files) ride
  bounded ``open_chunked``/``open_chunk`` frames instead of one frame.
- Flow control is explicit one-event credit: every ``__anext__`` sends
  ``{"v": 1, "id": ..., "type": "pull"}`` and the parent advances its
  source at most once per credit, answering with exactly one frame.
  Pipe backpressure applies to every send in both directions.
- The parent answers a credit with one ordered event frame
  ``{"v": 1, "id": ..., "type": "event", "seq": <n>, "event": {...}}``
  (large payloads use a chunked header plus ordered ``"type": "chunk"``
  part frames, same 64 KiB invariant as the unary/import transports),
  or with a terminal frame. The terminal frame is always outer metadata —
  ``{"v": 1, "id": ..., "type": "end"}`` on success or
  ``{"v": 1, "id": ..., "type": "error", "status": <http-status>,
  "detail": <str>}`` on failure — never a business payload, so a business
  ``done`` event is yielded as data and the stream only closes on the
  later outer terminal. A provider error therefore maps to one terminal
  error without dropping already delivered events.
- Early close and task cancellation send
  ``{"v": 1, "id": ..., "type": "cancel"}`` from any task, even while
  another task is parked in ``__anext__``. The parent stops/closes its
  source and answers ``{"v": 1, "id": ..., "type": "cancelled"}``; the
  child then permits a later stream on the same channel. If the graceful
  cancel/ack fails, the child fails closed and breaks only this channel —
  never switching to HTTP.
- Every frame is bounded by ``MAX_FRAME_BYTES``; anything larger is
  rejected without unbounded allocation. This module never uses pickle.
- The whole stream — open plus per-event credits — runs under one
  deadline: a timeout, EOF, or protocol violation breaks only this
  channel and raises loudly. Cancellation re-raises ``CancelledError``
  to its own caller unchanged after sending the cancel control frame.

Selection is explicit: the engine child entrypoint
(``template_process._run_forked_child``) installs the transport before user
code runs. There is no user-controlled flag — outside an engine child no
transport is installed.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import threading
import uuid
from typing import Any, NoReturn

# Wire version. The parent rejects anything else instead of guessing.
TRANSPORT_VERSION = 1

# Largest single frame either side will accept, in bytes. Receivers pass
# ``MAX_FRAME_BYTES + 1`` as the ``recv_bytes`` maxlength so an oversized
# peer cannot force unbounded allocation. Same invariant as the unary and
# import transports.
MAX_FRAME_BYTES = 64 * 1024

# Bound on one stream operation (open plus per-event credits). Covers setup
# (the parent's stream-setup work) and idle periods between credits with
# room for the parent's own idle deadline to answer with an error frame
# first.
DEFAULT_STREAM_TIMEOUT_SECONDS = 35.0

# Bound on waiting for the parent's ``cancelled`` acknowledgement after the
# child sends ``cancel``. A missing ack breaks only this channel.
CANCEL_ACK_TIMEOUT_SECONDS = 5.0

# Raw event bytes per chunk part. Base64 expands 4/3, so one part encodes
# to 65024 chars; with the part-frame envelope every emitted frame stays
# under MAX_FRAME_BYTES (same part-size math as the other transports).
_CHUNK_RAW_BYTES = 48768


# Named stream operation for SDK AI streaming. The child opens it with the
# already-composed ``CLIAICompleteRequest`` fields as params (large
# ``input_files`` ride the bounded ``open_chunked``/``open_chunk`` frames);
# the parent validates the same DTO as the HTTP handler and runs the shared
# ``shared.sdk_ai.stream_sdk_ai`` generator for them.
OP_AI_STREAM = "ai.stream"


class StreamTransportError(RuntimeError):
    """A local stream operation could not complete."""


class StreamTransportClosed(StreamTransportError):
    """The parent side closed the stream channel (shutdown, crash, recycle)."""


class StreamTransportTimeout(StreamTransportError, TimeoutError):
    """The parent did not answer before the stream deadline."""


class StreamServiceError(RuntimeError):
    """The parent returned a valid HTTP-style terminal stream error."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        detail: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail if detail is not None else message


def encode_frame(message: dict[str, Any]) -> bytes:
    """Serialize one frame, enforcing the byte bound before sending."""
    payload = json.dumps(message, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_FRAME_BYTES:
        raise StreamTransportError(
            f"local stream frame is {len(payload)} bytes "
            f"(limit {MAX_FRAME_BYTES}); refusing to send"
        )
    return payload


def decode_frame(raw: bytes) -> dict[str, Any]:
    """Parse one received frame into a dict."""
    try:
        message = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise StreamTransportError(f"malformed local stream frame: {e}") from e
    if not isinstance(message, dict):
        raise StreamTransportError("malformed local stream frame: not an object")
    return message


def raise_for_stream_status(status: int, detail: str, op: str = "stream") -> NoReturn:
    """Raise the same public exception the HTTP path raises for a status.

    Builds a synthetic ``httpx.Response`` and reuses the SDK's
    ``raise_for_status_with_detail`` mapping, so a terminal 401 is
    ``BifrostAuthenticationError`` etc. — identical to the HTTP behavior.
    No HTTP request is made; the URL is a ``local://`` marker.

    Only meaningful for 4xx/5xx statuses; callers must not invoke it
    otherwise.
    """
    import httpx

    from .client import raise_for_status_with_detail

    request = httpx.Request("POST", f"local://sdk/stream/{op.replace('.', '/')}")
    response = httpx.Response(
        status,
        json={"detail": detail},
        request=request,
    )
    raise_for_status_with_detail(response)
    raise StreamTransportError(f"unmapped local stream status {status}")  # pragma: no cover


class ChildStream:
    """One open stream: async-iterates event payloads, then terminates.

    Exactly one ``ChildStream`` is active per transport. Events arrive
    incrementally — each ``__anext__`` sends one ``pull`` credit and yields
    the next event as it arrives rather than accumulating the response.
    A business ``{"done": True}`` payload is yielded as data; the stream
    ends only on the outer ``end`` terminal (``StopAsyncIteration``) or a
    terminal ``error`` (raised, mapped like the HTTP path).
    """

    def __init__(
        self,
        transport: ChildStreamTransport,
        stream_id: str,
        op: str,
        timeout: float,
    ) -> None:
        self._transport = transport
        self._id = stream_id
        self._op = op
        self._timeout = timeout
        self._seq = 0
        self._closed = False
        self._cancelling = False

    @property
    def stream_id(self) -> str:
        """Correlation id of this stream (matches every parent frame)."""
        return self._id

    @property
    def closed(self) -> bool:
        """True once the stream reached a terminal frame or was closed."""
        return self._closed

    def __aiter__(self) -> ChildStream:
        return self

    async def __anext__(self) -> dict[str, Any]:
        transport = self._transport
        if transport._broken is not None:
            raise transport._broken
        # The reader token serializes this credit against a concurrent
        # ``aclose()``: exactly one side consumes the next parent frame, so
        # a cancel acknowledgement can never be split across two readers.
        async with transport._recv_lock:
            if self._closed or transport._active is not self:
                raise StopAsyncIteration
            async with transport._send_lock:
                try:
                    await asyncio.to_thread(
                        transport._send.send_bytes,
                        encode_frame(
                            {"v": TRANSPORT_VERSION, "id": self._id, "type": "pull"}
                        ),
                    )
                except (EOFError, OSError) as e:
                    transport._fail(
                        StreamTransportClosed(
                            f"local stream channel closed by parent: {e}"
                        )
                    )
            try:
                frame = await self._recv_one()
                return await self._handle_frame(frame)
            except asyncio.CancelledError:
                if transport._broken is not None:
                    raise
                # Task cancellation must still tell the parent to stop its
                # source, then re-raise so workflow/service cancellation
                # keeps working. The reader token is already held, so the
                # acknowledgement read below cannot race ``aclose()``. A
                # clean ack leaves the channel reusable for a later stream;
                # a failed ack breaks only this channel.
                already_cancelling = self._cancelling
                self._cancelling = True
                try:
                    if not already_cancelling:
                        await asyncio.shield(self._send_cancel())
                    await asyncio.shield(self._await_ack())
                except StreamTransportError as e:
                    transport._fail(e)
                except asyncio.CancelledError:
                    transport._break(
                        StreamTransportClosed(
                            f"local stream {self._op} cancelled during cancel; "
                            "channel closed"
                        )
                    )
                raise

    async def _read_raw(self, timeout: float) -> bytes:
        """Wait without leaving a cancelled pipe reader to steal the next frame."""
        transport = self._transport
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise asyncio.TimeoutError
            if await asyncio.to_thread(transport._recv.poll, min(remaining, 0.1)):
                break
        read = asyncio.create_task(
            asyncio.to_thread(transport._recv.recv_bytes, MAX_FRAME_BYTES + 1)
        )
        try:
            return await asyncio.wait_for(asyncio.shield(read), deadline - loop.time())
        except asyncio.CancelledError:
            # Cancellation after poll but during recv cannot safely hand the
            # connection to another reader. Close this channel; the unary and
            # import channels remain independent.
            transport._break(
                StreamTransportClosed("local stream read cancelled; channel closed")
            )
            raise
        except asyncio.TimeoutError:
            transport._break(
                StreamTransportTimeout("local stream read timed out; channel closed")
            )
            raise

    async def _recv_one(self) -> dict[str, Any]:
        """Read one bounded parent frame under the stream deadline.

        The caller must hold the reader token (``_recv_lock``), so no two
        tasks consume parent frames concurrently.
        """
        transport = self._transport
        try:
            raw = await self._read_raw(self._timeout)
        except asyncio.TimeoutError:
            transport._fail(
                StreamTransportTimeout(
                    f"local stream {self._op} timed out after "
                    f"{self._timeout}s (parent did not respond)"
                )
            )
        except (EOFError, OSError) as e:
            transport._fail(
                StreamTransportClosed(f"local stream channel closed by parent: {e}")
            )
        if len(raw) > MAX_FRAME_BYTES:
            transport._fail(
                StreamTransportError(
                    "local stream response exceeded the frame bound; "
                    "channel closed"
                )
            )
        try:
            return decode_frame(raw)
        except StreamTransportError as e:
            transport._fail(e)
        raise AssertionError("unreachable: _fail always raises")

    def _check_envelope(self, frame: dict[str, Any]) -> None:
        if frame.get("v") != TRANSPORT_VERSION or frame.get("id") != self._id:
            self._transport._fail(
                StreamTransportError("local stream frame version/id mismatch; channel closed")
            )

    async def _reassemble(self, header: dict[str, Any]) -> dict[str, Any]:
        """Rebuild one chunked event payload, validating order and size."""
        total = header.get("total")
        parts = header.get("parts")
        if (
            not isinstance(total, int)
            or isinstance(total, bool)
            or total <= 0
            or not isinstance(parts, int)
            or isinstance(parts, bool)
            or parts < 2
            or parts != -(-total // _CHUNK_RAW_BYTES)
        ):
            self._transport._fail(
                StreamTransportError("invalid stream chunk header; channel closed")
            )
        buf = bytearray()
        for i in range(parts):
            frame = await self._recv_one()
            self._check_envelope(frame)
            if frame.get("type") != "chunk" or frame.get("seq") != self._seq:
                self._transport._fail(
                    StreamTransportError("stream chunk desynchronized; channel closed")
                )
            part = frame.get("part")
            if not isinstance(part, int) or isinstance(part, bool) or part != i:
                self._transport._fail(
                    StreamTransportError("stream chunk desynchronized; channel closed")
                )
            data = frame.get("data")
            if not isinstance(data, str):
                self._transport._fail(
                    StreamTransportError("invalid stream chunk encoding; channel closed")
                )
            try:
                chunk = base64.b64decode(data.encode("ascii"), validate=True)
            except (ValueError, UnicodeEncodeError, binascii.Error) as e:
                self._transport._fail(
                    StreamTransportError(f"invalid stream chunk payload: {e}; channel closed")
                )
            buf.extend(chunk)
            if len(buf) > total:
                self._transport._fail(
                    StreamTransportError(
                        "stream chunk exceeded declared total; channel closed"
                    )
                )
        if len(buf) != total:
            self._transport._fail(
                StreamTransportError("stream chunk length mismatch; channel closed")
            )
        try:
            event = json.loads(bytes(buf).decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as e:
            self._transport._fail(
                StreamTransportError(f"invalid chunked stream event: {e}; channel closed")
            )
        if not isinstance(event, dict):
            self._transport._fail(
                StreamTransportError("malformed stream event; channel closed")
            )
        return event

    async def _handle_frame(self, frame: dict[str, Any]) -> dict[str, Any]:
        """Map one parent frame to an event, termination, or failure."""
        transport = self._transport
        self._check_envelope(frame)
        kind = frame.get("type")
        if kind == "event":
            seq = frame.get("seq")
            if not isinstance(seq, int) or isinstance(seq, bool) or seq != self._seq:
                transport._fail(
                    StreamTransportError("local stream event out of order; channel closed")
                )
            if frame.get("chunked"):
                event = await self._reassemble(frame)
            else:
                event = frame.get("event")
                if not isinstance(event, dict):
                    transport._fail(
                        StreamTransportError("malformed stream event; channel closed")
                    )
            self._seq += 1
            return event
        if kind == "end":
            self._finish()
            raise StopAsyncIteration
        if kind == "error":
            self._finish()
            try:
                status = int(frame.get("status", 500))
            except (TypeError, ValueError):
                status = 500
            detail = frame.get("detail", "local stream failed")
            if not isinstance(detail, str):
                detail = str(detail)
            if 400 <= status <= 599:
                raise_for_stream_status(status, detail, self._op)
            transport._fail(
                StreamTransportError(
                    f"invalid local stream error status {status!r}; channel closed"
                )
            )
        if kind == "cancelled":
            # A concurrent ``aclose()`` (or a cancelled ``__anext__``) owns
            # termination; this credit simply ends.
            self._finish()
            raise StopAsyncIteration
        error = StreamTransportError(
            f"unexpected local stream frame {kind!r}; channel closed"
        )
        transport._break(error)
        raise error

    async def _send_cancel(self) -> None:
        transport = self._transport
        async with transport._send_lock:
            try:
                await asyncio.to_thread(
                    transport._send.send_bytes,
                    encode_frame(
                        {"v": TRANSPORT_VERSION, "id": self._id, "type": "cancel"}
                    ),
                )
            except (EOFError, OSError) as e:
                raise StreamTransportClosed(
                    f"local stream channel closed during cancel: {e}"
                ) from e

    async def _await_ack(self) -> None:
        """Consume the ``cancelled`` acknowledgement for this stream.

        The caller must hold the reader token (``_recv_lock``): the parked
        ``__anext__`` (if any) finishes first and this side then observes
        the already-closed stream instead of sending a redundant cancel.
        """
        try:
            raw = await self._read_raw(CANCEL_ACK_TIMEOUT_SECONDS)
        except asyncio.TimeoutError as e:
            raise StreamTransportClosed(
                "local stream cancel acknowledgement timed out; channel closed"
            ) from e
        except (EOFError, OSError) as e:
            raise StreamTransportClosed(
                f"local stream channel closed during cancel: {e}"
            ) from e
        if len(raw) > MAX_FRAME_BYTES:
            raise StreamTransportError(
                "local stream response exceeded the frame bound; channel closed"
            )
        frame = decode_frame(raw)
        if (
            frame.get("v") != TRANSPORT_VERSION
            or frame.get("id") != self._id
            or frame.get("type") != "cancelled"
        ):
            raise StreamTransportError(
                "local stream cancel acknowledgement mismatch; channel closed"
            )
        self._finish()

    def _finish(self) -> None:
        self._closed = True
        if self._transport._active is self:
            self._transport._active = None

    async def aclose(self) -> None:
        """Cancel this stream early and wait for the parent's acknowledgement.

        Stops the parent's source and leaves the channel reusable for a
        later stream. If the graceful cancel/ack fails, breaks only this
        channel (never HTTP). Concurrent use with a parked ``__anext__``
        is safe: whichever side consumes the ``cancelled`` frame finishes
        the stream and the other side observes the closed state.
        """
        transport = self._transport
        if transport._broken is not None:
            raise transport._broken
        if self._closed or transport._active is not self:
            return
        if not self._cancelling:
            self._cancelling = True
            await self._send_cancel()
        # Whoever holds the reader token consumes the acknowledgement: a
        # parked ``__anext__`` finishes first and this side then observes
        # the already-closed stream, so no redundant cancel is ever sent.
        async with transport._recv_lock:
            if self._closed or transport._active is not self:
                return
            try:
                await self._await_ack()
            except StreamTransportError as e:
                transport._fail(e)


class ChildStreamTransport:
    """One engine child's endpoint of the local streaming transport.

    Thread-safety: ``asyncio``-safe via separate send/recv locks; at most
    one stream is active no matter which task opens it. Blocking pipe IO
    runs in ``asyncio.to_thread`` so the workflow event loop stays
    responsive.
    """

    def __init__(self, send_conn: Any, recv_conn: Any) -> None:
        self._send = send_conn
        self._recv = recv_conn
        self._send_lock = asyncio.Lock()
        self._recv_lock = asyncio.Lock()
        self._seq = 0
        self._active: ChildStream | None = None
        self._broken: StreamTransportError | None = None

    def _break(self, error: StreamTransportError) -> None:
        """Mark the channel broken and release its descriptors, without raising.

        A late parent frame must never be readable by a later stream, so any
        early exit (timeout, EOF, protocol violation, failed cancel) lands
        here. Only this channel breaks — the unary SDK and import channels
        are independent descriptors.
        """
        if self._broken is None:
            self._broken = error
        self._active = None
        for conn in (self._send, self._recv):
            try:
                conn.close()
            except Exception:
                continue

    def _fail(self, error: StreamTransportError) -> NoReturn:
        self._break(error)
        if isinstance(self._broken, StreamTransportError):
            raise self._broken
        raise error

    async def open_stream_async(
        self,
        op: str,
        params: dict[str, Any] | None = None,
        timeout: float = DEFAULT_STREAM_TIMEOUT_SECONDS,
    ) -> ChildStream:
        """Open one stream for an allowlisted operation. No HTTP fallback.

        At most one stream is active per child: opening while another is
        active raises. The parent validates ``op`` against its registry and
        answers unknown operations with a terminal error the first
        ``__anext__`` raises — the channel stays usable for a later
        stream.
        """
        if self._broken is not None:
            raise self._broken
        if self._active is not None:
            raise StreamTransportError(
                "a local stream is already active on this channel"
            )
        if not isinstance(op, str) or not op:
            raise StreamTransportError("stream operation must be a non-empty name")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            raise StreamTransportError("stream params must be an object")
        self._seq += 1
        stream_id = f"{self._seq}-{uuid.uuid4().hex}"
        stream = ChildStream(self, stream_id, op, timeout)
        request = {
            "v": TRANSPORT_VERSION,
            "id": stream_id,
            "type": "open",
            "op": op,
            "params": params,
        }
        raw = json.dumps(request, separators=(",", ":")).encode("utf-8")
        self._active = stream
        try:
            async with self._send_lock:
                if len(raw) <= MAX_FRAME_BYTES:
                    await asyncio.to_thread(self._send.send_bytes, raw)
                else:
                    parts = -(-len(raw) // _CHUNK_RAW_BYTES)
                    await asyncio.to_thread(
                        self._send.send_bytes,
                        encode_frame(
                            {
                                "v": TRANSPORT_VERSION,
                                "id": stream_id,
                                "type": "open_chunked",
                                "total": len(raw),
                                "parts": parts,
                            }
                        ),
                    )
                    for i in range(parts):
                        chunk = raw[i * _CHUNK_RAW_BYTES : (i + 1) * _CHUNK_RAW_BYTES]
                        await asyncio.to_thread(
                            self._send.send_bytes,
                            encode_frame(
                                {
                                    "v": TRANSPORT_VERSION,
                                    "id": stream_id,
                                    "type": "open_chunk",
                                    "part": i,
                                    "data": base64.b64encode(chunk).decode("ascii"),
                                }
                            ),
                        )
        except asyncio.CancelledError:
            self._break(StreamTransportClosed("local stream open cancelled; channel closed"))
            raise
        except (EOFError, OSError, StreamTransportError) as e:
            self._fail(StreamTransportClosed(f"local stream channel closed during open: {e}"))
        return stream


_installed: ChildStreamTransport | None = None
_install_lock = threading.Lock()


def install(send_conn: Any, recv_conn: Any) -> ChildStreamTransport:
    """Install the engine-selected stream transport for this child process.

    Called once by the engine child entrypoint before user code runs —
    never from user code and never based on a user-controlled flag.
    """
    global _installed
    transport = ChildStreamTransport(send_conn, recv_conn)
    with _install_lock:
        _installed = transport
    return transport


def clear() -> None:
    """Remove the installed transport (engine teardown)."""
    global _installed
    with _install_lock:
        _installed = None


def get() -> ChildStreamTransport | None:
    """Return the installed transport, or None outside an engine child."""
    with _install_lock:
        return _installed
