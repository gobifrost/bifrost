import asyncio
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from shared.file_compute import FileComputeResult, decode_file_result, encode_file_transfer
from shared.app_bundle_compute import MAX_RESULT_TRANSFER_BYTES as APP_BUNDLE_RESULT_LIMIT
from shared.worker_transfer import FileTransfer, FileTransferManifest, ReceivedFileTransfer
from src.jobs.platform.base import PlatformJobFailure
from src.jobs.schedulers.platform_jobs import ClaimedPlatformJob
from src.worker.remote.file_compute_host import ApiFileComputeProcess, RemoteFileCompute
from src.worker.remote.fencing import LeaseRef, current_remote_lease_ref
from src.worker.remote.ownership import RemoteOwnerLease


def _single_byte_manifest() -> FileTransferManifest:
    return FileTransferManifest(
        version=1, kind="test-result", total_size=1,
        sha256="2d711642b726b04401627ca9fbac32f5c8530fb1903cc4db02258717921a4881",
        chunk_size=1, chunk_count=1, max_total_size=1,
    )


def test_result_context_closes_the_received_transfer(tmp_path: Path) -> None:
    path = tmp_path / "result.zip"
    path.write_bytes(b"x")
    manifest = _single_byte_manifest()

    with FileComputeResult(ReceivedFileTransfer(manifest, path), {"kind": "test"}) as result:
        assert result.metadata == {"kind": "test"}
        assert result.transfer.path.exists()

    assert not path.exists()


@pytest.mark.asyncio
async def test_cancelled_encoder_closes_late_produced_transfer(tmp_path: Path) -> None:
    path = tmp_path / "encoded.zip"
    started, closed = asyncio.Event(), asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()

    class TrackedTransfer(FileTransfer):
        def close(self) -> None:
            super().close()
            loop.call_soon_threadsafe(closed.set)

    def encoder() -> FileTransfer:
        loop.call_soon_threadsafe(started.set)
        release.wait()
        path.write_bytes(b"x")
        return TrackedTransfer(_single_byte_manifest(), path)

    task = asyncio.create_task(encode_file_transfer(encoder))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    release.set()
    await closed.wait()
    assert not path.exists()


@pytest.mark.asyncio
async def test_cancelled_decoder_keeps_received_file_until_thread_finishes(tmp_path: Path) -> None:
    path = tmp_path / "result.zip"
    path.write_bytes(b"x")
    started, closed = asyncio.Event(), asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()

    class TrackedTransfer(ReceivedFileTransfer):
        def close(self) -> None:
            super().close()
            loop.call_soon_threadsafe(closed.set)

    def decoder(transfer: ReceivedFileTransfer) -> bytes:
        loop.call_soon_threadsafe(started.set)
        release.wait()
        result = transfer.path.read_bytes()
        return result

    outcome = FileComputeResult(TrackedTransfer(_single_byte_manifest(), path), {})
    task = asyncio.create_task(decode_file_result(outcome, decoder))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert path.exists()
    release.set()
    await closed.wait()
    assert not path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("inflated", [False, True])
async def test_result_limit_is_selected_by_the_api_operation_registry(
    tmp_path: Path, inflated: bool
) -> None:
    payload = tmp_path / "payload"
    payload.write_bytes(b"x")
    manifest = FileTransferManifest(
        version=1,
        kind="test-result",
        total_size=1,
        sha256="2d711642b726b04401627ca9fbac32f5c8530fb1903cc4db02258717921a4881",
        chunk_size=1,
        chunk_count=1,
        max_total_size=1,
    )
    compute = RemoteFileCompute(
        SimpleNamespace(compute_changed=asyncio.Event(), file_computes={}),
        ClaimedPlatformJob(uuid4(), uuid4(), 60, .95, 1, "application.publish"),
        "app.bundle.v1",
    )
    compute._checkpoint = AsyncMock()
    compute._mutate = AsyncMock(return_value=True)
    limit = APP_BUNDLE_RESULT_LIMIT + 1 if inflated else APP_BUNDLE_RESULT_LIMIT
    compute._read = AsyncMock(side_effect=[
        {**compute.binding, "state": "unknown", "input_next_index": 0},
        {**compute.binding, "state": "prepared", "input_next_index": 0},
        {**compute.binding, "state": "prepared", "input_next_index": 1},
        {**compute.binding, "state": "succeeded", "result_manifest": manifest.to_dict(), "result_max_total_size": limit},
        {**compute.binding, "chunk": {"index": 0, "data_b64": "eA=="}},
    ])

    try:
        if inflated:
            with pytest.raises(PlatformJobFailure, match="invalid result manifest"):
                await compute("app.bundle.v1", FileTransfer(manifest, payload))
            assert [call.args[0] for call in compute._read.await_args_list] == [
                "status", "status", "status", "status"
            ]
        else:
            with await compute("app.bundle.v1", FileTransfer(manifest, payload)) as result:
                assert result.transfer.path.read_bytes() == b"x"
    finally:
        payload.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_non_app_memory_failure_uses_canonical_platform_code(tmp_path: Path) -> None:
    payload = tmp_path / "payload"
    payload.write_bytes(b"x")
    manifest = FileTransferManifest(
        version=1, kind="test", total_size=1,
        sha256="2d711642b726b04401627ca9fbac32f5c8530fb1903cc4db02258717921a4881",
        chunk_size=1, chunk_count=1, max_total_size=1,
    )
    compute = RemoteFileCompute(
        SimpleNamespace(compute_changed=asyncio.Event(), file_computes={}),
        ClaimedPlatformJob(uuid4(), uuid4(), 60, .95, 1, "audit.archive"),
        "audit.archive.v1",
    )
    compute._checkpoint = AsyncMock()
    compute._mutate = AsyncMock(return_value=True)
    compute._read = AsyncMock(side_effect=[
        {**compute.binding, "state": "unknown", "input_next_index": 0},
        {**compute.binding, "state": "prepared", "input_next_index": 0},
        {**compute.binding, "state": "prepared", "input_next_index": 1},
        {**compute.binding, "state": "failed", "error": {"category": "memory", "message": "full"}},
    ])
    try:
        with pytest.raises(PlatformJobFailure) as error:
            await compute("audit.archive.v1", FileTransfer(manifest, payload))
        assert error.value.code == "memory_pressure"
    finally:
        payload.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_process_waits_for_uncertain_cleanup_while_it_owns_the_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.jobs.platform import runner
    from src.worker.remote import file_compute_host
    from src.worker.remote import ownership

    release_attempted = asyncio.Event()

    class Compute:
        def __init__(self, bridge, *_args) -> None:
            self.bridge = bridge
            self.stage_id = "stage"
            bridge.file_computes[self.stage_id] = self
            self.attempts = 0

        async def release(self) -> None:
            self.attempts += 1
            if self.attempts == 1:
                release_attempted.set()
                raise TimeoutError
            self.bridge.file_computes.pop(self.stage_id)

        def _stages(self):
            return self.bridge.file_computes

    lease = RemoteOwnerLease(uuid4(), uuid4(), uuid4(), 1)
    bridge = SimpleNamespace(
        file_computes={}, lease=lease, lease_ref=LeaseRef(lease),
        transport_changed=asyncio.Event(),
    )
    monkeypatch.setattr(file_compute_host, "RemoteFileCompute", Compute)
    monkeypatch.setattr(runner, "run_claimed_platform_job", AsyncMock(return_value=True))
    monkeypatch.setattr(ownership, "owns_remote_session", AsyncMock(return_value=True))
    process = ApiFileComputeProcess(
        bridge, ClaimedPlatformJob(uuid4(), uuid4(), 60, .95, 1, "audit.archive"),
        "audit.archive.v1",
    )

    await release_attempted.wait()
    assert not process.task.done()
    bridge.transport_changed.set()
    assert await process.wait() == 0
    assert not bridge.file_computes


@pytest.mark.asyncio
async def test_process_releases_admission_after_owner_loss_during_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.jobs.platform import runner
    from src.worker.remote import file_compute_host
    from src.worker.remote import ownership

    class Compute:
        def __init__(self, bridge, *_args) -> None:
            self.bridge = bridge
            self.stage_id = "stage"
            bridge.file_computes[self.stage_id] = self

        async def release(self) -> None:
            raise ConnectionError

        def _stages(self):
            return self.bridge.file_computes

    lease = RemoteOwnerLease(uuid4(), uuid4(), uuid4(), 1)
    bridge = SimpleNamespace(
        file_computes={}, lease=lease, lease_ref=LeaseRef(lease),
        transport_changed=asyncio.Event(),
    )
    monkeypatch.setattr(file_compute_host, "RemoteFileCompute", Compute)
    monkeypatch.setattr(runner, "run_claimed_platform_job", AsyncMock(return_value=True))
    monkeypatch.setattr(ownership, "owns_remote_session", AsyncMock(return_value=False))
    process = ApiFileComputeProcess(
        bridge, ClaimedPlatformJob(uuid4(), uuid4(), 60, .95, 1, "audit.archive"),
        "audit.archive.v1",
    )

    assert await process.wait() == 0
    assert not bridge.file_computes


@pytest.mark.asyncio
async def test_running_handler_tracks_same_owner_reconnect_generation(monkeypatch) -> None:
    from dataclasses import replace
    from src.jobs.platform import runner

    lease = RemoteOwnerLease(uuid4(), uuid4(), uuid4(), 1)
    bridge = SimpleNamespace(file_computes={}, lease=lease, lease_ref=LeaseRef(lease))
    started, resumed = asyncio.Event(), asyncio.Event()
    replacement = replace(lease, session_generation=2)

    async def handler(*_args, **_kwargs):
        ref = current_remote_lease_ref()
        started.set()
        await resumed.wait()
        assert ref is bridge.lease_ref
        assert ref.lease == replacement
        return True

    monkeypatch.setattr(runner, "run_claimed_platform_job", handler)
    process = ApiFileComputeProcess(
        bridge, ClaimedPlatformJob(uuid4(), uuid4(), 60, .95, 1, "audit.archive"),
        "audit.archive.v1",
    )
    await started.wait()
    bridge.lease = replacement
    bridge.lease_ref.lease = replacement
    resumed.set()
    assert await process.wait() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("next_operation", ["app.bundle.v1", "audit.archive.v1"])
async def test_sequential_batches_release_predecessor_before_next_stage(tmp_path: Path, monkeypatch, next_operation) -> None:
    from src.jobs.platform import catalog
    from src.worker.remote import file_compute_host

    metadata = catalog.get_platform_job_metadata("application.publish")
    assert metadata is not None
    monkeypatch.setattr(catalog, "_METADATA", {
        **catalog._METADATA,
        metadata.job_type: replace(metadata, additional_compute_capabilities=("audit.archive.v1",)),
    })
    payload = tmp_path / "payload"
    payload.write_bytes(b"x")
    manifest = FileTransferManifest(
        version=1, kind="test-result", total_size=1,
        sha256="2d711642b726b04401627ca9fbac32f5c8530fb1903cc4db02258717921a4881",
        chunk_size=1, chunk_count=1, max_total_size=1,
    )
    bridge = SimpleNamespace(
        file_computes={}, compute_changed=asyncio.Event(),
    )
    claim = ClaimedPlatformJob(uuid4(), uuid4(), 60, .95, 1, "application.publish")
    compute = RemoteFileCompute(bridge, claim, "app.bundle.v1")
    order: list[tuple[str, str, str | None]] = []
    state = {"value": "unknown"}

    async def checkpoint(_manifest):
        order.append((
            "checkpoint", compute.stage_id,
            None if compute.predecessor_stage_id is None else str(compute.predecessor_stage_id),
        ))
        compute.input_sha256 = _manifest.sha256

    async def mutate(action, **_payload):
        if action == "prepare":
            state["value"] = "prepared"
        elif action == "start":
            state["value"] = "succeeded"
        return True

    async def read(action, **_payload):
        if action == "result_chunk":
            return {**compute.binding, "chunk": {"index": 0, "data_b64": "eA=="}}
        response = {**compute.binding, "state": state["value"]}
        if state["value"] == "prepared":
            response["input_next_index"] = 1
        if state["value"] == "unknown":
            response["input_next_index"] = 0
        if state["value"] == "succeeded":
            response.update(
                result_manifest=manifest.to_dict(),
                result_max_total_size=file_compute_host._OPERATION_RESULT_LIMITS[compute.operation_name],
            )
        return response

    async def release_current():
        old = compute.stage_id
        order.append(("released", old, None))
        compute.predecessor_stage_id = UUID(old)
        compute._stages().pop(old, None)
        state["value"] = "unknown"

    compute._checkpoint = checkpoint
    compute._mutate = mutate
    compute._read = read
    compute._release_current = release_current
    try:
        with await compute("app.bundle.v1", FileTransfer(manifest, payload)):
            pass
        first_stage = compute.stage_id
        with await compute(next_operation, FileTransfer(manifest, payload)):
            pass
        second_stage = compute.stage_id
        assert compute.operation_name == next_operation
    finally:
        payload.unlink(missing_ok=True)

    assert first_stage != second_stage
    assert order == [
        ("checkpoint", first_stage, None),
        ("released", first_stage, None),
        ("checkpoint", second_stage, str(UUID(first_stage))),
    ]


@pytest.mark.asyncio
async def test_job_cannot_select_an_operation_outside_its_declared_pipeline(tmp_path):
    payload = tmp_path / "input"
    payload.write_bytes(b"x")
    compute = RemoteFileCompute(
        SimpleNamespace(file_computes={}),
        ClaimedPlatformJob(uuid4(), uuid4(), 60, .95, 1, "application.publish"),
        "app.bundle.v1",
    )
    checkpoint = AsyncMock()
    compute._checkpoint = checkpoint
    try:
        with FileTransfer(_single_byte_manifest(), payload) as transfer:
            with pytest.raises(PlatformJobFailure, match="undeclared"):
                await compute("audit.export.v1", transfer)
        checkpoint.assert_not_awaited()
    finally:
        await compute.release()


@pytest.mark.asyncio
async def test_owned_file_io_finishes_before_cancelled_caller_closes_handle():
    import asyncio
    import threading
    from shared.file_compute import run_owned_file_io

    entered, release = threading.Event(), threading.Event()
    closed = False

    def operation():
        entered.set()
        release.wait()
        assert not closed

    async def caller():
        nonlocal closed
        try:
            await run_owned_file_io(operation)
        finally:
            closed = True

    task = asyncio.create_task(caller())
    try:
        await asyncio.to_thread(entered.wait)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done() and not closed
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed
