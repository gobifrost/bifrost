"""
Child-side endpoint of the engine-local synchronous import transport.

This module is intentionally stdlib-only at import time: forked execution
children must never import PostgreSQL drivers, the ORM, or the API server
stack in order to resolve a cold import.

Protocol (engine SDK fast path: ``modules.resolve`` / ``modules.fetch``):

- One request frame, one-or-many response frames, JSON over
  ``multiprocessing.Connection.send_bytes`` / ``recv_bytes`` on a dedicated
  child<->parent channel pair. This pair is separate from the async SDK
  operation channel: an import may block the child's event loop while an
  async SDK request is awaiting a response, so sharing that channel's lock
  could deadlock. The parent serves both channels concurrently.
- Small requests carry ``{"v": 1, "id": <uuid>, "op": <name>, ...}``.
  ``modules.resolve`` sends only the logical ``name``;
  ``modules.fetch`` sends only the candidate storage ``path``. No request
  carries Solution identity, actor, or caller identity: the parent derives
  the source scope exclusively from its own dispatch context.
- Small responses carry the same ``id`` with either
  ``{"ok": true, "result": ...}`` or
  ``{"ok": false, "status": <http-status>, "detail": <str>}``. A
  ``modules.fetch`` 404 error frame means "try the next candidate"; any
  other failure raises loudly and never falls back to HTTP/S3.
- Large results in the parent->child direction use bounded chunked
  transfer: a header frame ``{"ok": true, "chunked": true,
  "total": <bytes>, "parts": <n>}`` followed by exactly ``n`` part frames
  ``{"id": ..., "part": <i>, "data": <base64>}`` in order. Every frame is
  bounded by ``MAX_FRAME_BYTES``; there is deliberately no total cap (the
  HTTP path has none). Backpressure comes from the pipe itself: each side
  sends sequentially and blocks while the peer is slow. Requests are tiny
  (a name or a path) and always fit in one frame.
- The transport is safe for concurrent callers (user-created threads may
  import): one ``threading.Lock`` serializes whole round trips so frames
  can never interleave. Blocking pipe IO runs on the calling thread —
  the import system itself is synchronous.
- A timeout, EOF, or protocol violation breaks the channel instead of
  risking a desynchronized stream — and it NEVER falls back to HTTP/S3:
  a local attempt that fails raises loudly.

Selection is explicit: the engine child entrypoint
(``template_process._run_forked_child``) installs the transport before user
code runs. There is no user-controlled flag — outside an engine child no
transport is installed and module fetches use HTTP/S3 exactly as before.
"""

from __future__ import annotations

import base64
import binascii
import json
import threading
import time
import uuid
from typing import Any, NoReturn

# Operation allowlist: cold module-name resolution and candidate source
# fetch. The parent enforces the same allowlist; anything else is a 404
# response.
OP_MODULES_RESOLVE = "modules.resolve"
OP_MODULES_FETCH = "modules.fetch"

# Wire version. The parent rejects anything else instead of guessing.
TRANSPORT_VERSION = 1

# Largest single frame (request or response) either side will accept, in
# bytes. Receivers pass ``MAX_FRAME_BYTES + 1`` as the ``recv_bytes``
# maxlength so an oversized peer cannot force unbounded allocation.
MAX_FRAME_BYTES = 64 * 1024

# Bound on one local import round trip (request plus single or chunked
# response). Module resolution is an indexed read plus a bounded storage
# probe; a stall means the parent is gone or wedged, so fail loudly
# rather than hang the import. Slightly above the parent's 25 s dispatch
# deadline so a slow-but-live parent can still answer with an error frame.
DEFAULT_IMPORT_TIMEOUT_SECONDS = 30.0

# Raw result bytes per chunk part. Base64 expands 4/3, so one part encodes
# to 65024 chars; with the part-frame envelope every emitted frame stays
# under MAX_FRAME_BYTES (same part-size math as the async SDK channel).
_CHUNK_RAW_BYTES = 48768


class ImportTransportError(RuntimeError):
    """A local module import could not complete."""


class ImportTransportProtocolError(ImportTransportError):
    """The parent sent a frame that violates the import wire protocol."""


class ImportTransportClosed(ImportTransportError):
    """The parent side closed the channel (shutdown, crash, or recycle)."""


class ImportTransportTimeout(ImportTransportError, TimeoutError):
    """The parent did not answer before the import deadline."""


class ImportServiceError(RuntimeError):
    """The parent returned a valid HTTP-style service error response."""


class ImportNotFound(ImportServiceError):
    """One candidate storage path is absent (parent 404).

    The caller advances to the next candidate; this is normal control
    flow, not a transport failure. Must be caught before
    ``ImportServiceError``.
    """


def encode_frame(message: dict[str, Any]) -> bytes:
    """Serialize one frame, enforcing the byte bound before sending."""
    payload = json.dumps(message, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_FRAME_BYTES:
        raise ImportTransportProtocolError(
            f"local import frame is {len(payload)} bytes "
            f"(limit {MAX_FRAME_BYTES}); refusing to send"
        )
    return payload


def decode_frame(raw: bytes) -> dict[str, Any]:
    """Parse one received frame into a dict."""
    try:
        message = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise ImportTransportProtocolError(f"malformed local import frame: {e}") from e
    if not isinstance(message, dict):
        raise ImportTransportProtocolError("malformed local import frame: not an object")
    return message


class ChildSyncImportTransport:
    """One engine child's endpoint of the local import transport.

    Thread-safety: serialized with a ``threading.Lock``; at most one frame
    pair is in flight no matter which thread imports. Blocking pipe IO runs
    on the calling thread — the import system is synchronous, so there is
    no event loop to keep responsive here.
    """

    def __init__(self, send_conn: Any, recv_conn: Any) -> None:
        self._send = send_conn
        self._recv = recv_conn
        self._lock = threading.Lock()
        self._seq = 0
        self._broken: ImportTransportError | None = None

    def _break(self, error: ImportTransportError) -> None:
        """Mark the channel broken and release its descriptors, without raising.

        A late parent response must never be readable by a later call, so
        any early exit (timeout, EOF, protocol violation) lands here.
        """
        if self._broken is None:
            self._broken = error
        for conn in (self._send, self._recv):
            try:
                conn.close()
            except Exception:
                pass

    def _fail(self, error: ImportTransportError) -> NoReturn:
        self._break(error)
        assert self._broken is not None
        raise self._broken

    def _recv_frame(self, deadline: float) -> dict[str, Any]:
        """Read one bounded frame, waiting at most until ``deadline``.

        ``multiprocessing.Connection.poll`` bounds the wait; ``recv_bytes``
        bounds the allocation. EOF/OSError/protocol violations break the
        channel via the caller.
        """
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ImportTransportTimeout("local import timed out waiting for parent")
        try:
            ready = self._recv.poll(remaining)
        except OSError as e:
            raise ImportTransportClosed(
                f"local import channel failed while waiting: {e}"
            ) from e
        if not ready:
            raise ImportTransportTimeout("local import timed out waiting for parent")
        try:
            raw = self._recv.recv_bytes(MAX_FRAME_BYTES + 1)
        except EOFError as e:
            raise ImportTransportClosed(
                "local import channel closed by parent"
            ) from e
        except OSError as e:
            raise ImportTransportClosed(
                f"local import channel failed while receiving: {e}"
            ) from e
        if len(raw) > MAX_FRAME_BYTES:
            raise ImportTransportProtocolError(
                "local import response exceeded the frame bound"
            )
        try:
            return decode_frame(raw)
        except ImportTransportError as e:
            raise ImportTransportProtocolError(str(e)) from e

    @staticmethod
    def _validate_response_common(frame: dict[str, Any], request_id: str) -> None:
        if frame.get("v") != TRANSPORT_VERSION:
            raise ImportTransportProtocolError("local import response version mismatch")
        if frame.get("id") != request_id:
            raise ImportTransportProtocolError("local import response id mismatch")
        if not isinstance(frame.get("ok"), bool):
            raise ImportTransportProtocolError("local import response has invalid ok field")
        if "chunked" in frame and not isinstance(frame["chunked"], bool):
            raise ImportTransportProtocolError(
                "local import response has invalid chunked field"
            )

    def _check_id(self, frame: dict[str, Any], request_id: str) -> None:
        self._validate_response_common(frame, request_id)

    def _reassemble(
        self, request_id: str, header: dict[str, Any], deadline: float
    ) -> Any:
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
            raise ImportTransportProtocolError("invalid chunk header")
        buf = bytearray()
        for i in range(parts):
            frame = self._recv_frame(deadline)
            if (
                frame.get("v") != TRANSPORT_VERSION
                or frame.get("id") != request_id
                or frame.get("part") != i
                or isinstance(frame.get("part"), bool)
            ):
                raise ImportTransportProtocolError("chunk stream desynchronized")
            data = frame.get("data")
            if not isinstance(data, str):
                raise ImportTransportProtocolError("invalid chunk encoding")
            try:
                chunk = base64.b64decode(data.encode("ascii"), validate=True)
            except (ValueError, UnicodeEncodeError, binascii.Error) as e:
                raise ImportTransportProtocolError(f"invalid chunk payload: {e}") from e
            buf.extend(chunk)
            if len(buf) > total:
                raise ImportTransportProtocolError("chunk stream exceeded declared total")
        if len(buf) != total:
            raise ImportTransportProtocolError("chunk stream length mismatch")
        try:
            return json.loads(bytes(buf).decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as e:
            raise ImportTransportProtocolError(f"invalid chunked result: {e}") from e

    def _interpret(self, response: dict[str, Any], op: str) -> Any:
        """Map an error response to its exception, else return the result.

        Valid error responses raise without breaking the channel (the next
        call starts clean). A failure without a usable error status is a
        parent protocol violation and breaks the channel.
        """
        if not response["ok"]:
            status = response.get("status")
            detail = response.get("detail")
            if (
                not isinstance(status, int)
                or isinstance(status, bool)
                or not 400 <= status <= 599
                or not isinstance(detail, str)
            ):
                raise ImportTransportProtocolError("invalid local import service error")
            if status == 404:
                raise ImportNotFound(f"local {op} missed: {detail}")
            raise ImportServiceError(f"local {op} failed ({status}): {detail}")
        if "result" not in response:
            raise ImportTransportProtocolError("local import response has no result")
        return response["result"]

    def _roundtrip(self, request: dict[str, Any], deadline: float) -> Any:
        request_id = request["id"]
        op = request["op"]
        # Import requests are a logical name or a storage path: always far
        # below the frame bound. Refuse to chunk rather than grow a second
        # request-chunking protocol.
        self._send.send_bytes(encode_frame(request))
        first = self._recv_frame(deadline)
        self._check_id(first, request_id)
        if first.get("chunked"):
            if not first["ok"]:
                raise ImportTransportProtocolError(
                    "local import service error cannot be chunked"
                )
            return self._reassemble(request_id, first, deadline)
        return self._interpret(first, op)

    def _call(
        self,
        op: str,
        fields: dict[str, Any],
        timeout: float,
    ) -> Any:
        """One local import operation with no HTTP/S3 fallback.

        Serialized with the other calls on this channel; a timeout, EOF,
        or protocol violation breaks the channel instead of risking a
        desynchronized stream — and it NEVER falls back to HTTP/S3.
        """
        with self._lock:
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
            deadline = time.monotonic() + timeout
            try:
                return self._roundtrip(request, deadline)
            except ImportServiceError:
                raise
            except ImportTransportTimeout:
                self._fail(
                    ImportTransportTimeout(
                        f"local {op} timed out after {timeout}s "
                        "(parent did not respond; no HTTP/S3 fallback)"
                    )
                )
            except ImportTransportError as e:
                self._fail(e)
            except (EOFError, OSError) as e:
                self._fail(ImportTransportClosed(f"local import channel failed: {e}"))

    def call_modules_resolve(
        self,
        name: str,
        timeout: float = DEFAULT_IMPORT_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        """Resolve one logical import name through the parent.

        Returns the shared resolver dict (module/package/namespace/
        not_found). No HTTP/S3 fallback. Parent error responses raise
        ``ImportServiceError``; transport loss raises ``ImportTransportError``.
        """
        result = self._call(OP_MODULES_RESOLVE, {"name": name}, timeout)
        if not isinstance(result, dict):
            self._fail(
                ImportTransportProtocolError(
                    "malformed local import result"
                )
            )
        return result

    def call_modules_fetch(
        self,
        path: str,
        timeout: float = DEFAULT_IMPORT_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        """Fetch one candidate storage path's source through the parent.

        Returns the shared source dict. A 404 raises ``ImportNotFound``
        (the caller advances to the next candidate) without breaking the
        channel. Any other failure raises loudly with no HTTP/S3 fallback.
        """
        result = self._call(OP_MODULES_FETCH, {"path": path}, timeout)
        if not isinstance(result, dict):
            self._fail(
                ImportTransportProtocolError(
                    "malformed local import result"
                )
            )
        return result


_installed: ChildSyncImportTransport | None = None
_install_lock = threading.Lock()


def install(send_conn: Any, recv_conn: Any) -> ChildSyncImportTransport:
    """Install the engine-selected import transport for this child process.

    Called once by the engine child entrypoint before user code runs —
    never from user code and never based on a user-controlled flag.
    """
    global _installed
    transport = ChildSyncImportTransport(send_conn, recv_conn)
    with _install_lock:
        _installed = transport
    return transport


def clear() -> None:
    """Remove the installed transport (engine teardown)."""
    global _installed
    with _install_lock:
        _installed = None


def get() -> ChildSyncImportTransport | None:
    """Return the installed transport, or None outside an engine child."""
    with _install_lock:
        return _installed
