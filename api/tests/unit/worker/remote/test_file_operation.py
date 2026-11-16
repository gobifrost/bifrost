"""Unit contracts for code-registered file-backed compute stages."""

from __future__ import annotations

from base64 import b64encode
from dataclasses import replace
from hashlib import sha256

import pytest

from shared.worker_transfer import FileTransferChunk, FileTransferManifest
from src.worker.remote.file_operation import (
    FileOperationDefinition,
    FileOperationChild,
    FileOperationResult,
    NamedFileOperationStages,
)


def _definition() -> FileOperationDefinition:
    def validate_input(manifest) -> None:
        if manifest.kind != "test_input":
            raise ValueError("Test input must be an input transfer")

    def instruction(stage) -> dict[str, object]:
        return {"input_path": str(stage.input_transfer.path)}

    def result(_receipt, _stage) -> FileOperationResult:
        raise AssertionError("This test does not start a child")

    def child() -> FileOperationChild:
        raise AssertionError("This test does not start a child")

    return FileOperationDefinition(
        name="test.operation.v1",
        label="Test operation",
        input_max_bytes=1024,
        validate_input=validate_input,
        child_instruction=instruction,
        decode_success=result,
        child=child,
        output_name="result.bin",
        temp_prefix="bifrost-test-operation-",
    )


def _manifest() -> FileTransferManifest:
    return FileTransferManifest(
        version=1,
        kind="test_input",
        total_size=5,
        sha256=sha256(b"input").hexdigest(),
        chunk_size=16,
        chunk_count=1,
        max_total_size=1024,
    )


def test_named_file_operation_rejects_an_unregistered_operation() -> None:
    stages = NamedFileOperationStages(
        operations={"test.operation.v1": _definition()},
    )
    with pytest.raises(ValueError, match="not registered"):
        stages.operation("not.registered")


@pytest.mark.asyncio
async def test_named_file_operation_does_not_accept_operation_selection_from_payload(
    tmp_path,
) -> None:
    manifest = _manifest()
    engine = NamedFileOperationStages(
        operations={"test.operation.v1": _definition()},
        temp_dir=tmp_path,
    )
    stages = engine.operation("test.operation.v1")
    payload = {
        "stage_id": "e2312a47-3c2e-4e12-a2e5-7ca178cbe5dc",
        "job_id": "c7c8119f-31b6-498a-bbaf-f47d1a638f08",
        "lease_token": "6ed55d0f-921d-49cd-b67b-ab2e129984cf",
        "operation": "not.registered",
        "input": {"manifest": manifest.to_dict()},
    }
    try:
        prepared = stages.prepare(payload)
        assert prepared["state"] == "prepared"
        assert stages.operation_name == "test.operation.v1"
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
async def test_named_file_operations_share_admission_and_operation_identity(tmp_path) -> None:
    first = _definition()
    second = replace(first, name="other.operation.v1", label="Other operation")
    engine = NamedFileOperationStages(
        operations={first.name: first, second.name: second},
        temp_dir=tmp_path,
        max_active=1,
    )
    manifest = _manifest()
    payload = {
        "stage_id": "e2312a47-3c2e-4e12-a2e5-7ca178cbe5dc",
        "job_id": "c7c8119f-31b6-498a-bbaf-f47d1a638f08",
        "lease_token": "6ed55d0f-921d-49cd-b67b-ab2e129984cf",
        "input": {"manifest": manifest.to_dict()},
    }
    try:
        engine.operation(first.name).prepare(payload)
        with pytest.raises(ValueError, match="existing operation"):
            engine.operation(second.name).prepare(payload)
        with pytest.raises(ValueError, match="existing operation"):
            engine.operation(second.name).status(payload)
        with pytest.raises(ValueError, match="existing operation"):
            await engine.operation(second.name).release(payload)
        other_payload = {**payload, "stage_id": "3526a967-0580-4d62-96d9-3d52a06f1b37"}
        with pytest.raises(RuntimeError, match="stage limit"):
            engine.operation(second.name).prepare(other_payload)
        await engine.operation(first.name).release(payload)
        assert engine.operation(first.name).status(payload)["state"] == "released"
        with pytest.raises(ValueError, match="existing operation"):
            engine.operation(second.name).prepare(payload)
        with pytest.raises(ValueError, match="existing operation"):
            engine.operation(second.name).status(payload)
        with pytest.raises(ValueError, match="existing operation"):
            await engine.operation(second.name).release(payload)
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
async def test_instruction_failure_becomes_a_terminal_failed_stage(tmp_path) -> None:
    def raising_instruction(_stage) -> dict[str, object]:
        raise RuntimeError("instruction failed")

    definition = replace(_definition(), child_instruction=raising_instruction)
    notifications: list[dict[str, str]] = []

    async def terminal_notification(binding: dict[str, str]) -> None:
        notifications.append(binding)

    engine = NamedFileOperationStages(
        operations={definition.name: definition},
        temp_dir=tmp_path,
        terminal_notification=terminal_notification,
    )
    stages = engine.operation(definition.name)
    manifest = _manifest()
    payload = {
        "stage_id": "e2312a47-3c2e-4e12-a2e5-7ca178cbe5dc",
        "job_id": "c7c8119f-31b6-498a-bbaf-f47d1a638f08",
        "lease_token": "6ed55d0f-921d-49cd-b67b-ab2e129984cf",
        "input": {"manifest": manifest.to_dict()},
    }
    binding = {key: payload[key] for key in ("stage_id", "job_id", "lease_token")}
    try:
        stages.prepare(payload)
        stages.add_chunk(
            {
                **payload,
                "chunk": FileTransferChunk(
                    index=0, data_b64=b64encode(b"input").decode("ascii")
                ).to_dict(),
            }
        )
        status = await stages.start(payload)
        assert status == {
            **binding,
            "state": "failed",
        "error": {
            "category": "buildfailure",
            "message": "Test operation child could not start: instruction failed",
        },
        }
        assert notifications == [binding]
    finally:
        await engine.shutdown()


@pytest.mark.parametrize("bound", [-1, True, 1.5])
def test_workspace_bound_is_validated_when_registering_an_operation(bound):
    definition = replace(_definition(), workspace_max_bytes=bound)
    with pytest.raises(ValueError, match="nonnegative integer"):
        NamedFileOperationStages(operations={definition.name: definition})


@pytest.mark.asyncio
async def test_prepared_stages_reserve_disk_before_accepting_more_input(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from uuid import uuid4

    from shared.worker_transfer import FileTransferError
    from src.worker.remote.file_operation import DISK_HEADROOM_BYTES

    definition = replace(_definition(), workspace_max_bytes=100)
    monkeypatch.setattr(
        "src.worker.remote.file_operation.shutil.disk_usage",
        lambda _path: SimpleNamespace(free=DISK_HEADROOM_BYTES + 150),
    )
    engine = NamedFileOperationStages(operations={definition.name: definition}, temp_dir=tmp_path)
    payload = {
        "stage_id": str(uuid4()), "job_id": str(uuid4()), "lease_token": str(uuid4()),
        "input": {"manifest": _manifest().to_dict()},
    }
    try:
        engine.prepare(definition.name, payload)
        initial_files = set(tmp_path.iterdir())
        second = {**payload, "stage_id": str(uuid4())}
        with pytest.raises(FileTransferError, match="unreserved staging space"):
            engine.prepare(definition.name, second)
        assert set(tmp_path.iterdir()) == initial_files
        assert len(engine._stages) == 1
        # Idempotent prepare spends no additional reservation.
        assert engine.prepare(definition.name, payload)["state"] == "prepared"
        await engine.operation(definition.name).release(payload)
        assert engine.prepare(definition.name, second)["state"] == "prepared"
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
async def test_disk_admission_failure_creates_no_stage_or_files(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from uuid import uuid4

    from shared.worker_transfer import FileTransferError
    from src.worker.remote.file_operation import DISK_HEADROOM_BYTES

    definition = _definition()
    monkeypatch.setattr(
        "src.worker.remote.file_operation.shutil.disk_usage",
        lambda _path: SimpleNamespace(free=DISK_HEADROOM_BYTES + 4),
    )
    engine = NamedFileOperationStages(operations={definition.name: definition}, temp_dir=tmp_path)
    try:
        with pytest.raises(FileTransferError, match="unreserved staging space"):
            engine.prepare(definition.name, {
                "stage_id": str(uuid4()), "job_id": str(uuid4()), "lease_token": str(uuid4()),
                "input": {"manifest": _manifest().to_dict()},
            })
        assert not engine._stages
        assert not list(tmp_path.iterdir())
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
async def test_release_removes_only_owned_nested_scratch(tmp_path):
    definition = _definition()
    engine = NamedFileOperationStages(operations={definition.name: definition}, temp_dir=tmp_path)
    operation = engine.operation(definition.name)
    payload = {
        'stage_id': 'e2312a47-3c2e-4e12-a2e5-7ca178cbe5dc',
        'job_id': 'c7c8119f-31b6-498a-bbaf-f47d1a638f08',
        'lease_token': '6ed55d0f-921d-49cd-b67b-ab2e129984cf',
        'input': {'manifest': _manifest().to_dict()},
    }
    neighbor = tmp_path / 'neighbor'
    neighbor.mkdir()
    (neighbor / 'keep.txt').write_text('not owned by stage')
    try:
        operation.prepare(payload)
        stage = engine._stages[payload['stage_id']]
        scratch = stage.directory / 'interrupted-source'
        scratch.mkdir()
        (scratch / 'source.py').write_text('pass')
        (scratch / 'neighbor-link').symlink_to(neighbor, target_is_directory=True)
        await operation.release(payload)
        assert not stage.directory.exists()
        assert (neighbor / 'keep.txt').read_text() == 'not owned by stage'
        assert operation.status(payload)['state'] == 'released'
    finally:
        await engine.shutdown()
