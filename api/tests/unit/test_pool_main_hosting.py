"""An executor also hosts services and local platform jobs (#769 stage 4c)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

from src.services.memory_requests import MIB, MemoryBudget
from src.worker import pool_main
from src.worker.admission import Admission
from src.worker.pool_main import PoolClaim, PoolWorker

GIB = 1024 * MIB


def _worker_with_pool(
    services: int = 0, running: set[str] | None = None, budget: int = GIB
) -> PoolWorker:
    """``running``: execution ids whose children are still alive."""
    worker = PoolWorker()
    children = running if running is not None else set()
    pool = SimpleNamespace(
        service_processes={f"svc-{i}": object() for i in range(services)},
        active_execution_count=lambda: len(children),
        active_execution_ids=lambda: set(children),
        workflow_growth=dict,
        drain_active_executions=AsyncMock(return_value=True),
    )
    worker._workflow_consumer = SimpleNamespace(_pool=pool)  # type: ignore[assignment]
    worker.admission = Admission(
        budget_bytes=budget,
        under_pressure=lambda: False,
        running_workflow_ids=pool.active_execution_ids,
        workflow_growth=pool.workflow_growth,
    )
    return worker


def test_services_and_platform_jobs_keep_an_executor_busy_but_free_no_run_slots() -> None:
    idle = _worker_with_pool()
    assert not idle.busy()

    with_service = _worker_with_pool(services=1)
    assert with_service.busy()
    assert with_service.active_runs() == 0, "capacity planning counts runs only"

    with_job = _worker_with_pool()
    with_job._platform_jobs.add(uuid4())
    assert with_job.busy()
    assert with_job.active_runs() == 0


async def test_a_platform_job_holds_its_memory_only_while_it_runs(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    worker = _worker_with_pool()
    claim = SimpleNamespace(id=uuid4(), memory_required_bytes=300 * 2**20)
    seen: list[tuple[bool, int]] = []
    fits: list[int] = []

    async def claim_platform_job(*, backend, fits_bytes):  # type: ignore[no-untyped-def]
        fits.append(fits_bytes)
        return claim if len(fits) == 1 else None

    async def run(claimed) -> bool:  # type: ignore[no-untyped-def]
        seen.append((claimed.id in worker._platform_jobs, worker.admission.held_bytes()))
        worker._platform_stop.set()
        return True

    monkeypatch.setattr(pool_main, "recover_expired_platform_jobs", AsyncMock(return_value=(0, 0)))
    monkeypatch.setattr(pool_main, "claim_platform_job", claim_platform_job)
    monkeypatch.setattr(pool_main, "run_claimed_platform_job", run)

    await worker._platform_job_loop()
    await asyncio.sleep(0)

    assert fits[0] == GIB
    assert seen == [(True, 300 * 2**20)]
    assert worker._platform_jobs == set()
    assert worker.admission.held_bytes() == 0


async def test_draining_hands_services_over_first_and_waits_for_platform_jobs(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    worker = _worker_with_pool(services=1)
    order: list[str] = []
    service_loop = SimpleNamespace(stop=AsyncMock(side_effect=lambda: order.append("services")))
    worker._service_claim_loop = service_loop  # type: ignore[assignment]
    job = uuid4()
    worker._platform_jobs.add(job)

    drain = asyncio.create_task(worker._drain())
    await asyncio.sleep(0.1)
    assert order == ["services"]
    assert worker._platform_stop.is_set(), "no new platform job is claimed"
    assert not drain.done(), "a running platform job is waited for"

    worker._platform_jobs.discard(job)
    await asyncio.wait_for(drain, timeout=2)


async def test_draining_hands_workflow_children_the_rest_of_the_grace(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Children finish (or are recorded as interrupted) only after the
    in-flight claims that route them have settled."""
    from src.worker import pool_main

    monkeypatch.setattr(pool_main, "DRAIN_GRACE_SECONDS", 30.0)
    worker = _worker_with_pool()
    routing = asyncio.create_task(asyncio.sleep(0.2))
    worker._tasks["workflow.execute"].add(routing)
    routing.add_done_callback(worker._tasks["workflow.execute"].discard)
    pool = worker._workflow_consumer._pool  # type: ignore[union-attr]

    await asyncio.wait_for(worker._drain(), timeout=5)

    assert routing.done(), "the run being routed was waited for first"
    pool.drain_active_executions.assert_awaited_once()
    (grace,) = pool.drain_active_executions.await_args.args
    assert 0 < grace <= 30.0


async def test_an_incarnation_makes_a_job_worker_and_none_a_static_one(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from src.worker import pool_main

    formed: list[tuple[bool, object]] = []

    async def run(self) -> int:  # type: ignore[no-untyped-def]
        formed.append((self.static, self._on_ready))
        return 0

    monkeypatch.setattr(pool_main.PoolWorker, "run", run)
    ready = object()

    monkeypatch.delenv("BIFROST_WORKER_INCARNATION_ID", raising=False)
    assert await pool_main.run_executor(on_ready=ready) == 0  # type: ignore[arg-type]

    monkeypatch.setenv("BIFROST_WORKER_INCARNATION_ID", str(uuid4()))
    monkeypatch.setenv("BIFROST_KUBERNETES_JOB_UID", "job-uid")
    monkeypatch.setenv("BIFROST_KUBERNETES_POD_UID", "pod-uid")
    assert await pool_main.run_executor() == 0

    assert formed == [(True, ready), (False, None)]


async def test_the_process_pool_is_ready_before_any_work_is_claimed(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Regression: a worker once took work before its pool's template process
    had started, and a since-removed spawn fallback leaked ~800 MB per pod."""
    from src.jobs.consumers import agent_run, package_install, workflow_execution
    from src.worker import pool_main

    order: list[str] = []

    class _Workflows:
        _pool = SimpleNamespace(
            service_processes={}, active_execution_count=lambda: 0, active_execution_ids=set,
            workflow_growth=dict,
        )

        async def start_pool(self) -> None:
            await asyncio.sleep(0)  # starting the pool waits on I/O
            order.append("pool started")

        async def stop_pool(self) -> None:
            order.append("pool stopped")

    async def claim_next(incarnation_id, handlers, *, fits_bytes, budget_bytes):  # type: ignore[no-untyped-def]
        order.append("claim")
        worker._stop.set()

    for name in ("init_db", "wait_for_schema", "close_db", "begin_drain", "retire_worker",
                 "remove_scheduler_replica", "replica_heartbeat_loop",
                 "recover_expired_platform_jobs", "claim_platform_job"):
        monkeypatch.setattr(pool_main, name, AsyncMock())
    monkeypatch.setattr(pool_main, "get_engine", lambda *args: None)
    monkeypatch.setattr(pool_main, "register_static_worker", AsyncMock(return_value=uuid4()))
    monkeypatch.setattr(pool_main, "current_package_generation", AsyncMock(return_value=0))
    monkeypatch.setattr(pool_main, "start_kubernetes_loops", AsyncMock(return_value=[]))
    monkeypatch.setattr(pool_main, "claim_next", claim_next)
    monkeypatch.setattr(
        pool_main, "measure_budget",
        lambda threshold: MemoryBudget(limit_bytes=GIB, baseline_bytes=0, threshold=threshold),
    )
    monkeypatch.setattr(workflow_execution, "WorkflowExecutionConsumer", _Workflows)
    monkeypatch.setattr(agent_run, "AgentRunConsumer", object)
    monkeypatch.setattr(package_install, "PackageInstallConsumer", object)

    worker = PoolWorker()
    for loop in ("_wake_listener", "_package_loop", "_start_service_claim_loop", "_drain"):
        monkeypatch.setattr(worker, loop, AsyncMock())

    async def heartbeat() -> None:
        order.append("heartbeat")

    monkeypatch.setattr(worker, "_heartbeat_loop", heartbeat)

    assert await worker.run() == 0
    # Heartbeats start at registration: starting the pool can take longer
    # than a silent executor is given before it counts as lost.
    assert order == ["heartbeat", "pool started", "claim", "pool stopped"]


QUEUED_AT = datetime(2026, 10, 23, tzinfo=timezone.utc)


def _workflow_claim(execution_id: str, request: int) -> PoolClaim:
    return PoolClaim(
        dispatch_id=uuid4(),
        handler="workflow.execute",
        message={"execution_id": execution_id},
        subject_id=UUID(execution_id),
        memory_request_bytes=request,
        queued_at=QUEUED_AT,
        claimed={"execution_id": execution_id, "sync": False},
    )


async def _run_to_routed(worker: PoolWorker, claim: PoolClaim) -> None:
    worker._start(claim)
    await asyncio.gather(*worker._tasks["workflow.execute"])


async def test_a_workflow_run_is_held_until_its_child_ends() -> None:
    execution_id = str(uuid4())
    children = {execution_id}
    worker = _worker_with_pool(running=children)
    worker._workflow_consumer.process_message = AsyncMock()  # type: ignore[union-attr]
    worker._agent_consumer = object()  # type: ignore[assignment]

    await _run_to_routed(worker, _workflow_claim(execution_id, 100 * MIB))
    assert worker.admission.free_bytes() == GIB - 100 * MIB, "routed, its child still runs"
    assert worker.active_runs() == 1

    children.clear()
    assert worker.admission.free_bytes() == GIB


async def test_a_refused_fork_hands_the_run_back(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from src.services.execution import workflow_dispatch

    worker = _worker_with_pool()
    worker._workflow_consumer.process_message = AsyncMock(side_effect=MemoryError("pressure"))  # type: ignore[union-attr]
    worker._agent_consumer = object()  # type: ignore[assignment]
    release = AsyncMock()
    monkeypatch.setattr(workflow_dispatch, "release_workflow_run", release)
    execution_id = str(uuid4())

    await _run_to_routed(worker, _workflow_claim(execution_id, 100 * MIB))

    release.assert_awaited_once_with(
        worker_id=worker.incarnation_id,
        message={"execution_id": execution_id},
        payload={"execution_id": execution_id, "sync": False},
        memory_request_bytes=100 * MIB,
        queued_at=QUEUED_AT,
    )
    assert worker.admission.free_bytes() == GIB, "the hold went back with the run"


async def test_an_executor_declared_lost_stops_its_runs_without_a_grace(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The control plane already ended its runs; their results are refused."""
    worker = _worker_with_pool()
    monkeypatch.setattr(pool_main, "heartbeat_worker", AsyncMock(return_value=None))
    beating = asyncio.create_task(worker._heartbeat_loop())
    await asyncio.wait_for(worker._stop.wait(), timeout=1)
    beating.cancel()

    await worker._drain()

    assert worker._lost
    pool = worker._workflow_consumer._pool  # type: ignore[union-attr]
    pool.drain_active_executions.assert_awaited_once_with(0.0)


async def test_a_database_error_while_claiming_does_not_end_the_executor(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A failover or dropped connection must not crash the executor and fail
    its runs: the claim loop backs off and looks again."""
    worker = _worker_with_pool()
    worker._agent_consumer = object()  # type: ignore[assignment]
    calls: list[str] = []

    async def claim_next(incarnation_id, handlers, *, fits_bytes, budget_bytes):  # type: ignore[no-untyped-def]
        calls.append("claim")
        if len(calls) == 1:
            raise ConnectionError("database restarting")
        worker._stop.set()

    monkeypatch.setattr(pool_main, "claim_next", claim_next)
    monkeypatch.setattr(pool_main, "HEARTBEAT_SECONDS", 0.01)
    monkeypatch.setattr(pool_main, "begin_drain", AsyncMock())

    release = AsyncMock()
    monkeypatch.setattr(pool_main, "release_unstarted_claims", release)

    await asyncio.wait_for(worker._claim_loop(), timeout=5)

    assert calls == ["claim", "claim"]
    pool_main.begin_drain.assert_awaited_once()
    # Its claim may have committed though the reply was lost.
    release.assert_awaited_once()


async def test_a_hung_heartbeat_does_not_stop_the_next_one(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    worker = _worker_with_pool()
    attempts: list[int] = []

    async def heartbeat_worker(*args, **kwargs):  # type: ignore[no-untyped-def]
        attempts.append(1)
        await asyncio.sleep(60)  # a connection hung on a dead network

    monkeypatch.setattr(pool_main, "heartbeat_worker", heartbeat_worker)
    monkeypatch.setattr(pool_main, "HEARTBEAT_SECONDS", 0.05)
    beating = asyncio.create_task(worker._heartbeat_loop())
    await asyncio.sleep(0.4)
    beating.cancel()

    assert len(attempts) >= 3


async def test_a_lost_executor_cancels_its_agent_work() -> None:
    worker = _worker_with_pool()
    worker._lost = True
    agent = asyncio.create_task(asyncio.sleep(60))
    worker._tasks["agent.run"].add(agent)
    agent.add_done_callback(worker._tasks["agent.run"].discard)

    await asyncio.wait_for(worker._drain(), timeout=5)
    await asyncio.gather(agent, return_exceptions=True)

    assert agent.cancelled()
