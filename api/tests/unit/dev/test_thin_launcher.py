"""OS supervisor contracts that must survive the footprint split."""
import asyncio
import json
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.dev.thin_launcher import Child, Launcher


@pytest.mark.asyncio
async def test_timeout_owns_result_after_deadline_was_observed():
    socket = Mock(send=AsyncMock())
    launcher = Launcher(socket)
    child = Child("route", "execution", 123, Mock(), Mock(), 0, 10)
    child.result.fileno.return_value = 456
    child.terminal_reason = ("TimeoutError", "Execution timed out")
    launcher.children[child.route_id] = child
    with patch.object(asyncio.get_running_loop(), "remove_reader"):
        await launcher.finish(child, {"success": True, "status": "Success"})
    event = json.loads(socket.send.await_args.args[0])
    assert event["result"]["error_type"] == "TimeoutError"
    assert event["result"]["success"] is False
    await launcher.finish(child, {"success": True})
    socket.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_monitor_closes_control_connection():
    socket = Mock(close=AsyncMock())
    launcher = Launcher(socket)
    async def failed_monitor():
        raise RuntimeError("Template died")
    launcher.task(failed_monitor())
    # Flush the task and its done callback deterministically.
    await asyncio.gather(*launcher._tasks, return_exceptions=True)
    await asyncio.sleep(0)
    socket.close.assert_awaited_once_with(code=1011, reason="Launcher supervision failed")


@pytest.mark.asyncio
async def test_job_exit_cleans_route_when_transport_has_disconnected():
    launcher = Launcher(Mock(send=AsyncMock(side_effect=ConnectionError)))
    process = Mock(wait=AsyncMock(return_value=0))
    launcher.jobs["job"] = process
    with pytest.raises(ConnectionError):
        await launcher.wait_job("job", process)
    assert launcher.jobs == {}


@pytest.mark.asyncio
async def test_memory_pressure_stops_only_the_largest_growing_child_at_the_danger_threshold():
    launcher = Launcher(Mock())
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
