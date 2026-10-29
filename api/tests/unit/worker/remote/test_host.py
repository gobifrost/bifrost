"""API-host lifecycle contracts for remote control."""

from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock, Mock, patch
from uuid import UUID

import pytest

from src.services.execution.template_process import TEMPLATE_START_TIMEOUT_SECONDS
from src.worker.remote.host import ApiExecutor, Bridge, RemoteControlHost, RemoteSession, _reply_timeout
from src.worker.remote.ownership import RemoteOwnerLease
from src.worker.remote.protocol import Hello, sign_hello
from src.worker.pool_main import PoolWorker


LEASE = RemoteOwnerLease(
    worker_id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
    owner_id=UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
    launcher_id=UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
    session_generation=1,
)


@pytest.mark.asyncio
async def test_admission_stale_telemetry_still_renews_the_connected_owner() -> None:
    bridge = Bridge(
        Mock(), {"current": 1, "limit": 100, "requirements": {}, "hostname": "runner"}, LEASE
    )
    bridge.sample_at = 96
    session = object.__new__(RemoteSession)
    session.bridge, session.lease = bridge, LEASE
    session.lose_ownership = AsyncMock()

    with (
        patch("src.worker.remote.host.time.monotonic", return_value=100),
        patch("src.worker.remote.host.asyncio.sleep", new=AsyncMock(side_effect=[None, asyncio.CancelledError()])),
        patch("src.worker.remote.host.renew_remote_owner", new=AsyncMock(return_value=True)) as renew,
    ):
        # Claims pause at three seconds, but that is not an expired owner.
        assert bridge.memory()[0] == -1
        with pytest.raises(asyncio.CancelledError):
            await session._renew()

    renew.assert_awaited_once_with(LEASE)
    session.lose_ownership.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", [False, True, RuntimeError("stale renewal failed")])
async def test_stale_renewal_cannot_drain_a_reconnected_session(outcome: bool | Exception) -> None:
    bridge = Bridge(
        Mock(), {"current": 1, "limit": 100, "requirements": {}, "hostname": "runner"}, LEASE
    )
    replacement = RemoteOwnerLease(
        worker_id=LEASE.worker_id,
        owner_id=LEASE.owner_id,
        launcher_id=LEASE.launcher_id,
        session_generation=2,
    )
    session = object.__new__(RemoteSession)
    session.bridge, session.lease = bridge, LEASE
    session.owned_until = 133
    session.lose_ownership = AsyncMock()
    renewal_started = asyncio.Event()
    release_renewal = asyncio.Event()
    renewals = 0

    async def renew(lease: RemoteOwnerLease) -> bool:
        nonlocal renewals
        renewals += 1
        if renewals == 1:
            assert lease == LEASE
            renewal_started.set()
            await release_renewal.wait()
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        assert lease == replacement
        raise asyncio.CancelledError()

    async def sleep(_seconds: float) -> None:
        return None

    with (
        patch("src.worker.remote.host.time.monotonic", return_value=100),
        patch("src.worker.remote.host.asyncio.sleep", new=sleep),
        patch("src.worker.remote.host.renew_remote_owner", new=renew),
    ):
        task = asyncio.create_task(session._renew())
        await renewal_started.wait()
        session.lease = replacement
        release_renewal.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    session.lose_ownership.assert_not_awaited()
    assert session.owned_until == 133


@pytest.mark.asyncio
@pytest.mark.parametrize("connected,age", [(False, 4), (True, 20), (False, 20)])
async def test_owner_does_not_renew_disconnected_or_expired_telemetry(connected, age) -> None:
    bridge = Bridge(
        Mock(), {"current": 1, "limit": 100, "requirements": {}, "hostname": "runner"}, LEASE
    )
    bridge.connected, bridge.sample_at = connected, 100 - age
    session = object.__new__(RemoteSession)
    session.bridge, session.lease = bridge, LEASE
    session.lose_ownership = AsyncMock()

    with (
        patch("src.worker.remote.host.time.monotonic", return_value=100),
        patch("src.worker.remote.host.asyncio.sleep", new=AsyncMock(side_effect=[None, asyncio.CancelledError()])),
        patch("src.worker.remote.host.renew_remote_owner", new_callable=AsyncMock) as renew,
    ):
        if age < 20:
            with pytest.raises(asyncio.CancelledError):
                await session._renew()
        else:
            await session._renew()

    renew.assert_not_awaited()
    if age >= 20:
        session.lose_ownership.assert_awaited_once_with("Remote launcher telemetry expired")
    else:
        session.lose_ownership.assert_not_awaited()


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
@pytest.mark.parametrize("reported", ["1.0.0-old", None])
async def test_a_job_pod_is_admitted_with_the_build_its_launcher_runs(reported) -> None:
    socket = Mock(accept=AsyncMock(), send_text=AsyncMock())
    hello = Hello(
        launcher_id=LEASE.launcher_id,
        incarnation_id=LEASE.worker_id,
        job_uid="job", pod_uid="pod", hostname="runner-0", form="job",
        issued_at=int(time.time()), nonce="n" * 32, hello_sequence=1,
    )
    initial = {
        "event": "hello", "hello": sign_hello("secret", hello).to_dict(),
        "sample": {"current": 1, "limit": 100}, "requirements": {},
    }
    if reported is not None:
        initial["version"] = reported
    socket.receive_text = AsyncMock(return_value=json.dumps(initial))
    with (
        patch("src.worker.remote.host.get_settings", return_value=Mock(secret_key="secret", memory_pressure_threshold=.85)),
        patch("src.worker.remote.host.admit_worker", new_callable=AsyncMock) as admit,
        patch("src.worker.remote.host.acquire_remote_owner", new_callable=AsyncMock, return_value=LEASE),
        patch("src.worker.remote.host.ApiExecutor"),
        patch("src.worker.remote.host.RemoteSession", return_value=Mock(task=Mock())),
    ):
        await RemoteControlHost().connect(socket)

    assert admit.await_args.kwargs["version"] == reported


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
        session = RemoteSession(bridge, worker, LEASE, owned_until=0.0)
        stopping = asyncio.create_task(session.stop())
        await asyncio.sleep(0)
        assert release.await_count == 0
        drained.set()
        await asyncio.gather(stopping)

    worker.request_stop.assert_called_once_with()
    release.assert_awaited_once_with(LEASE)


@pytest.mark.asyncio
async def test_disconnected_session_detaches_without_waiting_for_remote_work() -> None:
    bridge = Bridge(Mock(), {"current": 1, "limit": 100, "requirements": {}, "hostname": "runner"}, LEASE)
    bridge.disconnect()
    never = asyncio.Event()
    worker = Mock(request_stop=Mock(), run=AsyncMock(side_effect=never.wait))

    with patch("src.worker.remote.host.release_remote_owner", new_callable=AsyncMock) as release:
        session = RemoteSession(bridge, worker, LEASE, owned_until=0.0)
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
        session = RemoteSession(bridge, worker, LEASE, owned_until=0.0)
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
    session = RemoteSession(bridge, worker, LEASE, owned_until=0.0)
    task = session.reconcile_transport_inventory(socket)
    await asyncio.sleep(0)
    request_id = next(iter(bridge.pending))

    await bridge.receive({"reply": request_id, "result": {}})
    await asyncio.gather(task)

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


@pytest.mark.asyncio
async def test_lost_remote_owner_detaches_without_retiring_live_launcher_claims() -> None:
    executor = object.__new__(ApiExecutor)
    executor._lost = True
    executor.bridge = Mock(handoff=False, connected=True, lease=LEASE, request=AsyncMock())

    with (
        patch("src.worker.remote.host.owns_remote_session", new=AsyncMock(return_value=True)),
        patch.object(PoolWorker, "_retire", new_callable=AsyncMock) as retire,
    ):
        await executor._retire()

    retire.assert_not_awaited()
    executor.bridge.request.assert_not_awaited()


@pytest.mark.asyncio
async def test_drained_remote_owner_retires_its_launcher() -> None:
    executor = object.__new__(ApiExecutor)
    executor._lost = False
    executor.bridge = Mock(handoff=False, connected=True, lease=LEASE, request=AsyncMock())

    with (
        patch("src.worker.remote.host.owns_remote_session", new=AsyncMock(return_value=True)),
        patch.object(PoolWorker, "_retire", new_callable=AsyncMock) as retire,
    ):
        await executor._retire()

    retire.assert_awaited_once_with()
    executor.bridge.request.assert_awaited_once_with("retired", {})


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


@pytest.mark.asyncio
@pytest.mark.parametrize("valid_proof", [False, True], ids=["invalid-proof", "owner-refused"])
async def test_refused_remote_control_handshake_closes_once(valid_proof: bool) -> None:
    """A rejected handshake must not send a second close after connect rejects it."""
    from starlette.websockets import WebSocket

    from src.worker.remote import host as host_mod

    hello = Hello(
        launcher_id=LEASE.launcher_id,
        incarnation_id=LEASE.worker_id,
        job_uid="job", pod_uid="pod", hostname="runner-0", form="job",
        issued_at=int(time.time()), nonce="n" * 32, hello_sequence=1,
    )
    initial = {
        "event": "hello",
        "hello": sign_hello("secret" if valid_proof else "wrong-secret", hello).to_dict(),
        "sample": {"current": 1, "limit": 100},
    }
    send = AsyncMock()
    socket = WebSocket(
        {"type": "websocket", "path": "/api/worker-control"},
        receive=AsyncMock(side_effect=[
            {"type": "websocket.connect"},
            {"type": "websocket.receive", "text": json.dumps(initial)},
        ]),
        send=send,
    )
    host = RemoteControlHost()
    with (
        patch.object(host_mod, "_host", host),
        patch.object(host_mod, "get_settings", return_value=Mock(secret_key="secret")),
        patch.object(host_mod, "admit_worker", new=AsyncMock(return_value=object())),
        patch.object(host_mod, "acquire_remote_owner", new=AsyncMock(return_value=None)),
    ):
        await host_mod.remote_control(socket)

    assert [call.args[0]["type"] for call in send.await_args_list] == [
        "websocket.accept", "websocket.close",
    ]
    assert send.await_args_list[-1].args[0]["code"] == 1008
    assert host.sessions == {}


def test_agent_launches_wait_long_enough_for_a_template_rebuild() -> None:
    assert _reply_timeout("agent_run") > TEMPLATE_START_TIMEOUT_SECONDS
    assert _reply_timeout("terminate") < TEMPLATE_START_TIMEOUT_SECONDS


@pytest.mark.asyncio
async def test_telemetry_skips_the_database_fence_only_while_the_lease_is_known_current() -> None:
    from src.worker.remote import host as host_mod

    session = object.__new__(RemoteSession)
    session.lease = LEASE
    session.owned_until = time.monotonic() + 10
    sample = {"event": "sample", "current": 1, "limit": 100}
    with patch.object(host_mod, "owns_remote_session", new=AsyncMock(return_value=False)) as fence:
        assert await session.may_accept("event", sample) is True
        fence.assert_not_awaited()

        assert await session.may_accept("event", {"event": "result", "route_id": "r"}) is False
        assert await session.may_accept("event", {"event": "inventory"}) is False
        assert fence.await_count == 2

        session.owned_until = time.monotonic() - 1
        assert await session.may_accept("event", sample) is False
        assert fence.await_count == 3
