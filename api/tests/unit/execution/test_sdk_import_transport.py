"""Engine SDK synchronous import local channel: child transport unit tests.

Exercises ``bifrost._import_transport`` over real pipe pairs against a
fake parent running in a thread:

- resolve/fetch round trips, 404 control flow, and error responses that
  keep the channel usable;
- bounded chunked reassembly of large sources;
- malformed/oversized frames and timeouts breaking the channel with no
  HTTP/S3 fallback;
- thread-safe concurrent callers (user-created threads may import);
- requests carry only ``name``/``path`` — never Solution, actor, or
  caller identity.
"""

from __future__ import annotations

import base64
import json
import multiprocessing
import threading
import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from bifrost._import_transport import (
    _CHUNK_RAW_BYTES,
    MAX_FRAME_BYTES,
    ChildSyncImportTransport,
    ImportNotFound,
    ImportServiceError,
    ImportTransportError,
    ImportTransportTimeout,
    clear as clear_transport,
    decode_frame,
    encode_frame,
    get as get_transport,
    install as install_transport,
)


def _pipes() -> tuple[Any, Any, Any, Any]:
    """(child_send, child_recv, parent_recv, parent_send) for one channel.

    ``multiprocessing.Pipe(duplex=False)`` returns ``(read_end,
    write_end)`` — the first connection reads, the second writes.
    """
    parent_recv, child_send = multiprocessing.Pipe(duplex=False)
    child_recv, parent_send = multiprocessing.Pipe(duplex=False)
    return child_send, child_recv, parent_recv, parent_send


def _raw_send(conn: Any, payload: bytes) -> None:
    conn.send_bytes(payload)


def _raw_recv(conn: Any) -> bytes:
    return conn.recv_bytes(MAX_FRAME_BYTES + 1)


class FakeParent(threading.Thread):
    """Serve one import channel from canned per-op handlers (daemon)."""

    def __init__(
        self,
        recv_conn: Any,
        send_conn: Any,
        handler: Any,
        *,
        on_request: Any | None = None,
    ) -> None:
        super().__init__(daemon=True)
        self._recv = recv_conn
        self._send = send_conn
        self._handler = handler
        self._on_request = on_request
        self.requests: list[dict[str, Any]] = []

    def run(self) -> None:
        while True:
            try:
                raw = _raw_recv(self._recv)
            except (EOFError, OSError):
                return
            try:
                request = decode_frame(raw)
            except ImportTransportError:
                return
            self.requests.append(request)
            if self._on_request is not None:
                self._on_request(request)
            try:
                for frame in self._handler(request):
                    _raw_send(self._send, encode_frame(frame))
            except (EOFError, OSError):
                return
            except ImportTransportError:
                return


def _single(result: Any) -> list[dict[str, Any]]:
    return [{"v": 1, "id": None, "ok": True, "result": result}]


def _error(status: int, detail: str) -> list[dict[str, Any]]:
    return [{"v": 1, "id": None, "ok": False, "status": status, "detail": detail}]


def _with_id(frames: list[dict[str, Any]], request: dict[str, Any]) -> list[dict[str, Any]]:
    for frame in frames:
        frame["id"] = request["id"]
    return frames


def _chunked_frames(request: dict[str, Any], result: Any) -> list[dict[str, Any]]:
    raw = json.dumps(result, separators=(",", ":")).encode("utf-8")
    total = len(raw)
    parts = -(-total // _CHUNK_RAW_BYTES)
    assert parts >= 2
    frames: list[dict[str, Any]] = [
        {
            "v": 1,
            "id": request["id"],
            "ok": True,
            "chunked": True,
            "total": total,
            "parts": parts,
        }
    ]
    for i in range(parts):
        chunk = raw[i * _CHUNK_RAW_BYTES : (i + 1) * _CHUNK_RAW_BYTES]
        frames.append(
            {
                "v": 1,
                "id": request["id"],
                "part": i,
                "data": base64.b64encode(chunk).decode("ascii"),
            }
        )
    return frames


@pytest.fixture()
def channel():
    child_send, child_recv, parent_recv, parent_send = _pipes()
    transport = ChildSyncImportTransport(child_send, child_recv)
    parents: list[FakeParent] = []

    def _serve(handler: Any, on_request: Any | None = None) -> FakeParent:
        parent = FakeParent(parent_recv, parent_send, handler, on_request=on_request)
        parent.start()
        parents.append(parent)
        return parent

    yield transport, _serve
    for conn in (child_send, child_recv, parent_recv, parent_send):
        try:
            conn.close()
        except Exception:
            pass


class TestFrameCodec:
    def test_encode_rejects_oversized(self):
        with pytest.raises(ImportTransportError, match="refusing to send"):
            encode_frame({"v": 1, "id": "x", "pad": "q" * (MAX_FRAME_BYTES + 1)})

    def test_decode_rejects_garbage(self):
        with pytest.raises(ImportTransportError, match="malformed"):
            decode_frame(b"\x00\x01not-json")

    def test_decode_rejects_non_object(self):
        with pytest.raises(ImportTransportError, match="not an object"):
            decode_frame(b"[1,2]")


class TestResolveFetch:
    def test_resolve_roundtrip(self, channel):
        transport, serve = channel
        expected = {
            "kind": "module",
            "path": "cold/dep.py",
            "storage_path": "cold/dep.py",
            "content": "VALUE = 1\n",
            "hash": "abc",
        }
        serve(lambda req: _with_id(_single(expected), req))
        assert transport.call_modules_resolve("cold.dep") == expected

    def test_fetch_404_is_import_not_found_and_channel_survives(self, channel):
        transport, serve = channel
        expected = {"content": "VALUE = 2\n", "path": "b.py", "hash": "h"}
        calls: list[str] = []

        def _handler(req: dict[str, Any]) -> list[dict[str, Any]]:
            calls.append(req["path"])
            if req["path"] == "missing.py":
                return _with_id(_error(404, "Module not found: missing.py"), req)
            return _with_id(_single(expected), req)

        serve(_handler)
        with pytest.raises(ImportNotFound):
            transport.call_modules_fetch("missing.py")
        # The 404 did not desynchronize the stream: the next call works.
        assert transport.call_modules_fetch("b.py") == expected
        assert calls == ["missing.py", "b.py"]

    def test_error_response_raises_without_breaking(self, channel):
        transport, serve = channel

        def _handler(req: dict[str, Any]) -> list[dict[str, Any]]:
            if req.get("name") == "broken.mod":
                return _with_id(_error(500, "module store unavailable"), req)
            return _with_id(
                _single({"kind": "not_found", "path": "other"}), req
            )

        serve(_handler)
        with pytest.raises(ImportServiceError, match="500"):
            transport.call_modules_resolve("broken.mod")
        assert transport.call_modules_resolve("other") == {
            "kind": "not_found",
            "path": "other",
        }

    def test_requests_carry_only_name_or_path(self, channel):
        transport, serve = channel
        seen: list[dict[str, Any]] = []
        serve(
            lambda req: _with_id(_single({"kind": "not_found", "path": "x"}), req),
            on_request=seen.append,
        )
        transport.call_modules_resolve("some.mod")
        transport.call_modules_fetch("some/mod.py")
        assert [sorted(r.keys()) for r in seen] == [
            ["id", "name", "op", "v"],
            ["id", "op", "path", "v"],
        ]

    def test_chunked_large_source(self, channel):
        transport, serve = channel
        big = {"content": "z" * 100_000, "path": "big.py", "hash": "h"}
        serve(lambda req: _chunked_frames(req, big))
        assert transport.call_modules_fetch("big.py") == big


class TestChannelFailure:
    def test_timeout_breaks_channel(self):
        child_send, child_recv, parent_recv, parent_send = _pipes()
        transport = ChildSyncImportTransport(child_send, child_recv)
        try:
            # No parent serves: the poll deadline expires.
            with pytest.raises(ImportTransportTimeout, match="no HTTP/S3 fallback") as exc:
                transport.call_modules_resolve("cold.dep", timeout=0.3)
            assert isinstance(exc.value, TimeoutError)
            # The channel is broken: the next call fails fast with the
            # stored cause instead of hanging.
            with pytest.raises(ImportTransportError):
                transport.call_modules_resolve("cold.dep", timeout=5.0)
        finally:
            for conn in (child_send, child_recv, parent_recv, parent_send):
                try:
                    conn.close()
                except Exception:
                    pass

    def test_parent_gone_breaks_channel(self):
        child_send, child_recv, parent_recv, parent_send = _pipes()
        parent_recv.close()
        parent_send.close()
        transport = ChildSyncImportTransport(child_send, child_recv)
        try:
            with pytest.raises(ImportTransportError):
                transport.call_modules_resolve("cold.dep", timeout=5.0)
            with pytest.raises(ImportTransportError):
                transport.call_modules_fetch("cold/dep.py", timeout=5.0)
        finally:
            for conn in (child_send, child_recv):
                try:
                    conn.close()
                except Exception:
                    pass


class TestConcurrentCallers:
    def test_threads_serialize_roundtrips(self, channel):
        transport, serve = channel

        def _handler(req: dict[str, Any]) -> list[dict[str, Any]]:
            time.sleep(0.001)
            return _with_id(
                _single({"kind": "module", "path": req["name"], "content": "x"}),
                req,
            )

        serve(_handler)
        errors: list[BaseException] = []
        results: list[dict[str, Any]] = []

        def _work(n: int) -> None:
            try:
                for i in range(10):
                    results.append(transport.call_modules_resolve(f"mod.{n}.{i}"))
            except BaseException as e:  # noqa: BLE001 - collected and asserted
                errors.append(e)

        threads = [threading.Thread(target=_work, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        assert not errors
        assert len(results) == 80


class TestInstallState:
    def test_install_get_clear(self):
        child_send, child_recv, parent_recv, parent_send = _pipes()
        try:
            assert get_transport() is None
            transport = install_transport(child_send, child_recv)
            assert get_transport() is transport
        finally:
            clear_transport()
            for conn in (child_send, child_recv, parent_recv, parent_send):
                try:
                    conn.close()
                except Exception:
                    pass
        assert get_transport() is None


class TestChannelFailureDirect:
    """Malformed/oversized parent frames break the channel (raw parents)."""

    def test_malformed_frame_breaks_channel(self):
        child_send, child_recv, parent_recv, parent_send = _pipes()
        transport = ChildSyncImportTransport(child_send, child_recv)
        try:
            parent_send.send_bytes(b"\x00\x01not-json")
            with pytest.raises(ImportTransportError, match="malformed"):
                transport.call_modules_resolve("cold.dep", timeout=5.0)
            with pytest.raises(ImportTransportError):
                transport.call_modules_resolve("cold.dep", timeout=5.0)
        finally:
            for conn in (child_send, child_recv, parent_recv, parent_send):
                try:
                    conn.close()
                except Exception:
                    pass

    def test_oversized_frame_breaks_channel(self):
        child_send, child_recv, parent_recv, parent_send = _pipes()
        transport = ChildSyncImportTransport(child_send, child_recv)
        outcome: dict[str, Any] = {}

        def _call() -> None:
            try:
                transport.call_modules_resolve("cold.dep", timeout=10.0)
            except BaseException as e:  # noqa: BLE001 - asserted below
                outcome["error"] = e

        try:
            caller = threading.Thread(target=_call)
            caller.start()
            # Wait for the child's request, then answer oversized. The raw
            # send fits because the child is already polling.
            raw = parent_recv.recv_bytes(MAX_FRAME_BYTES + 1)
            assert decode_frame(raw)["op"] == "modules.resolve"
            try:
                parent_send.send_bytes(b"x" * (MAX_FRAME_BYTES + 1))
            except BrokenPipeError:
                # Racy by nature: the child rejects the oversized length
                # prefix and closes the channel, which can land while the
                # remainder of this write is still in flight. The assertion
                # below is on the child's deterministic rejection.
                pass
            caller.join(timeout=15.0)
            assert not caller.is_alive()
            assert isinstance(outcome.get("error"), ImportTransportError)
            assert "frame bound" in str(outcome["error"])
        finally:
            for conn in (child_send, child_recv, parent_recv, parent_send):
                try:
                    conn.close()
                except Exception:
                    pass

    def test_id_mismatch_breaks_channel(self):
        child_send, child_recv, parent_recv, parent_send = _pipes()
        transport = ChildSyncImportTransport(child_send, child_recv)
        outcome: dict[str, Any] = {}

        def _call() -> None:
            try:
                transport.call_modules_resolve("cold.dep", timeout=10.0)
            except BaseException as e:  # noqa: BLE001 - asserted below
                outcome["error"] = e

        try:
            caller = threading.Thread(target=_call)
            caller.start()
            raw = parent_recv.recv_bytes(MAX_FRAME_BYTES + 1)
            request = decode_frame(raw)
            assert request["op"] == "modules.resolve"
            parent_send.send_bytes(
                encode_frame({"v": 1, "id": "wrong-id", "ok": True, "result": {}})
            )
            caller.join(timeout=15.0)
            assert not caller.is_alive()
            assert isinstance(outcome.get("error"), ImportTransportError)
            assert "id mismatch" in str(outcome["error"])
        finally:
            for conn in (child_send, child_recv, parent_recv, parent_send):
                try:
                    conn.close()
                except Exception:
                    pass

    def test_poll_oserror_breaks_channel_without_message_matching(self):
        send_conn = MagicMock()
        recv_conn = MagicMock()
        recv_conn.poll.side_effect = OSError("arbitrary platform failure")
        transport = ChildSyncImportTransport(send_conn, recv_conn)

        with pytest.raises(ImportTransportError):
            transport.call_modules_resolve("cold.dep", timeout=1.0)

        send_conn.close.assert_called_once()
        recv_conn.close.assert_called_once()
