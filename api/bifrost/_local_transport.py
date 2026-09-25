"""
Child-side endpoint of the engine-local SDK operation transport.

This module is intentionally stdlib-only at import time: forked execution
children must never import PostgreSQL drivers, the ORM, or the API server
stack. (The synthetic HTTP error mapping imports ``httpx``/``bifrost.client``
lazily, inside the raising function, long after the child runtime is loaded.)

Protocol (stage 3b: ``config.get/set/list/delete``, the full
``integrations`` facade — ``get/list_mappings/get_mapping/upsert_mapping/
delete_mapping/refresh_token`` — and the full ``tables`` facade —
``create/list/delete`` metadata plus ``insert/upsert/get/update/
delete_document/batch/batch_delete/query/count`` document operations):

- One request frame (or a bounded chunked request), one-or-many response
  frames, JSON over ``multiprocessing.Connection.send_bytes`` /
  ``recv_bytes`` on a dedicated child<->parent channel pair. These
  channels are separate from the work/result pipes.
- Small requests carry ``{"v": 1, "id": <uuid>, "op": <name>, ...}``.
  ``id`` is always the request correlation id — no operation uses it
  as a field name (document ids ride ``doc_id``).
  ``config.get`` sends ``key``/``scope``; ``config.set`` sends
  ``key``/``value``/``is_secret``/``scope``; ``config.list`` sends
  ``scope``; ``config.delete`` sends ``key``/``scope``.
  ``integrations.get`` sends ``name``/``scope``/``oauth_scope``;
  ``integrations.list_mappings`` sends ``name``/``scope``;
  ``integrations.get_mapping`` sends ``name``/``scope``/``entity_id``;
  ``integrations.upsert_mapping`` sends
  ``name``/``scope``/``entity_id``/``entity_name``/``config``;
  ``integrations.delete_mapping`` sends ``name``/``scope``;
  ``integrations.refresh_token`` sends ``connection_name``/``scope``
  (the SDK ``refresh()`` call passes no scope, so the parent resolves
  the caller's own scope).
  ``tables.get`` sends ``table``/``doc_id``/``scope``/``solution``;
  ``tables.query`` sends ``table``/``query``/``scope``/``solution``;
  ``tables.count`` sends ``table``/``scope``/``solution``.
  ``tables.create`` sends ``name``/``description``/``table_schema``/
  ``scope``; ``tables.list`` sends ``scope``; ``tables.delete`` sends
  ``table_id``. ``tables.insert``/``tables.upsert`` send
  ``table``/``doc_id``/``data``/``created_by``/``updated_by``/``scope``
  (insert additionally carries the per-call ``solution`` target — the
  other writes inherit the caller's own install, exactly like their
  HTTP query strings);
  ``tables.update`` sends ``table``/``doc_id``/``data``/``updated_by``/
  ``scope``; ``tables.delete_document`` sends ``table``/``doc_id``/
  ``scope``; ``tables.batch`` sends ``table``/``documents``/``upsert``/
  ``write_mode``/``return_documents``/``scope``; ``tables.batch_delete``
  sends ``table``/``ids``/``scope``.
  No request carries Solution identity beyond the per-call table target:
  the parent derives the caller's own install id from its own dispatch
  context, never from child frames.
  Small responses carry the same ``id`` with either
  ``{"ok": true, "result": ...}`` or
  ``{"ok": false, "status": <http-status>, "detail": <str>}``. ``set``
  returns no body (``result`` null, like HTTP 204); ``list`` returns a
  dict; ``delete`` returns a bool. ``integrations.get``/``get_mapping``
  return a response dict or null (missing); ``list_mappings`` returns a
  dict envelope (``{"items": [...]}``); ``upsert_mapping`` returns a
  mapping dict; ``delete_mapping`` returns a ``{"deleted": bool}``
  envelope; ``refresh_token`` returns an
  ``{"access_token", "expires_at"}`` dict. ``tables.get`` returns a
  document dict (missing rows and tables are 404 error frames, never
  null results); ``tables.query`` returns a ``DocumentListResponse``
  dict (a missing table is a 404 error frame); ``tables.count`` returns
  a ``{"count": n}`` envelope (the transport result contract does not
  carry bare integers). ``tables.create`` returns a table-info dict;
  ``tables.list`` returns an ``{"items": [...]}`` envelope (the result
  contract does not carry bare lists); ``tables.delete`` returns true
  (a missing table is a 404 error frame). ``tables.insert``/``upsert``/
  ``update`` return a document dict; a missing row or table on update
  is a 404 error frame (the facade maps it to ``None``).
  ``tables.delete_document`` returns true (a missing row or table is a
  404 error frame the facade maps to ``False``). ``tables.batch``
  returns the ``DocumentBatchCreateResponse`` dict; ``tables.batch_delete``
  returns the ``DocumentBatchDeleteResponse`` dict (a missing table is a
  404 error frame the facade maps to an empty result).
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

# Operation allowlist (stage 3b): the config facade, the full
# integrations facade, and the full tables facade ride the local
# transport. The parent enforces the same allowlist; anything else is a
# 404 response.
OP_CONFIG_GET = "config.get"
OP_CONFIG_SET = "config.set"
OP_CONFIG_LIST = "config.list"
OP_CONFIG_DELETE = "config.delete"
OP_INTEGRATIONS_GET = "integrations.get"
OP_INTEGRATIONS_LIST_MAPPINGS = "integrations.list_mappings"
OP_INTEGRATIONS_GET_MAPPING = "integrations.get_mapping"
OP_INTEGRATIONS_UPSERT_MAPPING = "integrations.upsert_mapping"
OP_INTEGRATIONS_DELETE_MAPPING = "integrations.delete_mapping"
OP_INTEGRATIONS_REFRESH_TOKEN = "integrations.refresh_token"
OP_TABLES_CREATE = "tables.create"
OP_TABLES_LIST = "tables.list"
OP_TABLES_DELETE = "tables.delete"
OP_TABLES_INSERT = "tables.insert"
OP_TABLES_UPSERT = "tables.upsert"
OP_TABLES_GET = "tables.get"
OP_TABLES_UPDATE = "tables.update"
OP_TABLES_DELETE_DOCUMENT = "tables.delete_document"
OP_TABLES_BATCH = "tables.batch"
OP_TABLES_BATCH_DELETE = "tables.batch_delete"
OP_TABLES_QUERY = "tables.query"
OP_TABLES_COUNT = "tables.count"

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

    async def call_integrations_get(
        self,
        name: str,
        scope: str | None,
        oauth_scope: str | None = None,
        timeout: float = DEFAULT_OP_TIMEOUT_SECONDS,
    ) -> dict[str, Any] | None:
        """Resolve one integration through the parent. No HTTP fallback.

        Returns the ``SDKIntegrationsGetResponse`` dict, or None when the
        integration is not set up. The Solution install id is NOT sent:
        the parent derives it from its own dispatch context, so a child
        can never forge another install's declared-connection 424.
        Parent error responses raise the same public exceptions as the
        HTTP path; transport loss raises ``LocalTransportClosed``
        (synthetic 503) or ``TimeoutError``.
        """
        result = await self._call(
            OP_INTEGRATIONS_GET,
            {"name": name, "scope": scope, "oauth_scope": oauth_scope},
            timeout,
        )
        if result is not None and not isinstance(result, dict):
            self._fail(
                LocalTransportError(
                    "malformed local SDK result; channel closed"
                )
            )
        return result

    async def call_integrations_list_mappings(
        self,
        name: str,
        scope: str | None,
        timeout: float = DEFAULT_OP_TIMEOUT_SECONDS,
    ) -> dict[str, Any] | None:
        """List integration mappings through the parent. No HTTP fallback.

        Returns the ``{"items": [...]}`` envelope, or None when the
        integration is not found — identical to the HTTP path. Large
        listings arrive as bounded chunked response frames.
        """
        result = await self._call(
            OP_INTEGRATIONS_LIST_MAPPINGS, {"name": name, "scope": scope}, timeout
        )
        if result is not None and not isinstance(result, dict):
            self._fail(
                LocalTransportError(
                    "malformed local SDK result; channel closed"
                )
            )
        return result

    async def call_integrations_get_mapping(
        self,
        name: str,
        scope: str | None,
        entity_id: str | None = None,
        timeout: float = DEFAULT_OP_TIMEOUT_SECONDS,
    ) -> dict[str, Any] | None:
        """Resolve one integration mapping through the parent. No HTTP fallback.

        Returns the mapping dict, or None when the mapping is not found —
        identical to the HTTP path.
        """
        result = await self._call(
            OP_INTEGRATIONS_GET_MAPPING,
            {"name": name, "scope": scope, "entity_id": entity_id},
            timeout,
        )
        if result is not None and not isinstance(result, dict):
            self._fail(
                LocalTransportError(
                    "malformed local SDK result; channel closed"
                )
            )
        return result

    async def call_integrations_upsert_mapping(
        self,
        name: str,
        scope: str | None,
        entity_id: str,
        entity_name: str | None = None,
        config: dict[str, Any] | None = None,
        timeout: float = DEFAULT_OP_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        """Create or update an integration mapping through the parent.

        No HTTP fallback. Returns the mapping dict, identical to the
        HTTP path. Parent error responses raise the same public
        exceptions as the HTTP path (the facade maps them to the
        ``RuntimeError`` the HTTP upsert raises); transport loss raises
        ``LocalTransportClosed`` (synthetic 503) or ``TimeoutError``.
        """
        result = await self._call(
            OP_INTEGRATIONS_UPSERT_MAPPING,
            {
                "name": name,
                "scope": scope,
                "entity_id": entity_id,
                "entity_name": entity_name,
                "config": config,
            },
            timeout,
        )
        if not isinstance(result, dict):
            self._fail(
                LocalTransportError(
                    "malformed local SDK result; channel closed"
                )
            )
        return result

    async def call_integrations_delete_mapping(
        self,
        name: str,
        scope: str | None,
        timeout: float = DEFAULT_OP_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        """Delete an integration mapping through the parent. No HTTP fallback.

        Returns the ``{"deleted": bool}`` envelope, identical to the
        HTTP path (False for a missing integration/scope/mapping).
        Parent error responses raise the same public exceptions as the
        HTTP path; transport loss raises ``LocalTransportClosed``
        (synthetic 503) or ``TimeoutError``.
        """
        result = await self._call(
            OP_INTEGRATIONS_DELETE_MAPPING, {"name": name, "scope": scope}, timeout
        )
        if not isinstance(result, dict):
            self._fail(
                LocalTransportError(
                    "malformed local SDK result; channel closed"
                )
            )
        return result

    async def call_integrations_refresh_token(
        self,
        connection_name: str,
        scope: str | None = None,
        timeout: float = 35.0,
    ) -> dict[str, Any]:
        """Refresh an OAuth token through the parent. No HTTP fallback.

        Returns the ``{"access_token", "expires_at"}`` dict, identical
        to the HTTP path. The parent resolves a missing scope to the
        caller's own org (the SDK ``refresh()`` call passes no scope).
        The caller registers the fresh token with the SDK secret
        scrubber, like the HTTP SDK facade does. Parent error responses
        raise the same public exceptions as the HTTP path (the facade
        maps them to the ``RuntimeError`` the HTTP refresh raises).
        The 35-second child deadline leaves room for the parent's
        30-second OAuth provider/commit deadline to return an error frame.
        """
        result = await self._call(
            OP_INTEGRATIONS_REFRESH_TOKEN,
            {"connection_name": connection_name, "scope": scope},
            timeout,
        )
        if not isinstance(result, dict):
            self._fail(
                LocalTransportError(
                    "malformed local SDK result; channel closed"
                )
            )
        return result

    async def call_tables_create(
        self,
        name: str,
        description: str | None,
        table_schema: dict[str, Any] | None,
        scope: str | None,
        timeout: float = DEFAULT_OP_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        """Create one table through the parent. No HTTP fallback.

        Returns the table-info dict. A duplicate name is a 409 error
        frame; a Solution execution context is a 404 error frame —
        identical to the HTTP path. The per-call ``app`` argument the
        facade accepts is not sent: the HTTP DTO has no such field, so
        the server ignores it there too.
        """
        result = await self._call(
            OP_TABLES_CREATE,
            {
                "name": name,
                "description": description,
                "table_schema": table_schema,
                "scope": scope,
            },
            timeout,
        )
        if not isinstance(result, dict):
            self._fail(
                LocalTransportError(
                    "malformed local SDK result; channel closed"
                )
            )
        return result

    async def call_tables_list(
        self,
        scope: str | None,
        timeout: float = DEFAULT_OP_TIMEOUT_SECONDS,
    ) -> list[dict[str, Any]]:
        """List tables through the parent. No HTTP fallback.

        Returns the table-info dicts (unwrapped from the ``{"items"}``
        envelope — the transport result contract does not carry bare
        lists), identical to the HTTP path.
        """
        result = await self._call(
            OP_TABLES_LIST, {"scope": scope}, timeout
        )
        if not isinstance(result, dict) or not isinstance(
            result.get("items"), list
        ):
            self._fail(
                LocalTransportError(
                    "malformed local SDK result; channel closed"
                )
            )
        return result["items"]

    async def call_tables_delete(
        self,
        table_id: str,
        timeout: float = DEFAULT_OP_TIMEOUT_SECONDS,
    ) -> bool:
        """Delete one table through the parent. No HTTP fallback.

        Returns True. A missing table is a 404 error frame (the facade
        raises, like the HTTP path); a Solution-managed table is a 409.
        """
        result = await self._call(
            OP_TABLES_DELETE, {"table_id": table_id}, timeout
        )
        if result is not True:
            self._fail(
                LocalTransportError(
                    "malformed local SDK result; channel closed"
                )
            )
        return True

    async def call_tables_insert(
        self,
        table: str,
        doc_id: str | None,
        data: dict[str, Any],
        created_by: str | None,
        updated_by: str | None,
        scope: str | None,
        solution: str | None = None,
        timeout: float = DEFAULT_OP_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        """Insert one document through the parent. No HTTP fallback.

        Returns the ``DocumentPublic`` dict. A missing table is a 404
        error frame (the facade auto-creates outside a Solution and
        retries once, exactly like the HTTP path). Large documents ride
        bounded chunked request frames.
        """
        result = await self._call(
            OP_TABLES_INSERT,
            {
                "table": table,
                "doc_id": doc_id,
                "data": data,
                "created_by": created_by,
                "updated_by": updated_by,
                "scope": scope,
                "solution": solution,
            },
            timeout,
        )
        if not isinstance(result, dict):
            self._fail(
                LocalTransportError(
                    "malformed local SDK result; channel closed"
                )
            )
        return result

    async def call_tables_upsert(
        self,
        table: str,
        doc_id: str,
        data: dict[str, Any],
        created_by: str | None,
        updated_by: str | None,
        scope: str | None,
        timeout: float = DEFAULT_OP_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        """Replace-upsert one document through the parent. No HTTP fallback.

        Returns the ``DocumentPublic`` dict. A missing table is a 404
        error frame (the facade auto-creates outside a Solution and
        retries once, exactly like the HTTP path). Large documents ride
        bounded chunked request frames.
        """
        result = await self._call(
            OP_TABLES_UPSERT,
            {
                "table": table,
                "doc_id": doc_id,
                "data": data,
                "created_by": created_by,
                "updated_by": updated_by,
                "scope": scope,
            },
            timeout,
        )
        if not isinstance(result, dict):
            self._fail(
                LocalTransportError(
                    "malformed local SDK result; channel closed"
                )
            )
        return result

    async def call_tables_update(
        self,
        table: str,
        doc_id: str,
        data: dict[str, Any],
        updated_by: str | None,
        scope: str | None,
        timeout: float = DEFAULT_OP_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        """Merge-update one document through the parent. No HTTP fallback.

        Returns the ``DocumentPublic`` dict. A missing table or row is a
        404 error frame (the facade maps it to ``None``) — never a null
        result. Large patches ride bounded chunked request frames.
        """
        result = await self._call(
            OP_TABLES_UPDATE,
            {
                "table": table,
                "doc_id": doc_id,
                "data": data,
                "updated_by": updated_by,
                "scope": scope,
            },
            timeout,
        )
        if not isinstance(result, dict):
            self._fail(
                LocalTransportError(
                    "malformed local SDK result; channel closed"
                )
            )
        return result

    async def call_tables_delete_document(
        self,
        table: str,
        doc_id: str,
        scope: str | None,
        timeout: float = DEFAULT_OP_TIMEOUT_SECONDS,
    ) -> bool:
        """Delete one document through the parent. No HTTP fallback.

        Returns True. A missing table or row is a 404 error frame (the
        facade maps it to ``False``).
        """
        result = await self._call(
            OP_TABLES_DELETE_DOCUMENT,
            {"table": table, "doc_id": doc_id, "scope": scope},
            timeout,
        )
        if result is not True:
            self._fail(
                LocalTransportError(
                    "malformed local SDK result; channel closed"
                )
            )
        return True

    async def call_tables_batch(
        self,
        table: str,
        documents: list[dict[str, Any]],
        upsert: bool,
        write_mode: str | None,
        return_documents: bool,
        scope: str | None,
        timeout: float = DEFAULT_OP_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        """Insert or upsert a batch through the parent. No HTTP fallback.

        ``upsert``/``write_mode`` select the same effective write mode as
        the HTTP ``DocumentBatchCreate`` DTO (plain insert, legacy merge
        upsert, privileged replace upsert). Returns the
        ``DocumentBatchCreateResponse`` dict. A missing table is a 404
        error frame (the facade auto-creates outside a Solution and
        retries once); a concurrent-write conflict is a 409 the facade
        retries boundedly. Large batches ride bounded chunked request
        frames and large results return chunked.
        """
        result = await self._call(
            OP_TABLES_BATCH,
            {
                "table": table,
                "documents": documents,
                "upsert": upsert,
                "write_mode": write_mode,
                "return_documents": return_documents,
                "scope": scope,
            },
            timeout,
        )
        if not isinstance(result, dict):
            self._fail(
                LocalTransportError(
                    "malformed local SDK result; channel closed"
                )
            )
        return result

    async def call_tables_batch_delete(
        self,
        table: str,
        doc_ids: list[str],
        scope: str | None,
        timeout: float = DEFAULT_OP_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        """Delete a batch of documents through the parent. No HTTP fallback.

        Returns the ``DocumentBatchDeleteResponse`` dict. A missing table
        is a 404 error frame (the facade maps it to an empty result).
        """
        result = await self._call(
            OP_TABLES_BATCH_DELETE,
            {"table": table, "ids": doc_ids, "scope": scope},
            timeout,
        )
        if not isinstance(result, dict):
            self._fail(
                LocalTransportError(
                    "malformed local SDK result; channel closed"
                )
            )
        return result

    async def call_tables_get(
        self,
        table: str,
        doc_id: str,
        scope: str | None,
        solution: str | None = None,
        timeout: float = DEFAULT_OP_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        """Fetch one document through the parent. No HTTP fallback.

        Returns the ``DocumentPublic`` dict. A missing table or row is a
        404 error frame (the facade maps it to ``None``) — never a null
        result. The ``solution`` target is parent-resolved inside the
        target org; the caller's own install identity stays parent-owned.
        Parent error responses raise the same public exceptions as the
        HTTP path; transport loss raises ``LocalTransportClosed``
        (synthetic 503) or ``TimeoutError``.
        """
        result = await self._call(
            OP_TABLES_GET,
            {"table": table, "doc_id": doc_id, "scope": scope,
             "solution": solution},
            timeout,
        )
        if not isinstance(result, dict):
            self._fail(
                LocalTransportError(
                    "malformed local SDK result; channel closed"
                )
            )
        return result

    async def call_tables_query(
        self,
        table: str,
        query: dict[str, Any],
        scope: str | None,
        solution: str | None = None,
        timeout: float = DEFAULT_OP_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        """Query documents through the parent. No HTTP fallback.

        ``query`` is the ``DocumentQuery`` payload (same DTO as the HTTP
        handler). Returns the ``DocumentListResponse`` dict. A missing
        table is a 404 error frame (the facade maps it to an empty
        ``DocumentList``). Large results arrive as bounded chunked
        response frames.
        """
        result = await self._call(
            OP_TABLES_QUERY,
            {"table": table, "query": query, "scope": scope,
             "solution": solution},
            timeout,
        )
        if not isinstance(result, dict):
            self._fail(
                LocalTransportError(
                    "malformed local SDK result; channel closed"
                )
            )
        return result

    async def call_tables_count(
        self,
        table: str,
        scope: str | None,
        solution: str | None = None,
        timeout: float = DEFAULT_OP_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        """Count documents through the parent (unfiltered). No HTTP fallback.

        Returns the ``{"count": n}`` envelope — the transport result
        contract does not carry bare integers. A missing table is a 404
        error frame (the facade maps it to zero). Filtered counts are not
        a local operation: the facade composes them through the public
        ``tables.query(limit=1)``, exactly like the HTTP path.
        """
        result = await self._call(
            OP_TABLES_COUNT,
            {"table": table, "scope": scope, "solution": solution},
            timeout,
        )
        if not isinstance(result, dict):
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
