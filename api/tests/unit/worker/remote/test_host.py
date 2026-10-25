"""API-host lifecycle contracts for remote control."""

from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock, Mock, patch
from uuid import UUID

import pytest

from src.worker.remote.host import ApiExecutor, Bridge, RemoteControlHost, RemoteSession
from src.worker.remote.ownership import RemoteOwnerLease
from src.worker.remote.protocol import Hello, sign_hello


LEASE = RemoteOwnerLease(
    worker_id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
    owner_id=UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
    launcher_id=UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
    session_generation=1,
)


@pytest.mark.asyncio
async def test_one_api_agent_executor_starts_once_per_api_host() -> None:
    executor = Mock()
    executor.run.return_value = 0
    executor.run = AsyncMock(return_value=0)
    with patch("src.worker.api_agent.ApiAgentExecutor", return_value=executor) as agent:
        host = RemoteControlHost()
        await host.start()
        await host.start()

        assert agent.call_count == 1
        assert host.agent_executor is executor
        executor.request_stop.assert_not_called()

        await host.stop()

    executor.request_stop.assert_called_once_with()


@pytest.mark.asyncio
async def test_remote_connect_does_not_create_a_second_api_agent_executor() -> None:
    agent = Mock(run=AsyncMock(return_value=0))
    socket = Mock(accept=AsyncMock(), send_text=AsyncMock())
    hello = Hello(
        launcher_id=LEASE.launcher_id,
        incarnation_id=LEASE.worker_id,
        job_uid="job", pod_uid="pod", hostname="runner-0", form="job",
        issued_at=int(time.time()), nonce="n" * 32, hello_sequence=1,
    )
    socket.receive_text = AsyncMock(return_value=json.dumps({
        "event": "hello", "hello": sign_hello("secret", hello).to_dict(),
        "sample": {"current": 1, "limit": 100}, "requirements": {},
    }))
    session = Mock(worker=Mock(), bridge=Mock(connected=True))
    session.stop = AsyncMock()
    with (
        patch("src.worker.api_agent.ApiAgentExecutor", return_value=agent) as api_agent,
        patch("src.worker.remote.host.get_settings", return_value=Mock(secret_key="secret", memory_pressure_threshold=.85)),
        patch("src.services.memory_requests.measure_budget", return_value=Mock(budget_bytes=100)),
        patch("src.services.memory_requests.under_pressure", return_value=False),
        patch("src.worker.remote.host.admit_worker", new_callable=AsyncMock),
        patch("src.worker.remote.host.acquire_remote_owner", new_callable=AsyncMock, return_value=LEASE),
        patch("src.worker.remote.host.ApiExecutor"),
        patch("src.worker.remote.host.RemoteSession", return_value=session),
    ):
        host = RemoteControlHost()
        await host.start()
        await host.connect(socket)

        assert api_agent.call_count == 1
        await host.stop()


@pytest.mark.asyncio
async def test_connected_session_releases_lease_only_after_worker_drain() -> None:
    bridge = Bridge(Mock(), {"current": 1, "limit": 100, "requirements": {}, "hostname": "runner"}, LEASE)
    drained = asyncio.Event()
    worker = Mock(request_stop=Mock())

    async def run():
        await drained.wait()
        return 0

    worker.run = AsyncMock(side_effect=run)
    with patch("src.worker.remote.host.release_remote_owner", new_callable=AsyncMock) as release:
        session = RemoteSession(bridge, worker, LEASE)
        stopping = asyncio.create_task(session.stop())
        await asyncio.sleep(0)
        assert release.await_count == 0
        drained.set()
        await stopping

    worker.request_stop.assert_called_once_with()
    release.assert_awaited_once_with(LEASE)


@pytest.mark.asyncio
async def test_disconnected_session_detaches_without_waiting_for_remote_work() -> None:
    bridge = Bridge(Mock(), {"current": 1, "limit": 100, "requirements": {}, "hostname": "runner"}, LEASE)
    bridge.disconnect()
    never = asyncio.Event()
    worker = Mock(request_stop=Mock(), run=AsyncMock(side_effect=never.wait))

    with patch("src.worker.remote.host.release_remote_owner", new_callable=AsyncMock) as release:
        session = RemoteSession(bridge, worker, LEASE)
        await session.detach()

    worker.request_stop.assert_called_once_with()
    release.assert_awaited_once_with(LEASE)


@pytest.mark.asyncio
async def test_host_shutdown_does_not_stop_a_disconnected_launcher_before_handoff() -> None:
    host = RemoteControlHost()
    session = Mock()
    session.bridge.connected = False
    session.worker.request_stop = Mock()
    session.detach = AsyncMock()
    host.sessions[LEASE.worker_id] = session

    await host.stop()

    session.worker.request_stop.assert_not_called()
    session.detach.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_finished_session_releases_its_owner_and_is_removed_from_host() -> None:
    host = RemoteControlHost()
    bridge = Bridge(
        Mock(), {"current": 1, "limit": 100, "requirements": {}, "hostname": "runner"}, LEASE
    )
    worker = Mock(request_stop=Mock(), run=AsyncMock(return_value=0))

    with patch("src.worker.remote.host.release_remote_owner", new_callable=AsyncMock) as release:
        session = RemoteSession(bridge, worker, LEASE)
        host.sessions[LEASE.worker_id] = session
        session.task.add_done_callback(
            lambda task: host._session_finished(LEASE.worker_id, session, task)
        )
        reconciliation = asyncio.create_task(asyncio.Event().wait())
        session.reconciliation_tasks.add(reconciliation)
        await session.task
        await asyncio.sleep(0)
        assert session.cleanup_task is not None
        await session.cleanup_task

        assert LEASE.worker_id not in host.sessions
        release.assert_awaited_once_with(LEASE)
        assert reconciliation.cancelled()


@pytest.mark.asyncio
async def test_inventory_reconciliation_does_not_block_its_terminate_reply() -> None:
    socket = Mock(send_text=AsyncMock(), close=AsyncMock())
    bridge = Bridge(
        socket, {"current": 1, "limit": 100, "requirements": {}, "hostname": "runner"}, LEASE
    )
    stopped = asyncio.Event()
    worker = Mock(request_stop=Mock())

    async def run():
        await stopped.wait()
        return 0

    async def reconcile_transport_inventory(*, expected_generation):
        await bridge.request("terminate", {"route_id": "stale-route"})

    worker.run = AsyncMock(side_effect=run)
    worker.reconcile_transport_inventory = AsyncMock(side_effect=reconcile_transport_inventory)
    session = RemoteSession(bridge, worker, LEASE)
    task = session.reconcile_transport_inventory(socket)
    await asyncio.sleep(0)
    request_id = next(iter(bridge.pending))

    await bridge.receive({"reply": request_id, "result": {}})
    await task

    worker.reconcile_transport_inventory.assert_awaited_once_with(expected_generation=1)
    assert socket.close.await_count == 0
    stopped.set()
    await session.detach()


@pytest.mark.asyncio
async def test_reconnect_budget_uses_launcher_baseline_not_active_working_set() -> None:
    bridge = Bridge(
        Mock(), {"current": 600, "limit": 1000, "requirements": {}, "hostname": "runner"}, LEASE
    )
    await bridge.receive({
        "event": "inventory",
        "inventory": {"version": 1, "baseline_bytes": 80, "routes": [], "terminal_routes": []},
    })
    executor = object.__new__(ApiExecutor)
    executor.bridge = bridge
    executor.settings = Mock(memory_pressure_threshold=.8)

    budget = executor._measure_budget()

    assert budget.baseline_bytes == 80
    assert budget.budget_bytes == 720


def test_remote_admission_uses_launcher_pressure_ratio_not_unbounded_usage_ratio() -> None:
    bridge = Bridge(
        Mock(), {
            "current": 10, "limit": 1000, "pressure_ratio": .9,
            "requirements": {}, "hostname": "runner",
        }, LEASE,
    )
    executor = object.__new__(ApiExecutor)
    executor.bridge = bridge
    executor.settings = Mock(memory_pressure_threshold=.85)

    assert executor._under_pressure() is True
