"""Real normal-lane external-launcher recovery for the registered workspace preflight recipe.

This is deliberately a single local runtime case.  It starts the ordinary
``src.worker.remote.launcher`` process, authenticates its signed control hello
over a real loopback WebSocket, and drives only the registered
``workspace.preflight.v1`` control messages.  The harness pauses the owned
file-operation process group, severs the first control transport, accepts the
launcher's replacement session, then resumes and releases the same stage.

Only owned disposable worker/job rows are created for the real PostgreSQL
owner-acquisition and command-fencing checks; other workers and configuration
are unchanged.  The loopback peer verifies the production signed hello and exercises
real control framing, but it is not an API process; this test therefore proves
launcher transport/session fencing and physical stage survival, not a full
API-process takeover. The production owner-acquisition and
``control_authorization`` functions run against the real disposable database.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import hashlib

import pytest

from shared import workspace_preflight_compute
from src.worker.remote.protocol import ControlEnvelope, FragmentAssembler, Hello, SignedHello, fragment, verify_hello


pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


async def _wait_for(
    predicate: Callable[[], Awaitable[Any | None]], *, timeout: float, description: str
) -> Any:
    deadline = asyncio.get_running_loop().time() + timeout
    last: Any = None
    while asyncio.get_running_loop().time() < deadline:
        last = await predicate()
        if last is not None:
            return last
        await asyncio.sleep(0.05)
    raise AssertionError(f"Timed out waiting for {description}: {last!r}")


@dataclass
class _ControlTrace:
    secret: str
    binding: dict[str, str]
    input_chunks: list[dict[str, object]]
    initial_stage_pid: int | None = None
    replacement_stage_pid: int | None = None
    replacement_generation: int | None = None
    result_manifest: dict[str, object] | None = None
    result_chunks: list[dict[str, object]] = field(default_factory=list)
    inventory_after_reconnect: dict[str, Any] | None = None
    _next_sequence: int = 1

    async def command(
        self, socket: Any, generation: int, action: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        request_id = str(uuid4())
        envelope = ControlEnvelope(
            session_generation=generation,
            sequence=self._next_sequence,
            kind="command",
            payload={"id": request_id, "action": action, "payload": payload},
        )
        for part in fragment(envelope):
            await socket.send(part.to_json())
        self._next_sequence += len(list(fragment(envelope)))
        assembler = FragmentAssembler()
        while True:
            frame = ControlEnvelope.from_json(await socket.recv())
            assert frame.session_generation == generation
            frame = assembler.feed(frame)
            if frame is None:
                continue
            if frame.kind != "event" or frame.payload.get("reply") != request_id:
                continue
            assert "error" not in frame.payload, frame.payload
            result = frame.payload.get("result")
            if action in {"configure", "retired"}:
                return {}
            assert isinstance(result, dict), frame.payload
            return result

    async def hello(self, socket: Any, generation: int) -> Hello:
        # Sequence numbers are scoped to the fenced control generation.
        self._next_sequence = 1
        frame = json.loads(await socket.recv())
        assert frame.get("event") == "hello"
        signed = SignedHello.from_dict(frame["hello"])
        hello = verify_hello(self.secret, signed)
        assert hello.form == "external"
        assert hello.execution_lane == "normal"
        from shared.version import get_version
        await socket.send(json.dumps({"event": "welcome", "session_generation": generation, "api_version": get_version()}))
        await socket.send(ControlEnvelope(
            session_generation=generation,
            sequence=self._next_sequence,
            kind="bootstrap",
            payload={"requirements_content": None},
        ).to_json())
        self._next_sequence += 1
        return hello

    async def inventory(self, socket: Any, generation: int) -> dict[str, Any]:
        while True:
            frame = ControlEnvelope.from_json(await socket.recv())
            assert frame.session_generation == generation
            if frame.kind == "event" and frame.payload.get("event") == "inventory":
                inventory = frame.payload.get("inventory")
                assert isinstance(inventory, dict)
                return inventory


async def test_registered_preflight_survives_normal_launcher_reconnect(tmp_path: Path, async_session_factory) -> None:
    """Keep one real preflight stage through a fenced normal-launcher reconnect."""
    from websockets.asyncio.server import serve

    from sqlalchemy import delete, update, func, text
    from src.models.orm.execution_workers import ExecutionWorker
    from src.models.orm.platform_jobs import PlatformJob
    from src.jobs.platform.base import PlatformJobCancelled
    from src.jobs.schedulers.platform_jobs import ClaimedPlatformJob
    from src.worker.remote.file_compute_host import control_authorization
    from src.worker.remote.ownership import acquire_remote_owner

    secret = f"runtime-harness-{uuid4()}"
    binding = {
        "stage_id": str(uuid4()),
        "job_id": str(uuid4()),
        "lease_token": str(uuid4()),
    }
    # This source is intentionally lint-invalid so the result proves that the
    # registered child actually ran Ruff instead of merely returning a receipt.
    transfer = workspace_preflight_compute.encode_inputs({
        "sources": {"runtime_case.py": "import os\n" + "".join(f"value_{i:06d} = {i}\n" for i in range(150000))}, "configs": {},
    })
    trace = _ControlTrace(
        secret=secret,
        binding=binding,
        input_chunks=[chunk.to_dict() for chunk in transfer.iter_chunks()],
    )
    process: subprocess.Popen[bytes] | None = None

    finished = asyncio.get_running_loop().create_future()
    old_bridge = None
    new_bridge = None
    worker_id = None
    claim = ClaimedPlatformJob(UUID(binding["job_id"]), UUID(binding["lease_token"]), 60, .95, 1, "workspace.git")

    async def acquire(hello, owner):
        lease = await acquire_remote_owner(
            worker_id=hello.incarnation_id, owner_id=owner, launcher_id=hello.launcher_id,
            nonce_digest=hashlib.sha256(hello.nonce.encode()).hexdigest(),
            hello_sequence=hello.hello_sequence, lease_seconds=60,
        )
        assert lease is not None
        return SimpleNamespace(lease=lease)

    async def scenario(socket: Any) -> None:
        nonlocal old_bridge, new_bridge, worker_id
        if trace.initial_stage_pid is None:
            hello = await trace.hello(socket, 1)
            assert hello.hello_sequence == 1
            worker_id = hello.incarnation_id
            async with async_session_factory() as db:
                db.add(ExecutionWorker(id=worker_id, form="external", state="ready", resource_class="standard"))
                db.add(PlatformJob(id=claim.id, job_type="workspace.git", payload={}, status="running",
                    lease_token=claim.lease_token, lease_expires_at=datetime.now(timezone.utc)+timedelta(minutes=5),
                    title="Owned runtime fence fixture", requested_by_user_id=str(uuid4()),
                    requested_by_email="fixture@example.test", requested_by_name="Fixture"))
                await db.commit()
            old_bridge = await acquire(hello, uuid4())
            assert old_bridge.lease.session_generation == 1
            await trace.inventory(socket, 1)
            from src.config import Settings
            from src.worker.remote.remote_pool import DANGEROUS_MEMORY_RATIO
            settings = Settings()
            await trace.command(socket, 1, "configure", {
                "pressure_threshold": settings.memory_pressure_threshold,
                "dangerous_memory_ratio": DANGEROUS_MEMORY_RATIO,
            })
            prepared = await trace.command(
                socket,
                1,
                "file_compute_prepare",
                {**binding, "operation": workspace_preflight_compute.OPERATION,
                 "input": {"manifest": transfer.manifest.to_dict()}},
            )
            assert prepared["state"] == "prepared"
            for chunk in trace.input_chunks:
                accepted = await trace.command(
                    socket,
                    1,
                    "file_compute_chunk",
                    {**binding, "operation": workspace_preflight_compute.OPERATION, "chunk": chunk},
                )
                assert accepted["accepted"] is True
            started = await trace.command(
                socket,
                1,
                "file_compute_start",
                {**binding, "operation": workspace_preflight_compute.OPERATION},
            )
            assert started["state"] == "running"
            pid = started.get("pid")
            assert isinstance(pid, int) and pid > 0
            trace.initial_stage_pid = pid
            # The stage starts a new session.  Pausing its owned process group
            # gives the reconnect a deterministic in-flight physical child.
            os.killpg(pid, signal.SIGSTOP)
            async with control_authorization(old_bridge, claim):
                status = await trace.command(socket, 1, "file_compute_status",
                    {**binding, "operation": workspace_preflight_compute.OPERATION})
                assert status["pid"] == pid
            await socket.close()
            return

        hello = await trace.hello(socket, 2)
        assert hello.hello_sequence == 2
        assert hello.incarnation_id == worker_id
        async with async_session_factory() as db:
            await db.execute(update(ExecutionWorker).where(ExecutionWorker.id == worker_id).values(
                remote_owner_lease_expires_at=func.now()-text("INTERVAL '1 second'")))
            await db.commit()
        new_bridge = await acquire(hello, uuid4())
        assert new_bridge.lease.session_generation == 2
        assert new_bridge.lease.owner_id != old_bridge.lease.owner_id
        with pytest.raises(PlatformJobCancelled):
            async with control_authorization(old_bridge, claim):
                raise AssertionError("Stale owner entered the physical command boundary")
        trace.replacement_generation = 2
        inventory = await trace.inventory(socket, 2)
        trace.inventory_after_reconnect = inventory
        stages = inventory.get("compute_stages")
        assert isinstance(stages, list) and len(stages) == 1
        stage = stages[0]
        assert isinstance(stage, dict)
        assert {key: stage.get(key) for key in binding} == binding
        pid = stage.get("pid")
        assert isinstance(pid, int) and pid == trace.initial_stage_pid
        trace.replacement_stage_pid = pid
        os.killpg(pid, signal.SIGCONT)

        async def succeeded() -> dict[str, Any] | None:
            status = await trace.command(
                socket,
                2,
                "file_compute_status",
                {**binding, "operation": workspace_preflight_compute.OPERATION},
            )
            return status if status.get("state") == "succeeded" else None

        completed = await _wait_for(
            succeeded, timeout=30, description="resumed workspace preflight result"
        )
        manifest = completed.get("result_manifest")
        assert isinstance(manifest, dict)
        trace.result_manifest = manifest
        for index in range(int(manifest["chunk_count"])):
            result = await trace.command(
                socket,
                2,
                "file_compute_result_chunk",
                {**binding, "operation": workspace_preflight_compute.OPERATION, "index": index},
            )
            chunk = result.get("chunk")
            assert isinstance(chunk, dict)
            trace.result_chunks.append(chunk)
        async with control_authorization(new_bridge, claim):
            released = await trace.command(
                socket,
                2,
                "file_compute_release",
                {**binding, "operation": workspace_preflight_compute.OPERATION},
            )
        assert released["released"] is True
        await trace.command(socket, 2, "retired", {})
        finished.set_result(None)

    async def handler(socket: Any) -> None:
        try:
            await scenario(socket)
        except Exception as error:
            if not finished.done():
                finished.set_exception(error)
            await socket.close()

    log = (tmp_path / "launcher.log").open("wb")
    try:
        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            environment = {
                "PATH": os.environ["PATH"],
                "PYTHONPATH": os.environ.get("PYTHONPATH", "/app"),
                "HOME": str(tmp_path),
                "HOSTNAME": f"registered-preflight-{uuid4().hex[:8]}",
                "BIFROST_API_URL": f"http://127.0.0.1:{port}",
                "BIFROST_WORKER_CONTROL_SECRET": secret,
                "BIFROST_WORKER_FORM": "external",
                "BIFROST_WORKER_INCARNATION_ID": str(uuid4()),
                "BIFROST_WORKER_RESOURCE_UID": f"local-runtime-{uuid4()}",
            }
            process = subprocess.Popen(
                [sys.executable, "-m", "src.worker.remote.launcher"],
                env=environment,
                stdout=subprocess.DEVNULL,
                stderr=log,
                start_new_session=True,
            )
            await asyncio.wait_for(asyncio.shield(finished), timeout=60)
            returncode = await asyncio.to_thread(process.wait, 20)
            assert returncode == 0, (tmp_path / "launcher.log").read_text()

        assert trace.replacement_generation == 2
        assert trace.replacement_stage_pid == trace.initial_stage_pid
        assert trace.inventory_after_reconnect is not None
        assert trace.result_manifest is not None and trace.result_chunks
        from shared.worker_transfer import FileTransferChunk, FileTransferReceiver, FileTransferManifest

        manifest = FileTransferManifest.from_dict(
            trace.result_manifest, max_total_size=workspace_preflight_compute.MAX_RESULT_BYTES
        )
        with FileTransferReceiver(manifest) as receiver:
            for chunk in trace.result_chunks:
                receiver.add_chunk(FileTransferChunk.from_dict(chunk))
            with receiver.finish() as received:
                result = workspace_preflight_compute.decode_results(
                    received, digest=transfer.manifest.sha256, paths={"runtime_case.py"}
                )
        assert any(issue.category == "lint" and issue.path == "runtime_case.py" for issue in result)
    finally:
        transfer.close()
        if process is not None and process.poll() is None:
            if trace.initial_stage_pid is not None:
                try:
                    os.killpg(trace.initial_stage_pid, signal.SIGCONT)
                except ProcessLookupError:
                    pass
            process.terminate()
            await asyncio.to_thread(process.wait, 20)
        log.close()
        if worker_id is not None:
            async with async_session_factory() as db:
                await db.execute(delete(ExecutionWorker).where(ExecutionWorker.id == worker_id))
                await db.execute(delete(PlatformJob).where(PlatformJob.id == claim.id))
                await db.commit()
