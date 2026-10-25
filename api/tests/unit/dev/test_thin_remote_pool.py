"""Contracts for the production remote process-pool adapter."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from src.core.redis_client import ActiveExecution
from src.worker.remote.remote_pool import ThinRemoteProcessPool
from src.models.contracts.platform import ProcessInfo


class _Transport:
    connected = True
    hostname = "thin-1"

    def __init__(self) -> None:
        self.requests: list[tuple[str, dict]] = []
        self.inventory: list[dict[str, str]] = []

    async def request(self, action: str, payload: dict) -> dict:
        self.requests.append((action, payload))
        if action == "package_tool" and payload == {"operation": "inventory"}:
            return {"packages": self.inventory}
        return {"accepted": True, "pid": 12345}

    def memory_request_bytes(self, key: str) -> int:
        return 1024

    def memory(self) -> tuple[int, int]:
        return (600, 1000)


def _active(execution_id: str) -> ActiveExecution:
    return ActiveExecution(
        execution_id=execution_id,
        workflow_id="workflow-1",
        workflow_name="Example workflow",
        org_id=None,
        user_id="user-1",
        user_name="Developer",
        user_email="dev@example.test",
        sync=True,
        event=None,
    )


@pytest.mark.asyncio
async def test_routes_remote_workflow_and_delivers_remote_resource_metrics() -> None:
    transport = _Transport()
    on_result = AsyncMock()
    pool = ThinRemoteProcessPool(transport, on_result=on_result)

    context = {
        "timeout_seconds": 30,
        "engine_token": "secret-engine-token",
    }
    with (
        patch.object(pool, "_write_context_to_redis", new_callable=AsyncMock),
        patch.object(pool, "_write_active_execution_lease", new_callable=AsyncMock),
    ):
        await pool.route_execution("exec-1", context, _active("exec-1"))

    action, payload = transport.requests[-1]
    assert action == "workflow"
    assert payload["route_id"].startswith("workflow:exec-1:")
    assert payload["execution_id"] == "exec-1"
    assert payload["context"] is context
    assert payload["memory_kill_growth_bytes"] is None
    assert next(iter(pool.processes.values())).pid is None

    await pool.handle_event(
        {
            "event": "sample",
            "current": 700,
            "limit": 1000,
            "runs": {
                payload["route_id"]: {
                    "growth_bytes": 111,
                    "memory_peak_bytes": 222,
                    "cpu_seconds": 3.5,
                    "rss_bytes": 222,
                }
            },
        }
    )
    await pool.handle_event(
        {
            "event": "result",
            "route_id": payload["route_id"],
            "result": {"type": "result", "execution_id": "exec-1", "success": True},
        }
    )

    result = on_result.await_args.args[0]
    assert result["sync"] is True
    assert result["metrics"] == {
        "peak_memory_growth_bytes": 111,
        "peak_process_rss_bytes": 222,
        "cpu_total_seconds": 3.5,
    }
    assert pool.processes == {}
    heartbeat = pool._build_heartbeat()
    assert heartbeat["memory_current_bytes"] == 700
    assert heartbeat["memory_max_bytes"] == 1000


@pytest.mark.asyncio
async def test_heartbeat_uses_launcher_pid_while_the_local_handle_stays_pidless() -> None:
    transport = _Transport()
    pool = ThinRemoteProcessPool(transport)
    with (
        patch.object(pool, "_write_context_to_redis", new_callable=AsyncMock),
        patch.object(pool, "_write_active_execution_lease", new_callable=AsyncMock),
    ):
        await pool.route_execution("exec-1", {"timeout_seconds": 30}, _active("exec-1"))

    assert next(iter(pool.processes.values())).pid is None
    heartbeat_process = pool._build_heartbeat()["processes"][0]
    assert heartbeat_process["pid"] == 12345
    assert ProcessInfo.model_validate(heartbeat_process).pid == 12345


@pytest.mark.asyncio
async def test_result_arriving_before_dispatch_ack_is_completed_once() -> None:
    transport = _Transport()
    on_result = AsyncMock()
    pool = ThinRemoteProcessPool(transport, on_result=on_result)

    async def result_before_ack(action: str, payload: dict) -> dict:
        transport.requests.append((action, payload))
        if action == "workflow":
            await pool.handle_event(
                {
                    "event": "result",
                    "route_id": payload["route_id"],
                    "result": {
                        "type": "result",
                        "execution_id": payload["execution_id"],
                        "success": True,
                    },
                }
            )
        return {"accepted": True, "pid": 12345}

    with (
        patch.object(transport, "request", side_effect=result_before_ack),
        patch.object(pool, "_write_context_to_redis", new_callable=AsyncMock),
        patch.object(pool, "_write_active_execution_lease", new_callable=AsyncMock),
    ):
        await pool.route_execution("exec-1", {"timeout_seconds": 30}, _active("exec-1"))

    assert on_result.await_count == 1
    assert pool.processes == {}


@pytest.mark.asyncio
async def test_routes_service_and_uses_remote_terminate_without_a_local_pid() -> None:
    transport = _Transport()
    on_service_result = AsyncMock()
    pool = ThinRemoteProcessPool(transport, on_service_result=on_service_result)
    context = {
        "execution_id": "attempt-1",
        "service": {
            "service_id": "00000000-0000-4000-8000-000000000001",
            "attempt_id": "attempt-1",
            "lease_token": "lease-1",
        },
    }

    await pool.route_service(
        service_id=context["service"]["service_id"],
        attempt_id="attempt-1",
        lease_token="lease-1",
        context=context,
        graceful_shutdown_seconds=12,
    )

    action, payload = transport.requests[-1]
    assert action == "service"
    assert payload["route_id"].startswith("service:attempt-1:")
    assert payload["service"] == context["service"]
    assert payload["graceful_shutdown_seconds"] == 12
    assert next(iter(pool.service_processes.values())).pid is None

    assert await pool.stop_service_child("attempt-1") is True
    assert transport.requests[-1] == (
        "terminate",
        {
            "route_id": payload["route_id"],
            "error_type": "CancelledError",
            "error": "Service stop requested",
            "grace_seconds": 12,
        },
    )

    await pool.handle_event(
        {
            "event": "result",
            "route_id": payload["route_id"],
            "result": {"type": "result", "attempt_id": "attempt-1", "success": True},
        }
    )

    envelope = on_service_result.await_args.args[0]
    assert envelope["service"] == context["service"]
    assert pool.service_processes == {}


@pytest.mark.asyncio
async def test_fenced_ownership_loss_detaches_without_failing_adoptable_routes() -> None:
    transport = _Transport()
    on_result = AsyncMock()
    pool = ThinRemoteProcessPool(transport, on_result=on_result)
    with (
        patch.object(pool, "_write_context_to_redis", new_callable=AsyncMock),
        patch.object(pool, "_write_active_execution_lease", new_callable=AsyncMock),
    ):
        await pool.route_execution("exec-1", {"timeout_seconds": 30}, _active("exec-1"))

    transport.connected = False
    await pool.ownership_lost()

    on_result.assert_not_awaited()
    assert pool.processes == {}
    assert pool._routes == {}


@pytest.mark.asyncio
async def test_socket_loss_preserves_live_routes_for_reconnect() -> None:
    transport = _Transport()
    pool = ThinRemoteProcessPool(transport)
    with (
        patch.object(pool, "_write_context_to_redis", new_callable=AsyncMock),
        patch.object(pool, "_write_active_execution_lease", new_callable=AsyncMock),
    ):
        await pool.route_execution("exec-1", {"timeout_seconds": 30}, _active("exec-1"))

    transport.connected = False

    assert len(pool.processes) == 1
    assert len(pool._routes) == 1


@pytest.mark.asyncio
async def test_late_result_from_an_old_retry_cannot_complete_the_new_route() -> None:
    transport = _Transport()
    on_result = AsyncMock()
    pool = ThinRemoteProcessPool(transport, on_result=on_result)
    with (
        patch.object(pool, "_write_context_to_redis", new_callable=AsyncMock),
        patch.object(pool, "_write_active_execution_lease", new_callable=AsyncMock),
    ):
        await pool.route_execution("exec-1", {"timeout_seconds": 30}, _active("exec-1"))
        first_route = transport.requests[-1][1]["route_id"]
        await pool.handle_event(
            {
                "event": "result",
                "route_id": first_route,
                "result": {"type": "result", "execution_id": "exec-1", "success": True},
            }
        )
        await pool.route_execution("exec-1", {"timeout_seconds": 30}, _active("exec-1"))

    second_route = transport.requests[-1][1]["route_id"]
    assert second_route != first_route
    await pool.handle_event(
        {
            "event": "result",
            "route_id": first_route,
            "result": {"type": "result", "execution_id": "exec-1", "success": True},
        }
    )

    assert second_route in pool.processes
    assert on_result.await_count == 1


@pytest.mark.asyncio
async def test_remote_dispatch_survives_an_active_lease_write_failure() -> None:
    transport = _Transport()
    pool = ThinRemoteProcessPool(transport)
    with (
        patch.object(pool, "_write_context_to_redis", new_callable=AsyncMock),
        patch.object(
            pool,
            "_write_active_execution_lease",
            new_callable=AsyncMock,
            side_effect=ConnectionError("redis unavailable"),
        ),
    ):
        await pool.route_execution("exec-1", {"timeout_seconds": 30}, _active("exec-1"))

    assert transport.requests[-1][0] == "workflow"


@pytest.mark.asyncio
async def test_worker_registration_uses_the_remote_package_inventory() -> None:
    transport = _Transport()
    transport.inventory = [{"name": "remote-wheel", "version": "1.2.3"}]
    pool = ThinRemoteProcessPool(transport, worker_id="thin-worker")
    redis_client = AsyncMock()

    with patch.object(pool, "_get_redis", new=AsyncMock(return_value=redis_client)):
        await pool._register_worker()

    assert transport.requests == [("package_tool", {"operation": "inventory"})]
    mapping = redis_client.hset.await_args.kwargs["mapping"]
    assert mapping["hostname"] == transport.hostname
    assert json.loads(mapping["packages"]) == transport.inventory
    redis_client.expire.assert_awaited_once_with(
        "bifrost:pool:thin-worker", pool.registration_ttl_seconds
    )


@pytest.mark.asyncio
async def test_launch_ack_loss_preserves_route_for_inventory_reconciliation() -> None:
    transport = _Transport()
    pool = ThinRemoteProcessPool(transport)
    transport.request = AsyncMock(side_effect=ConnectionError("ACK lost"))
    with (
        patch.object(pool, "_write_context_to_redis", new_callable=AsyncMock),
        patch.object(pool, "_write_active_execution_lease", new_callable=AsyncMock),
    ):
        await pool.route_execution("exec-1", {"timeout_seconds": 30}, _active("exec-1"))
    assert len(pool._routes) == 1
    payload = transport.request.await_args.args[1]
    assert payload["memory_request_bytes"] == 1024
    assert payload["active_execution"]["sync"] is True


@pytest.mark.asyncio
async def test_callback_failure_retains_route_until_durable_result_replay() -> None:
    transport = _Transport()
    callback = AsyncMock(side_effect=[ConnectionError("DB unavailable"), None])
    pool = ThinRemoteProcessPool(transport, on_result=callback)
    with (
        patch.object(pool, "_write_context_to_redis", new_callable=AsyncMock),
        patch.object(pool, "_write_active_execution_lease", new_callable=AsyncMock),
    ):
        await pool.route_execution("exec-1", {"timeout_seconds": 30}, _active("exec-1"))
    route = transport.requests[-1][1]["route_id"]
    event = {"event": "result", "route_id": route,
             "result": {"execution_id": "exec-1", "success": True}}
    with pytest.raises(ConnectionError, match="DB unavailable"):
        await pool.handle_event(event)
    assert route in pool._routes
    await pool.handle_event(event)
    assert pool._routes == {}
    assert callback.await_count == 2
    assert callback.await_args.args[0]["sync"] is True
