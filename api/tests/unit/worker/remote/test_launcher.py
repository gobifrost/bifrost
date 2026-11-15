"""Physical launcher transport fencing contracts."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from shared.app_build_compute import MAX_INPUT_TRANSFER_BYTES
from shared.worker_transfer import FileTransferManifest
from src.worker.remote.identity import LauncherIdentity
from src.worker.remote.app_compile import (
    APP_COMPILE_OPERATION,
    AppCompileStages,
    app_compile_operation,
)
from src.worker.remote.file_operation import NamedFileOperationStages
from src.worker.remote.launcher import Launcher
from src.worker.remote.protocol import ControlEnvelope, MAX_FRAME_BYTES, fragment


@pytest.mark.asyncio
async def test_reconnect_blocks_events_until_welcome_fences_the_new_socket() -> None:
    identity = LauncherIdentity(
        launcher_id=uuid4(),
        incarnation_id=uuid4(),
        job_uid=None,
        pod_uid=None,
        hostname="runner",
        form="static",
    )
    old_socket = AsyncMock()
    new_socket = AsyncMock()
    welcome_pending = asyncio.Event()
    release_welcome = asyncio.Event()
    received_welcome = False

    async def receive_welcome() -> str:
        nonlocal received_welcome
        if received_welcome:
            return ControlEnvelope(
                session_generation=2,
                sequence=1,
                kind="bootstrap",
                payload={"requirements_content": None},
            ).to_json()
        welcome_pending.set()
        await release_welcome.wait()
        received_welcome = True
        return json.dumps({"event": "welcome", "session_generation": 2})

    new_socket.recv.side_effect = receive_welcome
    launcher = Launcher(old_socket, identity=identity, secret="secret")
    launcher.session_generation = 1

    reconnect = asyncio.create_task(launcher.reconnect(new_socket, {}))
    await welcome_pending.wait()
    sample = asyncio.create_task(launcher.send({"event": "sample"}))
    await asyncio.sleep(0)

    assert new_socket.send.await_count == 1
    release_welcome.set()
    await asyncio.gather(reconnect, sample)

    assert old_socket.send.await_count == 0
    sample_frame = ControlEnvelope.from_json(new_socket.send.await_args_list[1].args[0])
    assert sample_frame.session_generation == 2
    assert sample_frame.sequence == 1
    assert sample_frame.kind == "event"
    assert sample_frame.payload == {"event": "sample"}


@pytest.mark.asyncio
async def test_startup_requirements_cross_bounded_control_frames_without_backend_access():
    identity = LauncherIdentity(
        launcher_id=uuid4(),
        incarnation_id=uuid4(),
        job_uid=None,
        pod_uid=None,
        hostname="runner",
        form="static",
    )
    content = "# dependency metadata\n" * (MAX_FRAME_BYTES // 10)
    frames = list(
        fragment(
            ControlEnvelope(
                session_generation=1,
                sequence=1,
                kind="bootstrap",
                payload={"requirements_content": content},
            )
        )
    )
    socket = AsyncMock()
    socket.recv.side_effect = [
        json.dumps({"event": "welcome", "session_generation": 1}),
        *(frame.to_json() for frame in frames),
    ]
    launcher = Launcher(socket, identity=identity, secret="secret")
    await launcher.hello({})
    assert launcher.requirements_content == content
    assert launcher._last_command_sequence == len(frames)


@pytest.mark.asyncio
async def test_external_startup_uses_explicit_inputs_and_only_supported_runtimes():
    identity = LauncherIdentity(
        launcher_id=uuid4(),
        incarnation_id=uuid4(),
        job_uid=None,
        pod_uid=None,
        hostname="external",
        form="external",
        resource_uid="resource-1",
    )
    launcher = Launcher(AsyncMock(), identity=identity, secret="resource-control-key")
    launcher.requirements_content = "example-package==1\n"
    launcher.helper = AsyncMock(return_value={"requirements_total": 1})
    launcher.template = Mock()
    launcher.agent_compute_template = Mock()
    launcher.monitor = AsyncMock()
    assert launcher.agent_template is None
    assert all(handler == "workflow.execute" for handler, _version in launcher.handlers)
    await launcher.start()
    assert ("agent.run", 1) in launcher.handlers
    assert ("agent.summarize", 1) in launcher.handlers
    assert ("agent.summarize_backfill", 1) in launcher.handlers
    assert launcher.agent_template is None
    launcher.helper.assert_awaited_once_with(
        "src.services.execution.requirements_setup_helper",
        input_payload={"content": "example-package==1\n"},
    )
    assert launcher._monitor_task is not None
    await launcher._monitor_task
    launcher.agent_compute_template.start.assert_called_once_with()


@pytest.mark.asyncio
async def test_launcher_advertises_compute_capabilities_only_after_compute_template_warms() -> None:
    identity = LauncherIdentity(
        launcher_id=uuid4(),
        incarnation_id=uuid4(),
        job_uid=None,
        pod_uid=None,
        hostname="runner",
        form="static",
    )
    socket = AsyncMock()
    socket.recv.side_effect = [
        json.dumps({"event": "welcome", "session_generation": 1}),
        ControlEnvelope(
            session_generation=1,
            sequence=1,
            kind="bootstrap",
            payload={"requirements_content": None},
        ).to_json(),
    ]
    launcher = Launcher(socket, identity=identity, secret="secret")
    manifest = FileTransferManifest(
        version=1,
        kind="app_build_inputs",
        total_size=1,
        sha256="a" * 64,
        chunk_size=1,
        chunk_count=1,
        max_total_size=MAX_INPUT_TRANSFER_BYTES,
    )
    payload = {
        "stage_id": str(uuid4()),
        "job_id": str(uuid4()),
        "lease_token": str(uuid4()),
        "input": {"manifest": manifest.to_dict()},
    }
    try:
        await launcher.hello({})
        hello = json.loads(socket.send.await_args.args[0])
        assert hello["compute_capabilities"] == [["app.compile", 2]]

        warmed_socket = AsyncMock()
        warmed_socket.recv.side_effect = [
            json.dumps({"event": "welcome", "session_generation": 1}),
            ControlEnvelope(
                session_generation=1,
                sequence=1,
                kind="bootstrap",
                payload={"requirements_content": None},
            ).to_json(),
        ]
        warmed = Launcher(warmed_socket, identity=identity, secret="secret")
        warmed.agent_compute_template = Mock()
        await warmed.start_agent_compute_template()
        await warmed.hello({})
        warmed_hello = json.loads(warmed_socket.send.await_args.args[0])
        assert warmed_hello["compute_capabilities"] == [["app.compile", 2], ["agent.loop", 1], ["summary.compute", 1]]
        launcher.app_compiles.prepare(payload)
        assert launcher.inventory()["compute_stages"] == [
            {
                "stage_id": payload["stage_id"],
                "job_id": payload["job_id"],
                "lease_token": payload["lease_token"],
                "state": "prepared",
                "input_sha256": manifest.sha256,
            }
        ]
    finally:
        await launcher.file_operations.shutdown()


@pytest.mark.asyncio
async def test_compute_stage_journal_drains_only_after_final_release(tmp_path) -> None:
    identity = LauncherIdentity(
        launcher_id=uuid4(),
        incarnation_id=uuid4(),
        job_uid=None,
        pod_uid=None,
        hostname="runner",
        form="static",
    )
    socket = AsyncMock()
    launcher = Launcher(socket, identity=identity, secret="secret")
    launcher.session_generation = 1
    launcher.file_operations = NamedFileOperationStages(
        operations={APP_COMPILE_OPERATION: app_compile_operation()},
        temp_dir=tmp_path,
        max_active=2,
        max_records=2,
    )
    launcher.app_compiles = AppCompileStages(launcher.file_operations)
    manifest = FileTransferManifest(
        version=1,
        kind="app_build_inputs",
        total_size=1,
        sha256="a" * 64,
        chunk_size=1,
        chunk_count=1,
        max_total_size=MAX_INPUT_TRANSFER_BYTES,
    )

    def payload() -> dict[str, object]:
        return {
            "stage_id": str(uuid4()),
            "job_id": str(uuid4()),
            "lease_token": str(uuid4()),
            "input": {"manifest": manifest.to_dict()},
        }

    first, second = payload(), payload()
    first_binding = {key: first[key] for key in ("stage_id", "job_id", "lease_token")}
    second_binding = {key: second[key] for key in ("stage_id", "job_id", "lease_token")}
    try:
        launcher.app_compiles.prepare(first)
        launcher.app_compiles.prepare(second)
        await launcher.command(
            {"id": "first", "action": "app_compile_release", "payload": first_binding},
            generation=1,
        )
        await asyncio.sleep(0)
        assert launcher.file_operations.retire_requested is False
        assert not any(
            ControlEnvelope.from_json(call.args[0]).payload == {"event": "draining"}
            for call in socket.send.await_args_list
        )
        await launcher.command(
            {
                "id": "second",
                "action": "app_compile_release",
                "payload": second_binding,
            },
            generation=1,
        )
        await asyncio.sleep(0)
        assert launcher.inventory()["compute_retirement_requested"] is True
        assert any(
            ControlEnvelope.from_json(call.args[0]).payload == {"event": "draining"}
            for call in socket.send.await_args_list
        )
        with pytest.raises(ValueError, match="released"):
            launcher.app_compiles.prepare(first)
        with pytest.raises(RuntimeError, match="retirement"):
            launcher.app_compiles.prepare(payload())
    finally:
        await launcher.file_operations.shutdown()
