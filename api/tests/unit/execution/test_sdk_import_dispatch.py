"""Engine-local import channel: parent dispatch and principal tests.

- ``principal_from_context`` derives the module-source scope
  (``solution_id`` + ``solution_global_repo_access``) from parent-owned
  context only, failing closed on malformed flags.
- ``modules.resolve``/``modules.fetch`` ride the shared ``sdk_modules``
  service: Solution-first resolution, sealed-Solution restrictions,
  candidate 404s, chunked large sources, and request validation.
- Child frames can never forge scope: extra frame fields are ignored.
- Pump lifecycle (EOF/malformed) and dual-channel concurrency: a held
  async SDK request never blocks a synchronous import.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import multiprocessing
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio

from bifrost._import_transport import MAX_FRAME_BYTES
from src.services.execution.sdk_local_dispatch import (
    IMPORT_CHANNEL_ALLOWED_OPS,
    SDK_CHANNEL_ALLOWED_OPS,
    LocalDispatchPrincipal,
    LocalPrincipalError,
    dispatch_frame,
    dispatch_frames,
    principal_from_context,
    serve_channel,
)


@contextlib.asynccontextmanager
async def _null_factory():
    yield None


@pytest_asyncio.fixture(autouse=True)
async def _fresh_shared_redis():
    """Rebind the shared async Redis singleton to each test's loop.

    pytest-asyncio runs every test on a fresh event loop, but
    ``src.core.redis_client``'s singleton persists across tests; reusing a
    connection minted on a previous test's loop raises "attached to a
    different loop". Dispose around each test (mirrors the global
    ``isolate_global_db_engine`` fixture) so every use mints connections
    on the current loop.
    """
    from src.core.redis_client import close_redis_client

    await close_redis_client()
    yield
    await close_redis_client()


def _principal(**overrides: Any) -> LocalDispatchPrincipal:
    base: dict[str, Any] = {"caller_org_id": None}
    base.update(overrides)
    return LocalDispatchPrincipal(**base)


def _resolve_frame(name: str, **extra: Any) -> dict[str, Any]:
    frame: dict[str, Any] = {
        "v": 1,
        "id": f"req-{uuid4().hex[:8]}",
        "op": "modules.resolve",
        "name": name,
    }
    frame.update(extra)
    return frame


def _fetch_frame(path: str, **extra: Any) -> dict[str, Any]:
    frame: dict[str, Any] = {
        "v": 1,
        "id": f"req-{uuid4().hex[:8]}",
        "op": "modules.fetch",
        "path": path,
    }
    frame.update(extra)
    return frame


class TestPrincipalScopeFlag:
    def test_missing_flag_defaults_to_sealed(self):
        assert principal_from_context({}).solution_global_repo_access is False

    def test_explicit_flag_passes_through(self):
        assert (
            principal_from_context(
                {"solution_global_repo_access": True}
            ).solution_global_repo_access
            is True
        )
        assert (
            principal_from_context(
                {"solution_global_repo_access": False}
            ).solution_global_repo_access
            is False
        )

    @pytest.mark.parametrize("bad", ["yes", 1, 0, None, ["x"], {"a": 1}])
    def test_malformed_flag_fails_closed(self, bad: Any):
        with pytest.raises(LocalPrincipalError, match="solution_global_repo_access"):
            principal_from_context({"solution_global_repo_access": bad})

    def test_flag_flows_to_service_principal(self):
        principal = principal_from_context(
            {
                "solution_global_repo_access": True,
                "service": {"service_id": str(uuid4()), "attempt_id": "att-1"},
            }
        )
        assert principal.is_service is True
        assert principal.solution_global_repo_access is True


@pytest.mark.asyncio
class TestDispatchValidation:
    async def test_missing_name_is_422(self):
        response = await dispatch_frame(
            lambda: _null_factory(), _principal(), _resolve_frame("")
        )
        assert response["ok"] is False
        assert response["status"] == 422

    async def test_non_string_name_is_422(self):
        frame = _resolve_frame("x")
        frame["name"] = {"nested": 1}
        response = await dispatch_frame(
            lambda: _null_factory(),
            _principal(),
            frame,
        )
        assert response["ok"] is False
        assert response["status"] == 422

    async def test_missing_path_is_422(self):
        response = await dispatch_frame(
            lambda: _null_factory(), _principal(), _fetch_frame("")
        )
        assert response["ok"] is False
        assert response["status"] == 422

    async def test_unknown_operation_rejected(self):
        frame = _resolve_frame("a.b")
        frame["op"] = "modules.drop"
        response = await dispatch_frame(
            lambda: _null_factory(), _principal(), frame
        )
        assert response["ok"] is False
        assert response["status"] == 404

    async def test_bad_version_rejected(self):
        frame = _resolve_frame("a.b")
        frame["v"] = 999
        response = await dispatch_frame(
            lambda: _null_factory(), _principal(), frame
        )
        assert response["ok"] is False
        assert response["status"] == 400


@pytest.mark.asyncio
class TestDispatchSharedService:
    """End-to-end parent dispatch against the test stack (Redis + S3)."""

    async def _seed_repo(self, relpath: str, content: str) -> None:
        from src.services.repo_storage import RepoStorage

        await RepoStorage().write(relpath, content.encode("utf-8"))

    async def _drop_repo(self, relpath: str) -> None:
        from src.services.repo_storage import RepoStorage

        with contextlib.suppress(Exception):
            await RepoStorage().delete(relpath)

    async def test_resolve_and_fetch_workspace_module(self):
        tag = uuid4().hex[:8]
        relpath = f"cold_imp_{tag}/dep.py"
        content = f"VALUE_{tag.upper()} = 41\n"
        await self._seed_repo(relpath, content)
        try:
            name = f"cold_imp_{tag}.dep"
            response = await dispatch_frame(
                lambda: _null_factory(), _principal(), _resolve_frame(name)
            )
            assert response["ok"] is True, response
            result = response["result"]
            assert result["kind"] == "module"
            assert result["content"] == content
            assert result["storage_path"] == relpath

            fetched = await dispatch_frame(
                lambda: _null_factory(), _principal(), _fetch_frame(relpath)
            )
            assert fetched["ok"] is True, fetched
            assert fetched["result"]["content"] == content
        finally:
            await self._drop_repo(relpath)

    async def test_resolve_miss_is_not_found(self):
        response = await dispatch_frame(
            lambda: _null_factory(),
            _principal(),
            _resolve_frame(f"definitely_missing_{uuid4().hex[:8]}"),
        )
        assert response["ok"] is True, response
        assert response["result"]["kind"] == "not_found"

    async def test_fetch_miss_is_404(self):
        response = await dispatch_frame(
            lambda: _null_factory(),
            _principal(),
            _fetch_frame(f"missing_{uuid4().hex[:8]}.py"),
        )
        assert response["ok"] is False
        assert response["status"] == 404

    async def test_resolve_invalid_name_is_400(self):
        response = await dispatch_frame(
            lambda: _null_factory(),
            _principal(),
            _resolve_frame("not a name!!"),
        )
        assert response["ok"] is False
        assert response["status"] == 400

    async def test_sealed_solution_denied_workspace_fetch(self):
        tag = uuid4().hex[:8]
        relpath = f"cold_sealed_{tag}.py"
        await self._seed_repo(relpath, "X = 1\n")
        try:
            principal = _principal(
                solution_id=uuid4(), solution_global_repo_access=False
            )
            # The frame forges a global-repo grant — the parent ignores it.
            response = await dispatch_frame(
                lambda: _null_factory(),
                principal,
                _fetch_frame(
                    relpath,
                    solution_id=str(uuid4()),
                    global_repo_access=True,
                    actor="attacker@x.local",
                ),
            )
            assert response["ok"] is False
            assert response["status"] == 403
        finally:
            await self._drop_repo(relpath)

    async def test_solution_scope_resolves_and_fetches(self):
        from src.services.solutions.storage import SolutionStorage

        solution_id = uuid4()
        tag = uuid4().hex[:8]
        rel = f"cold_sol_{tag}/dep.py"
        content = "SOLVED = True\n"
        await SolutionStorage(solution_id).write(rel, content.encode("utf-8"))
        try:
            principal = _principal(
                solution_id=solution_id, solution_global_repo_access=False
            )
            storage_path = f"_solutions/{solution_id}/{rel}"
            response = await dispatch_frame(
                lambda: _null_factory(),
                principal,
                _resolve_frame(f"cold_sol_{tag}.dep"),
            )
            assert response["ok"] is True, response
            assert response["result"]["kind"] == "module"
            assert response["result"]["storage_path"] == storage_path

            fetched = await dispatch_frame(
                lambda: _null_factory(), principal, _fetch_frame(storage_path)
            )
            assert fetched["ok"] is True, fetched
            assert fetched["result"]["content"] == content
        finally:
            with contextlib.suppress(Exception):
                await SolutionStorage(solution_id).delete(rel)

    async def test_global_fallback_allowed_when_granted(self):
        tag = uuid4().hex[:8]
        relpath = f"cold_fb_{tag}.py"
        await self._seed_repo(relpath, "Y = 2\n")
        try:
            principal = _principal(
                solution_id=uuid4(), solution_global_repo_access=True
            )
            response = await dispatch_frame(
                lambda: _null_factory(), principal, _fetch_frame(relpath)
            )
            assert response["ok"] is True, response
            assert response["result"]["content"] == "Y = 2\n"
        finally:
            await self._drop_repo(relpath)

    async def test_out_of_install_solution_path_denied(self):
        principal = _principal(
            solution_id=uuid4(), solution_global_repo_access=True
        )
        response = await dispatch_frame(
            lambda: _null_factory(),
            principal,
            _fetch_frame(f"_solutions/{uuid4()}/evil.py"),
        )
        assert response["ok"] is False
        assert response["status"] == 403

    async def test_large_source_arrives_chunked(self):
        from bifrost._local_transport import MAX_FRAME_BYTES

        tag = uuid4().hex[:8]
        relpath = f"cold_big_{tag}.py"
        content = "z" * 100_000
        await self._seed_repo(relpath, content)
        try:
            frames = list(
                await dispatch_frames(
                    lambda: _null_factory(), _principal(), _fetch_frame(relpath)
                )
            )
            assert len(frames) > 1
            header, parts = frames[0], frames[1:]
            assert header.get("chunked") is True
            total = header["total"]
            assert header["parts"] == len(parts) == -(-total // 48768)
            buf = bytearray()
            for i, part in enumerate(parts):
                assert part["id"] == header["id"]
                assert part["part"] == i
                buf.extend(base64.b64decode(part["data"]))
            assert len(buf) == total
            assert json.loads(bytes(buf).decode("utf-8"))["content"] == content
            for frame in frames:
                raw = json.dumps(frame, separators=(",", ":")).encode("utf-8")
                assert len(raw) <= MAX_FRAME_BYTES
        finally:
            await self._drop_repo(relpath)


@pytest.mark.asyncio
class TestImportPumpLifecycle:
    async def test_exactly_max_plus_one_frame_closes_pump_as_oversized(self):
        parent_recv, child_send = multiprocessing.Pipe(duplex=False)
        child_recv, parent_send = multiprocessing.Pipe(duplex=False)
        try:
            pump = asyncio.create_task(
                serve_channel(
                    recv_conn=parent_recv,
                    send_conn=parent_send,
                    session_factory=lambda: _null_factory(),
                    principal=_principal(),
                    allowed_ops=IMPORT_CHANNEL_ALLOWED_OPS,
                )
            )
            await asyncio.to_thread(
                child_send.send_bytes, b"x" * (MAX_FRAME_BYTES + 1)
            )
            assert await asyncio.wait_for(pump, timeout=15.0) == "oversized"
        finally:
            for conn in (child_send, child_recv, parent_recv, parent_send):
                with contextlib.suppress(Exception):
                    conn.close()

    async def test_child_exit_reads_as_eof(self):
        parent_recv, child_send = multiprocessing.Pipe(duplex=False)
        child_recv, parent_send = multiprocessing.Pipe(duplex=False)
        try:
            pump = asyncio.create_task(
                serve_channel(
                    recv_conn=parent_recv,
                    send_conn=parent_send,
                    session_factory=lambda: _null_factory(),
                    principal=_principal(),
                )
            )
            child_send.close()
            child_recv.close()
            assert await asyncio.wait_for(pump, timeout=15.0) == "eof"
        finally:
            for conn in (child_send, child_recv, parent_recv, parent_send):
                with contextlib.suppress(Exception):
                    conn.close()

    async def test_malformed_frame_closes_pump(self):
        parent_recv, child_send = multiprocessing.Pipe(duplex=False)
        child_recv, parent_send = multiprocessing.Pipe(duplex=False)
        pump = None
        try:
            pump = asyncio.create_task(
                serve_channel(
                    recv_conn=parent_recv,
                    send_conn=parent_send,
                    session_factory=lambda: _null_factory(),
                    principal=_principal(),
                )
            )
            await asyncio.to_thread(child_send.send_bytes, b"\x00\x01not-a-frame")
            assert await asyncio.wait_for(pump, timeout=15.0) == "malformed"
            pump = None
        finally:
            if pump is not None:
                pump.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await pump
            for conn in (child_send, child_recv, parent_recv, parent_send):
                with contextlib.suppress(Exception):
                    conn.close()

    @pytest.mark.parametrize(
        ("allowed_ops", "op"),
        [
            (IMPORT_CHANNEL_ALLOWED_OPS, "config.get"),
            (SDK_CHANNEL_ALLOWED_OPS, "modules.resolve"),
        ],
    )
    async def test_channel_rejects_operations_owned_by_the_other_channel(
        self, allowed_ops: frozenset[str], op: str
    ):
        parent_recv, child_send = multiprocessing.Pipe(duplex=False)
        child_recv, parent_send = multiprocessing.Pipe(duplex=False)
        try:
            pump = asyncio.create_task(
                serve_channel(
                    recv_conn=parent_recv,
                    send_conn=parent_send,
                    session_factory=lambda: _null_factory(),
                    principal=_principal(),
                    allowed_ops=allowed_ops,
                )
            )
            await asyncio.to_thread(
                child_send.send_bytes,
                json.dumps({"v": 1, "id": "wrong-channel", "op": op}).encode(),
            )
            response = json.loads(await asyncio.to_thread(child_recv.recv_bytes, 65537))
            assert response["ok"] is False
            assert response["status"] == 404
            child_send.close()
            assert await asyncio.wait_for(pump, timeout=15.0) == "eof"
        finally:
            for conn in (child_send, child_recv, parent_recv, parent_send):
                with contextlib.suppress(Exception):
                    conn.close()


@pytest.mark.asyncio
class TestDualChannelConcurrency:
    """A held async SDK request never blocks a synchronous import.

    The child holds one ``config.get`` on the async channel while a
    ``modules.resolve`` completes on the import channel — sharing one
    channel/lock would deadlock here.
    """

    async def test_import_finishes_while_sdk_request_held(self):
        from bifrost._import_transport import ChildSyncImportTransport
        from bifrost._local_transport import ChildLocalTransport

        sdk_parent_recv, sdk_child_send = multiprocessing.Pipe(duplex=False)
        sdk_child_recv, sdk_parent_send = multiprocessing.Pipe(duplex=False)
        imp_parent_recv, imp_child_send = multiprocessing.Pipe(duplex=False)
        imp_child_recv, imp_parent_send = multiprocessing.Pipe(duplex=False)
        conns = [
            sdk_child_send,
            sdk_parent_recv,
            sdk_parent_send,
            sdk_child_recv,
            imp_child_send,
            imp_parent_recv,
            imp_parent_send,
            imp_child_recv,
        ]
        release = asyncio.Event()
        principal = _principal()

        async def _blocked_config_get(session: Any, **kwargs: Any) -> Any:
            await release.wait()
            return {"key": "k", "value": "v", "config_type": "string"}

        async def _fast_resolve(name: str, **kwargs: Any) -> Any:
            return {
                "kind": "module",
                "path": "cold/dep.py",
                "storage_path": "cold/dep.py",
                "content": "VALUE = 1\n",
                "hash": "abc",
            }

        sdk_pump = imp_pump = sdk_call = None
        try:
            with (
                patch(
                    "shared.sdk_config.get_sdk_config_dict",
                    new=AsyncMock(side_effect=_blocked_config_get),
                ),
                patch(
                    "shared.sdk_modules.resolve_module_name",
                    new=AsyncMock(side_effect=_fast_resolve),
                ),
            ):
                sdk_pump = asyncio.create_task(
                    serve_channel(
                        recv_conn=sdk_parent_recv,
                        send_conn=sdk_parent_send,
                        session_factory=lambda: _null_factory(),
                        principal=principal,
                        allowed_ops=SDK_CHANNEL_ALLOWED_OPS,
                    )
                )
                imp_pump = asyncio.create_task(
                    serve_channel(
                        recv_conn=imp_parent_recv,
                        send_conn=imp_parent_send,
                        session_factory=lambda: _null_factory(),
                        principal=principal,
                        allowed_ops=IMPORT_CHANNEL_ALLOWED_OPS,
                    )
                )
                sdk_transport = ChildLocalTransport(sdk_child_send, sdk_child_recv)
                imp_transport = ChildSyncImportTransport(
                    imp_child_send, imp_child_recv
                )
                sdk_call = asyncio.create_task(
                    sdk_transport.call_config_get("k", None)
                )
                await asyncio.sleep(0.5)
                assert not sdk_call.done()
                # The import completes while the SDK call is still held.
                resolved = await asyncio.wait_for(
                    asyncio.to_thread(
                        imp_transport.call_modules_resolve, "cold.dep"
                    ),
                    timeout=15.0,
                )
                assert resolved["kind"] == "module"
                assert not sdk_call.done()
                release.set()
                assert await asyncio.wait_for(sdk_call, timeout=15.0) == {
                    "key": "k",
                    "value": "v",
                    "config_type": "string",
                }
                sdk_call = None
        finally:
            release.set()
            for task in (sdk_call, sdk_pump, imp_pump):
                if task is not None and not task.done():
                    task.cancel()
            for task in (sdk_call, sdk_pump, imp_pump):
                if task is not None:
                    with contextlib.suppress(
                        asyncio.CancelledError, Exception
                    ):
                        await task
            for conn in conns:
                with contextlib.suppress(Exception):
                    conn.close()
