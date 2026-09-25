"""
Child-side endpoint of the engine-local SDK operation transport.

This module is intentionally stdlib-only at import time: forked execution
children must never import PostgreSQL drivers, the ORM, or the API server
stack. (The synthetic HTTP error mapping imports ``httpx``/``bifrost.client``
lazily, inside the raising function, long after the child runtime is loaded.)

Protocol (stage 2a: ``config.get/set/list/delete``):

- One request frame (or a bounded chunked request), one-or-many response
  frames, JSON over ``multiprocessing.Connection.send_bytes`` /
  ``recv_bytes`` on a dedicated child<->parent channel pair. These
  channels are separate from the work/result pipes.
- Small requests carry ``{"v": 1, "id": <uuid>, "op": <name>, ...}``.
  ``config.get`` sends ``key``/``scope``; ``config.set`` sends
  ``key``/``value``/``is_secret``/``scope``; ``config.list`` sends
  ``scope``; ``config.delete`` sends ``key``/``scope``. Small responses
  carry the same ``id`` with either ``{"ok": true, "result": ...}`` or
  ``{"ok": false, "status": <http-status>, "detail": <str>}``. ``set``
  returns no body (``result`` null, like HTTP 204); ``list`` returns a
  dict; ``delete`` returns a bool.
- Large payloads in EITHER direction use bounded chunked transfer: a
  header frame ``{"ok": true, "chunked": true, "total": <bytes>,
  "parts": <n>}`` (requests: ``{"op": ..., "chunked": true, "total",
  "parts"}``) followed by exactly ``n`` part frames
  ``{"id": ..., "part": <i>, "data": <base64>}`` in order. Every frame —
  request, response, header, part — is bounded by ``MAX_FRAME_BYTES``;
  there is deliberately no total cap (the HTTP path has none, so a new one
  would break parity — the reassembled size equals what HTTP
  materializes). Backpressure comes from the pipe itself: each side
  sends sequentially and blocks while the peer is slow.
- Frames are bounded: anything over ``MAX_FRAME_BYTES`` is rejected by the
  receiver without unbounded allocation. This module never uses pickle.
- The child serializes calls with an ``asyncio.Lock`` (one in-flight
  request), so concurrent ``asyncio.gather`` callers are safe by queuing.
  The whole round trip — send plus single or chunked receive — runs under
  one deadline. A timeout, EOF, or protocol violation breaks the channel
  instead of risking a desynchronized stream — and it NEVER falls back to
  HTTP: a local attempt that fails raises loudly. Cancellation also
  desynchronizes (breaks) the channel but re-raises ``CancelledError`` to
  the caller unchanged, so workflow/service cancellation keeps working.

Selection is explicit: the engine child entrypoint
(``template_process._run_forked_child``) installs the transport before user
code runs. There is no user-controlled flag — outside an engine child no
transport is installed and the SDK uses HTTP exactly as before.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import threading
import uuid
from typing import Any, NoReturn

# Operation allowlist (stage 2a): the config facade rides the local transport.
# The parent enforces the same allowlist; anything else is a 404 response.
OP_CONFIG_GET = "config.get"
OP_CONFIG_SET = "config.set"
OP_CONFIG_LIST = "config.list"
OP_CONFIG_DELETE = "config.delete"

# Wire version. The parent rejects anything else instead of guessing.
TRANSPORT_VERSION = 1

# Largest single frame (request or response) either side will accept, in
# bytes. Receivers pass ``MAX_FRAME_BYTES + 1`` as the ``recv_bytes``
# maxlength so an oversized peer cannot force unbounded allocation.
MAX_FRAME_BYTES = 64 * 1024

# Bound on one local round trip (request plus single or chunked response).
# Config resolution is a single indexed read; a stall means the parent is
# gone or wedged, so fail loudly rather than hang the workflow.
DEFAULT_OP_TIMEOUT_SECONDS = 30.0

# Raw result bytes per chunk part. Base64 expands 4/3, so one part encodes
# to 65024 chars; with the part-frame envelope every emitted frame stays
# under MAX_FRAME_BYTES (verified programmatically on the parent side).
_CHUNK_RAW_BYTES = 48768


class LocalTransportError(RuntimeError):
    """A local SDK operation could not complete."""


class LocalTransportClosed(LocalTransportError):
    """The parent side closed the channel (shutdown, crash, or recycle)."""


def encode_frame(message: dict[str, Any]) -> bytes:
    """Serialize one frame, enforcing the byte bound before sending."""
    payload = json.dumps(message, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_FRAME_BYTES:
        raise LocalTransportError(
            f"local SDK frame is {len(payload)} bytes "
            f"(limit {MAX_FRAME_BYTES}); refusing to send"
        )
    return payload


def decode_frame(raw: bytes) -> dict[str, Any]:
    """Parse one received frame into a dict."""
    try:
        message = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise LocalTransportError(f"malformed local SDK frame: {e}") from e
    if not isinstance(message, dict):
        raise LocalTransportError("malformed local SDK frame: not an object")
    return message


def raise_for_local_status(status: int, detail: str, op: str = OP_CONFIG_GET) -> None:
    """Raise the same public exception the HTTP path raises for a status.

    Builds a synthetic ``httpx.Response`` and reuses the SDK's
    ``raise_for_status_with_detail`` mapping, so local 403s are
    ``BifrostAuthorizationError`` etc. — identical to the HTTP behavior.
    No HTTP request is made; the URL is a ``local://`` marker.

    Only meaningful for 4xx/5xx statuses; callers must not invoke it
    otherwise (a ``2xx`` response returns normally, like its HTTP twin).
    """
    import httpx

    from .client import raise_for_status_with_detail

    request = httpx.Request("POST", f"local://sdk/{op.replace('.', '/')}")
    response = httpx.Response(
        status,
        json={"detail": detail},
        request=request,
    )
    raise_for_status_with_detail(response)


class ChildLocalTransport:
    """One engine child's endpoint of the local SDK transport.

    Thread-safety: ``asyncio``-safe via an internal lock; at most one frame
    pair is in flight. Blocking pipe IO runs in ``asyncio.to_thread`` so the
    workflow event loop stays responsive.
    """

    def __init__(self, send_conn: Any, recv_conn: Any) -> None:
        self._send = send_conn
        self._recv = recv_conn
        self._lock = asyncio.Lock()
        self._seq = 0
        self._broken: LocalTransportError | None = None

    def _break(self, error: LocalTransportError) -> None:
        """Mark the channel broken and release its descriptors, without raising.

        A late parent response must never be readable by a later call, so any
        early exit (timeout, cancellation, EOF, protocol violation) lands
        here. Cancellation uses this so it can re-raise ``CancelledError``
        to its own caller while still desynchronizing the channel.
        """
        if self._broken is None:
            self._broken = error
        for conn in (self._send, self._recv):
            try:
                conn.close()
            except Exception:
                pass

    def _fail(self, error: LocalTransportError) -> NoReturn:
        self._break(error)
        assert self._broken is not None
        raise self._broken

    async def _recv_frame(self) -> dict[str, Any]:
        """Read one bounded frame. EOF/OSError propagate to the caller."""
        raw = await asyncio.to_thread(self._recv.recv_bytes, MAX_FRAME_BYTES + 1)
        try:
            return decode_frame(raw)
        except LocalTransportError as e:
            self._fail(e)

    def _check_id(self, frame: dict[str, Any], request_id: str) -> None:
        if frame.get("id") != request_id:
            self._fail(
                LocalTransportError(
                    "local SDK response id mismatch; channel closed"
                )
            )

    async def _reassemble(
        self, request_id: str, header: dict[str, Any]
    ) -> dict[str, Any] | bool | None:
        """Read exactly the announced parts and rebuild the result object.

        The header's ``total``/``parts`` claim is checked for internal
        consistency before reading a single part; any deviation — wrong
        order, wrong id, bad encoding, length mismatch — breaks the
        channel. A stalling parent is covered by the call-level deadline.
        """
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
            self._fail(
                LocalTransportError(
                    "invalid chunk header; channel closed"
                )
            )
        buf = bytearray()
        for i in range(parts):
            frame = await self._recv_frame()
            if frame.get("id") != request_id or frame.get("part") != i:
                self._fail(
                    LocalTransportError(
                        "chunk stream desynchronized; channel closed"
                    )
                )
            data = frame.get("data")
            if not isinstance(data, str):
                self._fail(
                    LocalTransportError(
                        "invalid chunk encoding; channel closed"
                    )
                )
            try:
                chunk = base64.b64decode(data.encode("ascii"), validate=True)
            except (ValueError, UnicodeEncodeError, binascii.Error) as e:
                self._fail(
                    LocalTransportError(f"invalid chunk payload: {e}; channel closed")
                )
            buf.extend(chunk)
            if len(buf) > total:
                self._fail(
                    LocalTransportError(
                        "chunk stream exceeded declared total; channel closed"
                    )
                )
        if len(buf) != total:
            self._fail(
                LocalTransportError(
                    "chunk stream length mismatch; channel closed"
                )
            )
        try:
            result = json.loads(bytes(buf).decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as e:
            self._fail(
                LocalTransportError(f"invalid chunked result: {e}; channel closed")
            )
        if result is not None and not isinstance(result, (dict, bool)):
            self._fail(
                LocalTransportError(
                    "malformed local SDK result; channel closed"
                )
            )
        return result

    def _interpret(
        self, response: dict[str, Any], op: str
    ) -> dict[str, Any] | bool | None:
        """Map an error response to the public exception, else the result."""
        if not response.get("ok", False):
            try:
                status = int(response.get("status", 500))
            except (TypeError, ValueError):
                status = 500
            detail = response.get("detail", "local SDK request failed")
            if not isinstance(detail, str):
                detail = str(detail)
            if 400 <= status <= 599:
                raise_for_local_status(status, detail, op)
            # A failure without an error status is a parent protocol
            # violation: never treat it as a usable result.
            self._fail(
                LocalTransportError(
                    f"invalid local error status {status!r}; channel closed"
                )
            )
        result = response.get("result")
        if result is not None and not isinstance(result, (dict, bool)):
            self._fail(
                LocalTransportError(
                    "malformed local SDK result; channel closed"
                )
            )
        return result

    async def _send_request(self, request: dict[str, Any]) -> None:
        """Send one request, chunked when it exceeds the frame bound.

        Large ``config.set`` JSON values ride bounded part frames sent
        sequentially (pipe backpressure); there is no total request cap,
        matching HTTP. Every emitted frame stays within ``MAX_FRAME_BYTES``
        by the same part-size math the parent's response chunking uses.
        """
        raw = json.dumps(request, separators=(",", ":")).encode("utf-8")
        if len(raw) <= MAX_FRAME_BYTES:
            await asyncio.to_thread(self._send.send_bytes, raw)
            return
        total = len(raw)
        parts = -(-total // _CHUNK_RAW_BYTES)
        await asyncio.to_thread(
            self._send.send_bytes,
            encode_frame(
                {
                    "v": TRANSPORT_VERSION,
                    "id": request["id"],
                    "op": request["op"],
                    "chunked": True,
                    "total": total,
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
                        "id": request["id"],
                        "part": i,
                        "data": base64.b64encode(chunk).decode("ascii"),
                    }
                ),
            )

    async def _roundtrip(
        self, request: dict[str, Any]
    ) -> dict[str, Any] | bool | None:
        request_id = request["id"]
        op = request["op"]
        await self._send_request(request)
        first = await self._recv_frame()
        self._check_id(first, request_id)
        if first.get("chunked"):
            if not first.get("ok", False):
                return self._interpret(first, op)
            return await self._reassemble(request_id, first)
        return self._interpret(first, op)

    async def _call(
        self,
        op: str,
        fields: dict[str, Any],
        timeout: float,
    ) -> dict[str, Any] | bool | None:
        """One local operation with no HTTP fallback (shared by all ops).

        Serialized with the other calls on this channel; a timeout, EOF,
        or protocol violation breaks the channel instead of risking a
        desynchronized stream — and it NEVER falls back to HTTP.
        Cancellation breaks the channel but re-raises ``CancelledError``
        unchanged, so workflow/service cancellation keeps working.
        """
        async with self._lock:
            if self._broken is not None:
                raise self._broken
            self._seq += 1
            request_id = f"{self._seq}-{uuid.uuid4().hex}"
            request = {
                "v": TRANSPORT_VERSION,
                "id": request_id,
                "op": op,
                **fields,
            }
            try:
                return await asyncio.wait_for(
                    self._roundtrip(request), timeout
                )
            except asyncio.TimeoutError:
                self._fail(
                    TimeoutError(
                        f"local {op} timed out after {timeout}s "
                        "(parent did not respond; no HTTP fallback)"
                    )
                )
            except asyncio.CancelledError:
                self._break(
                    LocalTransportClosed(
                        f"local {op} cancelled; channel desynchronized "
                        "and closed"
                    )
                )
                raise
            except (EOFError, OSError) as e:
                if "bad message length" in str(e):
                    self._fail(
                        LocalTransportError(
                            "local SDK response exceeded the frame bound; "
                            "channel closed"
                        )
                    )
                else:
                    self._fail(
                        LocalTransportClosed(
                            f"local SDK channel closed by parent: {e}"
                        )
                    )

    async def call_config_get(
        self,
        key: str,
        scope: str | None,
        timeout: float = DEFAULT_OP_TIMEOUT_SECONDS,
    ) -> dict[str, Any] | None:
        """Resolve one config value through the parent. No HTTP fallback.

        Returns the ``{"key", "value", "config_type"}`` dict, or None when
        the key is not set. Parent error responses raise the same public
        exceptions as the HTTP path; transport loss raises
        ``LocalTransportClosed`` (synthetic 503) or ``TimeoutError``.
        Cancellation breaks the channel but re-raises ``CancelledError``
        unchanged so workflow/service cancellation keeps working.
        """
        result = await self._call(
            OP_CONFIG_GET, {"key": key, "scope": scope}, timeout
        )
        if result is not None and not isinstance(result, dict):
            self._fail(
                LocalTransportError(
                    "malformed local SDK result; channel closed"
                )
            )
        return result

    async def call_config_set(
        self,
        key: str,
        value: Any,
        is_secret: bool,
        scope: str | None,
        timeout: float = DEFAULT_OP_TIMEOUT_SECONDS,
    ) -> None:
        """Store one config value through the parent. No HTTP fallback.

        Large JSON values are sent as bounded chunked request frames with
        sequential pipe backpressure (no total cap, like HTTP). A non-null
        result is a parent protocol violation; parent errors raise the
        same public exceptions as the HTTP path.
        """
        result = await self._call(
            OP_CONFIG_SET,
            {"key": key, "value": value, "is_secret": is_secret, "scope": scope},
            timeout,
        )
        if result is not None:
            self._fail(
                LocalTransportError(
                    "malformed local SDK result; channel closed"
                )
            )

    async def call_config_list(
        self,
        scope: str | None,
        timeout: float = DEFAULT_OP_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        """List config values through the parent. No HTTP fallback.

        Returns the redacted value dict (secrets surface as ``"[SECRET]"``,
        exactly like HTTP). Large listings arrive as bounded chunked
        response frames.
        """
        result = await self._call(
            OP_CONFIG_LIST, {"scope": scope}, timeout
        )
        if not isinstance(result, dict):
            self._fail(
                LocalTransportError(
                    "malformed local SDK result; channel closed"
                )
            )
        return result

    async def call_config_delete(
        self,
        key: str,
        scope: str | None,
        timeout: float = DEFAULT_OP_TIMEOUT_SECONDS,
    ) -> bool:
        """Delete one config value through the parent. No HTTP fallback.

        Returns True when a row was deleted, False for a missing key —
        identical to the HTTP path.
        """
        result = await self._call(
            OP_CONFIG_DELETE, {"key": key, "scope": scope}, timeout
        )
        if not isinstance(result, bool):
            self._fail(
                LocalTransportError(
                    "malformed local SDK result; channel closed"
                )
            )
        return result


_installed: ChildLocalTransport | None = None
_install_lock = threading.Lock()


def install(send_conn: Any, recv_conn: Any) -> ChildLocalTransport:
    """Install the engine-selected transport for this child process.

    Called once by the engine child entrypoint before user code runs —
    never from user code and never based on a user-controlled flag.
    """
    global _installed
    transport = ChildLocalTransport(send_conn, recv_conn)
    with _install_lock:
        _installed = transport
    return transport


def clear() -> None:
    """Remove the installed transport (engine teardown)."""
    global _installed
    with _install_lock:
        _installed = None


def get() -> ChildLocalTransport | None:
    """Return the installed transport, or None outside an engine child."""
    with _install_lock:
        return _installed
