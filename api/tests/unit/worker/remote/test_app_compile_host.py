"""App codec checks and generic file-compute recovery share one transport."""
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
from shared.app_build_compute import (
    MAX_INPUT_TRANSFER_BYTES,
    MAX_RESULT_TRANSFER_BYTES,
    encode_app_build_inputs,
    encode_app_build_result,
)
from shared.solution_app_compiler import AppBuildInputs, AppBuildResult, source_build_identity
from shared.worker_transfer import FileTransferManifest, FileTransferReceiver
from src.jobs.platform.base import PlatformJobCancelled, PlatformJobFailure
from src.jobs.schedulers.platform_jobs import ClaimedPlatformJob
from src.worker.remote.app_compile_host import AppBuildFileCompute
from src.worker.remote import file_compute_host as generic


def inputs() -> AppBuildInputs:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        data = b'{"name":"bifrost"}'
        item = tarfile.TarInfo("package/package.json")
        item.size = len(data)
        archive.addfile(item, io.BytesIO(data))
    sdk = stream.getvalue()
    return AppBuildInputs(str(uuid4()), {"index.html": b"<main>source</main>"}, {}, sdk,
        {"schema": 1, "variant": "server-install-v2", "sdk": sdk_identity(sdk),
         "toolchain": "a" * 64, "environment": "b" * 64}, {})


@asynccontextmanager
async def permit(**_kwargs):
    yield


class Control:
    def __init__(self, source: AppBuildInputs, *, lose_replies=False, wrong_recipe=False):
        self.connected = True
        self.lease = SimpleNamespace(worker_id=uuid4())
        self.reconciliation_ready = asyncio.Event()
        self.reconciliation_ready.set()
        self.transport_changed, self.compute_changed = asyncio.Event(), asyncio.Event()
        self.file_computes = {}
        self.calls, self.state = Counter(), "unknown"
        self.source, self.lose_replies, self.wrong_recipe = source, lose_replies, wrong_recipe
        self.receiver = self.output = None

    async def request(self, action, payload, *, authorization):
        async with authorization:
            self.calls[action] += 1
            if action == "app_compile_prepare":
                self.receiver = FileTransferReceiver(FileTransferManifest.from_dict(
                    payload["input"]["manifest"], max_total_size=MAX_INPUT_TRANSFER_BYTES))
                self.state = "prepared"
            elif action == "app_compile_chunk":
                self.receiver.add_chunk(payload["chunk"])
            elif action == "app_compile_start":
                self.state = "running"
                asyncio.get_running_loop().call_soon(self.finish)
            elif action == "app_compile_result_chunk":
                return {**{key: payload[key] for key in ("stage_id", "job_id", "lease_token")},
                        "chunk": list(self.output.iter_chunks())[payload["index"]].to_dict()}
            elif action == "app_compile_release":
                self.state = "released"
            if self.lose_replies and action in {"app_compile_prepare", "app_compile_chunk", "app_compile_start"}:
                raise TimeoutError("reply lost after accepted mutation")
            result = {**{key: payload[key] for key in ("stage_id", "job_id", "lease_token")}, "state": self.state}
            if self.state in {"unknown", "prepared"}:
                result["input_next_index"] = self.receiver._next_index if self.receiver else 0
            if self.state == "succeeded":
                result.update(result_manifest=self.output.manifest.to_dict(),
                              result_max_total_size=MAX_RESULT_TRANSFER_BYTES,
                              build_recipe=self.recipe)
            return result

    def finish(self):
        self.recipe = {**self.source.recipe, "toolchain": "c" * 64, "environment": "d" * 64}
        self.result = AppBuildResult({"index.html": b"<main>compiled</main>"},
                                     source_build_identity(replace(self.source, recipe=self.recipe)))
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


def remote_app_compute(bridge, claim):
    compute = generic.RemoteFileCompute(bridge, claim, "app.compile.v2")
    compute.authorize = permit
    return compute, AppBuildFileCompute(compute)


@pytest.mark.asyncio
@pytest.mark.parametrize("lose_replies", [False, True])
async def test_app_adapter_resolves_lost_replies_without_duplicate_mutations(monkeypatch, lose_replies):
    source, bridge = inputs(), Control(inputs(), lose_replies=lose_replies)
    bridge.source = source
    compute, adapter = remote_app_compute(bridge, ClaimedPlatformJob(uuid4(), uuid4(), 60, .95, 1, "application.deploy"))
    checkpoint = AsyncMock()
    monkeypatch.setattr(compute, "_checkpoint", checkpoint)
    monkeypatch.setattr(generic, "control_authorization", lambda _bridge: permit())
    try:
        assert await adapter(source) == bridge.result
        assert bridge.calls["app_compile_prepare"] == bridge.calls["app_compile_chunk"] == bridge.calls["app_compile_start"] == 1
        checkpoint.assert_awaited_once()
        await compute.release()
        assert bridge.state == "released"
        assert not bridge.file_computes
    finally:
        bridge.close()


@pytest.mark.asyncio
async def test_app_adapter_rejects_wrong_sdk_recipe_before_artifact_read(monkeypatch):
    source, bridge = inputs(), Control(inputs(), wrong_recipe=True)
    bridge.source = source
    compute, adapter = remote_app_compute(bridge, ClaimedPlatformJob(uuid4(), uuid4(), 60, .95, 1))
    monkeypatch.setattr(compute, "_checkpoint", AsyncMock())
    try:
        with pytest.raises(PlatformJobFailure, match="provenance"):
            await adapter(source)
        assert bridge.calls["app_compile_result_chunk"] == 0
    finally:
        bridge.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("owner_current,job_current", [(False, True), (True, False)])
async def test_generic_control_send_guard_refuses_stale_owner_or_job(monkeypatch, owner_current, job_current):
    bridge = SimpleNamespace(lease=SimpleNamespace(worker_id=uuid4()))
    db = SimpleNamespace(scalar=AsyncMock(side_effect=[10, None if not job_current else uuid4()]))
    @asynccontextmanager
    async def database():
        yield db
    monkeypatch.setattr(generic, "get_db_context", database)
    monkeypatch.setattr(generic, "fence_remote_owner", AsyncMock(return_value=owner_current))
    with pytest.raises(PlatformJobCancelled):
        async with generic.control_authorization(bridge, ClaimedPlatformJob(uuid4(), uuid4(), 60, .95, 1)):
            pytest.fail("stale authority reached the command send")
    assert db.scalar.await_count == (2 if owner_current else 0)


@pytest.mark.asyncio
async def test_generic_transport_rejects_a_reply_bound_to_another_claim(monkeypatch):
    source, bridge = inputs(), Control(inputs())
    bridge.source = source
    original = bridge.request
    async def mismatched(action, payload, **kwargs):
        reply = await original(action, payload, **kwargs)
        return {**reply, "lease_token": str(uuid4())}
    bridge.request = mismatched
    compute, _adapter = remote_app_compute(bridge, ClaimedPlatformJob(uuid4(), uuid4(), 60, .95, 1))
    monkeypatch.setattr(compute, "_checkpoint", AsyncMock())
    with encode_app_build_inputs(source) as transfer:
        with pytest.raises(PlatformJobFailure) as error:
            await compute("app.compile.v2", transfer)
    assert error.value.code == "file_compute_stage_conflict"
    assert bridge.calls["app_compile_prepare"] == 0


@pytest.mark.asyncio
async def test_reconciliation_releases_orphan_before_runner_loss(monkeypatch):
    from src.jobs.schedulers import platform_jobs
    source, bridge = inputs(), Control(inputs())
    bridge.source = source
    binding = {key: str(uuid4()) for key in ("stage_id", "job_id", "lease_token")}
    digest = "a" * 64
    bridge.inventory = {"compute_stages": [{**binding, "operation": "app.compile.v2", "state": "running", "input_sha256": digest}]}
    bridge.state = "running"
    bridge.memory = lambda: (100, 1000)
    db = SimpleNamespace(scalar=AsyncMock(return_value=SimpleNamespace(
        attempt=2, executor_attempt=2, executor_worker_id=bridge.lease.worker_id,
        executor_stage_id=UUID(binding["stage_id"]), executor_input_sha256=digest,
        executor_compute_capability="app.compile.v2",
    )))
    @asynccontextmanager
    async def database():
        yield db
    async def after_stop(*_args, **_kwargs):
        assert bridge.state == "released"
    runner_loss = AsyncMock(side_effect=after_stop)
    monkeypatch.setattr(generic, "get_db_context", database)
    monkeypatch.setattr(generic, "control_authorization", lambda _bridge: permit())
    monkeypatch.setattr(platform_jobs, "_handle_runner_loss", runner_loss)
    await generic.reconcile_file_computes(bridge)
    runner_loss.assert_awaited_once_with(
        UUID(binding["job_id"]), UUID(binding["lease_token"]), error_code="runner_lost",
        error_message="The API file-compute orchestrator stopped before the operation completed.",
        memory_sample=(100, 1000),
    )


@pytest.mark.asyncio
async def test_reconciliation_never_fails_a_new_attempt_from_an_old_stage(monkeypatch):
    from src.jobs.schedulers import platform_jobs
    source, bridge = inputs(), Control(inputs())
    bridge.source = source
    binding = {key: str(uuid4()) for key in ("stage_id", "job_id", "lease_token")}
    digest = "a" * 64
    bridge.inventory = {"compute_stages": [{**binding, "operation": "app.compile.v2", "state": "running", "input_sha256": digest}]}
    bridge.state = "running"
    bridge.memory = lambda: (100, 1000)
    db = SimpleNamespace(scalar=AsyncMock(return_value=SimpleNamespace(
        attempt=3, executor_attempt=2, executor_worker_id=bridge.lease.worker_id,
        executor_stage_id=UUID(binding["stage_id"]), executor_input_sha256=digest,
        executor_compute_capability="app.compile.v2",
    )))
    @asynccontextmanager
    async def database():
        yield db
    runner_loss = AsyncMock()
    monkeypatch.setattr(generic, "get_db_context", database)
    monkeypatch.setattr(generic, "control_authorization", lambda _bridge: permit())
    monkeypatch.setattr(platform_jobs, "_handle_runner_loss", runner_loss)
    await generic.reconcile_file_computes(bridge)
    runner_loss.assert_not_awaited()
    assert bridge.state == "released"
