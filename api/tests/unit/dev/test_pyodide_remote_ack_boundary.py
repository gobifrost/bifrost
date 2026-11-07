"""Uncertain launch ACK contracts; no provider/SDK compatibility claim.

An opaque-handle field below is intentionally NOT an existing upstream API.
No physical work starts. Transport/Redis doubles isolate the current adapter's
ACK classification so a future coordinated change preserves Linux behavior.
"""
from unittest.mock import AsyncMock, patch

import pytest

from src.worker.remote.remote_pool import ThinRemoteProcessPool
from tests.unit.dev.test_thin_remote_pool import _Transport, _active


@pytest.mark.asyncio
@pytest.mark.parametrize("ack", [{"accepted": True, "runtime_handle": "unit-only-isolate"}, {"pid": True},
                                 {"pid": 0}, {"pid": -1}, {"pid": "12345"}, None, []])
async def test_malformed_ack_retains_uncertain_route_and_delivers_late_result_once(ack: object) -> None:
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
        await pool.route_execution("exec-boundary", {"timeout_seconds": 30}, _active("exec-boundary"))
    transport.request.assert_awaited_once()
    route = transport.request.await_args.args[1]["route_id"]
    assert len(pool.processes) == 1
    assert pool.processes[route].process.remote_pid is None
    redis.delete.assert_not_awaited()
    event = {"event": "result", "route_id": route,
             "result": {"execution_id": "exec-boundary", "success": True}}
    await pool.handle_event(event)
    await pool.handle_event(event)
    callback.assert_awaited_once()
    assert pool.processes == {}


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


@pytest.mark.asyncio
async def test_malformed_service_ack_retains_route_and_delivers_result_once() -> None:
    transport = _Transport()
    transport.request = AsyncMock(return_value={"pid": False})
    callback = AsyncMock()
    pool = ThinRemoteProcessPool(transport, on_service_result=callback)
    context = {"execution_id": "attempt-boundary", "service": {
        "service_id": "00000000-0000-4000-8000-000000000001", "attempt_id": "attempt-boundary", "lease_token": "lease-boundary"}}
    arguments = {"service_id": context["service"]["service_id"], "attempt_id": "attempt-boundary",
                 "lease_token": "lease-boundary", "context": context}
    await pool.route_service(**arguments)
    route = transport.request.await_args.args[1]["route_id"]
    assert pool.service_processes[route].process.remote_pid is None
    event = {"event": "result", "route_id": route, "result": {"success": True}}
    await pool.handle_event(event)
    await pool.handle_event(event)
    callback.assert_awaited_once()
    transport.request.assert_awaited_once()
    assert pool.service_processes == {}


@pytest.mark.asyncio
async def test_explicit_launcher_error_still_rejects_and_cleans_workflow_route() -> None:
    transport = _Transport()
    transport.request = AsyncMock(side_effect=RuntimeError("Rejected before fork"))
    pool = ThinRemoteProcessPool(transport)
    redis = AsyncMock()
    with (patch.object(pool, "_write_context_to_redis", new_callable=AsyncMock),
          patch.object(pool, "_write_active_execution_lease", new_callable=AsyncMock),
          patch.object(pool, "_get_redis", new=AsyncMock(return_value=redis))):
        with pytest.raises(RuntimeError, match="Rejected before fork"):
            await pool.route_execution("exec-rejected", {"timeout_seconds": 30}, _active("exec-rejected"))
    assert pool.processes == {}
    redis.delete.assert_awaited_once()
