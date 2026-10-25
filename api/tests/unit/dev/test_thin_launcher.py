"""OS supervisor contracts that must survive the footprint split."""
import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import uuid4
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.worker.remote.launcher import (
    Child,
    Launcher,
    _shutdown_launcher,
    _next_control_frame,
    _receive_control_commands,
    run_launcher,
)
from src.worker.remote.identity import LauncherIdentity
from src.worker.remote.protocol import ControlEnvelope


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
