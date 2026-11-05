import asyncio
from types import SimpleNamespace
from dataclasses import replace
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock, patch

import pytest
from uuid import UUID

from src.worker.remote.host import Bridge, RemoteProcess
from src.worker.remote.ownership import RemoteOwnerLease


LEASE = RemoteOwnerLease(
    worker_id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
    owner_id=UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
    launcher_id=UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
    session_generation=1,
)


def _bridge(socket):
    return Bridge(socket, {"current": 1, "limit": 100, "requirements": {}, "hostname": "thin-1"}, LEASE)


@pytest.mark.asyncio
async def test_resource_reports_arrive_before_consumer_initialization():
    bridge = _bridge(Mock())
    await bridge.receive({"event": "sample", "current": 2, "limit": 100, "runs": {}})
    assert bridge.memory() == (2, 100)


@pytest.mark.asyncio
async def test_platform_process_wait_survives_heartbeat_wait_cancellation():
    bridge = _bridge(Mock())
    bridge.reconciliation_ready.set()
    process = RemoteProcess(bridge, "route")
    bridge.jobs["route"] = process
    wait = asyncio.create_task(process.wait())
    await asyncio.sleep(0)
    wait.cancel()
    results = await asyncio.gather(wait, return_exceptions=True)
    assert isinstance(results[0], asyncio.CancelledError)
    assert not process.exited.cancelled()
    await bridge.receive({"event": "job_exit", "route_id": "route", "returncode": 0})
    assert await process.wait() == 0


@pytest.mark.asyncio
async def test_control_reply_resolves_only_its_own_request():
    socket = Mock(send_text=AsyncMock())
    bridge = _bridge(socket)
    request = asyncio.create_task(bridge.request("ready", {}))
    await asyncio.sleep(0)
    request_id = next(iter(bridge.pending))
    await bridge.receive({"reply": "another-request", "result": {}})
    assert not request.done()
    await bridge.receive({"reply": request_id, "result": {"ready": True}})
    assert await request == {"ready": True}


@pytest.mark.asyncio
async def test_terminal_result_is_acknowledged_only_after_durable_delivery():
    socket = Mock(send_text=AsyncMock())
    bridge = _bridge(socket)
    delivered = asyncio.Event()

    class Pool:
        async def handle_event(self, event):
            await delivered.wait()

    bridge.pool = Pool()
    bridge.reconciliation_ready.set()
    await bridge.receive({
        "event": "result", "route_id": "route", "result": {"success": True},
        "_event_id": "terminal-1",
    })
    await asyncio.sleep(0)
    socket.send_text.assert_not_awaited()

    delivered.set()
    await asyncio.gather(*bridge.results)

    assert '"event_id":"terminal-1"' in socket.send_text.await_args.args[0]


@pytest.mark.asyncio
async def test_terminal_delivery_failure_closes_its_current_transport_for_replay():
    socket = Mock(send_text=AsyncMock(), close=AsyncMock())
    bridge = _bridge(socket)

    class Pool:
        async def handle_event(self, event):
            raise RuntimeError("database unavailable")

    bridge.pool = Pool()
    bridge.reconciliation_ready.set()
    await bridge.receive({
        "event": "result", "route_id": "route", "result": {"success": True},
        "_event_id": "terminal-1",
    })

    await asyncio.gather(*bridge.results, return_exceptions=True)
    await asyncio.sleep(0)

    socket.close.assert_awaited_once_with(code=1011, reason="terminal delivery failed")
    socket.send_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_uncertain_platform_launch_retains_route_for_inventory_recovery():
    bridge = _bridge(Mock())
    claim = SimpleNamespace(id="job-1", lease_token="lease-1", memory_required_bytes=9)
    bridge.request = AsyncMock(side_effect=ConnectionError("socket closed"))

    process = await bridge.spawn(claim)

    assert bridge.jobs[process.route_id] is process
    assert bridge.platform_routes[("job-1", "lease-1")] == process.route_id


@pytest.mark.asyncio
async def test_platform_exit_releases_its_lease_route_mapping():
    bridge = _bridge(Mock(send_text=AsyncMock()))
    process = RemoteProcess(bridge, "route")
    bridge.jobs["route"] = process
    bridge.platform_routes[("job-1", "lease-1")] = "route"
    bridge.reconciliation_ready.set()

    await bridge.receive({"event": "job_exit", "route_id": "route", "returncode": 0})

    assert bridge.platform_routes == {}
    assert await process.wait() == 0


@pytest.mark.asyncio
async def test_inventory_missing_uncertain_platform_route_unblocks_supervisor_as_failed():
    bridge = _bridge(Mock())
    process = RemoteProcess(bridge, "route")
    bridge.jobs["route"] = process
    bridge.platform_routes[("job-1", "lease-1")] = "route"

    bridge.reject_missing_platform_routes(
        {"version": 1, "baseline_bytes": 1, "routes": [], "terminal_routes": []}
    )

    assert process.returncode == 1
    assert await process.wait() == 1
    assert bridge.jobs == {}
    assert bridge.platform_routes == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("event_type", ["result", "agent_exit"])
async def test_terminal_delivery_cannot_borrow_a_reconnected_sessions_permission(event_type):
    from src.worker.remote.fencing import LeaseRef, current_remote_lease_ref

    socket = Mock(send_text=AsyncMock(), close=AsyncMock())
    replacement = Mock(send_text=AsyncMock(), close=AsyncMock())
    bridge = _bridge(socket)
    bridge.lease_ref = LeaseRef(LEASE)
    bridge.reconciliation_ready.set()
    observed = []

    async def deliver(event):
        observed.append(current_remote_lease_ref().lease)

    bridge.pool = Mock(handle_event=AsyncMock(side_effect=deliver))
    bridge.agents = Mock(tracks=Mock(return_value=True), handle_exit=AsyncMock(side_effect=deliver))
    await bridge.receive({"event": event_type, "route_id": "route", "_event_id": "terminal"})
    # Replace the connection before the detached delivery task gets to run.
    newer = replace(LEASE, session_generation=2)
    bridge.lease_ref.lease = newer
    bridge.replace_socket(replacement, newer)
    with patch("src.worker.remote.host.owns_remote_session", new=AsyncMock(return_value=True)):
        outcomes = await asyncio.gather(*bridge.results, return_exceptions=True)
    assert observed == [LEASE]
    assert isinstance(outcomes[0], ConnectionError)
    socket.send_text.assert_not_awaited()
    replacement.send_text.assert_not_awaited()
    replacement.close.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("fence", [False, RuntimeError("database unavailable"), True])
async def test_terminal_ack_requires_current_database_ownership(fence, ack_database):
    from src.worker.remote.fencing import LeaseRef, remote_owner_context

    socket = Mock(send_text=AsyncMock())
    bridge = _bridge(socket)
    check = AsyncMock(side_effect=fence) if isinstance(fence, Exception) else AsyncMock(return_value=fence)
    with (
        remote_owner_context(LeaseRef(LEASE)),
        patch("src.worker.remote.host.fence_remote_owner", new=check),
    ):
        if fence is True:
            await bridge.acknowledge("terminal")
            socket.send_text.assert_awaited_once()
        else:
            with pytest.raises((ConnectionError, RuntimeError)):
                await bridge.acknowledge("terminal")
            socket.send_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_ack_rechecks_socket_after_awaited_database_confirmation(ack_database):
    from src.worker.remote.fencing import LeaseRef, remote_owner_context

    socket = Mock(send_text=AsyncMock())
    replacement = Mock(send_text=AsyncMock())
    bridge = _bridge(socket)

    async def check(db):
        bridge.replace_socket(replacement, replace(LEASE, session_generation=2))
        return True

    with (
        remote_owner_context(LeaseRef(LEASE)),
        patch("src.worker.remote.host.fence_remote_owner", new=check),
    ):
        with pytest.raises(ConnectionError, match="replaced"):
            await bridge.acknowledge("terminal")
    socket.send_text.assert_not_awaited()
    replacement.send_text.assert_not_awaited()


@pytest.fixture
def ack_database(monkeypatch):
    db = Mock(scalar=AsyncMock(return_value=20))

    @asynccontextmanager
    async def context():
        yield db

    monkeypatch.setattr("src.worker.remote.host.get_db_context", context)
    return db
