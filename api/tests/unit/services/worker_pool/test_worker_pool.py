"""Reusable executor protocol against real PostgreSQL and a fake cluster."""

from __future__ import annotations

import asyncio
import copy
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, AsyncGenerator
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.models.enums import ExecutionStatus
from src.models.orm.execution_workers import ExecutionWorker
from src.models.orm.executions import Execution
from src.models.orm.work_dispatches import WorkDispatch
from src.services import work_dispatch
from src.services.execution import async_executor, workflow_dispatch
from src.services.execution.async_executor import _publish_pending
from src.services.worker_pool import claims, controller, maintenance
from src.services.worker_pool.manifest import WorkerJobSpec, build_worker_job_manifest

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
    pool_worker_slots=2,
    pool_start_timeout_seconds=300,
    pool_worker_memory_request_mib=512,
    pool_worker_memory_limit_mib=2048,
    pool_worker_cpu_request="250m",
    pool_worker_cpu_limit="2",
)

SPEC = WorkerJobSpec(
    namespace="test",
    image="api:test",
    configmap="config",
    secret="secret",
    service_account="runner",
)


class FakeCluster:
    namespace = "test"

    def __init__(self) -> None:
        self.jobs: dict[str, dict[str, Any]] = {}
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

    for module in (claims, controller, maintenance, work_dispatch):
        monkeypatch.setattr(module, "get_db_context", context)
    monkeypatch.setattr(async_executor, "get_dispatch_db_context", context)
    monkeypatch.setattr("src.config.get_settings", lambda: SETTINGS)
    wake = AsyncMock()
    monkeypatch.setattr(work_dispatch, "wake_pool_workers", wake)
    monkeypatch.setattr(workflow_dispatch, "get_redis_client", AsyncMock)
    monkeypatch.setattr("src.services.execution.queue_tracker.add_to_queue", AsyncMock())
    return SimpleNamespace(sessions=async_session_factory, wake=wake)


@pytest.fixture(autouse=True)
async def _clean(async_session_factory):
    yield
    async with async_session_factory() as session:
        await session.execute(delete(WorkDispatch))
        await session.execute(delete(ExecutionWorker))
        await session.commit()


async def _accept_pool_run() -> str:
    execution_id = str(uuid4())
    await _publish_pending(
        execution_id=execution_id, workflow_id=None, parameters={}, org_id=None,
        user_id="00000000-0000-0000-0000-000000000001", user_name="Dev", user_email="",
        form_id=None, startup=None, form_inputs={}, embed={}, api_key_id=None,
        sync=False, is_platform_admin=True, file_path=None, lineage=None,
    )
    return execution_id


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


# --- placement -------------------------------------------------------------


async def test_accepted_runs_are_pool_placed_and_wake_the_executors(db) -> None:
    execution_id = await _accept_pool_run()

    async with db.sessions() as session:
        dispatch = (
            await session.execute(
                select(WorkDispatch).where(WorkDispatch.subject_id == UUID(execution_id))
            )
        ).scalar_one()
    assert dispatch.placement == "pool"
    db.wake.assert_awaited_once()
    # An unclaimed pool dispatch stays put until its claim deadline.
    async with db.sessions() as session:
        await session.execute(
            update(WorkDispatch).values(created_at=datetime.now(timezone.utc) - timedelta(minutes=5))
        )
        await session.commit()
    assert await work_dispatch.reconcile_work_dispatches() == 0


async def test_only_the_first_pod_of_the_bound_job_is_admitted(db) -> None:
    worker = await _job_worker(db.sessions)

    assert await claims.admit_worker(worker.id, job_uid="other-job", pod_uid="p0") is None
    assert await claims.admit_worker(worker.id, job_uid="job-uid", pod_uid="p1") == 2
    assert await claims.admit_worker(worker.id, job_uid="job-uid", pod_uid="p2") is None


async def test_a_ready_worker_claims_each_dispatch_once(db) -> None:
    first = await _job_worker(db.sessions, state="ready")
    second = await _job_worker(db.sessions, state="ready")
    execution_id = await _accept_pool_run()

    claims_made = await asyncio.gather(claims.claim_next(first.id), claims.claim_next(second.id))

    winners = [claim for claim in claims_made if claim is not None]
    assert len(winners) == 1
    assert winners[0].message["execution_id"] == execution_id
    assert winners[0].handler == "workflow.execute"


async def test_a_draining_worker_never_claims(db) -> None:
    worker = await _job_worker(db.sessions, state="ready")
    assert await claims.begin_drain(worker.id, "idle")
    await _accept_pool_run()

    assert await claims.claim_next(worker.id) is None
    assert not await claims.begin_drain(worker.id, "idle")  # once only


async def test_unsupported_handler_versions_wait_for_a_compatible_worker(db) -> None:
    worker = await _job_worker(db.sessions, state="ready")
    execution_id = await _accept_pool_run()
    async with db.sessions() as session:
        await session.execute(update(WorkDispatch).values(handler_version=99))
        await session.commit()

    assert await claims.claim_next(worker.id) is None
    async with db.sessions() as session:
        dispatch = (
            await session.execute(
                select(WorkDispatch).where(WorkDispatch.subject_id == UUID(execution_id))
            )
        ).scalar_one()
    assert dispatch.assigned_worker_id is None


async def test_static_executors_register_ready_and_claim(db) -> None:
    worker_id = await claims.register_static_worker(slots=3, hostname="worker-1")
    await _accept_pool_run()

    assert await claims.claim_next(worker_id) is not None


# --- controller ---------------------------------------------------------------


async def test_demand_launches_bounded_workers_and_binds_before_start(db) -> None:
    for _ in range(9):  # 9 runs / 2 slots would want 5 workers; the ceiling is 2
        await _accept_pool_run()
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


async def test_ready_free_slots_satisfy_demand_without_launching(db) -> None:
    await claims.register_static_worker(slots=4, hostname="static-1")
    await _accept_pool_run()
    cluster = FakeCluster()

    await controller.WorkerPoolController(cluster, SETTINGS, SPEC).reconcile()  # type: ignore[arg-type]

    assert cluster.created == []


async def test_silent_worker_is_lost_and_its_unstarted_work_released(db, monkeypatch) -> None:
    """An agent run is claimed by its consumer after assignment; if the worker
    dies in between, the run is still queued and goes to another worker."""
    from src.services.execution import agent_run_dispatch, agent_run_service
    from src.services.execution.agent_run_service import enqueue_agent_run

    monkeypatch.setattr(agent_run_service, "get_dispatch_db_context", claims.get_db_context)
    monkeypatch.setattr(agent_run_dispatch, "cache_agent_context", AsyncMock())
    cluster = FakeCluster()
    pool = controller.WorkerPoolController(cluster, SETTINGS, SPEC)  # type: ignore[arg-type]
    run_id = await enqueue_agent_run(agent_id=None, trigger_type="sdk", lineage=None)
    [worker_id] = await pool.launch_for_demand()
    await pool.reconcile_worker(worker_id)
    async with db.sessions() as session:
        job_uid = (await session.get(ExecutionWorker, worker_id)).kubernetes_job_uid
    assert await claims.admit_worker(worker_id, job_uid=job_uid, pod_uid="pod") == 2
    claim = await claims.claim_next(worker_id)
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
    assert await work_dispatch.reconcile_work_dispatches() == 0

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


async def test_ended_executors_are_pruned_and_silent_static_ones_ended(db) -> None:
    static_id = await claims.register_static_worker(slots=1, hostname="gone")
    old = await _job_worker(db.sessions, state="retired")
    async with db.sessions() as session:
        await session.execute(
            update(ExecutionWorker)
            .where(ExecutionWorker.id == static_id)
            .values(heartbeat_at=datetime.now(timezone.utc) - timedelta(hours=1))
        )
        await session.execute(
            update(ExecutionWorker)
            .where(ExecutionWorker.id == old.id)
            .values(ended_at=datetime.now(timezone.utc) - timedelta(days=30))
        )
        await session.commit()

    await maintenance.prune_execution_workers()

    async with db.sessions() as session:
        assert (await session.get(ExecutionWorker, static_id)).state == "lost"
        assert await session.get(ExecutionWorker, old.id) is None


def test_worker_job_is_suspended_tokenless_and_has_no_deadline() -> None:
    manifest = build_worker_job_manifest(uuid4(), 3, SETTINGS, SPEC)
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
    assert env["BIFROST_MAX_CONCURRENCY"]["value"] == "3"
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
    claim = await claims.claim_next(worker_id)
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
