"""Legacy wire-boundary characterization; no provider/SDK compatibility claim.

An opaque-handle field below is intentionally NOT an existing upstream API.
No physical work starts. Transport/Redis doubles isolate the current adapter's
ACK classification so a future coordinated change preserves Linux behavior.
"""
from unittest.mock import AsyncMock, patch

import pytest

from src.worker.remote.remote_pool import ThinRemoteProcessPool
from tests.unit.dev.test_thin_remote_pool import _Transport, _active


@pytest.mark.asyncio
@pytest.mark.parametrize("ack", [{"accepted": True, "runtime_handle": "unit-only-isolate"}, {"pid": True}])
async def test_legacy_non_pid_ack_is_rejected_after_send_and_late_result_is_untracked(ack: dict) -> None:
    transport = _Transport()
    transport.request = AsyncMock(return_value=ack)
    callback = AsyncMock()
    redis = AsyncMock()
    pool = ThinRemoteProcessPool(transport, on_result=callback)
    with (
        patch.object(pool, "_write_context_to_redis", new_callable=AsyncMock),
        patch.object(pool, "_write_active_execution_lease", new_callable=AsyncMock),
        patch.object(pool, "_get_redis", new=AsyncMock(return_value=redis)),
    ):
        with pytest.raises(RuntimeError, match="valid PID"):
            await pool.route_execution("exec-boundary", {"timeout_seconds": 30}, _active("exec-boundary"))
    transport.request.assert_awaited_once()
    route = transport.request.await_args.args[1]["route_id"]
    assert pool.processes == {}
    redis.delete.assert_awaited_once()
    await pool.handle_event({"event": "result", "route_id": route,
                            "result": {"execution_id": "exec-boundary", "success": True}})
    callback.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [{"pid": 12345}, ConnectionError("unit-only ACK loss")])
async def test_legacy_positive_pid_or_lost_ack_retains_route_for_result(response: dict | ConnectionError) -> None:
    transport = _Transport()
    transport.request = (AsyncMock(side_effect=response) if isinstance(response, Exception)
                         else AsyncMock(return_value=response))
    callback = AsyncMock()
    pool = ThinRemoteProcessPool(transport, on_result=callback)
    with (
        patch.object(pool, "_write_context_to_redis", new_callable=AsyncMock),
        patch.object(pool, "_write_active_execution_lease", new_callable=AsyncMock),
    ):
        await pool.route_execution("exec-boundary", {"timeout_seconds": 30}, _active("exec-boundary"))
    assert len(pool.processes) == 1
    route = transport.request.await_args.args[1]["route_id"]
    await pool.handle_event({"event": "result", "route_id": route,
                            "result": {"execution_id": "exec-boundary", "success": True}})
    callback.assert_awaited_once()
    assert pool.processes == {}
