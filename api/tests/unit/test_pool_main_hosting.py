"""An executor also hosts services and local platform jobs (#769 stage 4c)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from src.jobs.schedulers import platform_jobs
from src.worker.pool_main import PoolWorker


def _worker_with_pool(services: int = 0) -> PoolWorker:
    worker = PoolWorker()
    pool = SimpleNamespace(
        service_processes={f"svc-{i}": object() for i in range(services)},
        active_execution_count=lambda: 0,
        drain_active_executions=AsyncMock(return_value=True),
    )
    worker._workflow_consumer = SimpleNamespace(_pool=pool)  # type: ignore[assignment]
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


async def test_a_platform_job_is_active_only_while_it_runs(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    claim = SimpleNamespace(id=uuid4())
    seen_during_run: list[bool] = []
    active: set = set()

    async def run(claimed) -> bool:  # type: ignore[no-untyped-def]
        seen_during_run.append(claimed.id in active)
        return True

    monkeypatch.setattr(platform_jobs, "recover_expired_platform_jobs", AsyncMock(return_value=(0, 0)))
    monkeypatch.setattr(platform_jobs, "claim_platform_job", AsyncMock(return_value=claim))
    monkeypatch.setattr(platform_jobs, "run_claimed_platform_job", run)

    assert await platform_jobs.process_platform_jobs(active) == (1, 0)
    assert seen_during_run == [True]
    assert active == set()


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
        _pool = SimpleNamespace(service_processes={}, active_execution_count=lambda: 0)

        async def start_pool(self) -> None:
            order.append("pool started")

        async def stop_pool(self) -> None:
            order.append("pool stopped")

    async def claim_next(incarnation_id, handlers):  # type: ignore[no-untyped-def]
        order.append("claim")
        worker._stop.set()

    for name in ("init_db", "wait_for_schema", "close_db", "begin_drain", "retire_worker",
                 "remove_scheduler_replica", "replica_heartbeat_loop",
                 "platform_job_worker_loop"):
        monkeypatch.setattr(pool_main, name, AsyncMock())
    monkeypatch.setattr(pool_main, "get_engine", lambda *args: None)
    monkeypatch.setattr(pool_main, "register_static_worker", AsyncMock(return_value=uuid4()))
    monkeypatch.setattr(pool_main, "current_package_generation", AsyncMock(return_value=0))
    monkeypatch.setattr(pool_main, "start_kubernetes_loops", AsyncMock(return_value=[]))
    monkeypatch.setattr(pool_main, "claim_next", claim_next)
    monkeypatch.setattr(workflow_execution, "WorkflowExecutionConsumer", _Workflows)
    monkeypatch.setattr(agent_run, "AgentRunConsumer", lambda: object())
    monkeypatch.setattr(package_install, "PackageInstallConsumer", lambda: object())

    worker = PoolWorker()
    for loop in ("_heartbeat_loop", "_wake_listener", "_package_loop",
                 "_start_service_claim_loop", "_drain"):
        monkeypatch.setattr(worker, loop, AsyncMock())

    assert await worker.run() == 0
    assert order == ["pool started", "claim", "pool stopped"]


def test_over_the_memory_threshold_an_executor_claims_nothing(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from src.worker import pool_main

    worker = _worker_with_pool()
    limit = 1000 * 2**20
    monkeypatch.setattr(pool_main, "get_cgroup_memory", lambda: (int(limit * 0.95), limit))
    assert worker._claimable() == ()
    assert worker._memory_pressured, "reported in the next heartbeat"

    monkeypatch.setattr(pool_main, "get_cgroup_memory", lambda: (int(limit * 0.5), limit))
    assert ("workflow.execute", 1) in worker._claimable()
    assert not worker._memory_pressured


async def test_a_refused_fork_hands_the_run_back(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from src.services.execution import workflow_dispatch
    from src.worker.pool_main import PoolClaim

    worker = _worker_with_pool()
    worker._workflow_consumer.process_message = AsyncMock(side_effect=MemoryError("pressure"))  # type: ignore[union-attr]
    worker._agent_consumer = object()  # type: ignore[assignment]
    release = AsyncMock()
    monkeypatch.setattr(workflow_dispatch, "release_workflow_run", release)
    claim = PoolClaim(
        dispatch_id=uuid4(),
        handler="workflow.execute",
        message={"execution_id": "e1"},
        claimed={"execution_id": "e1", "sync": False},
    )

    await worker._run_claim(claim)

    release.assert_awaited_once_with(
        worker_id=worker.incarnation_id,
        message={"execution_id": "e1"},
        payload={"execution_id": "e1", "sync": False},
    )
