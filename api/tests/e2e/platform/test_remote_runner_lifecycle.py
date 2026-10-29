"""Live remote-launcher recovery contracts.

The test runner cannot restart the API container, so owner-transfer fencing is
covered by focused database tests.  These tests instead run a physical
``Launcher`` in the test process against the live API, sever only its WebSocket,
and prove that its child process survives the reconnect without a second run.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select, update

from shared.policies.probe import make_seed_admin_bypass
from src.config import get_settings
from src.models.enums import ExecutionStatus
from src.models.orm.execution_workers import ExecutionWorker, ExecutionWorkerClaim
from src.models.orm.executions import Execution
from src.models.orm.tables import Document, Table
from src.worker.remote.identity import LauncherIdentity
from src.worker.remote.launcher import Launcher
from src.worker.remote.protocol import ControlEnvelope, FragmentAssembler


pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

_LARGE_RESULT_BYTES = 33 * 1024 * 1024


async def _wait_for(
    predicate: Callable[[], Awaitable[Any | None]], *, timeout: float, description: str
) -> Any:
    deadline = time.monotonic() + timeout
    last: Any = None
    while time.monotonic() < deadline:
        last = await predicate()
        if last is not None:
            return last
        await asyncio.sleep(0.1)
    raise AssertionError(f"Timed out waiting for {description}: {last!r}")


class _TestLauncher:
    """One physical launcher whose WebSocket can be deliberately severed."""

    def __init__(self, *, api_url: str, identity: LauncherIdentity) -> None:
        self._control_url = api_url.replace("http://", "ws://", 1).rstrip("/")
        self._control_url += "/api/worker-control"
        self._identity = identity
        self._launcher: Launcher | None = None
        self._socket: Any = None
        self._receiver: asyncio.Task[None] | None = None

    @property
    def generation(self) -> int:
        assert self._launcher is not None
        return self._launcher.session_generation

    async def connect(self) -> None:
        from websockets.asyncio.client import connect

        socket = await connect(self._control_url, max_size=2**20, open_timeout=10)
        try:
            if self._launcher is None:
                self._launcher = Launcher(
                    socket,
                    identity=self._identity,
                    secret=get_settings().secret_key,
                )
                await self._launcher.start()
                await self._launcher.hello(self._launcher.requirements)
            else:
                await self._launcher.reconnect(socket, self._launcher.requirements)
            await self._launcher.send(
                {"event": "inventory", "inventory": self._launcher.inventory()}
            )
            for event in self._launcher.pending_terminal.values():
                await self._launcher.send(event)
        except BaseException:
            await socket.close()
            raise
        self._socket = socket
        self._receiver = asyncio.create_task(self._receive(socket))

    async def disconnect_and_reconnect(
        self, *, before_reconnect: Callable[[], Awaitable[None]] | None = None
    ) -> None:
        """Drop transport only; the launcher and its physical children stay alive."""
        socket, receiver = self._socket, self._receiver
        assert socket is not None
        await socket.close()
        if receiver is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(receiver, timeout=5)
        if before_reconnect is not None:
            await before_reconnect()
        await self.connect()

    async def stop(self) -> None:
        if self._receiver is not None:
            self._receiver.cancel()
            await asyncio.gather(self._receiver, return_exceptions=True)
        if self._socket is not None:
            await self._socket.close()
        if self._launcher is not None:
            await self._launcher.stop()

    async def request_drain(self) -> None:
        """Tell the API owner to drain; fixture teardown owns local shutdown."""
        assert self._launcher is not None
        await self._launcher.send({"event": "draining"})

    async def _receive(self, socket: Any) -> None:
        assert self._launcher is not None
        assembler = FragmentAssembler()
        async for raw in socket:
            frame = ControlEnvelope.from_json(raw)
            if frame.session_generation != self._launcher.session_generation:
                raise RuntimeError("test launcher received a stale API control session")
            if frame.sequence <= self._launcher._last_command_sequence:
                raise RuntimeError("test launcher received a stale API control command")
            self._launcher._last_command_sequence = frame.sequence
            command = assembler.feed(frame)
            if command is None:
                continue
            if command.kind == "ack":
                event_id = command.payload.get("event_id")
                if isinstance(event_id, str):
                    self._launcher.pending_terminal.pop(event_id, None)
                    self._launcher.pending_terminal_inventory.pop(event_id, None)
                continue
            if command.kind != "command":
                raise RuntimeError("test launcher received an unexpected control message")
            self._launcher.task(
                self._launcher.command(
                    command.payload, generation=self._launcher.session_generation
                )
            )


@pytest.fixture
async def remote_launcher(async_session_factory, e2e_api_url):
    """Give this test one exclusive, live remote workflow executor."""
    identity = LauncherIdentity(
        launcher_id=uuid4(),
        incarnation_id=uuid4(),
        job_uid=None,
        pod_uid=None,
        hostname=f"e2e-remote-{uuid4().hex[:8]}",
        form="static",
    )
    removed_capabilities: dict[UUID, list[str]] = {}
    launcher = _TestLauncher(api_url=e2e_api_url, identity=identity)

    async def retired() -> bool | None:
        async with async_session_factory() as db:
            worker = await db.get(ExecutionWorker, identity.incarnation_id)
            if worker is None or worker.state == "retired":
                return True
        return None

    try:
        await launcher.connect()

        async def ready() -> bool | None:
            async with async_session_factory() as db:
                worker = await db.get(ExecutionWorker, identity.incarnation_id)
                if (
                    worker is not None
                    and worker.state == "ready"
                    and worker.remote_owner_id is not None
                    and worker.memory_budget_bytes is not None
                ):
                    return True
            return None

        await _wait_for(ready, timeout=30, description="test remote launcher readiness")

        async def stack_executors_ready() -> bool | None:
            async with async_session_factory() as db:
                count = await db.scalar(
                    select(func.count(ExecutionWorker.id)).where(
                        ExecutionWorker.id != identity.incarnation_id,
                        ExecutionWorker.form == "static",
                        ExecutionWorker.state == "ready",
                        ExecutionWorker.remote_owner_id.is_not(None),
                        ExecutionWorker.supported_handlers.op("?")("workflow.execute"),
                    )
                )
            # The E2E profile starts both scheduler and worker launchers.
            # Waiting for both rows closes the delayed-container-registration
            # window before this test removes their workflow capability.
            return True if count is not None and count >= 2 else None

        await _wait_for(
            stack_executors_ready,
            timeout=30,
            description="scheduler and worker remote executor registration",
        )

        # Narrow persisted workflow capability only after every normal
        # launcher is registered, giving this fixture an exclusive dispatcher
        # without racing a late stack launcher.
        async with async_session_factory() as db:
            workers = (
                await db.scalars(
                    select(ExecutionWorker).where(
                        ExecutionWorker.id != identity.incarnation_id,
                        ExecutionWorker.supported_handlers.op("?")("workflow.execute"),
                    )
                )
            ).all()
            for worker in workers:
                removed_capabilities[worker.id] = list(worker.supported_handlers)
                worker.supported_handlers = [
                    handler
                    for handler in worker.supported_handlers
                    if handler != "workflow.execute"
                ]
            await db.commit()
        yield launcher, identity.incarnation_id
    finally:
        try:
            try:
                # Use the supported transport event so the API-owned executor
                # begins its physical drain before the launcher socket closes.
                await launcher.request_drain()
                await _wait_for(retired, timeout=20, description="test remote launcher retirement")
            finally:
                await launcher.stop()
        finally:
            # Always give the stack's executors their workflow capability
            # back: a failed retirement must fail this test, not leave every
            # later workflow test without an executor.
            async with async_session_factory() as db:
                for worker_id, handlers in removed_capabilities.items():
                    worker = await db.get(ExecutionWorker, worker_id)
                    if worker is not None:
                        worker.supported_handlers = handlers
                await db.execute(
                    delete(ExecutionWorker).where(ExecutionWorker.id == identity.incarnation_id)
                )
                await db.commit()


async def _register_workflow(
    e2e_client, headers, *, source: str, function_name: str
) -> dict[str, Any]:
    from tests.e2e.conftest import write_and_register

    return write_and_register(
        e2e_client,
        headers,
        f"workflows/{function_name}.py",
        source,
        function_name,
    )


async def _start(e2e_client, headers, workflow_id: str) -> str:
    response = e2e_client.post(
        "/api/workflows/execute",
        headers=headers,
        json={"workflow_id": workflow_id, "input_data": {}},
    )
    assert response.status_code == 200, response.text
    execution_id = response.json().get("execution_id")
    assert isinstance(execution_id, str)
    return execution_id


async def _document_count(async_session_factory, table_id: UUID) -> int:
    async with async_session_factory() as db:
        return await db.scalar(
            select(func.count(Document.id)).where(Document.table_id == table_id)
        ) or 0


async def _execution(async_session_factory, execution_id: str) -> Execution | None:
    async with async_session_factory() as db:
        return await db.get(Execution, UUID(execution_id))


async def test_remote_reconnect_keeps_one_side_effect_and_delivers_a_large_result(
    async_session_factory,
    e2e_client,
    platform_admin,
    remote_launcher,
):
    launcher, worker_id = remote_launcher
    suffix = uuid4().hex
    table_id = uuid4()
    table_name = f"e2e_remote_lifecycle_{suffix}"
    function_name = f"e2e_remote_large_{suffix}"
    source = f'''from bifrost import tables, workflow
import time
import uuid

@workflow(name="{function_name}")
async def {function_name}():
    await tables.insert(
        "{table_name}", {{"marker": "{suffix}"}}, id=str(uuid.uuid4()), scope="global"
    )
    time.sleep(3)
    return {{"marker": "{suffix}", "payload": "x" * {_LARGE_RESULT_BYTES}}}
'''
    try:
        async with async_session_factory() as db:
            db.add(
                Table(
                    id=table_id,
                    name=table_name,
                    organization_id=None,
                    created_by="e2e",
                    # Directly seeded tables are default-deny unless they
                    # carry the same platform-admin policy the table API
                    # supplies for a new table. The workflow runs as this
                    # fixture's platform-admin caller.
                    access=make_seed_admin_bypass(),
                )
            )
            await db.commit()
        workflow = await _register_workflow(
            e2e_client, platform_admin.headers, source=source, function_name=function_name
        )
        execution_id = await _start(e2e_client, platform_admin.headers, workflow["id"])

        async def started_once() -> bool | None:
            if await _document_count(async_session_factory, table_id) != 1:
                return None
            async with async_session_factory() as db:
                claimed = await db.scalar(
                    select(ExecutionWorkerClaim.id).where(
                        ExecutionWorkerClaim.worker_id == worker_id,
                        ExecutionWorkerClaim.family == "workflow_execution",
                        ExecutionWorkerClaim.subject_id == UUID(execution_id),
                    )
                )
            return True if claimed is not None else None

        await _wait_for(started_once, timeout=30, description="one remote workflow side effect")
        first_generation = launcher.generation
        # Telemetry can expire while a recently renewed owner lease remains
        # valid. Keep that precise exposing condition deterministic: losing
        # the API owner must not retire the physical launcher's pending result.
        async with async_session_factory() as db:
            await db.execute(
                update(ExecutionWorker)
                .where(ExecutionWorker.id == worker_id)
                .values(remote_owner_lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=1))
            )
            await db.commit()

        async def wait_for_owner_release() -> None:
            async def released() -> bool | None:
                async with async_session_factory() as db:
                    worker = await db.get(ExecutionWorker, worker_id)
                    assert worker is not None
                    assert worker.state not in ("retired", "lost")
                    expiry = worker.remote_owner_lease_expires_at
                    if expiry is not None and expiry <= datetime.now(timezone.utc):
                        return True
                return None

            await _wait_for(released, timeout=40, description="expired telemetry releases its API owner")
            execution = await _execution(async_session_factory, execution_id)
            assert execution is not None and execution.status == ExecutionStatus.RUNNING

        await launcher.disconnect_and_reconnect(before_reconnect=wait_for_owner_release)
        assert launcher.generation > first_generation

        async def complete() -> Execution | None:
            execution = await _execution(async_session_factory, execution_id)
            if execution is not None and execution.status == ExecutionStatus.SUCCESS:
                return execution
            return None

        execution = await _wait_for(complete, timeout=90, description="large remote result")
        assert execution.result == {"marker": suffix, "payload": "x" * _LARGE_RESULT_BYTES}
        assert await _document_count(async_session_factory, table_id) == 1
    finally:
        async with async_session_factory() as db:
            await db.execute(delete(Table).where(Table.id == table_id))
            await db.commit()
        e2e_client.delete(
            f"/api/files/editor?path=workflows/{function_name}.py",
            headers=platform_admin.headers,
        )


async def test_remote_reconnect_preserves_cancellation_of_the_original_child(
    async_session_factory,
    e2e_client,
    platform_admin,
    remote_launcher,
):
    launcher, _worker_id = remote_launcher
    suffix = uuid4().hex
    table_id = uuid4()
    table_name = f"e2e_remote_cancel_{suffix}"
    function_name = f"e2e_remote_cancel_{suffix}"
    source = f'''from bifrost import tables, workflow
import time
import uuid

@workflow(name="{function_name}")
async def {function_name}():
    await tables.insert(
        "{table_name}", {{"marker": "{suffix}"}}, id=str(uuid.uuid4()), scope="global"
    )
    time.sleep(30)
    return {{"unexpected": True}}
'''
    try:
        async with async_session_factory() as db:
            db.add(
                Table(
                    id=table_id,
                    name=table_name,
                    organization_id=None,
                    created_by="e2e",
                    access=make_seed_admin_bypass(),
                )
            )
            await db.commit()
        workflow = await _register_workflow(
            e2e_client, platform_admin.headers, source=source, function_name=function_name
        )
        execution_id = await _start(e2e_client, platform_admin.headers, workflow["id"])
        await _wait_for(
            lambda: _one_document(async_session_factory, table_id),
            timeout=30,
            description="cancellable remote workflow start",
        )
        await launcher.disconnect_and_reconnect()
        cancelled = e2e_client.post(
            f"/api/executions/{execution_id}/cancel", headers=platform_admin.headers
        )
        assert cancelled.status_code == 200, cancelled.text

        async def terminal() -> Execution | None:
            execution = await _execution(async_session_factory, execution_id)
            if execution is not None and execution.status == ExecutionStatus.CANCELLED:
                return execution
            return None

        await _wait_for(terminal, timeout=60, description="remote workflow cancellation")
        assert await _document_count(async_session_factory, table_id) == 1
    finally:
        async with async_session_factory() as db:
            await db.execute(delete(Table).where(Table.id == table_id))
            await db.commit()
        e2e_client.delete(
            f"/api/files/editor?path=workflows/{function_name}.py",
            headers=platform_admin.headers,
        )


async def _one_document(async_session_factory, table_id: UUID) -> bool | None:
    return True if await _document_count(async_session_factory, table_id) == 1 else None
