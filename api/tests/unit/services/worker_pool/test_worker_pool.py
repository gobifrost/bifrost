"""Reusable executor protocol against real PostgreSQL and a fake cluster."""

from __future__ import annotations

import asyncio
import json
import copy
import time
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, AsyncGenerator
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.config import get_settings
from src.core.cache.redis_client import get_redis
from src.models.enums import ExecutionStatus
from src.models.orm.config import SystemConfig
from src.models.orm.execution_workers import ExecutionWorker
from src.models.orm.executions import Execution
from src.models.orm.work_dispatches import WorkDispatch
from src.models.orm.workflows import Workflow
from src.services import work_dispatch
from src.services.execution import async_executor, workflow_dispatch
from src.services.execution.async_executor import _publish_pending
from src.services.kubernetes_execution import (
    KUBERNETES_CONFIG_CATEGORY,
    KUBERNETES_CONFIG_KEY,
    POLICY_FIELDS,
    KubernetesExecutionService,
    KubernetesPolicy,
)
from src.services.memory_requests import (
    DEFAULT_WORKFLOW_REQUEST_BYTES,
    LAUNCHABLE_KEY,
    MIB,
    WORKFLOW_REQUEST_MARGIN_BYTES,
)
from src.services.worker_pool import claims, controller, liveness, maintenance, recovery, sizing
from src.services.worker_pool.manifest import WorkerJobSpec, build_worker_job_manifest

GIB = 1024 * MIB

SETTINGS = SimpleNamespace(
    kubernetes_job_namespace="test",
    kubernetes_job_image="api:test",
    kubernetes_job_configmap="config",
    kubernetes_job_secret="secret",
    kubernetes_job_service_account="runner",
    pool_max_lifetime_seconds=21600,
    pool_start_timeout_seconds=300,
    pool_worker_memory_request_mib=512,
    pool_worker_cpu_request="250m",
    pool_worker_cpu_limit="2",
    memory_pressure_threshold=0.85,
    redis_url=get_settings().redis_url,
)

# Settings -> Kubernetes, stored for every test that uses the database.
POLICY = KubernetesPolicy(
    warm_workers=0, max_workers=2, worker_memory_mib=2048, largest_worker_mib=8192, idle_seconds=300
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
        self.annotated: list[tuple[str, dict[str, str]]] = []

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

    async def annotate_pod(self, name: str, annotations: dict[str, str]) -> None:
        await asyncio.sleep(0)
        self.annotated.append((name, dict(annotations)))


async def _delete_policy(sessions: async_sessionmaker[AsyncSession]) -> None:
    async with sessions() as session:
        await session.execute(
            delete(SystemConfig).where(
                SystemConfig.category == KUBERNETES_CONFIG_CATEGORY,
                SystemConfig.key == KUBERNETES_CONFIG_KEY,
            )
        )
        await session.commit()


async def _store_policy(sessions: async_sessionmaker[AsyncSession], policy: KubernetesPolicy) -> None:
    """What an admin saves in Settings -> Kubernetes."""
    async with sessions() as session:
        await KubernetesExecutionService(session).update_policy(
            {name: getattr(policy, name) for name in POLICY_FIELDS}, updated_by="test"
        )
        await session.commit()


@pytest_asyncio.fixture
async def db(async_session_factory: async_sessionmaker[AsyncSession], monkeypatch):
    @asynccontextmanager
    async def context() -> AsyncGenerator[AsyncSession, None]:
        async with async_session_factory() as session:
            yield session
            await session.commit()

    for module in (claims, controller, liveness, maintenance, recovery, work_dispatch, workflow_dispatch):
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
    monkeypatch.setattr("src.services.execution.queue_tracker.publish_queue_positions", AsyncMock())
    await _delete_policy(async_session_factory)
    await _store_policy(async_session_factory, POLICY)
    yield SimpleNamespace(sessions=async_session_factory, wake=wake, context=context)
    await _delete_policy(async_session_factory)


async def _clear_pool_state(sessions) -> None:  # type: ignore[no-untyped-def]
    from src.models.orm.platform_jobs import PlatformJob

    async with sessions() as session:
        await session.execute(delete(WorkDispatch))
        await session.execute(delete(ExecutionWorker))
        # Queued platform jobs count as launch demand; the stack's scheduler
        # enqueues some (model catalog, Solution updates) during the reset.
        await session.execute(delete(PlatformJob).where(PlatformJob.status == "queued"))
        await session.commit()
    # What a worker controller last published it can launch, and which
    # controllers are live.
    async with get_redis() as redis:
        await redis.delete(LAUNCHABLE_KEY, controller.CONTROLLERS_KEY)


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


async def _report_budget(worker_id: UUID, budget: int, *, pressured: bool = False) -> str | None:
    return await claims.heartbeat_worker(
        worker_id, active_runs=0, runs_handled=0, package_generation=0,
        memory_pressured=pressured, memory_budget_bytes=budget, memory_reserved_bytes=0,
    )


async def _static_executor(hostname: str) -> UUID:
    """A static executor whose pool is up, with a measured 1 GiB budget."""
    worker_id = await claims.register_static_worker(hostname=hostname)
    assert await claims.mark_static_ready(worker_id, memory_budget_bytes=GIB)
    return worker_id


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

    assert await claims.admit_worker(worker.id, job_uid="other-job", pod_uid="p0", version=claims.get_version()) is None
    assert await claims.admit_worker(worker.id, job_uid="job-uid", pod_uid="p1", version=claims.get_version()) == 2
    assert await claims.admit_worker(worker.id, job_uid="job-uid", pod_uid="p2", version=claims.get_version()) is None


async def test_an_admitted_worker_records_the_build_its_pod_reports(db) -> None:
    worker = await _job_worker(db.sessions)

    await claims.admit_worker(worker.id, job_uid="job-uid", pod_uid="p1", version="1.0.0-old")

    async with db.sessions() as session:
        assert (await session.get(ExecutionWorker, worker.id)).version == "1.0.0-old"


async def test_a_ready_worker_claims_each_dispatch_once(db) -> None:
    first = await _job_worker(db.sessions, state="ready")
    second = await _job_worker(db.sessions, state="ready")
    execution_id = await _accept_pool_run()

    claims_made = await asyncio.gather(_claim(first.id), _claim(second.id))

    winners = [claim for claim in claims_made if claim is not None]
    assert len(winners) == 1
    assert winners[0].message["execution_id"] == execution_id
    assert winners[0].handler == "workflow.execute"


async def test_claim_reserves_memory_before_its_assignment_commits(db) -> None:
    worker_id = await _static_executor("static-1")
    await _accept_pool_run()
    reserved: list[claims.PoolClaim] = []

    claim = await claims.claim_next(
        worker_id,
        fits_bytes=GIB,
        budget_bytes=GIB,
        reserve_claim=reserved.append,
    )

    assert claim is not None
    assert reserved == [claim]


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


async def test_a_static_executor_offers_nothing_until_its_pool_is_up(db) -> None:
    """While its pool starts (a minute or more), a static executor heartbeats
    but claims nothing and counts as no capacity, so work it cannot take yet
    gets a worker Job."""
    worker_id = await claims.register_static_worker(hostname="worker-1")
    await _accept_pool_run()
    assert await _report_budget(worker_id, GIB) == "starting"
    cluster = FakeCluster()

    assert await _claim(worker_id) is None
    await controller.WorkerPoolController(cluster, SETTINGS, SPEC).reconcile()  # type: ignore[arg-type]
    assert len(cluster.created) == 1

    assert await claims.mark_static_ready(worker_id, memory_budget_bytes=GIB)
    assert await _claim(worker_id) is not None
    assert not await claims.mark_static_ready(worker_id, memory_budget_bytes=GIB)  # once only


async def test_a_static_executor_that_dies_while_starting_is_lost(db) -> None:
    worker_id = await claims.register_static_worker(hostname="gone")
    async with db.sessions() as session:
        await session.execute(
            update(ExecutionWorker)
            .where(ExecutionWorker.id == worker_id)
            .values(heartbeat_at=datetime.now(timezone.utc) - timedelta(seconds=45))
        )
        await session.commit()

    await liveness.end_silent_static_executors()

    async with db.sessions() as session:
        assert (await session.get(ExecutionWorker, worker_id)).state == "lost"


# --- claiming by memory --------------------------------------------------------


async def test_work_larger_than_the_free_budget_waits_while_smaller_work_is_claimed(db) -> None:
    worker_id = await _static_executor("static-1")
    large = await _accept_pool_run()
    await _set_request(db.sessions, large, 500 * MIB, age=timedelta(seconds=5))
    small = await _accept_pool_run()

    claim = await _claim(worker_id, fits=100 * MIB)

    assert claim is not None and claim.message["execution_id"] == small


async def test_long_waiting_work_an_executor_could_hold_blocks_newer_claims(db) -> None:
    worker_id = await _static_executor("static-1")
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
    assert sizing.job_baseline([sized], 2 * GIB, SETTINGS.memory_pressure_threshold) == expected


async def test_a_sized_job_drains_when_idle_and_is_never_the_warm_minimum(db) -> None:
    sized = await _job_worker(db.sessions, state="ready")
    async with db.sessions() as session:
        await session.execute(
            update(ExecutionWorker)
            .where(ExecutionWorker.id == sized.id)
            .values(resource_class="sized", memory_limit_bytes=8 * GIB)
        )
        await session.commit()

    warm = replace(POLICY, warm_workers=1)
    await _store_policy(db.sessions, warm)
    launched = await controller.WorkerPoolController(FakeCluster(), SETTINGS, SPEC).launch_for_demand(warm)  # type: ignore[arg-type]
    assert len(launched) == 1, "the warm minimum is a standard Job"
    assert await claims.begin_idle_drain(sized.id)


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
    assert len(workers) == POLICY.max_workers
    assert all(w.state == "starting" and w.kubernetes_job_uid for w in workers)
    assert sorted(cluster.created) == sorted(w.kubernetes_job_name for w in workers)
    assert set(cluster.started) == set(cluster.created)
    manifest = cluster.jobs[cluster.created[0]]
    assert manifest["spec"]["template"]["spec"]["automountServiceAccountToken"] is False


async def test_a_free_reported_budget_satisfies_demand_without_launching(db) -> None:
    static_id = await _static_executor("static-1")
    await _report_budget(static_id, GIB)
    await _accept_pool_run()
    cluster = FakeCluster()

    await controller.WorkerPoolController(cluster, SETTINGS, SPEC).reconcile()  # type: ignore[arg-type]

    assert cluster.created == []


async def test_a_memory_pressured_executor_offers_no_capacity(db) -> None:
    """An executor over its memory threshold claims nothing, so its free
    budget does not count and a worker Job is started for the waiting run."""
    static_id = await _static_executor("static-1")
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

    first = await _static_executor("first")
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
    from src.services.execution.queue_tracker import get_queued_executions

    async with db.sessions() as session:
        queued = await get_queued_executions(session)
    assert [(entry.execution_id, entry.queued_at) for entry in queued] == [
        (UUID(execution_id), claim.queued_at)
    ]
    db.wake.assert_awaited_once()
    second = await _static_executor("second")
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
    run_id = await enqueue_agent_run(agent_id=None, trigger_type="sdk", lineage=None)
    worker_id = await claims.register_static_worker(
        hostname="api-agent", supported_handlers=["agent.run"]
    )
    wake = AsyncMock()
    assert await claims.mark_static_ready(worker_id, memory_budget_bytes=GIB)
    claim = await _claim(worker_id)
    assert claim is not None and claim.handler == "agent.run"

    # The API process dies: its consumer never starts the assigned run.
    async with db.sessions() as session:
        await session.execute(
            update(ExecutionWorker)
            .where(ExecutionWorker.id == worker_id)
            .values(heartbeat_at=datetime.now(timezone.utc) - timedelta(minutes=5))
        )
        await session.execute(
            update(WorkDispatch).values(created_at=datetime.now(timezone.utc) - timedelta(minutes=5))
        )
        await session.commit()
    await liveness.end_silent_static_executors()
    monkeypatch.setattr(work_dispatch, "wake_pool_workers", wake)
    await work_dispatch.reconcile_work_dispatches()
    wake.assert_awaited_once_with()  # the released run is offered to idle executors at once

    async with db.sessions() as session:
        worker = await session.get(ExecutionWorker, worker_id)
        dispatch = (
            await session.execute(
                select(WorkDispatch).where(WorkDispatch.subject_id == UUID(run_id))
            )
        ).scalar_one()
    assert (worker.state, worker.end_reason) == ("lost", "heartbeat_lost")
    assert dispatch.assigned_worker_id is None


async def test_api_agent_capacity_cannot_claim_oversized_agent_work_but_remote_workflows_fit(
    db, monkeypatch
) -> None:
    from src.services.execution import agent_run_service
    from src.services.execution.agent_run_service import enqueue_agent_run

    monkeypatch.setattr(agent_run_service, "get_dispatch_db_context", claims.get_db_context)
    await enqueue_agent_run(agent_id=None, trigger_type="sdk", lineage=None)
    api_worker = await claims.register_static_worker(
        hostname="api-agent", supported_handlers=["agent.run"]
    )
    assert await claims.mark_static_ready(api_worker, memory_budget_bytes=16 * MIB)

    assert await claims.claim_next(
        api_worker,
        (("agent.run", 1),),
        fits_bytes=16 * MIB,
        budget_bytes=16 * MIB,
    ) is None

    workflow_id = await _accept_pool_run()
    remote_worker = await claims.register_static_worker(
        hostname="remote", supported_handlers=["workflow.execute"]
    )
    assert await claims.mark_static_ready(remote_worker, memory_budget_bytes=GIB)
    workflow = await _claim(remote_worker)

    assert workflow is not None
    assert workflow.message["execution_id"] == workflow_id


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

    silent = await _static_executor("gone")
    alive = await _static_executor("here")
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


async def test_a_lost_executor_s_agent_runs_end_and_their_callers_hear(db, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Its children cannot report: each started run fails at once and its
    sync caller, chat stream and run subscribers are woken
    (announce_ended_agent_run). A run it claimed but never started stays
    queued for another worker."""
    from src.models.orm.agent_runs import AgentRun
    from src.models.orm.agents import Conversation
    from src.models.orm.users import User
    from src.services.execution import agent_run_service
    from src.services.execution.agent_run_service import enqueue_agent_run

    monkeypatch.setattr(agent_run_service, "get_dispatch_db_context", claims.get_db_context)
    monkeypatch.setattr(agent_run_service, "get_db_context", claims.get_db_context)
    update_published, chat_published = AsyncMock(), AsyncMock()
    monkeypatch.setattr(agent_run_service, "publish_agent_run_update", update_published)
    monkeypatch.setattr(agent_run_service, "publish_chat_run_event", chat_published)
    user = User(
        id=uuid4(), email=f"lost-{uuid4().hex[:8]}@example.com", name="Lost",
        is_active=True, is_superuser=True, is_verified=True, is_registered=True,
    )
    conversation = Conversation(id=uuid4(), user_id=user.id, title="Lost worker")
    async with db.sessions() as session:
        session.add(user)
        await session.flush()
        session.add(conversation)
        await session.commit()
    silent = await _static_executor("gone")
    sdk_run, chat_run, unstarted_run = [
        UUID(await enqueue_agent_run(agent_id=None, trigger_type="sdk", lineage=None, sync=True))
        for _ in range(3)
    ]
    for _ in range(3):
        claim = await _claim(silent)
        assert claim is not None and claim.handler == "agent.run"
    try:
        async with db.sessions() as session:
            await session.execute(
                update(AgentRun).where(AgentRun.id.in_([sdk_run, chat_run])).values(status="running")
            )
            await session.execute(
                update(AgentRun)
                .where(AgentRun.id == chat_run)
                .values(trigger_type="chat", conversation_id=conversation.id)
            )
            await session.execute(
                update(ExecutionWorker)
                .where(ExecutionWorker.id == silent)
                .values(heartbeat_at=datetime.now(timezone.utc) - timedelta(seconds=45))
            )
            await session.commit()

        await liveness.end_silent_static_executors()

        async with db.sessions() as session:
            for run_id in (sdk_run, chat_run):
                run = await session.get(AgentRun, run_id)
                assert (run.status, run.error) == ("failed", recovery.EXECUTOR_LOST)
            assert (await session.get(AgentRun, unstarted_run)).status == "queued"
        async with get_redis() as redis:
            for run_id in (sdk_run, chat_run):
                pushed = await redis.lpop(f"bifrost:agent_run:{run_id}:result")  # pyright: ignore[reportGeneralTypeIssues]
                assert pushed is not None and json.loads(pushed)["status"] == "failed"
            assert await redis.lpop(f"bifrost:agent_run:{unstarted_run}:result") is None  # pyright: ignore[reportGeneralTypeIssues]
        assert update_published.await_count == 2
        chat_published.assert_awaited_once()
        chat = chat_published.await_args.kwargs
        assert (chat["conversation_id"], chat["run_id"], chat["kind"]) == (
            conversation.id, chat_run, "error",
        )
        assert chat["payload"].error == recovery.EXECUTOR_LOST
    finally:
        async with db.sessions() as session:
            await session.execute(
                delete(AgentRun).where(AgentRun.id.in_([sdk_run, chat_run, unstarted_run]))
            )
            await session.execute(delete(Conversation).where(Conversation.id == conversation.id))
            await session.execute(delete(User).where(User.id == user.id))
            await session.commit()


async def test_a_requeued_agent_run_claimed_twice_executes_once(db, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """release_unstarted_claims can hand back a dispatch whose child has not
    started yet, and a second worker claims it: both children deliver the
    run. The AgentRun row lock and its queued-status guard let one execute."""
    from src.jobs.consumers.agent_run import AgentRunConsumer
    from src.models.orm.agent_runs import AgentRun
    from src.models.orm.agents import Agent
    from src.services.execution import agent_run_service
    from src.services.execution.agent_run_service import enqueue_agent_run
    from src.services.execution.autonomous_agent_executor import ActingUser

    executed: list[str] = []

    class Executor:
        subtree_tokens = 0

        def __init__(self, *_args, **_kwargs) -> None:  # type: ignore[no-untyped-def]
            pass

        async def run(self, *, run_id, **_kwargs):  # type: ignore[no-untyped-def]
            executed.append(run_id)
            await asyncio.sleep(0.05)
            return {"output": "ok", "status": "completed", "iterations_used": 1, "tokens_used": 1}

        async def flush_to_db(self, _db) -> None:  # type: ignore[no-untyped-def]
            return None

    actor = ActingUser(
        user_id=str(uuid4()), email="actor@example.com", name="Actor", organization_id=None,
        is_platform_admin=True, is_external=False, is_provider_org=False,
    )
    monkeypatch.setattr(agent_run_service, "get_dispatch_db_context", claims.get_db_context)
    monkeypatch.setattr("src.jobs.consumers.agent_run._load_acting_user", AsyncMock(return_value=actor))
    monkeypatch.setattr(
        "src.services.authorization.runtime_auth.load_runtime_principal", AsyncMock(return_value=object())
    )
    monkeypatch.setattr(
        "src.services.authorization.enforce.load_caller",
        AsyncMock(return_value=SimpleNamespace(ctx=object())),
    )
    monkeypatch.setattr(
        "src.services.execution.autonomous_agent_executor.AutonomousAgentExecutor", Executor
    )
    for name in ("publish_agent_run_update", "publish_chat_run_event"):
        monkeypatch.setattr(f"src.jobs.consumers.agent_run.{name}", AsyncMock())
    agent_id = uuid4()
    async with db.sessions() as session:
        session.add(Agent(
            id=agent_id, name=f"requeue-{agent_id.hex}", system_prompt="ok", created_by="test",
        ))
        await session.commit()
    try:
        run_id = await enqueue_agent_run(agent_id=str(agent_id), trigger_type="sdk", lineage=None)
        first_worker = await _static_executor("first")
        first = await _claim(first_worker)
        assert first is not None
        # Its reply was lost and the child had not started: handed back.
        await recovery.release_unstarted_claims(first_worker, set())
        second = await _claim(await _static_executor("second"))
        assert second is not None and second.dispatch_id == first.dispatch_id

        consumers = [AgentRunConsumer(), AgentRunConsumer()]
        for consumer in consumers:
            consumer._session_factory = db.sessions
        await asyncio.gather(
            consumers[0].process_message(first.message),
            consumers[1].process_message(second.message),
        )

        assert executed == [run_id]
        async with db.sessions() as session:
            run = await session.get(AgentRun, UUID(run_id))
            assert run.status == "completed"
            assert await session.get(WorkDispatch, first.dispatch_id) is None
    finally:
        async with db.sessions() as session:
            await session.execute(delete(Agent).where(Agent.id == agent_id))
            await session.commit()


async def test_no_executor_is_lost_until_the_database_was_reachable_long_enough(db) -> None:
    """After a database outage every heartbeat looks old at once; the
    controller waits until executors have had time to heartbeat again."""
    pool = controller.WorkerPoolController(FakeCluster(), SETTINGS, SPEC)  # type: ignore[arg-type]
    pool.reachability = liveness.Reachability()
    await _accept_pool_run()
    [worker_id] = await pool.launch_for_demand(POLICY)
    await pool.reconcile_worker(worker_id, POLICY)
    async with db.sessions() as session:
        job_uid = (await session.get(ExecutionWorker, worker_id)).kubernetes_job_uid
    await claims.admit_worker(worker_id, job_uid=job_uid, pod_uid="pod", version=claims.get_version())
    async with db.sessions() as session:
        await session.execute(
            update(ExecutionWorker).values(heartbeat_at=datetime.now(timezone.utc) - timedelta(minutes=5))
        )
        await session.commit()

    pool.reachability.connected()
    await pool.reconcile_worker(worker_id, POLICY)

    async with db.sessions() as session:
        assert (await session.get(ExecutionWorker, worker_id)).state == "ready"


async def test_a_ready_worker_is_evictable_only_while_idle(db, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The cluster autoscaler may move a worker only while it holds no work;
    the pod is patched only when that answer changes."""
    monkeypatch.setattr(controller, "EVICTABLE_AFTER_IDLE_SECONDS", 0.0)
    cluster = FakeCluster()
    pool = controller.WorkerPoolController(cluster, SETTINGS, SPEC)  # type: ignore[arg-type]
    await _accept_pool_run()
    [worker_id] = await pool.launch_for_demand(POLICY)
    await pool.reconcile_worker(worker_id, POLICY)
    async with db.sessions() as session:
        job_uid = (await session.get(ExecutionWorker, worker_id)).kubernetes_job_uid
    cluster.pods[job_uid] = [{"metadata": {"name": "worker-pod"}}]
    await claims.admit_worker(worker_id, job_uid=job_uid, pod_uid="pod", version=claims.get_version())
    safe = controller.SAFE_TO_EVICT

    async def reserved(memory: int) -> None:
        async with db.sessions() as session:
            await session.execute(
                update(ExecutionWorker)
                .where(ExecutionWorker.id == worker_id)
                .values(memory_reserved_bytes=memory, heartbeat_at=datetime.now(timezone.utc))
            )
            await session.commit()

    await reserved(0)
    await pool.reconcile_worker(worker_id, POLICY)
    await pool.reconcile_worker(worker_id, POLICY)
    assert cluster.annotated == [("worker-pod", {safe: "true"})]

    await reserved(64 * 2**20)
    await pool.reconcile_worker(worker_id, POLICY)
    assert cluster.annotated[-1] == ("worker-pod", {safe: "false"})
    assert len(cluster.annotated) == 2


def test_a_new_worker_pod_starts_not_evictable() -> None:
    manifest = build_worker_job_manifest(uuid4(), SETTINGS, SPEC, memory_limit_bytes=GIB, sized=False)
    assert manifest["spec"]["template"]["metadata"]["annotations"] == {
        controller.SAFE_TO_EVICT: "false"
    }


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

    async def reconcile_worker(worker_id: UUID, policy: KubernetesPolicy) -> None:
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
    manifest = build_worker_job_manifest(
        uuid4(), SETTINGS, SPEC, memory_limit_bytes=3072 * MIB, sized=False
    )
    spec = manifest["spec"]
    pod = spec["template"]["spec"]
    container = pod["containers"][0]
    env = {item["name"]: item for item in container["env"]}

    assert spec["suspend"] is True
    assert (spec["parallelism"], spec["completions"], spec["backoffLimit"]) == (1, 1, 0)
    assert "activeDeadlineSeconds" not in spec
    assert pod["restartPolicy"] == "Never"
    assert pod["automountServiceAccountToken"] is False
    assert container["command"] == ["python", "-m", "src.worker.main"]
    assert env["BIFROST_API_URL"] == {"name": "BIFROST_API_URL", "value": "http://api:8000"}
    assert container["resources"]["requests"]["memory"] == "512Mi"
    assert container["resources"]["limits"]["memory"] == "3072Mi", "Worker memory from Settings"
    assert "controller-uid" in env["BIFROST_KUBERNETES_JOB_UID"]["valueFrom"]["fieldRef"]["fieldPath"]
    # Warm workers and Idle time are read from Settings, not injected.
    assert {name for name in env if name.startswith("BIFROST_POOL_")} == {
        "BIFROST_POOL_MAX_LIFETIME_SECONDS"
    }


async def test_warm_minimum_is_kept_ready_until_settings_lower_it(db) -> None:
    warm = replace(POLICY, warm_workers=1)
    await _store_policy(db.sessions, warm)
    cluster = FakeCluster()
    pool = controller.WorkerPoolController(cluster, SETTINGS, SPEC)  # type: ignore[arg-type]

    [worker_id] = await pool.launch_for_demand(warm)  # no demand at all
    assert await pool.launch_for_demand(warm) == []  # already at the minimum
    await pool.reconcile_worker(worker_id, warm)
    async with db.sessions() as session:
        job_uid = (await session.get(ExecutionWorker, worker_id)).kubernetes_job_uid
    await claims.admit_worker(worker_id, job_uid=job_uid, pod_uid="pod", version=claims.get_version())

    assert not await claims.begin_idle_drain(worker_id)
    # An admin lowers Warm workers: the running idle worker drains.
    await _store_policy(db.sessions, POLICY)
    assert await claims.begin_idle_drain(worker_id)


async def test_a_max_workers_change_in_settings_applies_on_the_next_pass(db) -> None:
    for _ in range(3):  # each needs a standard worker to itself
        await _set_request(db.sessions, await _accept_pool_run(), 600 * MIB)
    cluster = FakeCluster()
    pool = controller.WorkerPoolController(cluster, SETTINGS, SPEC)  # type: ignore[arg-type]

    await pool.reconcile()
    assert len(cluster.created) == POLICY.max_workers

    await _store_policy(db.sessions, replace(POLICY, max_workers=3))
    await pool.reconcile()
    assert len(cluster.created) == 3, "the same controller, no restart"


async def test_runs_of_a_lost_worker_end_visibly_and_are_not_replayed(db) -> None:
    from src.services.execution.workflow_dispatch import claim_workflow_dispatch
    from src.services.worker_pool.recovery import EXECUTOR_LOST

    cluster = FakeCluster()
    pool = controller.WorkerPoolController(cluster, SETTINGS, SPEC)  # type: ignore[arg-type]
    running_id = await _accept_pool_run()
    [worker_id] = await pool.launch_for_demand(POLICY)
    await pool.reconcile_worker(worker_id, POLICY)
    async with db.sessions() as session:
        job_uid = (await session.get(ExecutionWorker, worker_id)).kubernetes_job_uid
    await claims.admit_worker(worker_id, job_uid=job_uid, pod_uid="pod", version=claims.get_version())
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
    await pool.reconcile_worker(worker_id, POLICY)

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

    worker_id = await _static_executor("here")
    other = await _static_executor("other")
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


async def test_stale_remote_owner_cannot_commit_a_workflow_result(db) -> None:
    from src.repositories.executions import update_execution
    from src.services.execution.workflow_dispatch import claim_workflow_dispatch
    from src.worker.remote.fencing import LeaseRef, remote_owner_context
    from src.worker.remote.ownership import RemoteOwnerLease

    worker_id = await _static_executor("remote")
    execution_id = await _accept_pool_run()
    claim = await _claim(worker_id)
    assert claim is not None
    current_owner, stale_owner, launcher = uuid4(), uuid4(), uuid4()
    async with db.sessions() as session:
        await claim_workflow_dispatch(
            session, dispatch_id=claim.dispatch_id, execution_id=UUID(execution_id)
        )
        await session.execute(
            update(ExecutionWorker)
            .where(ExecutionWorker.id == worker_id)
            .values(
                remote_owner_id=current_owner,
                remote_launcher_id=launcher,
                remote_session_generation=2,
                remote_owner_lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
            )
        )
        await session.commit()

    stale = LeaseRef(
        RemoteOwnerLease(
            worker_id=worker_id,
            owner_id=stale_owner,
            launcher_id=launcher,
            session_generation=1,
        )
    )
    with remote_owner_context(stale):
        async with db.sessions() as session:
            written = await update_execution(
                execution_id=execution_id,
                status=ExecutionStatus.SUCCESS,
                session=session,
                owner_worker_id=worker_id,
            )
            await session.commit()
            execution = await session.get(Execution, UUID(execution_id))

    assert not written
    assert execution.status == ExecutionStatus.RUNNING


async def test_stale_remote_owner_cannot_control_its_replaced_worker(db) -> None:
    from src.worker.remote.fencing import LeaseRef, remote_owner_context
    from src.worker.remote.ownership import RemoteOwnerLease

    worker_id = await _static_executor("remote")
    await _accept_pool_run()
    current_owner, stale_owner, launcher = uuid4(), uuid4(), uuid4()
    async with db.sessions() as session:
        await session.execute(
            update(ExecutionWorker)
            .where(ExecutionWorker.id == worker_id)
            .values(
                remote_owner_id=current_owner,
                remote_launcher_id=launcher,
                remote_session_generation=2,
                remote_owner_lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
            )
        )
        await session.commit()

    stale = LeaseRef(
        RemoteOwnerLease(
            worker_id=worker_id,
            owner_id=stale_owner,
            launcher_id=launcher,
            session_generation=1,
        )
    )
    with remote_owner_context(stale):
        assert (
            await claims.heartbeat_worker(
                worker_id,
                active_runs=1,
                runs_handled=1,
                package_generation=1,
                memory_pressured=False,
                memory_budget_bytes=GIB,
                memory_reserved_bytes=64 * MIB,
            )
            is None
        )
        assert (
            await claims.claim_next(worker_id, fits_bytes=GIB, budget_bytes=GIB)
        ) is None
        assert not await claims.begin_drain(worker_id, "stale owner")
        await claims.retire_worker(worker_id, runs_handled=99)

    async with db.sessions() as session:
        worker = await session.get(ExecutionWorker, worker_id)
        dispatch = await session.scalar(
            select(WorkDispatch).where(WorkDispatch.assigned_worker_id == worker_id)
        )
    assert worker is not None
    assert worker.state == "ready"
    assert worker.active_runs == 0
    assert worker.runs_handled == 0
    assert dispatch is None


async def test_stale_remote_owner_cannot_ready_a_starting_worker(db) -> None:
    from src.worker.remote.fencing import LeaseRef, remote_owner_context
    from src.worker.remote.ownership import RemoteOwnerLease

    worker_id = await claims.register_static_worker(hostname="remote")
    current_owner, stale_owner, launcher = uuid4(), uuid4(), uuid4()
    async with db.sessions() as session:
        await session.execute(
            update(ExecutionWorker)
            .where(ExecutionWorker.id == worker_id)
            .values(
                remote_owner_id=current_owner,
                remote_launcher_id=launcher,
                remote_session_generation=2,
                remote_owner_lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
                kubernetes_job_uid="job",
            )
        )
        await session.commit()

    stale = LeaseRef(
        RemoteOwnerLease(
            worker_id=worker_id,
            owner_id=stale_owner,
            launcher_id=launcher,
            session_generation=1,
        )
    )
    with remote_owner_context(stale):
        assert not await claims.mark_static_ready(worker_id, memory_budget_bytes=GIB)

    async with db.sessions() as session:
        worker = await session.get(ExecutionWorker, worker_id)
    assert worker is not None
    assert worker.state == "starting"
    assert worker.memory_budget_bytes is None


async def test_stale_remote_owner_cannot_admit_a_starting_worker(db) -> None:
    from src.worker.remote.fencing import LeaseRef, remote_owner_context
    from src.worker.remote.ownership import RemoteOwnerLease

    worker_id = await claims.register_static_worker(hostname="remote")
    current_owner, stale_owner, launcher = uuid4(), uuid4(), uuid4()
    async with db.sessions() as session:
        await session.execute(
            update(ExecutionWorker)
            .where(ExecutionWorker.id == worker_id)
            .values(
                remote_owner_id=current_owner,
                remote_launcher_id=launcher,
                remote_session_generation=2,
                remote_owner_lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
                kubernetes_job_uid="job",
            )
        )
        await session.commit()

    stale = LeaseRef(
        RemoteOwnerLease(
            worker_id=worker_id,
            owner_id=stale_owner,
            launcher_id=launcher,
            session_generation=1,
        )
    )
    with remote_owner_context(stale):
        assert await claims.admit_worker(worker_id, job_uid="job", pod_uid="pod", version=claims.get_version()) is None

    async with db.sessions() as session:
        worker = await session.get(ExecutionWorker, worker_id)
    assert worker is not None
    assert worker.state == "starting"
    assert worker.kubernetes_pod_uid is None


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
    [worker_id] = await pool.launch_for_demand(POLICY)
    await pool.reconcile_worker(worker_id, POLICY)
    async with db.sessions() as session:
        job_uid = (await session.get(ExecutionWorker, worker_id)).kubernetes_job_uid
    await claims.admit_worker(worker_id, job_uid=job_uid, pod_uid="pod", version=claims.get_version())
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
    await pool.reconcile_worker(worker_id, POLICY)
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


async def test_a_claim_whose_reply_was_lost_is_released(db, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A claim can commit while the executor never hears back: the workflow
    run (its dispatch gone with the claim) fails visibly, an agent run goes
    back to waiting, and work the executor started is left alone."""
    from src.services.execution import agent_run_service
    from src.services.execution.agent_run_service import enqueue_agent_run

    monkeypatch.setattr(agent_run_service, "get_dispatch_db_context", claims.get_db_context)
    monkeypatch.setattr(recovery, "announce_ended_runs", AsyncMock())
    worker = await _static_executor("here")
    lost_run = await _accept_pool_run()
    started_run = await _accept_pool_run()
    lost_claim = await _claim(worker)
    started_claim = await _claim(worker)
    await enqueue_agent_run(agent_id=None, trigger_type="sdk", lineage=None)
    agent_claim = await _claim(worker)
    assert lost_claim and started_claim and agent_claim
    assert agent_claim.handler == "agent.run"

    wake = AsyncMock()
    monkeypatch.setattr(work_dispatch, "wake_pool_workers", wake)
    ended = await recovery.release_unstarted_claims(
        worker, {str(started_claim.subject_id), str(started_claim.dispatch_id)}
    )

    assert ended == [lost_claim.subject_id]
    async with db.sessions() as session:
        lost = await session.get(Execution, UUID(lost_run))
        started = await session.get(Execution, UUID(started_run))
        agent_dispatch = await session.get(WorkDispatch, agent_claim.dispatch_id)
    assert (lost.status, lost.error_message) == (ExecutionStatus.FAILED, recovery.CLAIM_NOT_STARTED)
    assert started.status == ExecutionStatus.RUNNING
    assert agent_dispatch.assigned_worker_id is None
    wake.assert_awaited_once_with()  # idle executors look for the released run now


# --- sizing by memory -----------------------------------------------------------


def _plan(waiting: list[int], free: list[int]) -> controller.LaunchPlan:
    return controller._plan_launches(
        [controller.Request(uuid4(), request, "workflow.execute") for request in waiting],
        [controller.FreeBudget(room, frozenset(claims.REMOTE_WORKER_HANDLER_NAMES)) for room in free],
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
        [controller.Request(large, 1500, "workflow.execute")], [],
        standard_budget=1000, largest_budget=4000,
    )
    assert plan == controller.LaunchPlan(standard=0, sized=[(large, 1500)])


def test_work_larger_than_any_launchable_worker_is_left_out() -> None:
    assert _plan([5000], []) == controller.LaunchPlan(standard=0, sized=[])


def test_agent_work_fits_only_free_budgets_of_workers_that_claim_agent_runs() -> None:
    """A worker from an older build claims workflows only: its room does not
    hold an agent run, so a new worker is planned for it."""
    workflow_only = controller.FreeBudget(500, frozenset({"workflow.execute"}))
    agent = controller.Request(uuid4(), 300, "agent.run")
    workflow = controller.Request(uuid4(), 300, "workflow.execute")
    platform_job = controller.Request(uuid4(), 100)

    def plan(*waiting: controller.Request) -> controller.LaunchPlan:
        return controller._plan_launches(
            list(waiting), [workflow_only], standard_budget=1000, largest_budget=4000
        )

    assert plan(agent) == controller.LaunchPlan(standard=1, sized=[])
    assert plan(workflow, platform_job) == controller.LaunchPlan(standard=0, sized=[])


async def test_a_sized_worker_is_launched_once_for_its_dispatch(db) -> None:
    from src.services.memory_requests import job_budget_bytes, sized_job_limit_bytes

    execution_id = await _accept_pool_run()
    await _set_request(db.sessions, execution_id, 3 * GIB)
    cluster = FakeCluster()
    pool = controller.WorkerPoolController(cluster, SETTINGS, SPEC)  # type: ignore[arg-type]

    [worker_id] = await pool.launch_for_demand(POLICY)
    assert await pool.launch_for_demand(POLICY) == [], "its dispatch is already covered"

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

    assert await controller.WorkerPoolController(cluster, SETTINGS, SPEC).launch_for_demand(POLICY) == []  # type: ignore[arg-type]


def test_a_sized_worker_job_requests_its_whole_limit() -> None:
    manifest = build_worker_job_manifest(
        uuid4(), SETTINGS, SPEC, memory_limit_bytes=4 * GIB, sized=True
    )
    resources = manifest["spec"]["template"]["spec"]["containers"][0]["resources"]

    assert resources["requests"]["memory"] == resources["limits"]["memory"] == "4096Mi"


def test_a_sized_worker_job_is_not_held_to_the_standard_memory_request() -> None:
    """A sized Job requests its whole limit; only a standard Job requests
    the deployment's memory request, which must fit its limit."""
    from src.jobs.platform.kubernetes_client import KubernetesConfigurationError

    large_request = SimpleNamespace(**{**vars(SETTINGS), "pool_worker_memory_request_mib": 4096})

    manifest = build_worker_job_manifest(
        uuid4(), large_request, SPEC, memory_limit_bytes=6 * GIB, sized=True
    )
    resources = manifest["spec"]["template"]["spec"]["containers"][0]["resources"]
    assert resources["requests"]["memory"] == resources["limits"]["memory"] == "6144Mi"
    with pytest.raises(KubernetesConfigurationError):
        build_worker_job_manifest(
            uuid4(), large_request, SPEC, memory_limit_bytes=2 * GIB, sized=False
        )


def _job_memory_limit(cluster: FakeCluster, name: str) -> str:
    return cluster.jobs[name]["spec"]["template"]["spec"]["containers"][0]["resources"]["limits"]["memory"]


async def test_a_standard_job_keeps_the_limit_it_was_reserved_with(db) -> None:
    warm = replace(POLICY, warm_workers=1)
    await _store_policy(db.sessions, warm)
    cluster = FakeCluster()
    pool = controller.WorkerPoolController(cluster, SETTINGS, SPEC)  # type: ignore[arg-type]

    [worker_id] = await pool.launch_for_demand(warm)
    async with db.sessions() as session:
        worker = await session.get(ExecutionWorker, worker_id)
    assert (worker.resource_class, worker.memory_limit_bytes) == ("standard", 2 * GIB)

    # An admin raises Worker memory before the Job is created.
    await pool.reconcile_worker(worker_id, replace(warm, worker_memory_mib=4096))
    assert _job_memory_limit(cluster, worker.kubernetes_job_name) == "2048Mi"


async def test_raising_worker_memory_does_not_shrink_what_can_be_launched(db) -> None:
    """A running 2 GiB Job's baseline is measured against its own limit, not
    the new Worker memory, or large work it could launch for fails as too large."""
    from src.services.memory_requests import job_budget_bytes

    warm = replace(POLICY, warm_workers=1)
    await _store_policy(db.sessions, warm)
    pool = controller.WorkerPoolController(FakeCluster(), SETTINGS, SPEC)  # type: ignore[arg-type]
    [worker_id] = await pool.launch_for_demand(warm)
    await pool.reconcile_worker(worker_id, warm)
    await _admit(db.sessions, worker_id)  # reports a 1 GiB budget
    baseline = int(2 * GIB * SETTINGS.memory_pressure_threshold) - GIB

    await _store_policy(db.sessions, replace(warm, worker_memory_mib=4096))
    await pool.reconcile()

    async with get_redis() as redis:
        launchable = int(await redis.get(LAUNCHABLE_KEY))
    assert launchable == job_budget_bytes(8192 * MIB, SETTINGS.memory_pressure_threshold, baseline)


async def test_a_standard_job_reserved_before_the_upgrade_takes_the_current_worker_memory(db) -> None:
    """The previous release recorded no limit for a standard Job."""
    worker = await _job_worker(db.sessions, state="reserved")
    cluster = FakeCluster()

    await controller.WorkerPoolController(cluster, SETTINGS, SPEC).reconcile_worker(  # type: ignore[arg-type]
        worker.id, replace(POLICY, worker_memory_mib=3072)
    )

    assert _job_memory_limit(cluster, worker.kubernetes_job_name) == "3072Mi"


# --- work no executor can hold --------------------------------------------------


async def _waiting_run_of(sessions, request: int) -> str:  # type: ignore[no-untyped-def]
    execution_id = await _accept_pool_run()
    await _set_request(
        sessions, execution_id, request, age=work_dispatch.POOL_RECHECK_AFTER + timedelta(minutes=1)
    )
    return execution_id


async def test_work_larger_than_the_largest_executor_fails_visibly(db) -> None:
    static_id = await _static_executor("static-1")
    await _report_budget(static_id, 512 * MIB)
    execution_id = await _waiting_run_of(db.sessions, GIB)

    await work_dispatch.reconcile_work_dispatches()

    async with db.sessions() as session:
        execution = await session.get(Execution, UUID(execution_id))
    assert execution.status == ExecutionStatus.FAILED
    assert "needs 1024 MiB" in (execution.error_message or "")
    assert "can hold 512 MiB" in (execution.error_message or "")


async def test_work_no_executor_can_hold_fails_within_seconds(db) -> None:
    static_id = await _static_executor("static-1")
    await _report_budget(static_id, 512 * MIB)
    execution_id = await _accept_pool_run()
    await _set_request(db.sessions, execution_id, GIB)

    await work_dispatch.reconcile_work_dispatches()

    async with db.sessions() as session:
        assert (await session.get(Execution, UUID(execution_id))).status == ExecutionStatus.FAILED


async def test_an_executor_restarting_or_draining_still_counts_as_capacity(db) -> None:
    """Work is too large only if no executor live within RECENTLY_LIVE could
    hold it: a big executor restarting must not fail its work meanwhile."""
    ready = await _static_executor("staying")
    await _report_budget(ready, 512 * MIB)
    draining = await _static_executor("leaving")
    await _report_budget(draining, 2 * GIB)
    await claims.begin_drain(draining, "stopped")
    restarting = await _static_executor("restarting")
    await _report_budget(restarting, 4 * GIB)
    gone = await _static_executor("gone")
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
        failed = await unfit.fail_platform_jobs_that_never_fit(
            session, 512 * MIB, largest_pod=8 * GIB
        )
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


async def test_an_isolated_job_no_pod_can_hold_fails_visibly(db) -> None:
    """Compared with a pod at Largest worker, even before any executor has
    reported; one already placed in Kubernetes is left to its pod."""
    from src.models.orm.platform_jobs import PlatformJob
    from src.services.worker_pool import unfit

    def job(required: int, **values: Any) -> PlatformJob:
        return PlatformJob(
            job_type="application.deploy", payload={}, requested_by_user_id=str(uuid4()),
            requested_by_email="person@contoso.example", requested_by_name="Contoso User",
            title="Contoso Deploy", memory_required_bytes=required,
            execution_backend="kubernetes", **values,
        )

    largest_pod = 6 * GIB
    large, small = job(7 * GIB), job(GIB)
    placed = job(7 * GIB, status="running", kubernetes_job_name="bifrost-job-placed")
    async with db.sessions() as session:
        session.add_all([large, small, placed])
        await session.commit()
        ids = [large.id, small.id, placed.id]
        failed = await unfit.fail_platform_jobs_that_never_fit(
            session, None, largest_pod=largest_pod
        )
        await session.commit()
        rows = {
            row.id: (row.status, row.error_code, row.error_message)
            for row in (
                await session.scalars(select(PlatformJob).where(PlatformJob.id.in_(ids)))
            ).all()
        }
        await session.execute(delete(PlatformJob).where(PlatformJob.id.in_(ids)))
        await session.commit()

    assert [row.id for row in failed] == [large.id]
    assert rows[large.id] == (
        "failed",
        "too_large",
        "This work needs 7168 MiB of memory; the largest executor can hold 6144 MiB.",
    )
    assert rows[small.id][:2] == ("queued", None)
    assert rows[placed.id][:2] == ("running", None)


async def test_the_sweep_fails_isolated_jobs_by_the_settings_largest_pod(db, monkeypatch) -> None:
    """The largest pod is Settings -> Kubernetes Largest worker at the
    threshold and the Job baseline, whatever the executors report."""
    from src.models.orm.platform_jobs import PlatformJob
    from src.services.memory_requests import JOB_BASELINE_ESTIMATE_BYTES
    from src.services.worker_pool import unfit

    await _clear_pool_state(db.sessions)
    monkeypatch.setattr(unfit, "get_db_context", db.context)
    policy = replace(POLICY, largest_worker_mib=4096, isolated_jobs_enabled=True)
    await _store_policy(db.sessions, policy)
    budget = sizing.JobSizing.of(
        policy, SETTINGS.memory_pressure_threshold, JOB_BASELINE_ESTIMATE_BYTES
    ).largest_budget_bytes
    over, fits = (
        PlatformJob(
            job_type="application.deploy", payload={}, requested_by_user_id=str(uuid4()),
            requested_by_email="person@contoso.example", requested_by_name="Contoso User",
            title="Contoso Deploy", memory_required_bytes=required,
            execution_backend="kubernetes",
        )
        for required in (budget + 1, budget)
    )
    async with db.sessions() as session:
        session.add_all([over, fits])
        await session.commit()
    try:
        await unfit.settle_work_that_never_fits()
        async with db.sessions() as session:
            statuses = {
                row.id: row.status
                for row in (
                    await session.scalars(
                        select(PlatformJob).where(PlatformJob.id.in_([over.id, fits.id]))
                    )
                ).all()
            }
        assert statuses == {over.id: "failed", fits.id: "queued"}
    finally:
        async with db.sessions() as session:
            await session.execute(delete(PlatformJob).where(PlatformJob.id.in_([over.id, fits.id])))
            await session.commit()


async def test_with_isolation_off_the_sweep_leaves_isolated_jobs_to_move_to_workers(
    db, monkeypatch
) -> None:
    """A queued isolated job is not measured against a pod while isolation
    is off: the build loop moves it to the local backend, where a larger
    executor may hold it."""
    from src.models.orm.platform_jobs import PlatformJob
    from src.services.worker_pool import unfit

    await _clear_pool_state(db.sessions)
    monkeypatch.setattr(unfit, "get_db_context", db.context)
    await _store_policy(db.sessions, replace(POLICY, largest_worker_mib=4096))
    queued = PlatformJob(
        job_type="application.deploy", payload={}, requested_by_user_id=str(uuid4()),
        requested_by_email="person@contoso.example", requested_by_name="Contoso User",
        title="Contoso Deploy", memory_required_bytes=6 * GIB,
        execution_backend="kubernetes",
    )
    async with db.sessions() as session:
        session.add(queued)
        await session.commit()
    try:
        await unfit.settle_work_that_never_fits()
        async with db.sessions() as session:
            row = await session.get(PlatformJob, queued.id)
            assert row is not None
            assert (row.status, row.error_code) == ("queued", None)
    finally:
        async with db.sessions() as session:
            await session.execute(delete(PlatformJob).where(PlatformJob.id == queued.id))
            await session.commit()


@pytest.mark.parametrize("backend", ["local", "kubernetes"])
async def test_a_too_large_export_fails_its_backup_and_clears_its_options(
    db, backend: str
) -> None:
    """The export's projection takes the same terminal transition a lost
    runner gives it: failed, with its encrypted options cleared."""
    from src.models.orm.platform_jobs import PlatformJob
    from src.models.orm.solution_export_jobs import SolutionExportJob
    from src.models.orm.solutions import Solution
    from src.services.worker_pool import unfit

    solution = Solution(
        id=uuid4(), slug=f"export-unfit-{uuid4().hex[:8]}", name="Contoso Export",
        version="1.0.0", organization_id=None,
    )
    export = SolutionExportJob(
        id=uuid4(), solution_id=solution.id, status="pending", progress_percent=0,
        message="Queued", encrypted_options="opaque-encrypted-options",
    )
    job = PlatformJob(
        job_type="solution.export", payload={}, requested_by_user_id=str(uuid4()),
        requested_by_email="person@contoso.example", requested_by_name="Contoso User",
        title="Contoso Export", memory_required_bytes=8 * GIB,
        resource_type="solution_export", resource_id=str(export.id),
        execution_backend=backend,
    )
    async with db.sessions() as session:
        session.add_all([solution, export])
        await session.flush()
        session.add(job)
        await session.commit()
    try:
        async with db.sessions() as session:
            await unfit.fail_platform_jobs_that_never_fit(session, GIB, largest_pod=GIB)
            await session.commit()
        async with db.sessions() as session:
            platform = await session.get(PlatformJob, job.id)
            backup = await session.get(SolutionExportJob, export.id)
            assert platform is not None and backup is not None
            assert (platform.status, platform.error_code) == ("failed", "too_large")
            assert backup.status == "failed"
            assert backup.message == "Backup failed"
            assert backup.failure_message == (
                "Backup export needs more memory than any executor can hold"
            )
            assert backup.encrypted_options is None
            assert backup.completed_at is not None
    finally:
        async with db.sessions() as session:
            await session.execute(delete(PlatformJob).where(PlatformJob.id == job.id))
            await session.execute(delete(SolutionExportJob).where(SolutionExportJob.id == export.id))
            await session.execute(delete(Solution).where(Solution.id == solution.id))
            await session.commit()


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
        launched = await controller.WorkerPoolController(FakeCluster(), SETTINGS, SPEC).launch_for_demand(POLICY)  # type: ignore[arg-type]
        assert len(launched) == 1
        async with db.sessions() as session:
            worker = await session.get(ExecutionWorker, launched[0])
        assert worker.reserved_for_dispatch_id is None
    finally:
        async with db.sessions() as session:
            await session.execute(delete(PlatformJob).where(PlatformJob.id == waiting.id))
            await session.commit()


async def test_large_work_waits_while_no_executor_has_reported_a_budget(db) -> None:
    await _static_executor("static-1")
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


# --- replacing Jobs after an upgrade (#1052) ----------------------------------

WARM = replace(POLICY, warm_workers=1, max_workers=4)


async def _admitted_job(  # type: ignore[no-untyped-def]
    sessions, *, version: str | None, launched_by: UUID | None, reserved_for: UUID | None = None
) -> UUID:
    worker = await _job_worker(sessions, state="ready")
    async with sessions() as session:
        await session.execute(
            update(ExecutionWorker)
            .where(ExecutionWorker.id == worker.id)
            .values(
                version=version, launched_by=launched_by, reserved_for_dispatch_id=reserved_for,
                memory_budget_bytes=GIB,
            )
        )
        await session.commit()
    return worker.id


def _controller(cluster: FakeCluster, version: str, settings=SETTINGS) -> controller.WorkerPoolController:  # type: ignore[no-untyped-def]
    pool = controller.WorkerPoolController(cluster, settings, SPEC)  # type: ignore[arg-type]
    pool.version = version
    return pool


async def _pass(pool: controller.WorkerPoolController, policy: KubernetesPolicy = WARM) -> list[UUID]:
    """One reconcile pass without the per-Job reconciliation: publish, launch
    (creating the launched Jobs), replace. Returns the Jobs launched."""
    pool.live = await controller._publish_live(pool.id, pool.version)
    launched = await pool.launch_for_demand(policy)
    for worker_id in launched:
        await pool.reconcile_worker(worker_id, policy)
    await pool.replace_outdated(policy)
    return launched


async def _admit(sessions, worker_id: UUID, *, pool_up: bool = True) -> None:  # type: ignore[no-untyped-def]
    """Admit a launched Job's pod; ``pool_up``: its pool started and it reported its budget."""
    async with sessions() as session:
        job_uid = (await session.get(ExecutionWorker, worker_id)).kubernetes_job_uid
    assert await claims.admit_worker(worker_id, job_uid=job_uid, pod_uid=f"pod-{worker_id}", version=claims.get_version()) is not None
    if pool_up:
        await _report_budget(worker_id, GIB)


async def _state(sessions, worker_id: UUID) -> tuple[str, str | None]:  # type: ignore[no-untyped-def]
    async with sessions() as session:
        worker = await session.get(ExecutionWorker, worker_id)
    return worker.state, worker.end_reason


async def _set_ready_at(sessions, worker_id: UUID, ready_at: datetime | None) -> None:  # type: ignore[no-untyped-def]
    async with sessions() as session:
        await session.execute(
            update(ExecutionWorker).where(ExecutionWorker.id == worker_id).values(ready_at=ready_at)
        )
        await session.commit()


async def test_an_imminent_lifetime_warm_worker_prelaunches_one_handoff(db, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Keep a logical warm worker available while its lifetime replacement starts."""
    policy = replace(WARM, max_workers=2, warm_workers=1)
    pool = _controller(FakeCluster(), "2.0")
    warm = await _admitted_job(db.sessions, version="2.0", launched_by=pool.id)
    now = datetime(2026, 10, 27, tzinfo=timezone.utc)
    monkeypatch.setattr(controller, "_now", lambda: now)
    await _set_ready_at(
        db.sessions,
        warm,
        now
        - timedelta(seconds=SETTINGS.pool_max_lifetime_seconds - SETTINGS.pool_start_timeout_seconds),
    )

    [handoff] = await _pass(pool, policy)
    assert await _state(db.sessions, warm) == ("ready", None), "keeps claiming until its lifetime"
    assert await _state(db.sessions, handoff) == ("starting", None)
    assert await _pass(pool, policy) == [], "the starting handoff suppresses duplicate launches"

    await _admit(db.sessions, handoff)
    assert await _pass(pool, policy) == [], "the ready handoff suppresses duplicate launches"


async def test_a_lifetime_handoff_waits_until_its_lead_window(db, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    policy = replace(WARM, max_workers=2, warm_workers=1)
    pool = _controller(FakeCluster(), "2.0")
    warm = await _admitted_job(db.sessions, version="2.0", launched_by=pool.id)
    now = datetime(2026, 10, 27, tzinfo=timezone.utc)
    monkeypatch.setattr(controller, "_now", lambda: now)
    await _set_ready_at(
        db.sessions,
        warm,
        now
        - timedelta(seconds=SETTINGS.pool_max_lifetime_seconds - SETTINGS.pool_start_timeout_seconds)
        + timedelta(microseconds=1),
    )

    assert await _pass(pool, policy) == []


async def test_a_full_warm_pool_does_not_exceed_max_for_a_lifetime_handoff(db) -> None:
    policy = replace(WARM, max_workers=1, warm_workers=1)
    pool = _controller(FakeCluster(), "2.0")
    warm = await _admitted_job(db.sessions, version="2.0", launched_by=pool.id)
    await _set_ready_at(
        db.sessions,
        warm,
        datetime.now(timezone.utc)
        - timedelta(
            seconds=SETTINGS.pool_max_lifetime_seconds - SETTINGS.pool_start_timeout_seconds + 1
        ),
    )

    assert await _pass(pool, policy) == []


async def test_short_lifetimes_prelaunch_only_the_warm_shortfall(db) -> None:
    settings = SimpleNamespace(
        **{
            **vars(SETTINGS),
            "pool_max_lifetime_seconds": 60,
            "pool_start_timeout_seconds": 300,
        }
    )
    policy = replace(WARM, max_workers=6, warm_workers=1)
    pool = _controller(FakeCluster(), "2.0", settings)
    workers = [
        await _admitted_job(db.sessions, version="2.0", launched_by=pool.id)
        for _ in range(4)
    ]
    for worker_id in workers:
        await _set_ready_at(db.sessions, worker_id, datetime.now(timezone.utc) - timedelta(seconds=31))

    [handoff] = await _pass(pool, policy)
    await _admit(db.sessions, handoff)
    assert await _pass(pool, policy) == []


def test_lifetime_handoff_targets_only_ready_standard_jobs() -> None:
    pool = _controller(FakeCluster(), "2.0")
    now = datetime(2026, 10, 27, tzinfo=timezone.utc)

    def worker(
        *, form: str, resource_class: str, state: str = "ready", ready_at: datetime | None
    ) -> ExecutionWorker:
        return ExecutionWorker(
            id=uuid4(),
            form=form,
            state=state,
            resource_class=resource_class,
            slots=1,
            ready_at=ready_at,
        )

    due = now - timedelta(seconds=SETTINGS.pool_max_lifetime_seconds)
    assert not pool._lifetime_handoff_imminent(
        worker(form="static", resource_class="standard", ready_at=due), now
    )
    assert not pool._lifetime_handoff_imminent(
        worker(form="job", resource_class="sized", ready_at=due), now
    )
    assert not pool._lifetime_handoff_imminent(
        worker(form="job", resource_class="standard", ready_at=None), now
    )
    assert not pool._lifetime_handoff_imminent(
        worker(form="job", resource_class="standard", ready_at=now + timedelta(seconds=1)), now
    )
    assert not pool._lifetime_handoff_imminent(
        worker(form="job", resource_class="standard", state="starting", ready_at=due), now
    )
    assert not pool._lifetime_handoff_imminent(
        worker(form="job", resource_class="standard", state="draining", ready_at=due), now
    )


def test_a_job_is_outdated_only_when_its_build_and_its_launcher_are_gone() -> None:
    live_id = uuid4()
    live = controller.LiveControllers(ids=frozenset({live_id}), versions=frozenset({"2.0"}))

    def job(**values):  # type: ignore[no-untyped-def]
        return SimpleNamespace(**{"form": "job", "state": "ready", "version": "1.0", "launched_by": uuid4(), **values})

    assert live.outdated(job())
    assert live.outdated(job(version=None, launched_by=None)), "launched before this rule"
    assert not live.outdated(job(version="2.0")), "a live controller runs its build"
    assert not live.outdated(job(launched_by=live_id)), "its launcher is live: never a loop"
    assert not live.outdated(job(state="starting")), "its build is unknown until admitted"
    assert not live.outdated(job(form="static")), "static executors roll with their Deployment"


async def test_live_controllers_expire_and_withdraw(db) -> None:  # type: ignore[no-untyped-def]
    old, new = uuid4(), uuid4()
    await controller._publish_live(old, "1.0")
    async with get_redis() as redis:
        await redis.hset(controller.CONTROLLERS_KEY, str(uuid4()), '{"version": "0.9", "at": 0}')  # type: ignore[misc]

    live = await controller._publish_live(new, "2.0")
    assert (live.ids, live.versions) == (frozenset({old, new}), frozenset({"1.0", "2.0"}))

    await controller._withdraw_live(old)
    live = await controller._publish_live(new, "2.0")
    assert (live.ids, live.versions) == (frozenset({new}), frozenset({"2.0"}))


async def test_an_upgrade_replaces_the_warm_job_once_its_controllers_are_gone(db) -> None:
    from shared.version import get_version

    cluster = FakeCluster()
    old = _controller(cluster, "old-build")
    new = _controller(cluster, get_version())
    old.live = await controller._publish_live(old.id, old.version)
    warm = await _admitted_job(db.sessions, version="old-build", launched_by=old.id)

    # Rolling upgrade: the old replica is still live, so nothing moves.
    assert await _pass(new) == []
    assert await _state(db.sessions, warm) == ("ready", None)

    # The last old replica stops: a replacement is launched first...
    await controller._withdraw_live(old.id)
    [replacement] = await _pass(new)
    assert await _state(db.sessions, warm) == ("ready", None), "serves until the replacement is ready"
    async with db.sessions() as session:
        assert (await session.get(ExecutionWorker, replacement)).launched_by == new.id

    # ...and the old Job drains (never deleted) once the replacement is ready.
    await _admit(db.sessions, replacement)
    assert await _pass(new) == []
    assert await _state(db.sessions, warm) == ("draining", "rotated")
    assert await _pass(new) == [], "no further launches"
    assert cluster.deleted == []
    async with db.sessions() as session:
        assert (await session.get(ExecutionWorker, replacement)).version == get_version()


async def test_a_restart_on_the_same_build_replaces_nothing(db) -> None:
    pool = _controller(FakeCluster(), "1.0")
    warm = await _admitted_job(db.sessions, version="1.0", launched_by=uuid4())

    assert await _pass(pool) == []
    assert await _state(db.sessions, warm) == ("ready", None)


async def test_a_same_build_controller_adopts_bound_workers_without_new_jobs(db) -> None:
    """A replacement API controller must preserve the Job incarnation it finds."""
    from shared.version import get_version

    await _store_policy(db.sessions, WARM)
    cluster = FakeCluster()
    stopped = _controller(cluster, get_version())
    await stopped.reconcile()
    [name] = cluster.created
    async with db.sessions() as session:
        worker_id, uid = (
            await session.execute(
                select(ExecutionWorker.id, ExecutionWorker.kubernetes_job_uid).where(
                    ExecutionWorker.kubernetes_job_name == name
                )
            )
        ).one()
    await _admit(db.sessions, worker_id)
    await controller._withdraw_live(stopped.id)

    replacement = _controller(cluster, get_version())
    await replacement.reconcile()

    async with db.sessions() as session:
        worker = await session.get(ExecutionWorker, worker_id)
    assert (worker.kubernetes_job_name, worker.kubernetes_job_uid, worker.state) == (name, uid, "ready")
    assert cluster.created == [name]
    assert cluster.deleted == []


async def test_a_replacement_on_another_build_is_never_replaced_while_its_launcher_lives(db) -> None:
    """An explicit build image can run another build than the controller;
    the Jobs it launches must not be replaced in a loop."""
    cluster = FakeCluster()
    pool = _controller(cluster, "controller-build")
    warm = await _admitted_job(db.sessions, version="build-image", launched_by=pool.id)

    assert await _pass(pool) == []
    assert await _state(db.sessions, warm) == ("ready", None)
    assert cluster.created == []


async def test_a_job_launched_for_a_dispatch_drains_only_after_claiming_it(db) -> None:
    pool = _controller(FakeCluster(), "2.0")
    current = await _admitted_job(db.sessions, version="2.0", launched_by=pool.id)
    holding = await _admitted_job(db.sessions, version="1.0", launched_by=None, reserved_for=uuid4())

    assert await _pass(pool) == []
    assert await _state(db.sessions, holding) == ("ready", None)
    assert await _state(db.sessions, current) == ("ready", None)


async def test_without_a_warm_minimum_outdated_jobs_drain_at_once(db) -> None:
    pool = _controller(FakeCluster(), "2.0")
    outdated = await _admitted_job(db.sessions, version="1.0", launched_by=None)

    assert await _pass(pool, replace(WARM, warm_workers=0)) == []
    assert await _state(db.sessions, outdated) == ("draining", "rotated")


async def test_a_full_outdated_pool_drains_one_ready_job_above_the_warm_minimum(db) -> None:
    policy = replace(WARM, max_workers=2, warm_workers=1)
    cluster = FakeCluster()
    pool = _controller(cluster, "2.0")
    outdated = [
        await _admitted_job(db.sessions, version="1.0", launched_by=None),
        await _admitted_job(db.sessions, version="1.0", launched_by=None),
    ]
    async with db.sessions() as session:
        await session.execute(
            update(ExecutionWorker).where(ExecutionWorker.id.in_(outdated)).values(active_runs=1)
        )
        await session.commit()

    assert await _pass(pool, policy) == []
    draining = [
        worker_id
        for worker_id in outdated
        if await _state(db.sessions, worker_id) == ("draining", "rotated")
    ]
    assert len(draining) == 1
    remaining = next(worker_id for worker_id in outdated if worker_id not in draining)
    assert await _state(db.sessions, remaining) == ("ready", None)
    async with db.sessions() as session:
        assert (await session.get(ExecutionWorker, draining[0])).active_runs == 1
    assert cluster.deleted == []

    assert await _pass(pool, policy) == []
    assert [
        worker_id
        for worker_id in outdated
        if await _state(db.sessions, worker_id) == ("draining", "rotated")
    ] == draining

    async with db.sessions() as session:
        await session.execute(
            update(ExecutionWorker)
            .where(ExecutionWorker.id == draining[0])
            .values(state="retired", active_runs=0)
        )
        await session.commit()

    [replacement] = await _pass(pool, policy)
    assert await _state(db.sessions, replacement) == ("starting", None)
    assert await _state(db.sessions, remaining) == ("ready", None)


async def test_a_full_pool_at_its_warm_minimum_does_not_surge_or_drain(db) -> None:
    policy = replace(WARM, max_workers=2, warm_workers=2)
    pool = _controller(FakeCluster(), "2.0")
    outdated = [
        await _admitted_job(db.sessions, version="1.0", launched_by=None),
        await _admitted_job(db.sessions, version="1.0", launched_by=None),
    ]

    assert await _pass(pool, policy) == []
    assert [await _state(db.sessions, worker_id) for worker_id in outdated] == [
        ("ready", None),
        ("ready", None),
    ]
    assert await _pass(pool, policy) == []
    assert [await _state(db.sessions, worker_id) for worker_id in outdated] == [
        ("ready", None),
        ("ready", None),
    ]


async def test_an_unmeasured_ready_job_does_not_supply_warm_rotation_capacity(db) -> None:
    policy = replace(WARM, max_workers=2, warm_workers=1)
    pool = _controller(FakeCluster(), "2.0")
    measured = await _admitted_job(db.sessions, version="1.0", launched_by=None)
    unmeasured = await _admitted_job(db.sessions, version="1.0", launched_by=None)
    async with db.sessions() as session:
        await session.execute(
            update(ExecutionWorker)
            .where(ExecutionWorker.id == unmeasured)
            .values(memory_budget_bytes=None)
        )
        await session.commit()

    assert await _pass(pool, policy) == []
    assert [await _state(db.sessions, worker_id) for worker_id in (measured, unmeasured)] == [
        ("ready", None),
        ("ready", None),
    ]


async def test_headroom_launches_a_replacement_before_draining_outdated_workers(db) -> None:
    policy = replace(WARM, max_workers=3, warm_workers=1)
    pool = _controller(FakeCluster(), "2.0")
    outdated = [
        await _admitted_job(db.sessions, version="1.0", launched_by=None),
        await _admitted_job(db.sessions, version="1.0", launched_by=None),
    ]

    [replacement] = await _pass(pool, policy)
    assert await _state(db.sessions, replacement) == ("starting", None)
    assert [await _state(db.sessions, worker_id) for worker_id in outdated] == [
        ("ready", None),
        ("ready", None),
    ]


async def test_a_starting_current_replacement_prevents_an_early_excess_drain(db) -> None:
    policy = replace(WARM, max_workers=4, warm_workers=2)
    pool = _controller(FakeCluster(), "2.0")
    outdated = [
        await _admitted_job(db.sessions, version="1.0", launched_by=None)
        for _ in range(3)
    ]
    current = await _job_worker(db.sessions, state="starting")
    async with db.sessions() as session:
        await session.execute(
            update(ExecutionWorker)
            .where(ExecutionWorker.id == current.id)
            .values(version="2.0", launched_by=pool.id)
        )
        await session.commit()

    assert await _pass(pool, policy) == []
    assert [await _state(db.sessions, worker_id) for worker_id in outdated] == [
        ("ready", None),
        ("ready", None),
        ("ready", None),
    ]


async def test_a_full_current_pool_never_uses_a_rolling_replacement_slot(db) -> None:
    policy = replace(WARM, max_workers=2, warm_workers=2)
    pool = _controller(FakeCluster(), "2.0")
    current = [
        await _admitted_job(db.sessions, version="2.0", launched_by=pool.id),
        await _admitted_job(db.sessions, version="2.0", launched_by=pool.id),
    ]

    assert await _pass(pool, policy) == []
    assert [await _state(db.sessions, worker_id) for worker_id in current] == [
        ("ready", None),
        ("ready", None),
    ]


async def test_a_current_job_that_idles_out_first_is_relaunched(db) -> None:
    """Both API pods restarting can let the current Job reach its idle drain
    before the outdated one is replaced; the warm minimum converges."""
    pool = _controller(FakeCluster(), "2.0")
    outdated = await _admitted_job(db.sessions, version="1.0", launched_by=None)
    current = await _admitted_job(db.sessions, version="2.0", launched_by=pool.id)
    await _store_policy(db.sessions, WARM)
    assert await claims.begin_idle_drain(current)

    [relaunched] = await _pass(pool)
    assert await _state(db.sessions, outdated) == ("ready", None)
    await _admit(db.sessions, relaunched)
    await _pass(pool)
    assert await _state(db.sessions, outdated) == ("draining", "rotated")


async def test_the_creating_controller_is_the_launcher(db) -> None:
    """Any replica may create the Job for a row another reserved; the row
    names the one whose image the Job runs."""
    cluster = FakeCluster()
    reserving, creating = _controller(cluster, "2.0"), _controller(cluster, "2.0")
    [worker_id] = await reserving.launch_for_demand(WARM)

    await creating.reconcile_worker(worker_id, WARM)

    async with db.sessions() as session:
        assert (await session.get(ExecutionWorker, worker_id)).launched_by == creating.id


async def test_a_long_drain_is_reported_once(db, caplog) -> None:  # type: ignore[no-untyped-def]
    pool = _controller(FakeCluster(), "2.0", SimpleNamespace(**{**vars(SETTINGS), "pool_max_lifetime_seconds": 0}))
    worker = SimpleNamespace(id=uuid4(), state="draining", active_runs=1, end_reason="rotated")

    pool._watch_drain(worker)  # type: ignore[arg-type]
    pool._watch_drain(worker)  # type: ignore[arg-type]

    assert [r.message for r in caplog.records].count(
        "A draining worker still has runs after its maximum lifetime"
    ) == 1


async def test_worker_jobs_pull_like_the_controller() -> None:
    from src.services.worker_pool.manifest import resolve_worker_job_spec

    client = SimpleNamespace(
        namespace="test",
        own_container=AsyncMock(return_value={
            "image": "ghcr.io/gobifrost/bifrost-api:latest",
            "imagePullPolicy": "Always",
            "envFrom": [{"configMapRef": {"name": "config"}}],
        }),
    )
    unset = SimpleNamespace(**{
        **vars(SETTINGS), "kubernetes_job_image": None, "kubernetes_job_configmap": None,
        "kubernetes_job_secret": None, "kubernetes_job_service_account": None,
    })

    spec = await resolve_worker_job_spec(client, unset)  # type: ignore[arg-type]
    manifest = build_worker_job_manifest(
        uuid4(), SETTINGS, spec, memory_limit_bytes=2 * GIB, sized=False
    )

    assert manifest["spec"]["template"]["spec"]["containers"][0]["imagePullPolicy"] == "Always"
    assert (await resolve_worker_job_spec(client, SETTINGS)).image_pull_policy == "IfNotPresent"  # type: ignore[arg-type]


async def test_the_old_job_serves_until_its_replacement_can_claim(db) -> None:
    """An admitted replacement still starts its pool (seconds to minutes);
    the old Job drains only once the replacement has measured its budget."""
    pool = _controller(FakeCluster(), "2.0")
    outdated = await _admitted_job(db.sessions, version="1.0", launched_by=None)
    [replacement] = await _pass(pool)
    await _admit(db.sessions, replacement, pool_up=False)

    await _pass(pool)
    assert await _state(db.sessions, outdated) == ("ready", None)

    await _report_budget(replacement, GIB)
    await _pass(pool)
    assert await _state(db.sessions, outdated) == ("draining", "rotated")


async def test_an_adopted_job_keeps_no_launcher(db) -> None:
    """A Job created by an instance that died before binding it runs that
    instance's image: the adopting controller does not vouch for it."""
    cluster = FakeCluster()
    dead, adopting = _controller(cluster, "1.0"), _controller(cluster, "2.0")
    [worker_id] = await dead.launch_for_demand(WARM)
    async with db.sessions() as session:
        worker = await session.get(ExecutionWorker, worker_id)
        await cluster.create_job(
            build_worker_job_manifest(
                worker_id, SETTINGS, SPEC, memory_limit_bytes=WARM.worker_memory_mib * MIB,
                sized=False,
            )
        )
        name = worker.kubernetes_job_name

    await adopting.reconcile_worker(worker_id, WARM)

    async with db.sessions() as session:
        worker = await session.get(ExecutionWorker, worker_id)
    assert (worker.state, worker.launched_by) == ("starting", None)
    assert cluster.created == [name]


async def test_a_full_reconcile_launches_before_it_drains(db) -> None:
    from shared.version import get_version

    await _store_policy(db.sessions, WARM)
    cluster = FakeCluster()
    old = _controller(cluster, "old-build")
    await old.reconcile()
    [warm] = cluster.created
    async with db.sessions() as session:
        warm_id = (
            await session.execute(select(ExecutionWorker.id).where(ExecutionWorker.kubernetes_job_name == warm))
        ).scalar_one()
    await _admit(db.sessions, warm_id)
    async with db.sessions() as session:
        await session.execute(update(ExecutionWorker).where(ExecutionWorker.id == warm_id).values(version="old-build"))
        await session.commit()
    await controller._withdraw_live(old.id)
    new = _controller(cluster, get_version())

    await new.reconcile()
    assert await _state(db.sessions, warm_id) == ("ready", None)
    replacement = next(name for name in cluster.created if name != warm)
    async with db.sessions() as session:
        replacement_id = (
            await session.execute(select(ExecutionWorker.id).where(ExecutionWorker.kubernetes_job_name == replacement))
        ).scalar_one()
    await _admit(db.sessions, replacement_id)
    await new.reconcile()

    assert await _state(db.sessions, warm_id) == ("draining", "rotated")
    assert len(cluster.created) == 2 and cluster.deleted == []


async def test_the_worker_controller_retries_a_kubernetes_api_error_reading_its_pod(
    monkeypatch,
) -> None:
    """Like the build loop: a transient API error at loop start is retried
    with backoff instead of disabling reusable workers until a restart."""
    from src.jobs.platform.kubernetes_client import KubernetesApiError
    from src.services.worker_pool import manifest

    client = AsyncMock()
    monkeypatch.setattr(controller, "KubernetesJobClient", lambda settings: client)
    monkeypatch.setattr(manifest, "SPEC_RETRY_INITIAL_SECONDS", 0.01)
    resolve = AsyncMock(side_effect=[KubernetesApiError("GET", "/pods", 503), SPEC])
    monkeypatch.setattr(manifest, "resolve_worker_job_spec", resolve)
    monkeypatch.setattr(controller, "_withdraw_live", AsyncMock())
    stop = asyncio.Event()
    passes = []

    class Pool:
        def __init__(self, client, settings, spec):  # type: ignore[no-untyped-def]
            self.id = uuid4()
            self.spec = spec
            self.reachability = SimpleNamespace(check=AsyncMock(return_value=True))

        async def reconcile(self) -> None:
            passes.append(self.spec)
            stop.set()

    monkeypatch.setattr(controller, "WorkerPoolController", Pool)

    await asyncio.wait_for(controller.worker_pool_loop(stop, SETTINGS), timeout=10)

    assert resolve.await_count == 2
    assert passes == [SPEC]
    client.close.assert_awaited_once()


async def test_the_worker_controller_idles_when_its_pod_gives_no_job_identity(
    monkeypatch,
) -> None:
    from src.jobs.platform.kubernetes_client import KubernetesConfigurationError
    from src.services.worker_pool import manifest

    client = AsyncMock()
    monkeypatch.setattr(controller, "KubernetesJobClient", lambda settings: client)
    resolve = AsyncMock(side_effect=KubernetesConfigurationError("No image is known for Jobs"))
    monkeypatch.setattr(manifest, "resolve_worker_job_spec", resolve)
    stop = asyncio.Event()
    loop = asyncio.create_task(controller.worker_pool_loop(stop, SETTINGS))
    await asyncio.sleep(0.05)
    assert not loop.done()
    stop.set()
    await asyncio.wait_for(loop, timeout=10)

    resolve.assert_awaited_once()
    client.close.assert_awaited_once()
