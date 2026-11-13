"""API-host lifecycle contracts for remote control."""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import UUID

import pytest
from sqlalchemy.exc import OperationalError

from src.services.execution.template_process import TEMPLATE_START_TIMEOUT_SECONDS
from src.worker.remote.host import ApiExecutor, Bridge, RemoteControlHost, RemoteSession, _reply_timeout
from src.worker.remote.ownership import RemoteOwnerLease
from src.worker.remote.protocol import AuthenticationError, ControlEnvelope, FragmentAssembler, Hello, sign_hello
from src.worker.pool_main import PoolWorker
from src.worker.admission import Admission
from src.worker.remote.agent_route import AgentRoute


LEASE = RemoteOwnerLease(
    worker_id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
    owner_id=UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
    launcher_id=UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
    session_generation=1,
)


@pytest.mark.asyncio
async def test_external_fast_path_hello_is_refused_before_admission() -> None:
    hello = Hello(
        launcher_id=LEASE.launcher_id, incarnation_id=LEASE.worker_id,
        job_uid=None, pod_uid=None, hostname="external-fast", form="external",
        issued_at=int(time.time()), nonce="n" * 32, hello_sequence=1,
        resource_uid="external-capacity-1", execution_lane="fast_path",
    )
    socket = AsyncMock()
    socket.receive_text.return_value = json.dumps({
        "event": "hello", "hello": sign_hello("test-control-key", hello).to_dict(),
        "sample": {"current": 1, "limit": 100},
    })
    with (
        patch("src.worker.remote.host.get_settings", return_value=SimpleNamespace(secret_key="test-control-key")),
        patch("src.worker.remote.host.admit_external_worker", new=AsyncMock()) as admit,
    ):
        with pytest.raises(AuthenticationError, match="only support the Normal lane"):
            await RemoteControlHost().connect(socket)
    admit.assert_not_awaited()


@pytest.mark.asyncio
async def test_admission_stale_telemetry_still_renews_the_connected_owner() -> None:
    bridge = Bridge(
        Mock(), {"current": 1, "limit": 100, "requirements": {}, "hostname": "runner"}, LEASE
    )
    bridge.sample_at = 96
    session = object.__new__(RemoteSession)
    session.bridge, session.lease, session.owned_until = bridge, LEASE, 120
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
async def test_owner_renewal_retries_before_the_current_lease_expires() -> None:
    bridge = Bridge(
        Mock(), {"current": 1, "limit": 100, "requirements": {}, "hostname": "runner"}, LEASE
    )
    bridge.sample_at = 100
    session = object.__new__(RemoteSession)
    session.bridge, session.lease, session.owned_until = bridge, LEASE, 120
    session.lose_ownership = AsyncMock()
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    with (
        patch("src.worker.remote.host.time.monotonic", return_value=100),
        patch("src.worker.remote.host.asyncio.sleep", new=sleep),
        patch(
            "src.worker.remote.host.renew_remote_owner",
            new=AsyncMock(side_effect=[RuntimeError("database restarted"), True, asyncio.CancelledError()]),
        ) as renew,
    ):
        with pytest.raises(asyncio.CancelledError):
            await session._renew()

    assert sleeps == [10, 2, 10]
    assert session.owned_until == 120
    assert renew.await_count == 3
    session.lose_ownership.assert_not_awaited()


@pytest.mark.asyncio
async def test_owner_renewal_failing_throughout_loses_ownership_only_when_no_retry_fits() -> None:
    bridge = Bridge(
        Mock(), {"current": 1, "limit": 100, "requirements": {}, "hostname": "runner"}, LEASE
    )
    clock = SimpleNamespace(now=100.0)
    bridge.sample_at = 100
    session = object.__new__(RemoteSession)
    session.bridge, session.lease, session.owned_until = bridge, LEASE, 120.0
    lost_at: list[float] = []

    async def lose_ownership(_reason: str) -> None:
        lost_at.append(clock.now)

    async def sleep(seconds: float) -> None:
        clock.now += seconds
        bridge.sample_at = clock.now  # telemetry stays fresh; only the database fails

    session.lose_ownership = lose_ownership
    with (
        patch("src.worker.remote.host.time.monotonic", new=lambda: clock.now),
        patch("src.worker.remote.host.asyncio.sleep", new=sleep),
        patch(
            "src.worker.remote.host.renew_remote_owner",
            new=AsyncMock(side_effect=RuntimeError("database restarted")),
        ) as renew,
    ):
        await session._renew()

    # Attempts at 110, 112, 114, 116 and 118; after 118 a 2 s retry would end at the lease.
    assert renew.await_count == 5
    assert lost_at == [118.0]


@pytest.mark.asyncio
async def test_owner_renewal_failure_without_a_retry_window_loses_ownership() -> None:
    bridge = Bridge(
        Mock(), {"current": 1, "limit": 100, "requirements": {}, "hostname": "runner"}, LEASE
    )
    bridge.sample_at = 100
    session = object.__new__(RemoteSession)
    session.bridge, session.lease, session.owned_until = bridge, LEASE, 102
    session.lose_ownership = AsyncMock()

    with (
        patch("src.worker.remote.host.time.monotonic", return_value=100),
        patch("src.worker.remote.host.asyncio.sleep", new=AsyncMock()),
        patch(
            "src.worker.remote.host.renew_remote_owner",
            new=AsyncMock(side_effect=RuntimeError("database restarted")),
        ),
    ):
        await session._renew()

    session.lose_ownership.assert_awaited_once_with(
        "Remote API owner lease could not be renewed"
    )


@pytest.mark.asyncio
async def test_owner_renewal_hung_past_the_current_lease_loses_ownership() -> None:
    bridge = Bridge(
        Mock(), {"current": 1, "limit": 100, "requirements": {}, "hostname": "runner"}, LEASE
    )
    bridge.sample_at = time.monotonic()
    session = object.__new__(RemoteSession)
    session.bridge, session.lease = bridge, LEASE
    session.owned_until = time.monotonic() + 0.001
    session.lose_ownership = AsyncMock()

    async def hung_renewal(_lease: RemoteOwnerLease) -> bool:
        await asyncio.Future()
        return True

    with (
        patch("src.worker.remote.host.asyncio.sleep", new=AsyncMock()),
        patch("src.worker.remote.host.renew_remote_owner", new=hung_renewal),
    ):
        await session._renew()

    session.lose_ownership.assert_awaited_once_with(
        "Remote API owner lease could not be renewed"
    )


@pytest.mark.asyncio
async def test_owner_renewal_refusal_loses_ownership_immediately() -> None:
    bridge = Bridge(
        Mock(), {"current": 1, "limit": 100, "requirements": {}, "hostname": "runner"}, LEASE
    )
    bridge.sample_at = 100
    session = object.__new__(RemoteSession)
    session.bridge, session.lease, session.owned_until = bridge, LEASE, 120
    session.lose_ownership = AsyncMock()

    with (
        patch("src.worker.remote.host.time.monotonic", return_value=100),
        patch("src.worker.remote.host.asyncio.sleep", new=AsyncMock()),
        patch("src.worker.remote.host.renew_remote_owner", new=AsyncMock(return_value=False)),
    ):
        await session._renew()

    session.lose_ownership.assert_awaited_once_with("Remote API owner lease expired")


@pytest.mark.asyncio
async def test_control_host_startup_does_not_start_a_token_worker_in_the_api() -> None:
    from src.worker.remote import host

    with patch.object(host, "_host", None), patch.object(asyncio, "create_task") as spawn:
        await host.start_remote_control_host(Mock())
        await host.start_remote_control_host(Mock())
        spawn.assert_not_called()
        await host.stop_remote_control_host()


@pytest.mark.asyncio
@pytest.mark.parametrize("reported", ["1.0.0-old", None])
@pytest.mark.parametrize("form", ["job", "static", "external"])
async def test_a_worker_is_admitted_with_the_build_its_launcher_runs(reported, form) -> None:
    socket = Mock(accept=AsyncMock(), send_text=AsyncMock())
    hello = Hello(
        launcher_id=LEASE.launcher_id,
        incarnation_id=LEASE.worker_id,
        job_uid="job" if form == "job" else None,
        pod_uid="pod" if form == "job" else None, hostname="runner-0", form=form,
        issued_at=int(time.time()), nonce="n" * 32, hello_sequence=1,
        resource_uid="capacity-resource" if form == "external" else None,
    )
    initial = {
        "event": "hello", "hello": sign_hello("secret", hello).to_dict(),
        "sample": {"current": 1, "limit": 100}, "requirements": {},
    }
    if reported is not None:
        initial["version"] = reported
    socket.receive_text = AsyncMock(return_value=json.dumps(initial))

    @asynccontextmanager
    async def database():
        yield Mock(
            scalar=AsyncMock(return_value=None),
            execute=AsyncMock(return_value=Mock(one_or_none=Mock(return_value=None))),
        )

    with (
        patch("src.worker.remote.host.get_settings", return_value=Mock(secret_key="secret", memory_pressure_threshold=.85)),
        patch("src.worker.remote.host.admit_worker", new_callable=AsyncMock) as admit,
        patch("src.worker.remote.host.admit_external_worker", new_callable=AsyncMock) as external,
        patch("src.worker.remote.host.register_static_worker", new_callable=AsyncMock) as register,
        patch("src.worker.remote.host.get_liveness_db_context", new=database),
        patch("src.worker.remote.host.acquire_remote_owner", new_callable=AsyncMock, return_value=LEASE),
        patch("src.worker.remote.host.ApiExecutor"),
        patch("src.worker.remote.host.RemoteSession", return_value=Mock(task=Mock())),
    ):
        await RemoteControlHost().connect(socket)

    methods = {"job": admit, "static": register, "external": external}
    registered = methods[form]
    assert registered.await_args.kwargs["version"] == reported
    for other_form, method in methods.items():
        if other_form != form:
            method.assert_not_awaited()
    if form == "external":
        external.assert_awaited_once_with(
            LEASE.worker_id, resource_uid="capacity-resource",
            launcher_id=LEASE.launcher_id, version=reported, hostname="runner-0",
        )


@pytest.mark.asyncio
async def test_remote_connect_starts_one_physical_executor() -> None:
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
        patch("src.worker.remote.host.get_settings", return_value=Mock(secret_key="secret", memory_pressure_threshold=.85)),
        patch("src.worker.remote.host.admit_worker", new_callable=AsyncMock),
        patch("src.worker.remote.host.acquire_remote_owner", new_callable=AsyncMock, return_value=LEASE),
        patch("src.worker.remote.host.ApiExecutor") as executor,
        patch("src.worker.remote.host.RemoteSession", return_value=session),
    ):
        host = RemoteControlHost()
        await host.connect(socket)

        assert executor.call_count == 1
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
        assert fence.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("event", ["inventory", "result", "agent_exit", "job_exit"])
async def test_control_defers_delivery_until_database_ownership_is_confirmed(event: str) -> None:
    from fastapi import WebSocketDisconnect

    from src.worker.remote import host as host_mod

    socket = Mock(receive_text=AsyncMock(side_effect=[
        ControlEnvelope(1, 1, "event", {"event": event}).to_json(),
        WebSocketDisconnect(),
    ]), close=AsyncMock())
    bridge = Bridge(socket, {"current": 1, "limit": 100, "hostname": "runner", "requirements": {}}, LEASE)
    bridge.receive = AsyncMock()
    session = object.__new__(RemoteSession)
    session.bridge, session.lease, session.owned_until = bridge, LEASE, 120
    session.last_sequence = 0
    session.reconcile_transport_inventory = Mock()
    session.lose_ownership = AsyncMock()
    host = Mock(stopping=False, connect=AsyncMock(return_value=(session, FragmentAssembler())))

    async def retry_delay(_seconds: float) -> None:
        bridge.receive.assert_not_awaited()
        socket.close.assert_not_awaited()

    with (
        patch.object(host_mod, "_host", host),
        patch.object(host_mod.time, "monotonic", return_value=100),
        patch.object(host_mod.asyncio, "sleep", new=retry_delay),
        patch.object(host_mod, "owns_remote_session", new=AsyncMock(side_effect=[
            OperationalError("select", {}, Exception("database restarted")), True,
        ])) as fence,
    ):
        await host_mod.remote_control(socket)

    assert fence.await_count == 2
    bridge.receive.assert_awaited_once_with({"event": event})
    socket.close.assert_not_awaited()
    session.lose_ownership.assert_not_awaited()
    assert session.lease == LEASE


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", [True, False, "db-error", "socket-only"])
async def test_control_rejects_socket_replaced_during_ownership_check(outcome) -> None:
    from src.worker.remote import host as host_mod

    socket = Mock(receive_text=AsyncMock(return_value=ControlEnvelope(
        1, 1, "event", {"event": "draining"}
    ).to_json()), close=AsyncMock())
    bridge = Bridge(socket, {"current": 1, "limit": 100, "hostname": "runner", "requirements": {}}, LEASE)
    bridge.receive = AsyncMock()
    session = object.__new__(RemoteSession)
    session.bridge, session.lease, session.owned_until = bridge, LEASE, 120
    session.last_sequence = 0
    session.lose_ownership = AsyncMock()
    session.begin_physical_drain = Mock()
    host = Mock(stopping=False, connect=AsyncMock(return_value=(session, FragmentAssembler())))

    async def check(_lease):
        # A real reconnect replaces the shared session while the old check awaits.
        if outcome != "socket-only":
            session.lease = replace(LEASE, session_generation=2)
        session.owned_until = 140
        bridge.replace_socket(Mock(), session.lease)
        if outcome == "db-error":
            raise OperationalError("select", {}, Exception("database restarted"))
        return outcome

    with (
        patch.object(host_mod, "_host", host),
        patch.object(host_mod.time, "monotonic", return_value=100),
        patch.object(host_mod, "owns_remote_session", new=check),
    ):
        await host_mod.remote_control(socket)

    bridge.receive.assert_not_awaited()
    session.begin_physical_drain.assert_not_called()
    session.lose_ownership.assert_not_awaited()
    socket.close.assert_awaited_once_with(code=1008)
    assert bridge.connected


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["expired", "lost", "released", "renewed"])
async def test_message_retry_cannot_extend_or_outlive_its_original_lease(change: str) -> None:
    from src.worker.remote import host as host_mod

    session = object.__new__(RemoteSession)
    session.lease, session.owned_until = LEASE, 102
    session._release_lock, session._lease_released = asyncio.Lock(), False
    clock = SimpleNamespace(now=100.0)

    async def check(_lease):
        if change == "renewed":
            session.owned_until = 200
        elif change == "lost":
            session.owned_until = 0
        elif change == "released":
            await session.release_lease()
        clock.now = 100 if change in {"lost", "released"} else 103
        return True

    with (
        patch.object(host_mod.time, "monotonic", side_effect=lambda: clock.now),
        patch.object(host_mod, "owns_remote_session", new=check),
        patch.object(host_mod, "release_remote_owner", new=AsyncMock()),
    ):
        assert await session.may_accept("event", {"event": "agent_exit"}) is False


@pytest.mark.asyncio
async def test_database_outage_retries_only_until_the_original_deadline() -> None:
    from src.worker.remote import host as host_mod

    session = object.__new__(RemoteSession)
    session.lease, session.owned_until = LEASE, 120
    clock = SimpleNamespace(now=100.0)
    attempts = []

    async def check(_lease):
        attempts.append(clock.now)
        raise OperationalError("select", {}, Exception("database restarted"))

    async def delay(seconds):
        clock.now += seconds

    with (
        patch.object(host_mod.time, "monotonic", side_effect=lambda: clock.now),
        patch.object(host_mod.asyncio, "sleep", new=delay),
        patch.object(host_mod, "owns_remote_session", new=check),
    ):
        assert await session.may_accept("event", {"event": "inventory"}) is False
    assert attempts == list(range(100, 120, 2))


@pytest.mark.asyncio
async def test_programming_errors_in_ownership_checks_are_not_retried() -> None:
    from src.worker.remote import host as host_mod

    session = object.__new__(RemoteSession)
    session.lease, session.owned_until = LEASE, time.monotonic() + 10
    with patch.object(host_mod, "owns_remote_session", new=AsyncMock(side_effect=RuntimeError("bug"))):
        with pytest.raises(RuntimeError, match="bug"):
            await session.may_accept("event", {"event": "inventory"})


@pytest.mark.asyncio
async def test_drain_skips_terminate_when_fence_read_fails() -> None:
    executor = object.__new__(ApiExecutor)
    executor._lost = False
    agents = Mock(routes={"r": Mock()}, terminate_active_runs=AsyncMock())
    executor.bridge = Mock(handoff=False, lease=LEASE, agents=agents)

    with (
        patch(
            "src.worker.remote.host.owns_remote_session",
            new=AsyncMock(side_effect=RuntimeError("database restarted")),
        ),
        patch.object(PoolWorker, "_drain", new_callable=AsyncMock) as drain,
    ):
        await executor._drain()

    drain.assert_awaited_once_with()
    agents.terminate_active_runs.assert_not_awaited()


@pytest.mark.asyncio
async def test_retire_skips_when_fence_read_fails() -> None:
    executor = object.__new__(ApiExecutor)
    executor._lost = False
    executor.bridge = Mock(handoff=False, connected=True, lease=LEASE, request=AsyncMock())

    with (
        patch(
            "src.worker.remote.host.owns_remote_session",
            new=AsyncMock(side_effect=RuntimeError("database restarted")),
        ),
        patch.object(PoolWorker, "_retire", new_callable=AsyncMock) as retire,
    ):
        await executor._retire()

    retire.assert_not_awaited()
    executor.bridge.request.assert_not_awaited()


@pytest.mark.asyncio
async def test_pool_owns_session_fails_closed_on_db_error() -> None:
    from src.worker.remote import ownership as ownership_mod
    from src.worker.remote.fencing import LeaseRef, remote_owner_context
    from src.worker.remote.remote_pool import ThinRemoteProcessPool

    pool = ThinRemoteProcessPool(Mock(), worker_id="test-worker")
    with (
        patch.object(
            ownership_mod, "owns_remote_session",
            new=AsyncMock(side_effect=RuntimeError("database restarted")),
        ),
        remote_owner_context(LeaseRef(LEASE)),
    ):
        assert await pool._owns_session() is False


@pytest.mark.asyncio
async def test_fast_path_hello_requires_lane_aware_handler() -> None:
    hello = Hello(
        launcher_id=LEASE.launcher_id, incarnation_id=LEASE.worker_id,
        job_uid=None, pod_uid=None, hostname="old-fast", form="static",
        issued_at=int(time.time()), nonce="n" * 32, hello_sequence=1, execution_lane="fast_path",
    )
    socket = AsyncMock()
    socket.receive_text.return_value = json.dumps({
        "event": "hello", "hello": sign_hello("test-control-key", hello).to_dict(),
        "sample": {"current": 1, "limit": 100},
    })
    with patch("src.worker.remote.host.get_settings", return_value=SimpleNamespace(secret_key="test-control-key")):
        with pytest.raises(AuthenticationError, match="version 2"):
            await RemoteControlHost().connect(socket)


@pytest.mark.parametrize("lane", ["normal", "fast_path"])
async def test_each_lane_reconciles_inventory_before_opening_claims(lane) -> None:
    executor = object.__new__(ApiExecutor)
    executor.execution_lane = lane
    executor.admission = object()
    executor._admission_prepared = False
    executor.bridge = SimpleNamespace(admission=None, reconciliation_complete=AsyncMock())
    executor._reconcile_transport_inventory = AsyncMock(return_value={})
    executor._apply_reconciled_snapshot = AsyncMock()
    with patch.object(PoolWorker, "_start_service_claim_loop", new_callable=AsyncMock) as services:
        await executor._prepare_admission()
    assert services.await_count == (1 if lane == "normal" else 0)
    executor._reconcile_transport_inventory.assert_awaited_once_with()
    executor._apply_reconciled_snapshot.assert_awaited_once_with({})
    assert executor.bridge.admission is executor.admission
    assert executor._admission_prepared
    executor.bridge.reconciliation_complete.assert_awaited_once_with()
    executor._reconcile_transport_inventory.reset_mock()
    await executor.reconcile_transport_inventory()
    executor._reconcile_transport_inventory.assert_awaited_once_with(None)
    assert executor.bridge.reconciliation_complete.await_count == 2


@pytest.mark.asyncio
async def test_an_adopted_agent_precondition_failure_releases_its_memory() -> None:
    bridge = Bridge(Mock(), {"current": 1, "limit": 100, "hostname": "runner", "requirements": {}}, LEASE)
    bridge.agents = Mock()
    worker = ApiExecutor(bridge, LEASE.worker_id)
    worker.admission = Admission(
        budget_bytes=100, under_pressure=lambda: False,
        running_workflow_ids=set, workflow_growth=dict,
    )
    route = AgentRoute(route_id="adopted", run_id=LEASE.owner_id)

    worker._wait_for_adopted_agent_run(route, 40)
    tasks = list(worker._tasks[route.handler])
    assert worker.admission.free_bytes() == 60
    # The scheduled coroutine must keep its cleanup even if its precondition
    # is broken before it begins supervising the adopted child.
    bridge.agents = None
    with pytest.raises(AssertionError):
        await asyncio.gather(*tasks)

    assert worker.admission.free_bytes() == 100
    assert worker._tasks[route.handler] == set()
