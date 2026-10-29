"""The API side of agent runs executed by a remote launcher."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, Mock, patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete

from src.models.orm.agent_runs import AgentRun
from src.models.orm.execution_workers import ExecutionWorker, ExecutionWorkerClaim
from src.services.memory_requests import MIB
from src.worker.admission import Admission
from src.worker.api_agent import ApiAgentExecutor
from src.worker.remote import agent_route
from src.worker.remote.agent_route import RemoteAgentConsumer
from src.worker.remote.host import ApiExecutor, Bridge, RemoteSession
from src.worker.remote.ownership import RemoteOwnerLease
from src.worker.remote.remote_pool import ThinRemoteProcessPool

LEASE = RemoteOwnerLease(
    worker_id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
    owner_id=UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
    launcher_id=UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
    session_generation=1,
)


class Launcher:
    """The launcher side of the control channel, as the consumer sees it."""

    def __init__(self, *, refuse: BaseException | None = None) -> None:
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.refuse = refuse
        self.launched = asyncio.Event()

    async def request(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.requests.append((action, payload))
        if action == "agent_run":
            if self.refuse is not None:
                raise self.refuse
            self.launched.set()
            return {"pid": 4242}
        return {}

    def memory_request_bytes(self, key: str) -> int | None:
        return 32 * MIB

    def actions(self) -> list[str]:
        return [action for action, _payload in self.requests]


def _body(run_id: UUID | None = None) -> dict[str, Any]:
    return {
        "run_id": str(run_id or uuid4()),
        "dispatch_id": str(uuid4()),
        "agent_id": None,
        "trigger_type": "sdk",
        "sync": True,
    }


@pytest.fixture
def announce(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    announced = AsyncMock(return_value=True)
    monkeypatch.setattr(agent_route, "announce_ended_agent_run", announced)
    monkeypatch.setattr(agent_route, "run_timeout_seconds", AsyncMock(return_value=1800))
    return announced


async def _launch(consumer: RemoteAgentConsumer, launcher: Launcher, body: dict[str, Any]):
    waiting = asyncio.create_task(consumer.process_message(body))
    await asyncio.wait_for(launcher.launched.wait(), 1)
    [route_id] = consumer.routes
    return waiting, route_id


async def test_a_claimed_run_launches_one_child_and_ends_with_its_exit(announce) -> None:
    launcher = Launcher()
    consumer = RemoteAgentConsumer(launcher, worker_id=LEASE.worker_id)
    body = _body()

    waiting, route_id = await _launch(consumer, launcher, body)
    assert not waiting.done()
    await consumer.handle_exit({"event": "agent_exit", "route_id": route_id, "ok": True})
    await asyncio.wait_for(waiting, 1)

    assert launcher.requests == [("agent_run", {
        "route_id": route_id, "run_id": body["run_id"], "message": body,
        "memory_request_bytes": 32 * MIB,
    })]
    announce.assert_not_awaited()
    assert consumer.routes == {}


async def test_a_child_that_exits_without_an_outcome_ends_its_run(announce) -> None:
    launcher = Launcher()
    consumer = RemoteAgentConsumer(launcher, worker_id=LEASE.worker_id)
    body = _body()

    waiting, route_id = await _launch(consumer, launcher, body)
    await consumer.handle_exit({
        "event": "agent_exit", "route_id": route_id, "ok": False,
        "error_type": "ProcessCrashError", "error": "killed by signal 9",
    })
    await asyncio.wait_for(waiting, 1)

    announce.assert_awaited_once()
    run_id, error = announce.await_args.args
    assert run_id == UUID(body["run_id"])
    assert "killed by signal 9" in error


async def test_a_launch_refused_for_memory_hands_the_run_back(announce, monkeypatch) -> None:
    release = AsyncMock()
    monkeypatch.setattr(agent_route, "release_agent_run", release)
    consumer = RemoteAgentConsumer(Launcher(refuse=MemoryError("pressure")), worker_id=LEASE.worker_id)
    body = _body()

    await consumer.process_message(body)

    release.assert_awaited_once_with(
        worker_id=LEASE.worker_id, run_id=UUID(body["run_id"]),
        dispatch_id=UUID(body["dispatch_id"]),
    )
    announce.assert_not_awaited()
    assert consumer.routes == {}


async def test_a_lost_launch_reply_keeps_the_route_for_inventory(announce) -> None:
    launcher = Launcher(refuse=ConnectionError("Launcher disconnected"))
    consumer = RemoteAgentConsumer(launcher, worker_id=LEASE.worker_id)
    body = _body()

    waiting = asyncio.create_task(consumer.process_message(body))
    await asyncio.sleep(0.05)
    assert not waiting.done()
    [route_id] = consumer.routes

    # Reconnect inventory without the route proves it never started.
    await consumer.reject_missing({"routes": [], "terminal_routes": []})
    await asyncio.wait_for(waiting, 1)
    announce.assert_awaited_once()
    assert announce.await_args.args[0] == UUID(body["run_id"])
    assert route_id not in consumer.routes


async def test_a_route_is_released_even_when_its_end_cannot_be_recorded(announce) -> None:
    announce.side_effect = RuntimeError("database unavailable")
    launcher = Launcher(refuse=ConnectionError("Launcher disconnected"))
    consumer = RemoteAgentConsumer(launcher, worker_id=LEASE.worker_id)

    waiting = asyncio.create_task(consumer.process_message(_body()))
    await asyncio.sleep(0.05)
    [route_id] = consumer.routes

    with pytest.raises(RuntimeError):
        await consumer.reject_missing({"routes": [], "terminal_routes": []})
    await asyncio.wait_for(waiting, 1)
    assert not consumer.tracks(route_id)


async def test_a_run_past_its_timeout_is_terminated(announce, monkeypatch) -> None:
    monkeypatch.setattr(agent_route, "SUPERVISE_SECONDS", 0.01)
    monkeypatch.setattr(agent_route, "DEADLINE_GRACE_SECONDS", 0)
    monkeypatch.setattr(agent_route, "run_timeout_seconds", AsyncMock(return_value=0))
    launcher = Launcher()
    consumer = RemoteAgentConsumer(launcher, worker_id=LEASE.worker_id)

    waiting, route_id = await _launch(consumer, launcher, _body())
    for _ in range(100):
        if "terminate" in launcher.actions():
            break
        await asyncio.sleep(0.01)
    await consumer.handle_exit({
        "event": "agent_exit", "route_id": route_id, "ok": False,
        "error_type": "TimeoutError", "error": "Agent run exceeded its 0s timeout",
    })
    await asyncio.wait_for(waiting, 1)

    assert launcher.actions() == ["agent_run", "terminate"]
    assert launcher.requests[1][1]["route_id"] == route_id
    assert launcher.requests[1][1]["error_type"] == "TimeoutError"


async def test_a_cancelled_run_is_terminated_after_its_grace(announce, monkeypatch) -> None:
    monkeypatch.setattr(agent_route, "SUPERVISE_SECONDS", 0.01)
    monkeypatch.setattr(agent_route, "CANCEL_GRACE_SECONDS", 0.05)
    flags = Mock(check_agent_run_cancel_flag=AsyncMock(return_value=True))
    monkeypatch.setattr(agent_route, "get_redis_client", lambda: flags)
    launcher = Launcher()
    consumer = RemoteAgentConsumer(launcher, worker_id=LEASE.worker_id)

    waiting, route_id = await _launch(consumer, launcher, _body())
    await asyncio.sleep(0.02)
    assert launcher.actions() == ["agent_run"], "the child gets its grace first"
    for _ in range(100):
        if "terminate" in launcher.actions():
            break
        await asyncio.sleep(0.01)
    assert launcher.requests[-1][1]["error_type"] == "CancelledError"
    await consumer.handle_exit({"event": "agent_exit", "route_id": route_id, "ok": False})
    await asyncio.wait_for(waiting, 1)


async def test_a_terminated_child_the_launcher_never_reports_is_settled(announce, monkeypatch) -> None:
    monkeypatch.setattr(agent_route, "SUPERVISE_SECONDS", 0.01)
    monkeypatch.setattr(agent_route, "DEADLINE_GRACE_SECONDS", 0)
    monkeypatch.setattr(agent_route, "EXIT_REPORT_SECONDS", 0.05)
    monkeypatch.setattr(agent_route, "run_timeout_seconds", AsyncMock(return_value=0))
    launcher = Launcher()
    consumer = RemoteAgentConsumer(launcher, worker_id=LEASE.worker_id)

    waiting, _route_id = await _launch(consumer, launcher, _body())
    await asyncio.wait_for(waiting, 2)

    announce.assert_awaited_once()
    assert announce.await_args.args[1] == agent_route.MISSING_ERROR


async def test_detaching_stops_waiting_without_terminating_the_child(announce) -> None:
    launcher = Launcher()
    consumer = RemoteAgentConsumer(launcher, worker_id=LEASE.worker_id)

    waiting, _route_id = await _launch(consumer, launcher, _body())
    consumer.detach()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(waiting, 1)
    assert launcher.actions() == ["agent_run"]
    announce.assert_not_awaited()


async def test_a_detached_session_detaches_its_agent_waits() -> None:
    bridge = Bridge(Mock(), {"current": 1, "limit": 100, "requirements": {}, "hostname": "runner"}, LEASE)
    bridge.agents = Mock()
    never = asyncio.Event()
    worker = Mock(request_stop=Mock(), run=AsyncMock(side_effect=never.wait))

    with patch("src.worker.remote.host.release_remote_owner", new_callable=AsyncMock):
        session = RemoteSession(bridge, worker, LEASE, owned_until=0.0)
        await session.detach()

    bridge.agents.detach.assert_called_once_with()


def _bridge() -> Bridge:
    socket = Mock(send_text=AsyncMock())
    return Bridge(socket, {"current": 1, "limit": 100, "requirements": {}, "hostname": "runner"}, LEASE)


def _acks(bridge: Bridge) -> list[str]:
    sent = [json.loads(call.args[0]) for call in bridge.socket.send_text.await_args_list]
    return [frame["payload"]["event_id"] for frame in sent if frame["kind"] == "ack"]


async def test_the_executor_claims_agent_runs_and_routes_them_to_the_launcher() -> None:
    bridge = _bridge()
    executor = ApiExecutor(bridge, LEASE.worker_id)

    consumer = executor._make_agent_consumer()

    assert ("agent.run", 1) in executor._claimable()
    assert ("workflow.execute", 1) in executor._claimable()
    assert isinstance(consumer, RemoteAgentConsumer)
    assert executor._make_agent_consumer() is consumer is bridge.agents


async def test_an_agent_exit_is_acknowledged_only_after_it_was_handled(announce) -> None:
    bridge = _bridge()
    bridge.agents = RemoteAgentConsumer(bridge, worker_id=LEASE.worker_id)
    route = bridge.agents.adopt("agent:route", uuid4())
    bridge.reconciliation_ready.set()

    await bridge.receive({
        "event": "agent_exit", "route_id": "agent:route", "ok": False,
        "error_type": "ProcessCrashError", "error": "boom", "_event_id": "exit-1",
    })
    await asyncio.gather(*bridge.results)

    announce.assert_awaited_once()
    assert route.exited.done()
    assert _acks(bridge) == ["exit-1"]


async def test_an_agent_exit_waits_for_reconciliation_then_is_acknowledged_if_unclaimed() -> None:
    bridge = _bridge()
    bridge.agents = RemoteAgentConsumer(bridge, worker_id=LEASE.worker_id)
    exit_event = {"event": "agent_exit", "route_id": "agent:gone", "ok": True, "_event_id": "exit-2"}

    await bridge.receive(exit_event)
    assert _acks(bridge) == [], "the route may still be adopted from inventory"

    await bridge.reconciliation_complete()
    assert _acks(bridge) == ["exit-2"]


def test_the_api_executor_runs_only_summaries() -> None:
    executor = ApiAgentExecutor(Admission(
        budget_bytes=256 * MIB, under_pressure=lambda: False,
        running_workflow_ids=set, workflow_growth=dict,
    ))
    assert "agent.run" not in {handler for handler, _version in executor._claimable()}


# --- adoption from inventory -------------------------------------------------


@asynccontextmanager
async def _seeded_run(async_session_factory, *, status: str, claimed: bool = True):
    worker_id, run_id = uuid4(), uuid4()
    async with async_session_factory() as session:
        session.add(ExecutionWorker(
            id=worker_id, form="static", state="ready", resource_class="standard", slots=0,
        ))
        session.add(AgentRun(id=run_id, trigger_type="sdk", status=status))
        await session.flush()
        if claimed:
            session.add(ExecutionWorkerClaim(worker_id=worker_id, family="agent_run", subject_id=run_id))
        await session.commit()
    try:
        yield worker_id, run_id
    finally:
        async with async_session_factory() as session:
            await session.execute(delete(ExecutionWorker).where(ExecutionWorker.id == worker_id))
            await session.execute(delete(AgentRun).where(AgentRun.id == run_id))
            await session.commit()


def _agent_route(run_id: UUID) -> dict[str, Any]:
    return {
        "route_id": f"agent:{run_id}:1", "kind": "agent_run", "subject_id": str(run_id),
        "pid": 4242, "memory_request_bytes": 48 * MIB,
    }


@pytest.mark.parametrize("status", ["running", "cancelling"])
async def test_inventory_adopts_a_live_child_of_its_claimed_run(
    async_session_factory, monkeypatch, status: str,
) -> None:
    @asynccontextmanager
    async def context():
        async with async_session_factory() as session:
            yield session

    monkeypatch.setattr("src.core.database.get_db_context", context)
    async with _seeded_run(async_session_factory, status=status) as (worker_id, run_id):
        transport = AsyncMock()
        pool = ThinRemoteProcessPool(transport, worker_id=str(worker_id))
        route = _agent_route(run_id)

        snapshot = await pool.reconcile_inventory({"version": 1, "routes": [route], "terminal_routes": []})

    assert snapshot["agent_runs"] == {route["route_id"]: (run_id, 48 * MIB)}
    transport.request.assert_not_awaited()


@pytest.mark.parametrize("finished", ["completed", "unclaimed"])
async def test_inventory_terminates_a_child_whose_run_is_not_this_workers(
    async_session_factory, monkeypatch, finished: str,
) -> None:
    @asynccontextmanager
    async def context():
        async with async_session_factory() as session:
            yield session

    monkeypatch.setattr("src.core.database.get_db_context", context)
    status = "completed" if finished == "completed" else "running"
    async with _seeded_run(
        async_session_factory, status=status, claimed=finished != "unclaimed",
    ) as (worker_id, run_id):
        transport = AsyncMock()
        pool = ThinRemoteProcessPool(transport, worker_id=str(worker_id))
        route = _agent_route(run_id)

        snapshot = await pool.reconcile_inventory({"version": 1, "routes": [route], "terminal_routes": []})

    assert snapshot["agent_runs"] == {}
    transport.request.assert_awaited_once_with("terminate", {
        "route_id": route["route_id"], "error_type": "OwnerLost",
        "error": "Claim no longer belongs to this launcher",
    })


async def test_an_adopted_child_holds_its_memory_and_counts_as_a_run_until_it_exits(announce) -> None:
    bridge = _bridge()
    executor = ApiExecutor(bridge, LEASE.worker_id)
    consumer = executor._make_agent_consumer()
    executor.admission = Admission(
        budget_bytes=512 * MIB, under_pressure=lambda: False,
        running_workflow_ids=set, workflow_growth=dict,
    )
    run_id = uuid4()

    await executor._apply_reconciled_snapshot({
        "workflow_requests": {}, "services": [], "platform_jobs": [],
        "agent_runs": {"agent:adopted": (run_id, 48 * MIB)},
    })
    assert executor.admission.held_bytes() == 48 * MIB
    assert executor.active_runs() == 1

    await consumer.handle_exit({"event": "agent_exit", "route_id": "agent:adopted", "ok": True})
    await asyncio.gather(*executor._tasks["agent.run"])
    assert executor.admission.held_bytes() == 0
    assert executor.active_runs() == 0
    announce.assert_not_awaited()


async def test_a_drain_past_its_grace_ends_live_agent_runs_before_retiring(announce, monkeypatch) -> None:
    """The launcher kills every child once retired, so a run still going at
    the drain deadline is terminated and ended (waking its callers) first."""
    monkeypatch.setattr("src.worker.pool_main.DRAIN_GRACE_SECONDS", 0.05)
    monkeypatch.setattr("src.worker.pool_main.replica_heartbeat_loop", AsyncMock())
    monkeypatch.setattr("src.worker.pool_main.remove_scheduler_replica", AsyncMock())
    monkeypatch.setattr("src.worker.remote.host.owns_remote_session", AsyncMock(return_value=True))
    launcher = Launcher()
    bridge = _bridge()
    executor = ApiExecutor(bridge, LEASE.worker_id)
    executor._on_ready = None
    for name in (
        "_start_executor", "_claim_loop", "_heartbeat_loop", "_wake_listener",
        "_package_loop", "_platform_job_loop",
    ):
        monkeypatch.setattr(executor, name, AsyncMock())
    announced_before_retire: list[int] = []

    async def retire() -> None:
        announced_before_retire.append(announce.await_count)

    monkeypatch.setattr(executor, "_retire", retire)
    bridge.agents = consumer = RemoteAgentConsumer(launcher, worker_id=LEASE.worker_id)
    body = _body()
    waiting, route_id = await _launch(consumer, launcher, body)
    executor._tasks["agent.run"].add(waiting)

    executor.request_stop()
    assert await asyncio.wait_for(executor.run(), 5) == 0

    assert launcher.actions() == ["agent_run", "terminate"]
    assert launcher.requests[1][1]["route_id"] == route_id
    assert launcher.requests[1][1]["error_type"] == "WorkerShutdown"
    announce.assert_awaited_once_with(UUID(body["run_id"]), "Remote pool drain deadline exceeded")
    assert announced_before_retire == [1], "the run ends before the launcher is retired"
    assert consumer.routes == {}


async def test_a_reconnected_launcher_gets_no_work_until_its_inventory_is_reconciled(
    announce, monkeypatch,
) -> None:
    """The launcher takes its inventory before it reads any command. A run
    claimed between the reconnect and reconciliation would be missing from
    that inventory and failed while its child runs, so nothing is claimed
    (workflow or agent runs, services, platform jobs) until reconciliation."""
    from datetime import datetime, timezone

    from src.services.worker_pool.claims import PoolClaim

    launcher = Launcher()
    bridge = _bridge()
    executor = ApiExecutor(bridge, LEASE.worker_id)
    executor.admission = Admission(
        budget_bytes=512 * MIB, under_pressure=executor._under_pressure,
        running_workflow_ids=set, workflow_growth=dict, claims_open=executor._claims_open,
    )
    bridge.agents = executor._agent_consumer = consumer = RemoteAgentConsumer(
        launcher, worker_id=LEASE.worker_id,
    )
    body = _body()
    claim = PoolClaim(
        dispatch_id=UUID(body["dispatch_id"]), handler="agent.run", message=body,
        subject_id=UUID(body["run_id"]), memory_request_bytes=32 * MIB,
        queued_at=datetime.now(timezone.utc),
    )

    async def claim_next(*_args: Any, reserve_claim: Any, **_kwargs: Any) -> PoolClaim:
        reserve_claim(claim)
        return claim

    claim_next_mock = AsyncMock(side_effect=claim_next)
    monkeypatch.setattr("src.worker.pool_main.claim_next", claim_next_mock)
    await bridge.reconciliation_complete()

    bridge.replace_socket(Mock(send_text=AsyncMock()), LEASE)
    assert await executor._claim_and_start() is None
    async with executor.admission.claiming() as fits:
        assert fits is None, "service and platform-job claims wait too"
    claim_next_mock.assert_not_awaited()
    # The hold is not memory pressure: the heartbeat keeps telling the truth.
    heartbeat = AsyncMock(return_value="ready")
    monkeypatch.setattr("src.worker.pool_main.heartbeat_worker", heartbeat)
    beating = asyncio.create_task(executor._heartbeat_loop())
    for _ in range(100):
        if heartbeat.await_count:
            break
        await asyncio.sleep(0.01)
    beating.cancel()
    await asyncio.gather(beating, return_exceptions=True)
    assert heartbeat.await_args.kwargs["memory_pressured"] is False

    # The reconnect inventory, taken before any new command, is reconciled.
    await consumer.reject_missing({"routes": [], "terminal_routes": []})
    await bridge.reconciliation_complete()
    assert await executor._claim_and_start() == claim
    await asyncio.wait_for(launcher.launched.wait(), 1)
    assert list(consumer.routes.values())[0].run_id == UUID(body["run_id"])
    announce.assert_not_awaited()

    consumer.detach()
    await asyncio.gather(*executor._tasks["agent.run"], return_exceptions=True)
