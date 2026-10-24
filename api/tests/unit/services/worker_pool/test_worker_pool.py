"""Reusable executor protocol against real PostgreSQL and a fake cluster."""

from __future__ import annotations

import asyncio
import copy
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, AsyncGenerator
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.config import get_settings
from src.core.cache.redis_client import get_redis
from src.models.enums import ExecutionStatus
from src.models.orm.execution_workers import ExecutionWorker
from src.models.orm.executions import Execution
from src.models.orm.work_dispatches import WorkDispatch
from src.models.orm.workflows import Workflow
from src.services import work_dispatch
from src.services.execution import async_executor, workflow_dispatch
from src.services.execution.async_executor import _publish_pending
from src.services.memory_requests import (
    DEFAULT_WORKFLOW_REQUEST_BYTES,
    LAUNCHABLE_KEY,
    MIB,
    WORKFLOW_REQUEST_MARGIN_BYTES,
)
from src.services.worker_pool import claims, controller, liveness, maintenance
from src.services.worker_pool.manifest import WorkerJobSpec, build_worker_job_manifest

GIB = 1024 * MIB

SETTINGS = SimpleNamespace(
    kubernetes_build_namespace="test",
    kubernetes_build_image="api:test",
    kubernetes_build_configmap="config",
    kubernetes_build_secret="secret",
    kubernetes_build_service_account="runner",
    pool_max_workers=2,
    pool_min_workers=0,
    pool_idle_seconds=300,
    pool_max_lifetime_seconds=21600,
    pool_start_timeout_seconds=300,
    pool_worker_memory_request_mib=512,
    pool_worker_memory_limit_mib=2048,
    pool_worker_max_memory_mib=8192,
    pool_worker_cpu_request="250m",
    pool_worker_cpu_limit="2",
    memory_pressure_threshold=0.85,
    redis_url=get_settings().redis_url,
)

SPEC = WorkerJobSpec(
    namespace="test",
    image="api:test",
    env_from=({"configMapRef": {"name": "config"}}, {"secretRef": {"name": "secret"}}),
    service_account="runner",
)


class FakeCluster:
    namespace = "test"

    def __init__(self) -> None:
        self.jobs: dict[str, dict[str, Any]] = {}
        # Pods by Job UID (only what a test sets).
        self.pods: dict[str, list[dict[str, Any]]] = {}
        self.created: list[str] = []
        self.started: list[str] = []
        self.deleted: list[str] = []

    async def get_job(self, name: str) -> dict[str, Any] | None:
        await asyncio.sleep(0)
        job = self.jobs.get(name)
        return copy.deepcopy(job) if job else None

    async def create_job(self, manifest: dict[str, Any]) -> dict[str, Any]:
        await asyncio.sleep(0)
        name = manifest["metadata"]["name"]
        if name not in self.jobs:
            job = copy.deepcopy(manifest)
            job["metadata"]["uid"] = str(uuid4())
            self.jobs[name] = job
            self.created.append(name)
        return copy.deepcopy(self.jobs[name])

    async def start_job(self, name: str, uid: str) -> dict[str, Any]:
        job = self.jobs[name]
        assert job["metadata"]["uid"] == uid
        job["spec"]["suspend"] = False
        self.started.append(name)
        return copy.deepcopy(job)

    async def list_pods(self, job_uid: str) -> list[dict[str, Any]]:
        await asyncio.sleep(0)
        return copy.deepcopy(self.pods.get(job_uid, []))

    async def delete_job(self, name: str, uid: str) -> None:
        assert self.jobs[name]["metadata"]["uid"] == uid
        del self.jobs[name]
        self.deleted.append(name)


@pytest.fixture
def db(async_session_factory: async_sessionmaker[AsyncSession], monkeypatch):
    @asynccontextmanager
    async def context() -> AsyncGenerator[AsyncSession, None]:
        async with async_session_factory() as session:
            yield session
            await session.commit()

    for module in (claims, controller, liveness, maintenance, work_dispatch, workflow_dispatch):
        monkeypatch.setattr(module, "get_db_context", context)
    monkeypatch.setattr(claims, "get_liveness_db_context", context)
    # These controllers have long reached the database (liveness.Reachability).
    monkeypatch.setattr(
        controller,
        "Reachability",
        lambda: liveness.Reachability(connected_since=time.monotonic() - 3600),
    )
    monkeypatch.setattr(async_executor, "get_dispatch_db_context", context)
    monkeypatch.setattr("src.config.get_settings", lambda: SETTINGS)
    wake = AsyncMock()
    monkeypatch.setattr(work_dispatch, "wake_pool_workers", wake)
    monkeypatch.setattr(workflow_dispatch, "get_redis_client", AsyncMock)
    monkeypatch.setattr("src.services.execution.queue_tracker.add_to_queue", AsyncMock())
    return SimpleNamespace(sessions=async_session_factory, wake=wake)


async def _clear_pool_state(sessions) -> None:  # type: ignore[no-untyped-def]
    from src.models.orm.platform_jobs import PlatformJob

    async with sessions() as session:
        await session.execute(delete(WorkDispatch))
        await session.execute(delete(ExecutionWorker))
        # Queued platform jobs count as launch demand; the stack's scheduler
        # enqueues some (model catalog, Solution updates) during the reset.
        await session.execute(delete(PlatformJob).where(PlatformJob.status == "queued"))
        await session.commit()
    # What a worker controller last published it can launch.
    async with get_redis() as redis:
        await redis.delete(LAUNCHABLE_KEY)


@pytest.fixture(autouse=True)
async def _clean(async_session_factory):
    # Before too: the stack's executors register (with a measured budget)
    # during the state reset and are stopped for unit runs; a row a killed
    # one left "ready" would count as capacity.
    await _clear_pool_state(async_session_factory)
    yield
    await _clear_pool_state(async_session_factory)


async def _accept_pool_run() -> str:
    execution_id = str(uuid4())
    await _publish_pending(
        execution_id=execution_id, workflow_id=None, parameters={}, org_id=None,
        user_id="00000000-0000-0000-0000-000000000001", user_name="Dev", user_email="",
        form_id=None, startup=None, form_inputs={}, embed={}, api_key_id=None,
        sync=False, is_platform_admin=True, file_path=None, lineage=None,
    )
    return execution_id


async def _claim(worker_id: UUID, *, fits: int = GIB, budget: int = GIB) -> claims.PoolClaim | None:
    return await claims.claim_next(worker_id, fits_bytes=fits, budget_bytes=budget)


async def _set_request(sessions, execution_id: str, request: int, *, age: timedelta | None = None) -> None:  # type: ignore[no-untyped-def]
    values: dict[str, Any] = {"memory_request_bytes": request}
    if age is not None:
        values["created_at"] = datetime.now(timezone.utc) - age
    async with sessions() as session:
        await session.execute(
            update(WorkDispatch).where(WorkDispatch.subject_id == UUID(execution_id)).values(**values)
        )
        await session.commit()


async def _report_budget(worker_id: UUID, budget: int, *, pressured: bool = False) -> None:
    await claims.heartbeat_worker(
        worker_id, active_runs=0, runs_handled=0, package_generation=0,
        memory_pressured=pressured, memory_budget_bytes=budget, memory_reserved_bytes=0,
    )


async def _job_worker(sessions, *, state: str = "starting") -> ExecutionWorker:  # type: ignore[no-untyped-def]
    worker_id = uuid4()
    async with sessions() as session:
        worker = ExecutionWorker(
            id=worker_id, form="job", state=state, resource_class="standard", slots=2,
            kubernetes_namespace="test", kubernetes_job_name=f"bifrost-worker-{worker_id.hex[:24]}",
            kubernetes_job_uid="job-uid",
        )
        session.add(worker)
        await session.commit()
        return worker


# --- acceptance ------------------------------------------------------------


async def test_accepted_runs_are_claimable_and_wake_the_executors(db) -> None:
    execution_id = await _accept_pool_run()

    async with db.sessions() as session:
        dispatch = (
            await session.execute(
                select(WorkDispatch).where(WorkDispatch.subject_id == UUID(execution_id))
            )
        ).scalar_one()
    assert dispatch.assigned_worker_id is None
    db.wake.assert_awaited_once()
    # An unclaimed dispatch stays put until its claim deadline.
    async with db.sessions() as session:
        await session.execute(
            update(WorkDispatch).values(created_at=datetime.now(timezone.utc) - timedelta(minutes=5))
        )
        await session.commit()
    await work_dispatch.reconcile_work_dispatches()
    async with db.sessions() as session:
        assert await session.get(WorkDispatch, dispatch.id) is not None


async def test_only_the_first_pod_of_the_bound_job_is_admitted(db) -> None:
    worker = await _job_worker(db.sessions)

    assert await claims.admit_worker(worker.id, job_uid="other-job", pod_uid="p0") is None
    assert await claims.admit_worker(worker.id, job_uid="job-uid", pod_uid="p1") == 2
    assert await claims.admit_worker(worker.id, job_uid="job-uid", pod_uid="p2") is None


async def test_a_ready_worker_claims_each_dispatch_once(db) -> None:
    first = await _job_worker(db.sessions, state="ready")
    second = await _job_worker(db.sessions, state="ready")
    execution_id = await _accept_pool_run()

    claims_made = await asyncio.gather(_claim(first.id), _claim(second.id))

    winners = [claim for claim in claims_made if claim is not None]
    assert len(winners) == 1
    assert winners[0].message["execution_id"] == execution_id
    assert winners[0].handler == "workflow.execute"


async def test_a_draining_worker_never_claims(db) -> None:
    worker = await _job_worker(db.sessions, state="ready")
    assert await claims.begin_drain(worker.id, "idle")
    await _accept_pool_run()

    assert await _claim(worker.id) is None
    assert not await claims.begin_drain(worker.id, "idle")  # once only


async def test_unsupported_handler_versions_wait_for_a_compatible_worker(db) -> None:
    worker = await _job_worker(db.sessions, state="ready")
    execution_id = await _accept_pool_run()
    async with db.sessions() as session:
        await session.execute(update(WorkDispatch).values(handler_version=99))
        await session.commit()

    assert await _claim(worker.id) is None
    async with db.sessions() as session:
        dispatch = (
            await session.execute(
                select(WorkDispatch).where(WorkDispatch.subject_id == UUID(execution_id))
            )
        ).scalar_one()
    assert dispatch.assigned_worker_id is None


async def test_static_executors_register_ready_and_claim(db) -> None:
    worker_id = await claims.register_static_worker(slots=3, hostname="worker-1", memory_budget_bytes=GIB)
    await _accept_pool_run()

    assert await _claim(worker_id) is not None


# --- claiming by memory --------------------------------------------------------


async def test_work_larger_than_the_free_budget_waits_while_smaller_work_is_claimed(db) -> None:
    worker_id = await claims.register_static_worker(slots=1, hostname="static-1", memory_budget_bytes=GIB)
    large = await _accept_pool_run()
    await _set_request(db.sessions, large, 500 * MIB, age=timedelta(seconds=5))
    small = await _accept_pool_run()

    claim = await _claim(worker_id, fits=100 * MIB)

    assert claim is not None and claim.message["execution_id"] == small


async def test_long_waiting_work_an_executor_could_hold_blocks_newer_claims(db) -> None:
    worker_id = await claims.register_static_worker(slots=1, hostname="static-1", memory_budget_bytes=GIB)
    large = await _accept_pool_run()
    await _set_request(
        db.sessions, large, 500 * MIB, age=claims.HEAD_OF_LINE_WAIT + timedelta(seconds=5)
    )
    small = await _accept_pool_run()

    assert await _claim(worker_id, fits=100 * MIB, budget=GIB) is None
    # An executor whose whole budget could never hold it is not held up.
    claim = await _claim(worker_id, fits=100 * MIB, budget=400 * MIB)
    assert claim is not None and claim.message["execution_id"] == small


async def test_a_worker_launched_for_a_dispatch_claims_it_first(db) -> None:
    worker = await _job_worker(db.sessions, state="ready")
    await _accept_pool_run()
    reserved = await _accept_pool_run()
    async with db.sessions() as session:
        dispatch_id = await session.scalar(
            select(WorkDispatch.id).where(WorkDispatch.subject_id == UUID(reserved))
        )
        await session.execute(
            update(ExecutionWorker)
            .where(ExecutionWorker.id == worker.id)
            .values(reserved_for_dispatch_id=dispatch_id)
        )
        await session.commit()

    claim = await _claim(worker.id)

    assert claim is not None and claim.dispatch_id == dispatch_id
    async with db.sessions() as session:
        assert (await session.get(ExecutionWorker, worker.id)).reserved_for_dispatch_id is None


async def test_a_worker_launched_for_a_dispatch_takes_it_even_when_smaller_than_planned(db) -> None:
    """Otherwise the dispatch stays uncovered and the controller launches
    another Job of the same wrong size, again and again."""
    worker = await _job_worker(db.sessions, state="ready")
    reserved = await _accept_pool_run()
    await _set_request(db.sessions, reserved, 900 * MIB)
    async with db.sessions() as session:
        dispatch_id = await session.scalar(
            select(WorkDispatch.id).where(WorkDispatch.subject_id == UUID(reserved))
        )
        await session.execute(
            update(ExecutionWorker)
            .where(ExecutionWorker.id == worker.id)
            .values(reserved_for_dispatch_id=dispatch_id)
        )
        await session.commit()

    claim = await _claim(worker.id, fits=800 * MIB, budget=800 * MIB)

    assert claim is not None and claim.dispatch_id == dispatch_id


def test_the_job_baseline_learns_from_sized_jobs_too() -> None:
    sized = SimpleNamespace(
        form="job", memory_limit_bytes=4 * GIB, memory_budget_bytes=2 * GIB
    )
    expected = int(4 * GIB * SETTINGS.memory_pressure_threshold) - 2 * GIB
    assert controller._job_baseline([sized], SETTINGS) == expected  # type: ignore[arg-type]


async def test_a_sized_job_drains_when_idle_and_is_never_the_warm_minimum(db) -> None:
    sized = await _job_worker(db.sessions, state="ready")
    async with db.sessions() as session:
        await session.execute(
            update(ExecutionWorker)
            .where(ExecutionWorker.id == sized.id)
            .values(resource_class="sized", memory_limit_bytes=8 * GIB)
        )
        await session.commit()

    warm = SimpleNamespace(**{**vars(SETTINGS), "pool_min_workers": 1})
    launched = await controller.WorkerPoolController(FakeCluster(), warm, SPEC).launch_for_demand()  # type: ignore[arg-type]
    assert len(launched) == 1, "the warm minimum is a standard Job"
    assert await claims.begin_idle_drain(sized.id, keep_ready=1)


# --- controller ---------------------------------------------------------------


async def test_demand_launches_bounded_workers_and_binds_before_start(db) -> None:
    for _ in range(3):  # each needs a standard worker to itself; the ceiling is 2
        await _set_request(db.sessions, await _accept_pool_run(), 600 * MIB)
    cluster = FakeCluster()
    replicas = [controller.WorkerPoolController(cluster, SETTINGS, SPEC) for _ in range(2)]  # type: ignore[arg-type]

    await asyncio.gather(*(replica.reconcile() for replica in replicas))
    await asyncio.gather(*(replica.reconcile() for replica in replicas))

    async with db.sessions() as session:
        workers = (await session.execute(select(ExecutionWorker))).scalars().all()
    assert len(workers) == SETTINGS.pool_max_workers
    assert all(w.state == "starting" and w.kubernetes_job_uid for w in workers)
    assert sorted(cluster.created) == sorted(w.kubernetes_job_name for w in workers)
    assert set(cluster.started) == set(cluster.created)
    manifest = cluster.jobs[cluster.created[0]]
    assert manifest["spec"]["template"]["spec"]["automountServiceAccountToken"] is False


async def test_a_free_reported_budget_satisfies_demand_without_launching(db) -> None:
    static_id = await claims.register_static_worker(slots=4, hostname="static-1", memory_budget_bytes=GIB)
    await _report_budget(static_id, GIB)
    await _accept_pool_run()
    cluster = FakeCluster()

    await controller.WorkerPoolController(cluster, SETTINGS, SPEC).reconcile()  # type: ignore[arg-type]

    assert cluster.created == []


async def test_a_memory_pressured_executor_offers_no_capacity(db) -> None:
    """An executor over its memory threshold claims nothing, so its free
    budget does not count and a worker Job is started for the waiting run."""
    static_id = await claims.register_static_worker(slots=4, hostname="static-1", memory_budget_bytes=GIB)
    await _report_budget(static_id, GIB, pressured=True)
    await _accept_pool_run()
    cluster = FakeCluster()

    await controller.WorkerPoolController(cluster, SETTINGS, SPEC).reconcile()  # type: ignore[arg-type]

    assert len(cluster.created) == 1


async def test_a_run_handed_back_before_it_started_waits_for_another_executor(db) -> None:
    """A run whose fork was refused after its claim goes back to PENDING with
    a fresh dispatch carrying the same request and its place in line, and
    without the first executor's claim record."""
    from src.models.orm.execution_workers import ExecutionWorkerClaim

    first = await claims.register_static_worker(slots=1, hostname="first", memory_budget_bytes=GIB)
    execution_id = await _accept_pool_run()
    await _set_request(db.sessions, execution_id, 200 * MIB)
    claim = await _claim(first)
    assert claim is not None and claim.claimed is not None
    assert claim.memory_request_bytes == 200 * MIB
    db.wake.reset_mock()

    await workflow_dispatch.release_workflow_run(
        worker_id=first, message=claim.message, payload=claim.claimed,
        memory_request_bytes=claim.memory_request_bytes, queued_at=claim.queued_at,
    )

    async with db.sessions() as session:
        execution = await session.get(Execution, UUID(execution_id))
        held = (
            await session.execute(
                select(ExecutionWorkerClaim).where(ExecutionWorkerClaim.worker_id == first)
            )
        ).scalars().all()
    assert (execution.status, execution.started_at) == (ExecutionStatus.PENDING, None)
    assert held == []
    db.wake.assert_awaited_once()
    second = await claims.register_static_worker(slots=1, hostname="second", memory_budget_bytes=GIB)
    again = await _claim(second)
    assert again is not None and again.claimed is not None
    assert again.claimed["execution_id"] == execution_id
    assert again.memory_request_bytes == 200 * MIB
    assert again.queued_at == claim.queued_at, "it keeps its place and claim deadline"


async def test_silent_worker_is_lost_and_its_unstarted_work_released(db, monkeypatch) -> None:
    """An agent run is claimed by its consumer after assignment; if the worker
    dies in between, the run is still queued and goes to another worker."""
    from src.services.execution import agent_run_service
    from src.services.execution.agent_run_service import enqueue_agent_run

    monkeypatch.setattr(agent_run_service, "get_dispatch_db_context", claims.get_db_context)
    cluster = FakeCluster()
    pool = controller.WorkerPoolController(cluster, SETTINGS, SPEC)  # type: ignore[arg-type]
    run_id = await enqueue_agent_run(agent_id=None, trigger_type="sdk", lineage=None)
    [worker_id] = await pool.launch_for_demand()
    await pool.reconcile_worker(worker_id)
    async with db.sessions() as session:
        job_uid = (await session.get(ExecutionWorker, worker_id)).kubernetes_job_uid
    assert await claims.admit_worker(worker_id, job_uid=job_uid, pod_uid="pod") is not None
    claim = await _claim(worker_id)
    assert claim is not None and claim.handler == "agent.run"

    # The node dies: no heartbeat, the pod never claims the run.
    async with db.sessions() as session:
        await session.execute(
            update(ExecutionWorker).values(heartbeat_at=datetime.now(timezone.utc) - timedelta(minutes=5))
        )
        await session.execute(
            update(WorkDispatch).values(created_at=datetime.now(timezone.utc) - timedelta(minutes=5))
        )
        await session.commit()
    await pool.reconcile_worker(worker_id)
    await work_dispatch.reconcile_work_dispatches()

    async with db.sessions() as session:
        worker = await session.get(ExecutionWorker, worker_id)
        dispatch = (
            await session.execute(
                select(WorkDispatch).where(WorkDispatch.subject_id == UUID(run_id))
            )
        ).scalar_one()
    assert (worker.state, worker.end_reason) == ("lost", "heartbeat_lost")
    assert dispatch.assigned_worker_id is None
    assert cluster.deleted == []  # deleted on the next pass, as a lost worker
    await pool.reconcile_worker(worker_id)
    assert cluster.deleted == [worker.kubernetes_job_name]


async def test_pool_run_fails_visibly_when_no_capacity_arrives(db) -> None:
    execution_id = await _accept_pool_run()
    async with db.sessions() as session:
        await session.execute(
            update(WorkDispatch).values(
                created_at=datetime.now(timezone.utc) - work_dispatch.POOL_CLAIM_DEADLINE * 2
            )
        )
        await session.commit()

    await work_dispatch.reconcile_work_dispatches()

    async with db.sessions() as session:
        execution = await session.get(Execution, UUID(execution_id))
    assert execution.status == ExecutionStatus.FAILED
    assert "No worker capacity" in (execution.error_message or "")


async def test_ended_executors_are_pruned(db) -> None:
    old = await _job_worker(db.sessions, state="retired")
    async with db.sessions() as session:
        await session.execute(
            update(ExecutionWorker)
            .where(ExecutionWorker.id == old.id)
            .values(ended_at=datetime.now(timezone.utc) - timedelta(days=30))
        )
        await session.commit()

    await maintenance.prune_execution_workers()

    async with db.sessions() as session:
        assert await session.get(ExecutionWorker, old.id) is None


async def test_a_silent_static_executor_is_lost_and_its_runs_end(db) -> None:
    from src.services.execution.workflow_dispatch import claim_workflow_dispatch
    from src.services.worker_pool.recovery import EXECUTOR_LOST

    silent = await claims.register_static_worker(slots=1, hostname="gone", memory_budget_bytes=GIB)
    alive = await claims.register_static_worker(slots=1, hostname="here", memory_budget_bytes=GIB)
    execution_id = await _accept_pool_run()
    claim = await _claim(silent)
    assert claim is not None
    async with db.sessions() as session:
        await claim_workflow_dispatch(
            session, dispatch_id=claim.dispatch_id, execution_id=UUID(execution_id)
        )
        await session.execute(
            update(ExecutionWorker)
            .where(ExecutionWorker.id == silent)
            .values(heartbeat_at=datetime.now(timezone.utc) - timedelta(seconds=45))
        )
        await session.commit()

    assert await liveness.end_silent_static_executors() == [UUID(execution_id)]

    async with db.sessions() as session:
        assert (await session.get(ExecutionWorker, silent)).end_reason == "heartbeat_lost"
        assert (await session.get(ExecutionWorker, alive)).state == "ready"
        execution = await session.get(Execution, UUID(execution_id))
    assert (execution.status, execution.error_message) == (ExecutionStatus.FAILED, EXECUTOR_LOST)


async def test_no_executor_is_lost_until_the_database_was_reachable_long_enough(db) -> None:
    """After a database outage every heartbeat looks old at once; the
    controller waits until executors have had time to heartbeat again."""
    pool = controller.WorkerPoolController(FakeCluster(), SETTINGS, SPEC)  # type: ignore[arg-type]
    pool.reachability = liveness.Reachability()
    await _accept_pool_run()
    [worker_id] = await pool.launch_for_demand()
    await pool.reconcile_worker(worker_id)
    async with db.sessions() as session:
        job_uid = (await session.get(ExecutionWorker, worker_id)).kubernetes_job_uid
    await claims.admit_worker(worker_id, job_uid=job_uid, pod_uid="pod")
    async with db.sessions() as session:
        await session.execute(
            update(ExecutionWorker).values(heartbeat_at=datetime.now(timezone.utc) - timedelta(minutes=5))
        )
        await session.commit()

    pool.reachability.connected()
    await pool.reconcile_worker(worker_id)

    async with db.sessions() as session:
        assert (await session.get(ExecutionWorker, worker_id)).state == "ready"


async def test_reachability_is_measured_by_reaching_the_database(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Only the database counts: Kubernetes or Redis trouble never resets it."""
    from contextlib import asynccontextmanager

    reachable = [True]

    @asynccontextmanager
    async def database():  # type: ignore[no-untyped-def]
        if not reachable[0]:
            raise ConnectionError("database restarting")
        yield SimpleNamespace(execute=AsyncMock())

    monkeypatch.setattr(liveness, "get_db_context", database)
    reachability = liveness.Reachability(
        connected_since=time.monotonic() - liveness.HEARTBEAT_LOST_AFTER.total_seconds()
    )
    assert await reachability.check() and reachability.steady()

    reachable[0] = False
    assert not await reachability.check()
    assert not reachability.steady()


async def test_one_worker_s_failure_does_not_stop_the_others(db, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    first = await _job_worker(db.sessions, state="ready")
    second = await _job_worker(db.sessions, state="ready")
    pool = controller.WorkerPoolController(FakeCluster(), SETTINGS, SPEC)  # type: ignore[arg-type]
    reconciled: list[UUID] = []

    async def reconcile_worker(worker_id: UUID) -> None:
        reconciled.append(worker_id)
        if worker_id == first.id:
            raise RuntimeError("Worker Job UID does not match its incarnation")

    monkeypatch.setattr(pool, "reconcile_worker", reconcile_worker)
    await pool.reconcile()

    assert set(reconciled) == {first.id, second.id}


async def test_a_sync_caller_of_a_lost_run_hears_at_once(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from src.services.worker_pool import recovery

    pushed = AsyncMock()
    monkeypatch.setattr("src.core.pubsub.publish_execution_update", AsyncMock())
    monkeypatch.setattr(
        "src.core.redis_client.get_redis_client", lambda: SimpleNamespace(push_result=pushed)
    )
    run_id = uuid4()

    await recovery.announce_ended_runs([run_id])

    pushed.assert_awaited_once()
    assert pushed.await_args.kwargs["execution_id"] == str(run_id)
    assert pushed.await_args.kwargs["status"] == "Failed"


def test_reachability_is_steady_only_after_an_unbroken_lost_window() -> None:
    reachability = liveness.Reachability()
    assert not reachability.steady()
    reachability = liveness.Reachability(
        connected_since=time.monotonic() - liveness.HEARTBEAT_LOST_AFTER.total_seconds()
    )
    assert reachability.steady()
    reachability.broken()
    assert not reachability.steady()
    reachability.connected()
    assert not reachability.steady(), "counting starts again"


def test_worker_job_is_suspended_tokenless_and_has_no_deadline() -> None:
    manifest = build_worker_job_manifest(uuid4(), SETTINGS, SPEC, memory_limit_bytes=None)
    spec = manifest["spec"]
    pod = spec["template"]["spec"]
    container = pod["containers"][0]
    env = {item["name"]: item for item in container["env"]}

    assert spec["suspend"] is True
    assert (spec["parallelism"], spec["completions"], spec["backoffLimit"]) == (1, 1, 0)
    assert "activeDeadlineSeconds" not in spec
    assert pod["restartPolicy"] == "Never"
    assert pod["automountServiceAccountToken"] is False
    assert container["command"] == ["python", "-m", "src.worker.pool_main"]
    assert container["resources"]["requests"]["memory"] == "512Mi"
    assert container["resources"]["limits"]["memory"] == "2048Mi"
    assert "controller-uid" in env["BIFROST_KUBERNETES_JOB_UID"]["valueFrom"]["fieldRef"]["fieldPath"]


async def test_warm_minimum_is_kept_ready_and_never_idles_out(db) -> None:
    warm = SimpleNamespace(**{**vars(SETTINGS), "pool_min_workers": 1})
    cluster = FakeCluster()
    pool = controller.WorkerPoolController(cluster, warm, SPEC)  # type: ignore[arg-type]

    [worker_id] = await pool.launch_for_demand()  # no demand at all
    assert await pool.launch_for_demand() == []  # already at the minimum
    await pool.reconcile_worker(worker_id)
    async with db.sessions() as session:
        job_uid = (await session.get(ExecutionWorker, worker_id)).kubernetes_job_uid
    await claims.admit_worker(worker_id, job_uid=job_uid, pod_uid="pod")

    assert not await claims.begin_idle_drain(worker_id, keep_ready=1)
    assert await claims.begin_idle_drain(worker_id, keep_ready=0)


async def test_runs_of_a_lost_worker_end_visibly_and_are_not_replayed(db) -> None:
    from src.services.execution.workflow_dispatch import claim_workflow_dispatch
    from src.services.worker_pool.recovery import EXECUTOR_LOST

    cluster = FakeCluster()
    pool = controller.WorkerPoolController(cluster, SETTINGS, SPEC)  # type: ignore[arg-type]
    running_id = await _accept_pool_run()
    [worker_id] = await pool.launch_for_demand()
    await pool.reconcile_worker(worker_id)
    async with db.sessions() as session:
        job_uid = (await session.get(ExecutionWorker, worker_id)).kubernetes_job_uid
    await claims.admit_worker(worker_id, job_uid=job_uid, pod_uid="pod")
    claim = await _claim(worker_id)
    assert claim is not None
    async with db.sessions() as session:
        await claim_workflow_dispatch(
            session, dispatch_id=claim.dispatch_id, execution_id=UUID(running_id)
        )
        await session.commit()

    async with db.sessions() as session:
        await session.execute(
            update(ExecutionWorker).values(heartbeat_at=datetime.now(timezone.utc) - timedelta(minutes=5))
        )
        await session.commit()
    await pool.reconcile_worker(worker_id)

    async with db.sessions() as session:
        execution = await session.get(Execution, UUID(running_id))
    assert execution.status == ExecutionStatus.FAILED
    assert execution.error_message == EXECUTOR_LOST

    # The lost executor's late result is refused, not written over the loss.
    from src.repositories.executions import update_execution

    async with db.sessions() as session:
        written = await update_execution(
            execution_id=running_id, status=ExecutionStatus.SUCCESS, session=session,
            owner_worker_id=worker_id,
        )
        await session.commit()
        execution = await session.get(Execution, UUID(running_id))
    assert not written
    assert execution.status == ExecutionStatus.FAILED


async def test_the_executor_holding_a_run_writes_its_result(db) -> None:
    from src.repositories.executions import update_execution
    from src.services.execution.workflow_dispatch import claim_workflow_dispatch

    worker_id = await claims.register_static_worker(slots=1, hostname="here", memory_budget_bytes=GIB)
    other = await claims.register_static_worker(slots=1, hostname="other", memory_budget_bytes=GIB)
    execution_id = await _accept_pool_run()
    claim = await _claim(worker_id)
    assert claim is not None
    async with db.sessions() as session:
        await claim_workflow_dispatch(
            session, dispatch_id=claim.dispatch_id, execution_id=UUID(execution_id)
        )
        await session.commit()

    async with db.sessions() as session:
        assert not await update_execution(
            execution_id=execution_id, status=ExecutionStatus.SUCCESS, session=session,
            owner_worker_id=other,
        ), "an executor that does not hold the run cannot end it"
        assert await update_execution(
            execution_id=execution_id, status=ExecutionStatus.SUCCESS, session=session,
            owner_worker_id=worker_id,
        )
        await session.commit()
        execution = await session.get(Execution, UUID(execution_id))
    assert execution.status == ExecutionStatus.SUCCESS


async def _lose_running_job_worker(db, *, oom: bool) -> tuple[UUID, UUID]:  # type: ignore[no-untyped-def]
    """A Job worker with one claimed run of a 16 MiB workflow goes silent;
    Kubernetes reports its pod OOMKilled or not. Returns (workflow, worker)."""
    from src.services.execution.workflow_dispatch import claim_workflow_dispatch

    suffix = uuid4().hex[:8]
    workflow = Workflow(
        id=uuid4(), name=f"lost-{suffix}", function_name="main",
        path=f"workflows/lost_{suffix}.py", type="workflow", is_active=True,
        learned_memory_bytes=16 * MIB,
    )
    async with db.sessions() as session:
        session.add(workflow)
        await session.commit()
    execution_id = str(uuid4())
    await _publish_pending(
        execution_id=execution_id, workflow_id=str(workflow.id), parameters={}, org_id=None,
        user_id="00000000-0000-0000-0000-000000000001", user_name="Dev", user_email="",
        form_id=None, startup=None, form_inputs={}, embed={}, api_key_id=None,
        sync=False, is_platform_admin=True, file_path=None, lineage=None,
    )
    cluster = FakeCluster()
    pool = controller.WorkerPoolController(cluster, SETTINGS, SPEC)  # type: ignore[arg-type]
    [worker_id] = await pool.launch_for_demand()
    await pool.reconcile_worker(worker_id)
    async with db.sessions() as session:
        job_uid = (await session.get(ExecutionWorker, worker_id)).kubernetes_job_uid
    await claims.admit_worker(worker_id, job_uid=job_uid, pod_uid="pod")
    await _report_budget(worker_id, 546 * MIB)
    claim = await _claim(worker_id)
    assert claim is not None
    async with db.sessions() as session:
        await claim_workflow_dispatch(
            session, dispatch_id=claim.dispatch_id, execution_id=UUID(execution_id)
        )
        await session.execute(
            update(ExecutionWorker).values(heartbeat_at=datetime.now(timezone.utc) - timedelta(minutes=5))
        )
        await session.commit()
    reason = "OOMKilled" if oom else "Error"
    cluster.pods[job_uid] = [
        {"status": {"containerStatuses": [{"state": {"terminated": {"reason": reason}}}]}}
    ]
    await pool.reconcile_worker(worker_id)
    return workflow.id, worker_id


async def test_a_run_lost_to_an_oom_killed_worker_learns_the_worker_s_budget(db) -> None:
    """Its run could not report what it used, so its next run should fit an
    executor alone."""
    workflow_id, worker_id = await _lose_running_job_worker(db, oom=True)

    async with db.sessions() as session:
        learned = await session.scalar(
            select(Workflow.learned_memory_bytes).where(Workflow.id == workflow_id)
        )
        worker = await session.get(ExecutionWorker, worker_id)
    assert learned == 546 * MIB - WORKFLOW_REQUEST_MARGIN_BYTES
    assert (worker.state, worker.end_reason) == ("lost", "out_of_memory")


async def test_a_run_lost_for_any_other_reason_teaches_nothing(db) -> None:
    """A crash, a forced stop or a lost node says nothing about memory."""
    workflow_id, worker_id = await _lose_running_job_worker(db, oom=False)

    async with db.sessions() as session:
        learned = await session.scalar(
            select(Workflow.learned_memory_bytes).where(Workflow.id == workflow_id)
        )
        worker = await session.get(ExecutionWorker, worker_id)
    assert learned == 16 * MIB
    assert (worker.state, worker.end_reason) == ("lost", "heartbeat_lost")


async def test_a_run_waiting_for_capacity_is_not_failed_as_stuck(db, monkeypatch) -> None:
    """Its claim deadline (POOL_CLAIM_DEADLINE) decides; the stuck-run
    cleanup only fails pending runs nothing is waiting to claim."""
    from src.jobs.schedulers import execution_cleanup as cleanup

    for name in ("publish_execution_update", "publish_history_update"):
        monkeypatch.setattr(cleanup, name, AsyncMock())
    monkeypatch.setattr(cleanup, "get_session_factory", lambda: db.sessions)
    waiting = await _accept_pool_run()
    orphan = await _accept_pool_run()
    async with db.sessions() as session:
        await session.execute(delete(WorkDispatch).where(WorkDispatch.subject_id == UUID(orphan)))
        await session.execute(
            update(Execution)
            .where(Execution.id.in_([UUID(waiting), UUID(orphan)]))
            .values(started_at=datetime.now(timezone.utc) - timedelta(minutes=20))
        )
        await session.commit()

    await cleanup.cleanup_stuck_executions()

    async with db.sessions() as session:
        assert (await session.get(Execution, UUID(waiting))).status == ExecutionStatus.PENDING
        assert (await session.get(Execution, UUID(orphan))).status == ExecutionStatus.TIMEOUT


# --- sizing by memory -----------------------------------------------------------


def _plan(waiting: list[int], free: list[int]) -> controller.LaunchPlan:
    return controller._plan_launches(
        [(uuid4(), request) for request in waiting],
        free,
        standard_budget=1000,
        largest_budget=4000,
    )


def test_waiting_work_that_fits_free_budgets_launches_nothing() -> None:
    assert _plan([300, 300], [400, 300]) == controller.LaunchPlan(standard=0, sized=[])


def test_overflow_is_packed_first_fit_into_new_standard_workers() -> None:
    assert _plan([600, 600, 300, 100], []) == controller.LaunchPlan(standard=2, sized=[])


def test_work_a_standard_worker_cannot_hold_gets_a_sized_worker() -> None:
    large = uuid4()
    plan = controller._plan_launches(
        [(large, 1500)], [], standard_budget=1000, largest_budget=4000
    )
    assert plan == controller.LaunchPlan(standard=0, sized=[(large, 1500)])


def test_work_larger_than_any_launchable_worker_is_left_out() -> None:
    assert _plan([5000], []) == controller.LaunchPlan(standard=0, sized=[])


async def test_a_sized_worker_is_launched_once_for_its_dispatch(db) -> None:
    from src.services.memory_requests import job_budget_bytes, sized_job_limit_bytes

    execution_id = await _accept_pool_run()
    await _set_request(db.sessions, execution_id, 3 * GIB)
    cluster = FakeCluster()
    pool = controller.WorkerPoolController(cluster, SETTINGS, SPEC)  # type: ignore[arg-type]

    [worker_id] = await pool.launch_for_demand()
    assert await pool.launch_for_demand() == [], "its dispatch is already covered"

    async with db.sessions() as session:
        worker = await session.get(ExecutionWorker, worker_id)
        dispatch_id = await session.scalar(
            select(WorkDispatch.id).where(WorkDispatch.subject_id == UUID(execution_id))
        )
    baseline = 768 * MIB  # no standard worker has reported its budget yet
    assert worker.resource_class == "sized"
    assert worker.reserved_for_dispatch_id == dispatch_id
    assert worker.memory_limit_bytes == sized_job_limit_bytes(3 * GIB, 0.85, baseline, 8192 * MIB)
    # Every reconcile publishes the largest budget it could launch.
    await pool.reconcile()
    async with get_redis() as redis:
        launchable = int(await redis.get(LAUNCHABLE_KEY))
    assert launchable == job_budget_bytes(8192 * MIB, 0.85, baseline)


async def test_a_job_worker_counts_its_expected_budget_until_it_reports_one(db) -> None:
    """Admitted but still starting its pool, a Job worker has not measured
    its budget; its waiting work must not launch another worker."""
    await _job_worker(db.sessions, state="ready")
    await _accept_pool_run()
    cluster = FakeCluster()

    assert await controller.WorkerPoolController(cluster, SETTINGS, SPEC).launch_for_demand() == []  # type: ignore[arg-type]


def test_a_sized_worker_job_requests_its_whole_limit() -> None:
    manifest = build_worker_job_manifest(uuid4(), SETTINGS, SPEC, memory_limit_bytes=4 * GIB)
    resources = manifest["spec"]["template"]["spec"]["containers"][0]["resources"]

    assert resources["requests"]["memory"] == resources["limits"]["memory"] == "4096Mi"


# --- work no executor can hold --------------------------------------------------


async def _waiting_run_of(sessions, request: int) -> str:  # type: ignore[no-untyped-def]
    execution_id = await _accept_pool_run()
    await _set_request(
        sessions, execution_id, request, age=work_dispatch.POOL_RECHECK_AFTER + timedelta(minutes=1)
    )
    return execution_id


async def test_work_larger_than_the_largest_executor_fails_visibly(db) -> None:
    static_id = await claims.register_static_worker(slots=1, hostname="static-1", memory_budget_bytes=GIB)
    await _report_budget(static_id, 512 * MIB)
    execution_id = await _waiting_run_of(db.sessions, GIB)

    await work_dispatch.reconcile_work_dispatches()

    async with db.sessions() as session:
        execution = await session.get(Execution, UUID(execution_id))
    assert execution.status == ExecutionStatus.FAILED
    assert "needs 1024 MiB" in (execution.error_message or "")
    assert "can hold 512 MiB" in (execution.error_message or "")


async def test_work_no_executor_can_hold_fails_within_seconds(db) -> None:
    static_id = await claims.register_static_worker(slots=1, hostname="static-1", memory_budget_bytes=GIB)
    await _report_budget(static_id, 512 * MIB)
    execution_id = await _accept_pool_run()
    await _set_request(db.sessions, execution_id, GIB)

    await work_dispatch.reconcile_work_dispatches()

    async with db.sessions() as session:
        assert (await session.get(Execution, UUID(execution_id))).status == ExecutionStatus.FAILED


async def test_an_executor_restarting_or_draining_still_counts_as_capacity(db) -> None:
    """Work is too large only if no executor live within RECENTLY_LIVE could
    hold it: a big executor restarting must not fail its work meanwhile."""
    ready = await claims.register_static_worker(slots=1, hostname="staying", memory_budget_bytes=GIB)
    await _report_budget(ready, 512 * MIB)
    draining = await claims.register_static_worker(slots=1, hostname="leaving", memory_budget_bytes=GIB)
    await _report_budget(draining, 2 * GIB)
    await claims.begin_drain(draining, "stopped")
    restarting = await claims.register_static_worker(slots=1, hostname="restarting", memory_budget_bytes=GIB)
    await _report_budget(restarting, 4 * GIB)
    gone = await claims.register_static_worker(slots=1, hostname="gone", memory_budget_bytes=GIB)
    await _report_budget(gone, 8 * GIB)
    async with db.sessions() as session:
        now = datetime.now(timezone.utc)
        await session.execute(
            update(ExecutionWorker).where(ExecutionWorker.id == restarting)
            .values(state="lost", ended_at=now - timedelta(minutes=2))
        )
        await session.execute(
            update(ExecutionWorker).where(ExecutionWorker.id == gone)
            .values(state="lost", ended_at=now - work_dispatch.RECENTLY_LIVE - timedelta(minutes=1))
        )
        await session.commit()

    async with db.sessions() as session:
        assert await work_dispatch.largest_budget(session) == 4 * GIB


async def test_a_platform_job_no_executor_can_hold_fails_visibly(db) -> None:
    from src.models.orm.platform_jobs import PlatformJob
    from src.services.worker_pool import unfit

    def job(required: int) -> PlatformJob:
        return PlatformJob(
            job_type="workspace.reindex", payload={}, requested_by_user_id=str(uuid4()),
            requested_by_email="person@contoso.example", requested_by_name="Contoso User",
            title="Contoso Reindex", memory_required_bytes=required,
        )

    large, small = job(GIB), job(256 * MIB)
    async with db.sessions() as session:
        session.add_all([large, small])
        await session.commit()
        failed = await unfit.fail_platform_jobs_that_never_fit(session, 512 * MIB)
        await session.commit()
        statuses = {
            row.id: (row.status, row.error_code)
            for row in (
                await session.scalars(
                    select(PlatformJob).where(PlatformJob.id.in_([large.id, small.id]))
                )
            ).all()
        }
        await session.execute(delete(PlatformJob).where(PlatformJob.id.in_([large.id, small.id])))
        await session.commit()

    assert [row.id for row in failed] == [large.id]
    assert statuses == {large.id: ("failed", "too_large"), small.id: ("queued", None)}


async def test_a_platform_job_waiting_for_memory_launches_a_worker(db) -> None:
    from src.models.orm.platform_jobs import PlatformJob
    from src.services.platform_jobs import WAITING_FOR_MEMORY

    waiting = PlatformJob(
        job_type="workspace.reindex", payload={}, requested_by_user_id=str(uuid4()),
        requested_by_email="person@contoso.example", requested_by_name="Contoso User",
        title="Contoso Reindex", memory_required_bytes=512 * MIB, phase=WAITING_FOR_MEMORY,
    )
    async with db.sessions() as session:
        session.add(waiting)
        await session.commit()
    try:
        launched = await controller.WorkerPoolController(FakeCluster(), SETTINGS, SPEC).launch_for_demand()  # type: ignore[arg-type]
        assert len(launched) == 1
        async with db.sessions() as session:
            worker = await session.get(ExecutionWorker, launched[0])
        assert worker.reserved_for_dispatch_id is None
    finally:
        async with db.sessions() as session:
            await session.execute(delete(PlatformJob).where(PlatformJob.id == waiting.id))
            await session.commit()


async def test_large_work_waits_while_no_executor_has_reported_a_budget(db) -> None:
    await claims.register_static_worker(slots=1, hostname="static-1", memory_budget_bytes=GIB)
    execution_id = await _waiting_run_of(db.sessions, GIB)

    await work_dispatch.reconcile_work_dispatches()

    async with db.sessions() as session:
        execution = await session.get(Execution, UUID(execution_id))
        waiting = await session.scalar(
            select(WorkDispatch.id).where(WorkDispatch.subject_id == UUID(execution_id))
        )
    assert execution.status == ExecutionStatus.PENDING
    assert waiting is not None


# --- requests at acceptance -----------------------------------------------------


async def test_a_workflow_run_requests_its_learned_peak_or_the_default(db) -> None:
    learned = Workflow(
        id=uuid4(), name=f"learned-{uuid4().hex[:8]}", function_name="main",
        path="workflows/learned.py", type="workflow", is_active=True,
        learned_memory_bytes=200 * MIB,
    )
    new = Workflow(
        id=uuid4(), name=f"new-{uuid4().hex[:8]}", function_name="main",
        path="workflows/new.py", type="workflow", is_active=True,
    )
    async with db.sessions() as session:
        session.add_all([learned, new])
        await session.commit()

    requests: dict[UUID, int] = {}
    for workflow in (learned, new):
        execution_id = str(uuid4())
        await _publish_pending(
            execution_id=execution_id, workflow_id=str(workflow.id), parameters={}, org_id=None,
            user_id="00000000-0000-0000-0000-000000000001", user_name="Dev", user_email="",
            form_id=None, startup=None, form_inputs={}, embed={}, api_key_id=None,
            sync=False, is_platform_admin=True, file_path=None, lineage=None,
        )
        async with db.sessions() as session:
            requests[workflow.id] = await session.scalar(
                select(WorkDispatch.memory_request_bytes).where(
                    WorkDispatch.subject_id == UUID(execution_id)
                )
            )

    try:
        assert requests == {
            learned.id: 200 * MIB + WORKFLOW_REQUEST_MARGIN_BYTES,
            new.id: DEFAULT_WORKFLOW_REQUEST_BYTES,
        }
    finally:
        async with db.sessions() as session:
            await session.execute(delete(WorkDispatch))
            await session.execute(
                delete(Execution).where(Execution.workflow_id.in_((learned.id, new.id)))
            )
            await session.execute(delete(Workflow).where(Workflow.id.in_((learned.id, new.id))))
            await session.commit()
