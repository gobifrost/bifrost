"""Compute transfers use one claim, resolve uncertainty, and verify provenance."""
import asyncio
import io
import tarfile
from collections import Counter
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from bifrost.app_build_identity import sdk_identity
from shared.app_build_compute import MAX_INPUT_TRANSFER_BYTES, encode_app_build_result
from shared.solution_app_compiler import AppBuildInputs, AppBuildResult, source_build_identity
from shared.worker_transfer import FileTransferManifest, FileTransferReceiver
from src.jobs.platform.base import PlatformJobCancelled, PlatformJobFailure
from src.jobs.schedulers.platform_jobs import ClaimedPlatformJob
from src.worker.remote import app_compile_host as host


def inputs():
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        data = b'{"name":"bifrost"}'
        item = tarfile.TarInfo("package/package.json")
        item.size = len(data)
        archive.addfile(item, io.BytesIO(data))
    sdk = stream.getvalue()
    return AppBuildInputs(
        str(uuid4()), {"index.html": b"<main>source</main>"}, {}, sdk,
        {"schema": 1, "variant": "server-install-v2", "sdk": sdk_identity(sdk),
         "toolchain": "a" * 64, "environment": "b" * 64}, {},
    )


@asynccontextmanager
async def permit(**_kwargs):
    yield


class Control:
    def __init__(self, source, *, lose_replies=False, wrong_recipe=False):
        self.connected = True
        self.lease = SimpleNamespace(worker_id=uuid4())
        self.reconciliation_ready = asyncio.Event()
        self.reconciliation_ready.set()
        self.transport_changed, self.compute_changed = asyncio.Event(), asyncio.Event()
        self.app_compilers = {}
        self.calls = Counter()
        self.state = "unknown"
        self.source, self.lose_replies, self.wrong_recipe = source, lose_replies, wrong_recipe
        self.receiver = self.output = None

    async def request(self, action, payload, *, authorization):
        async with authorization:
            self.calls[action] += 1
            if action == "app_compile_prepare":
                self.receiver = FileTransferReceiver(
                    FileTransferManifest.from_dict(
                        payload["input"]["manifest"], max_total_size=MAX_INPUT_TRANSFER_BYTES
                    )
                )
                self.state = "prepared"
            elif action == "app_compile_chunk":
                self.receiver.add_chunk(payload["chunk"])
            elif action == "app_compile_start":
                self.state = "running"
                asyncio.get_running_loop().call_soon(self.finish)
            elif action == "app_compile_result_chunk":
                return {**{k: payload[k] for k in ("stage_id", "job_id", "lease_token")},
                        "chunk": list(self.output.iter_chunks())[payload["index"]].to_dict()}
            elif action == "app_compile_release":
                self.state = "released"
            if self.lose_replies and action in {"app_compile_prepare", "app_compile_chunk", "app_compile_start"}:
                raise TimeoutError("reply lost after accepted mutation")
            result = {**{k: payload[k] for k in ("stage_id", "job_id", "lease_token")}, "state": self.state}
            if self.state in ("unknown", "prepared"):
                result["input_next_index"] = self.receiver._next_index if self.receiver else 0
            if self.state == "succeeded":
                result.update(result_manifest=self.output.manifest.to_dict(), build_recipe=self.recipe)
            return result

    def finish(self):
        self.recipe = {**self.source.recipe, "toolchain": "c" * 64, "environment": "d" * 64}
        actual = replace(self.source, recipe=self.recipe)
        self.result = AppBuildResult({"index.html": b"<main>compiled</main>"}, source_build_identity(actual))
        self.output = encode_app_build_result(self.result)
        if self.wrong_recipe:
            self.recipe = {**self.recipe, "sdk": "e" * 64}
        self.state = "succeeded"
        self.compute_changed.set()

    def close(self):
        if self.receiver:
            self.receiver.close()
        if self.output:
            self.output.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("lose_replies", [False, True])
async def test_compile_resolves_lost_replies_without_duplicate_mutations(monkeypatch, lose_replies):
    source = inputs()
    bridge = Control(source, lose_replies=lose_replies)
    claim = ClaimedPlatformJob(uuid4(), uuid4(), 60, .95, 512 * 1024**2, "application.deploy")
    compiler = host.RemoteAppCompiler(bridge, claim)
    compiler.authorize = permit
    checkpoint = AsyncMock()
    monkeypatch.setattr(compiler, "_checkpoint", checkpoint)
    monkeypatch.setattr(host, "control_authorization", lambda _bridge: permit())
    try:
        result = await compiler(source)
        assert result == bridge.result
        assert result.build_identity != source_build_identity(source)
        assert bridge.calls["app_compile_prepare"] == 1
        assert bridge.calls["app_compile_chunk"] == 1
        assert bridge.calls["app_compile_start"] == 1
        checkpoint.assert_awaited_once()
        await compiler.release()
        assert bridge.state == "released"
        assert not bridge.app_compilers
    finally:
        bridge.close()


@pytest.mark.asyncio
async def test_wrong_sdk_recipe_cannot_return_artifacts(monkeypatch):
    source = inputs()
    bridge = Control(source, wrong_recipe=True)
    compiler = host.RemoteAppCompiler(bridge, ClaimedPlatformJob(uuid4(), uuid4(), 60, .95, 1))
    compiler.authorize = permit
    monkeypatch.setattr(compiler, "_checkpoint", AsyncMock())
    try:
        with pytest.raises(PlatformJobFailure) as error:
            await compiler(source)
        assert error.value.code == "compile_provenance_invalid"
        assert bridge.calls["app_compile_result_chunk"] == 0
    finally:
        bridge.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("owner_current,job_current", [(False, True), (True, False)])
async def test_control_send_guard_refuses_stale_owner_or_job(monkeypatch, owner_current, job_current):
    bridge = SimpleNamespace(lease=SimpleNamespace(worker_id=uuid4()))
    db = SimpleNamespace(scalar=AsyncMock(side_effect=[10, None if not job_current else uuid4()]))

    @asynccontextmanager
    async def database():
        yield db

    monkeypatch.setattr(host, "get_db_context", database)
    monkeypatch.setattr(host, "fence_remote_owner", AsyncMock(return_value=owner_current))
    with pytest.raises(PlatformJobCancelled):
        async with host.control_authorization(bridge, ClaimedPlatformJob(uuid4(), uuid4(), 60, .95, 1)):
            pytest.fail("stale authority reached the command send")
    assert db.scalar.await_count == (2 if owner_current else 0)


@pytest.mark.asyncio
async def test_reply_from_another_claim_is_rejected_before_prepare(monkeypatch):
    bridge = Control(inputs())
    original = bridge.request

    async def mismatched(action, payload, **kwargs):
        reply = await original(action, payload, **kwargs)
        return {**reply, "lease_token": str(uuid4())}

    bridge.request = mismatched
    compiler = host.RemoteAppCompiler(bridge, ClaimedPlatformJob(uuid4(), uuid4(), 60, .95, 1))
    compiler.authorize = permit
    monkeypatch.setattr(compiler, "_checkpoint", AsyncMock())
    with pytest.raises(PlatformJobFailure) as error:
        await compiler(bridge.source)
    assert error.value.code == "compile_stage_conflict"
    assert bridge.calls["app_compile_prepare"] == 0


@pytest.mark.asyncio
async def test_process_keeps_memory_reserved_until_compute_cleanup(monkeypatch):
    from src.jobs.platform import runner

    cleanup_started, allow_cleanup = asyncio.Event(), asyncio.Event()
    monkeypatch.setattr(runner, "run_claimed_platform_job", AsyncMock(return_value=True))

    async def release(_self):
        cleanup_started.set()
        await allow_cleanup.wait()

    monkeypatch.setattr(host.RemoteAppCompiler, "release", release)
    bridge = SimpleNamespace(app_compilers={})
    process = host.ApiAppBuildProcess(bridge, ClaimedPlatformJob(uuid4(), uuid4(), 60, .95, 1))
    await cleanup_started.wait()
    assert process.returncode is None
    allow_cleanup.set()
    assert await process.wait() == 0
    assert process.returncode == 0


@pytest.mark.asyncio
async def test_reconnect_stops_orphan_compute_before_canonical_runner_loss(monkeypatch):
    from src.jobs.schedulers import platform_jobs

    bridge = Control(inputs())
    binding = {key: str(uuid4()) for key in ("stage_id", "job_id", "lease_token")}
    digest = "a" * 64
    bridge.inventory = {"compute_stages": [{**binding, "state": "running", "input_sha256": digest}]}
    bridge.state = "running"
    bridge.memory = lambda: (100, 1000)
    db = SimpleNamespace(scalar=AsyncMock(return_value=SimpleNamespace(
        attempt=2,
        executor_attempt=2,
        executor_worker_id=bridge.lease.worker_id,
        executor_stage_id=UUID(binding["stage_id"]),
        executor_input_sha256=digest,
        executor_compute_capability="app.compile.v2",
    )))

    @asynccontextmanager
    async def database():
        yield db

    async def after_physical_stop(*_args, **_kwargs):
        assert bridge.state == "released"

    runner_loss = AsyncMock(side_effect=after_physical_stop)
    monkeypatch.setattr(host, "get_db_context", database)
    monkeypatch.setattr(host, "control_authorization", lambda _bridge: permit())
    monkeypatch.setattr(platform_jobs, "_handle_runner_loss", runner_loss)
    await host.reconcile_app_compiles(bridge)
    runner_loss.assert_awaited_once_with(
        UUID(binding["job_id"]), UUID(binding["lease_token"]),
        error_code="runner_lost",
        error_message="The API build orchestrator stopped before the deployment completed.",
        memory_sample=(100, 1000),
    )
    assert bridge.state == "released"
    assert bridge.calls["app_compile_start"] == 0


@pytest.mark.asyncio
async def test_reconnect_never_fails_a_new_attempt_from_an_old_stage(monkeypatch):
    from src.jobs.schedulers import platform_jobs

    bridge = Control(inputs())
    binding = {key: str(uuid4()) for key in ("stage_id", "job_id", "lease_token")}
    bridge.inventory = [{**binding, "state": "running", "input_sha256": "a" * 64}]
    bridge.inventory = {"compute_stages": bridge.inventory}
    bridge.state = "running"
    bridge.memory = lambda: (100, 1000)
    db = SimpleNamespace(scalar=AsyncMock(return_value=SimpleNamespace(
        attempt=3,
        executor_attempt=2,
        executor_worker_id=bridge.lease.worker_id,
        executor_stage_id=UUID(binding["stage_id"]),
        executor_input_sha256="a" * 64,
        executor_compute_capability="app.compile.v2",
    )))

    @asynccontextmanager
    async def database():
        yield db

    runner_loss = AsyncMock()
    monkeypatch.setattr(host, "get_db_context", database)
    monkeypatch.setattr(host, "control_authorization", lambda _bridge: permit())
    monkeypatch.setattr(platform_jobs, "_handle_runner_loss", runner_loss)

    await host.reconcile_app_compiles(bridge)

    runner_loss.assert_not_awaited()
    assert bridge.state == "released"
