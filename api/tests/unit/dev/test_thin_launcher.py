"""OS supervisor contracts that must survive the footprint split."""
import asyncio
import json
import multiprocessing
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.services.execution.template_process import _RecvQueue, _SendQueue
from src.worker.remote.launcher import (
    Child,
    Launcher,
    ProcStat,
    _shutdown_launcher,
    _next_control_frame,
    _receive_control_commands,
    run_launcher,
)
from src.worker.remote.identity import LauncherIdentity
from src.worker.remote.protocol import ControlEnvelope


def _live_template() -> Mock:
    return Mock(is_alive=Mock(return_value=True), collect_child_exit_statuses=Mock(return_value={}))


def _launcher(socket):
    launcher = Launcher(
        socket,
        identity=LauncherIdentity(
            launcher_id=uuid4(), incarnation_id=uuid4(),
            job_uid=None, pod_uid=None, hostname="thin-1", form="static",
        ),
        secret="test-secret",
    )
    launcher.session_generation = 1
    return launcher


@pytest.mark.asyncio
async def test_timeout_owns_result_after_deadline_was_observed():
    socket = Mock(send=AsyncMock())
    launcher = _launcher(socket)
    child = Child("route", "execution", 123, Mock(), Mock(), 0, 10)
    child.result.fileno.return_value = 456
    child.terminal_reason = ("TimeoutError", "Execution timed out")
    launcher.children[child.route_id] = child
    with patch.object(asyncio.get_running_loop(), "remove_reader"):
        await launcher.finish(child, {"success": True, "status": "Success"})
    event = json.loads(socket.send.await_args.args[0])
    assert event["payload"]["result"]["error_type"] == "TimeoutError"
    assert event["payload"]["result"]["success"] is False
    await launcher.finish(child, {"success": True})
    socket.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_template_replacement_marks_launcher_failed_and_closes_connection():
    socket = Mock(close=AsyncMock())
    launcher = _launcher(socket)
    launcher.on_stop = Mock()
    launcher.template = Mock(
        is_alive=Mock(return_value=False), start=Mock(side_effect=RuntimeError("Template failed")),
    )
    launcher._monitor_task = launcher.task(launcher.monitor())
    # Flush the task and its done callback deterministically.
    await asyncio.gather(*launcher._tasks, return_exceptions=True)
    await asyncio.sleep(0)
    socket.close.assert_awaited_once_with(code=1011, reason="Launcher supervision failed")
    assert launcher.failed
    launcher.on_stop.assert_called_once_with()


@pytest.mark.asyncio
async def test_template_crash_replacement_retains_live_child_handles() -> None:
    launcher = _launcher(Mock())
    launcher.template = Mock(is_alive=Mock(return_value=False))
    launcher.template.start.side_effect = lambda: setattr(launcher.template.is_alive, "return_value", True)
    launcher.agent_template = _live_template()
    child = Child("route", "execution", 123, Mock(), Mock(), 0, 0)
    child.sampler = Mock(last_process_rss_bytes=1, peak_cpu_cores=0)
    launcher.children[child.route_id] = child
    sampled = asyncio.Event()

    async def send(_payload):
        sampled.set()

    launcher.send = AsyncMock(side_effect=send)
    with (
        patch("src.worker.remote.launcher.effective_memory_sample", return_value=(1, 100)),
        patch("src.worker.remote.launcher.memory_pressure_ratio", return_value=.1),
    ):
        task = asyncio.create_task(launcher.monitor())
        try:
            await asyncio.wait_for(sampled.wait(), timeout=1)
            launcher.template.start.assert_called_once_with()
            assert launcher.children == {child.route_id: child}
            assert not launcher.failed
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_fatal_launcher_exits_instead_of_reconnecting() -> None:
    closed = asyncio.Event()
    socket = Mock(close=AsyncMock(side_effect=lambda **_kwargs: closed.set()))
    launcher = _launcher(socket)
    launcher.template = Mock(
        is_alive=Mock(return_value=False), start=Mock(side_effect=RuntimeError("Template failed")),
    )

    async def start():
        launcher._monitor_task = launcher.task(launcher.monitor())

    launcher.start = AsyncMock(side_effect=start)
    launcher.hello, launcher.send, launcher.stop = (AsyncMock() for _ in range(3))
    launcher.inventory = Mock(return_value={})

    @asynccontextmanager
    async def connect(*_args, **_kwargs):
        yield socket

    async def receive(*_args):
        await closed.wait()
        raise ConnectionError("monitor failed")

    with (
        patch("websockets.asyncio.client.connect", new=connect),
        patch("src.worker.remote.launcher.Launcher", return_value=launcher) as factory,
        patch("src.worker.remote.launcher.load_launcher_identity", return_value=launcher.identity),
        patch("src.worker.remote.launcher._receive_control_commands", new=receive),
        patch("src.worker.remote.launcher.asyncio.sleep", new=AsyncMock(side_effect=AssertionError("must not reconnect"))),
        patch.object(asyncio.get_running_loop(), "add_signal_handler"),
        patch.dict("os.environ", {"BIFROST_SECRET_KEY": "test-secret"}),
    ):
        assert await run_launcher() == 1

    factory.assert_called_once()
    assert launcher.failed
    socket.close.assert_awaited_once_with(code=1011, reason="Launcher supervision failed")
    launcher.stop.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_monitor_waits_for_intentional_template_replacement() -> None:
    launcher = _launcher(Mock())
    launcher.template = Mock(is_alive=Mock(return_value=False))
    launcher.agent_template = _live_template()
    sampled = asyncio.Event()
    entered = asyncio.Event()

    async def send(_payload):
        sampled.set()

    async def monitor():
        entered.set()
        await launcher.monitor()

    launcher.send = AsyncMock(side_effect=send)
    # Recycling owns this lock while the old template is stopped and the new
    # one has not yet started. That interval must not look like a crash.
    await launcher._template_lock.acquire()
    task = asyncio.create_task(monitor())
    try:
        await entered.wait()
        assert not task.done()
        launcher.template.is_alive.assert_not_called()
        launcher.template.is_alive.return_value = True
        launcher._template_lock.release()
        with (
            patch("src.worker.remote.launcher.effective_memory_sample", return_value=(1, 100)),
            patch("src.worker.remote.launcher.memory_pressure_ratio", return_value=.1),
        ):
            await asyncio.wait_for(sampled.wait(), timeout=1)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        if launcher._template_lock.locked():
            launcher._template_lock.release()


@pytest.mark.asyncio
async def test_job_exit_cleans_route_when_transport_has_disconnected():
    launcher = _launcher(Mock(send=AsyncMock(side_effect=ConnectionError)))
    process = Mock(wait=AsyncMock(return_value=0))
    launcher.jobs["job"] = process
    with pytest.raises(ConnectionError):
        await launcher.wait_job("job", process)
    assert launcher.jobs == {}


@pytest.mark.asyncio
async def test_terminal_datetime_result_replays_after_reconnect() -> None:
    socket = Mock(send=AsyncMock())
    launcher = _launcher(socket)
    created_at = datetime(2026, 10, 25, 12, 34, 56, tzinfo=timezone.utc)

    await launcher.send_terminal(
        {
            "event": "result",
            "route_id": "route-1",
            "result": {"metadata": {"created_at": created_at}},
        },
        inventory={"route_id": "route-1", "kind": "workflow", "subject_id": "execution-1"},
    )
    pending = next(iter(launcher.pending_terminal.values()))
    await launcher.send(pending)

    sent = [ControlEnvelope.from_json(call.args[0]).payload for call in socket.send.await_args_list]
    assert [payload["event"] for payload in sent] == ["result", "result"]
    assert all(
        payload["result"]["metadata"]["created_at"] == str(created_at)
        for payload in sent
    )


@pytest.mark.asyncio
async def test_memory_pressure_stops_only_the_largest_growing_child_at_the_danger_threshold():
    launcher = _launcher(Mock())
    launcher.dangerous_memory_ratio = .95
    launcher.terminate = AsyncMock()
    small = Child("small", "a", 1, Mock(), Mock(), 0, 0, current_growth=10)
    large = Child("large", "b", 2, Mock(), Mock(), 0, 0, current_growth=20)
    launcher.children = {"small": small, "large": large}
    launcher.stop_largest_when_dangerous(94, 100)
    launcher.terminate.assert_not_called()
    launcher.stop_largest_when_dangerous(95, 100)
    await asyncio.gather(*launcher._tasks)
    launcher.terminate.assert_awaited_once_with(
        "large", "MemoryLimitExceeded", "Runner memory pressure", grace=0,
    )
    assert small.terminal_reason is None


def test_inventory_describes_only_physical_routes_and_terminal_replay_state() -> None:
    launcher = _launcher(Mock())
    launcher.baseline_bytes = 80
    launcher.children["workflow-route"] = Child(
        "workflow-route", "execution-1", 4321, Mock(), Mock(), 0, 30,
    )
    launcher.jobs["job-route"] = Mock(pid=5432)
    launcher.job_claims["job-route"] = {
        "job_id": "job-1", "lease_token": "opaque", "memory_request_bytes": 42,
    }
    launcher.pending_terminal_inventory["event-1"] = {
        "event_id": "event-1", "route_id": "finished-route", "kind": "workflow",
        "subject_id": "execution-finished", "active_execution": {"sync": True},
        "started_at": "2026-10-25T00:00:00+00:00", "timeout_seconds": 30,
        "memory_request_bytes": 13,
    }

    inventory = launcher.inventory()

    assert inventory["version"] == 1
    assert inventory["baseline_bytes"] == 80
    assert inventory["routes"] == [
        {
            "route_id": "workflow-route", "kind": "workflow", "subject_id": "execution-1",
            "pid": 4321, "service": None, "active_execution": None,
            "started_at": None, "timeout_seconds": 30, "memory_request_bytes": None,
        },
        {
            "route_id": "job-route", "kind": "platform_job", "subject_id": "job-1",
            "pid": 5432, "lease_token": "opaque", "memory_request_bytes": 42,
        },
    ]
    assert inventory["terminal_routes"] == [launcher.pending_terminal_inventory["event-1"]]


@pytest.mark.asyncio
async def test_stop_signal_unblocks_a_healthy_control_receive() -> None:
    waiting = asyncio.Event()

    class Socket:
        async def recv(self):
            await waiting.wait()
            return "unexpected"

    stopping = asyncio.Event()
    receive = asyncio.create_task(_next_control_frame(Socket(), stopping))
    await asyncio.sleep(0)
    stopping.set()

    assert await receive is None


@pytest.mark.asyncio
async def test_retired_command_unblocks_a_healthy_control_receive_during_drain() -> None:
    waiting = asyncio.Event()

    class Socket:
        async def recv(self):
            await waiting.wait()
            return "unexpected"

    stopping = asyncio.Event()
    retired = asyncio.Event()
    drain = asyncio.create_task(asyncio.sleep(60))
    receive = asyncio.create_task(_next_control_frame(Socket(), stopping, retired, drain))
    await asyncio.sleep(0)
    retired.set()

    assert await receive is None
    drain.cancel()
    await asyncio.gather(drain, return_exceptions=True)


@pytest.mark.asyncio
async def test_retired_command_unblocks_a_healthy_control_receive_without_drain() -> None:
    waiting = asyncio.Event()

    class Socket:
        async def recv(self):
            await waiting.wait()
            return "unexpected"

    stopping = asyncio.Event()
    retired = asyncio.Event()
    receive = asyncio.create_task(_next_control_frame(Socket(), stopping, retired))
    await asyncio.sleep(0)
    retired.set()

    assert await receive is None


@pytest.mark.asyncio
async def test_control_loop_keeps_receiving_retirement_while_signal_drain_runs() -> None:
    gate = asyncio.Event()
    drain_started = asyncio.Event()
    retired = ControlEnvelope(
        session_generation=1,
        sequence=1,
        kind="command",
        payload={"id": "retire", "action": "retired", "payload": {}},
    ).to_json()

    class Socket:
        def __init__(self):
            self.frames = [retired]
            self.sent: list[str] = []

        async def recv(self):
            if self.frames:
                await drain_started.wait()
                return self.frames.pop(0)
            await gate.wait()
            return "unexpected"

        async def send(self, frame):
            self.sent.append(frame)

    socket = Socket()
    launcher = _launcher(socket)
    stopping = asyncio.Event()
    stopping.set()

    async def drain():
        drain_started.set()
        await gate.wait()

    launcher.begin_drain = drain
    drain_task = await _receive_control_commands(launcher, socket, stopping)

    assert launcher.retired.is_set()
    assert drain_task is not None
    assert not drain_task.done()
    gate.set()
    await asyncio.gather(drain_task)


@pytest.mark.asyncio
async def test_shutdown_after_disconnect_starts_one_bounded_drain_before_stop() -> None:
    gate = asyncio.Event()
    drain_started = asyncio.Event()
    launcher = Mock(stop=AsyncMock())

    async def begin_drain():
        drain_started.set()
        await gate.wait()

    launcher.begin_drain = AsyncMock(side_effect=begin_drain)
    stopping = asyncio.Event()
    stopping.set()
    shutdown = asyncio.create_task(_shutdown_launcher(launcher, None, stopping))
    await drain_started.wait()

    launcher.begin_drain.assert_awaited_once_with()
    assert launcher.stop.await_count == 0
    gate.set()
    await asyncio.gather(shutdown)
    launcher.stop.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_shutdown_reuses_the_existing_drain_task() -> None:
    gate = asyncio.Event()
    launcher = Mock(begin_drain=AsyncMock(), stop=AsyncMock())
    stopping = asyncio.Event()
    stopping.set()
    drain = asyncio.create_task(gate.wait())
    shutdown = asyncio.create_task(_shutdown_launcher(launcher, drain, stopping))
    await asyncio.sleep(0)

    assert launcher.begin_drain.await_count == 0
    gate.set()
    await asyncio.gather(shutdown)
    launcher.stop.assert_awaited_once_with()


class _FakeTemplate:
    """A template stand-in whose forks are real pipes to this test."""

    def __init__(self, profile: str = "workflow") -> None:
        self.profile = profile
        self.alive = False
        self.starts = 0
        self.shutdowns = 0
        self.forks: list[tuple[str, Any, Any]] = []

    def start(self) -> None:
        self.alive = True
        self.starts += 1

    def is_alive(self) -> bool:
        return self.alive

    def shutdown(self) -> None:
        self.alive = False
        self.shutdowns += 1

    def fork(self, worker_id="worker", persistent=False, *, sdk_socket_path=None):
        work_recv, work_send = multiprocessing.Pipe(duplex=False)
        result_recv, result_send = multiprocessing.Pipe(duplex=False)
        self.forks.append((worker_id, work_recv, result_send))
        return os.getpid(), _SendQueue(work_send), _RecvQueue(result_recv)

    def collect_child_exit_statuses(self) -> dict[int, int]:
        return {}


def _agent_launcher(socket) -> Launcher:
    launcher = _launcher(socket)
    launcher.pressure_threshold, launcher.dangerous_memory_ratio = .85, .95
    launcher.agent_template.start()
    return launcher


def _sent(socket) -> list[dict]:
    return [ControlEnvelope.from_json(call.args[0]).payload for call in socket.send.await_args_list]


async def _until(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "condition not reached"
        await asyncio.sleep(.01)


def _agent_run_command(route_id: str = "agent-route", run_id: str = "run-1") -> dict:
    return {"id": f"cmd-{route_id}", "action": "agent_run", "payload": {
        "route_id": route_id, "run_id": run_id,
        "message": {"run_id": run_id, "agent_id": "agent-1"},
        "memory_request_bytes": 32 * 1024 * 1024,
    }}


@pytest.fixture
def agent_templates():
    created: list[_FakeTemplate] = []

    def factory(profile: str = "workflow") -> _FakeTemplate:
        # Records agent templates only; every Launcher also builds a workflow one.
        template = _FakeTemplate(profile)
        if profile == "agent":
            created.append(template)
        return template

    with (
        patch("src.worker.remote.launcher.TemplateProcess", side_effect=factory),
        patch("src.worker.remote.launcher.effective_memory_sample", return_value=(10, 100)),
        patch("src.worker.remote.launcher.memory_pressure_ratio", return_value=.1),
    ):
        yield created


@pytest.mark.asyncio
async def test_launcher_loads_both_templates_before_measuring_its_baseline(agent_templates) -> None:
    launcher = _launcher(Mock(send=AsyncMock()))
    launcher.helper = AsyncMock(return_value={})  # type: ignore[method-assign]
    loaded_at_baseline: list[bool] = []

    def sample() -> tuple[int, int]:
        loaded_at_baseline.append(launcher.template.is_alive() and launcher.agent_template.is_alive())
        return (10, 100)

    with patch("src.worker.remote.launcher.effective_memory_sample", side_effect=sample):
        await launcher.start()
    try:
        assert [template.profile for template in agent_templates] == ["agent"]
        assert launcher.agent_template.starts == 1
        assert loaded_at_baseline[0] is True
        assert launcher.baseline_bytes == 10
    finally:
        await launcher.stop()


@pytest.mark.asyncio
async def test_agent_run_forks_from_the_agent_template_and_reports_agent_exit(agent_templates) -> None:
    socket = Mock(send=AsyncMock())
    launcher = _agent_launcher(socket)
    command = _agent_run_command()

    await launcher.command(command, generation=1)

    template = launcher.agent_template
    assert template is agent_templates[0] and template.profile == "agent" and template.starts == 1
    route_id, work_recv, result_send = template.forks[0]
    assert route_id == "agent-route"
    assert work_recv.recv() == {"message": command["payload"]["message"]}
    assert _sent(socket)[0] == {"reply": "cmd-agent-route", "result": {"pid": os.getpid()}}
    assert launcher.inventory()["routes"] == [{
        "route_id": "agent-route", "kind": "agent_run", "subject_id": "run-1",
        "pid": os.getpid(), "memory_request_bytes": 32 * 1024 * 1024,
    }]

    result_send.send({"ok": True})
    await _until(lambda: launcher.pending_terminal)

    exit_event = _sent(socket)[-1]
    assert {key: exit_event[key] for key in ("event", "route_id", "ok", "error_type", "error")} == {
        "event": "agent_exit", "route_id": "agent-route", "ok": True,
        "error_type": None, "error": None,
    }
    assert list(launcher.pending_terminal_inventory.values()) == [{
        "event_id": exit_event["_event_id"], "route_id": "agent-route", "kind": "agent_run",
        "subject_id": "run-1", "memory_request_bytes": 32 * 1024 * 1024,
    }]
    assert launcher.children == {}


@pytest.mark.asyncio
async def test_agent_child_that_dies_without_a_result_reports_a_crash(agent_templates) -> None:
    socket = Mock(send=AsyncMock())
    launcher = _agent_launcher(socket)
    await launcher.command(_agent_run_command(), generation=1)

    _, _, result_send = launcher.agent_template.forks[0]
    result_send.close()
    await _until(lambda: launcher.pending_terminal)

    exit_event = _sent(socket)[-1]
    assert exit_event["event"] == "agent_exit"
    assert exit_event["ok"] is False
    assert exit_event["error_type"] == "ProcessCrashError"


@pytest.mark.asyncio
async def test_terminated_agent_child_reports_the_termination_reason(agent_templates) -> None:
    socket = Mock(send=AsyncMock())
    launcher = _agent_launcher(socket)
    await launcher.command(_agent_run_command(), generation=1)

    # The fake child's pid is this test process: never deliver the signal.
    with patch("src.worker.remote.launcher.os.kill") as kill:
        await launcher.terminate("agent-route", "MemoryLimitExceeded", "Runner memory pressure", grace=0)

    assert kill.call_count == 2
    exit_event = _sent(socket)[-1]
    assert (exit_event["event"], exit_event["ok"], exit_event["error_type"]) == (
        "agent_exit", False, "MemoryLimitExceeded",
    )
    assert launcher.children == {}


@pytest.mark.asyncio
async def test_monitor_replaces_a_dead_agent_template(agent_templates) -> None:
    socket = Mock(send=AsyncMock())
    launcher = _agent_launcher(socket)
    launcher.template = _FakeTemplate()
    launcher.template.start()
    launcher.agent_template.alive = False

    monitor = asyncio.create_task(launcher.monitor())
    try:
        await _until(lambda: launcher.agent_template.alive)
    finally:
        monitor.cancel()
        await asyncio.gather(monitor, return_exceptions=True)
    assert launcher.agent_template.starts == 2
    assert launcher.agent_template.shutdowns == 0


@pytest.mark.asyncio
async def test_recycle_restarts_only_the_workflow_template(agent_templates) -> None:
    socket = Mock(send=AsyncMock())
    launcher = _agent_launcher(socket)
    launcher.template = _FakeTemplate()
    launcher.template.start()
    await launcher.command(_agent_run_command(), generation=1)
    agent_template = launcher.agent_template

    with patch("src.worker.remote.launcher.os.kill") as kill:
        await asyncio.wait_for(
            launcher.command({"id": "recycle", "action": "recycle", "payload": {}}, generation=1),
            timeout=2,
        )

    kill.assert_not_called()
    assert list(launcher.children) == ["agent-route"]
    assert (launcher.template.shutdowns, launcher.template.starts) == (1, 2)
    assert (agent_template.shutdowns, agent_template.starts) == (0, 1)
    assert launcher.agent_template is agent_template


@pytest.mark.asyncio
async def test_terminate_treats_a_zombie_agent_child_as_exited(agent_templates) -> None:
    socket = Mock(send=AsyncMock())
    launcher = _agent_launcher(socket)
    await launcher.command(_agent_run_command(), generation=1)

    # An orphaned child that has exited stays in /proc as a zombie until it
    # is reaped; the grace period must not be spent waiting on it.
    with (
        patch("src.worker.remote.launcher.os.kill"),
        patch("src.worker.remote.launcher.proc_stat", return_value=ProcStat("Z", 1, 7)),
    ):
        await asyncio.wait_for(
            launcher.terminate("agent-route", "CancelledError", "Cancelled", grace=30), timeout=1,
        )

    assert _sent(socket)[-1]["error_type"] == "CancelledError"
    assert launcher.children == {}


@pytest.mark.asyncio
async def test_memory_pressure_can_stop_an_agent_child():
    launcher = _launcher(Mock())
    launcher.dangerous_memory_ratio = .95
    launcher.terminate = AsyncMock()
    workflow = Child("workflow", "a", 1, Mock(), Mock(), 0, 0, current_growth=10)
    agent = Child("agent", "run-1", 2, Mock(), Mock(), 0, 0, current_growth=20, agent_run=True)
    launcher.children = {"workflow": workflow, "agent": agent}

    launcher.stop_largest_when_dangerous(95, 100)
    await asyncio.gather(*launcher._tasks)

    launcher.terminate.assert_awaited_once_with(
        "agent", "MemoryLimitExceeded", "Runner memory pressure", grace=0,
    )
