"""Exclusive jobs share physical provisioning, but never reusable workers."""

from dataclasses import replace
import pytest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import select

from src.jobs.platform.policy import EXTERNAL_ISOLATED_BACKEND
from src.models.orm.execution_workers import ExecutionWorker
from src.models.orm.platform_jobs import PlatformJob
from src.services.worker_pool.controller import plan_capacity_launches
from src.services.worker_pool.external_controller import ExternalWorkerPoolController
from tests.unit.services.worker_pool.test_external_capacity import capacity as capacity_fixture
from tests.unit.services.worker_pool.test_worker_pool import (
    MIB,
    _accept_pool_run,
    _clean as _clean,
    _set_request,
    db as db_fixture,
)

db = db_fixture
capacity = capacity_fixture


async def _queued_job(db):
    job = PlatformJob(
        job_type="application.deploy", payload={}, execution_backend=EXTERNAL_ISOLATED_BACKEND,
        requested_by_user_id=str(uuid4()), requested_by_email="fixture@example.test",
        requested_by_name="Fixture", title="Isolated App build", memory_required_bytes=256 * MIB,
    )
    async with db.context() as session:
        session.add(job)
    return job.id


def _policy(capacity):
    return replace(
        capacity.policy, isolated_jobs_enabled=True,
        enabled_job_types=frozenset({"application.deploy"}), warm_workers=0,
    )


async def test_isolated_placement_reserves_a_worker_without_claiming_the_job(db, capacity):
    job_id = await _queued_job(db)
    [worker_id] = (await plan_capacity_launches(_policy(capacity), (capacity.pool,)))[capacity.pool]

    async with db.context() as session:
        worker = await session.get(ExecutionWorker, worker_id)
        job = await session.get(PlatformJob, job_id)
        assert worker.resource_class == "isolated"
        assert worker.isolated_platform_job_id == job_id
        assert worker.isolated_platform_job_attempt == 1
        assert worker.supported_handlers == []
        assert worker.supported_compute == ["app.compile.v2"]
        assert worker.memory_limit_bytes == capacity.provider.profile.memory_limit_bytes
        assert job.status == "queued" and job.attempt == 0 and job.lease_token is None

    assert (await plan_capacity_launches(_policy(capacity), (capacity.pool,)))[capacity.pool] == []
    async with db.context() as session:
        worker = await session.get(ExecutionWorker, worker_id)
        worker.state = "lost"

    # Loss does not release the job binding; only the physical deletion
    # receipt may permit a new resource for its next attempt.
    assert (await plan_capacity_launches(_policy(capacity), (capacity.pool,)))[capacity.pool] == []


async def test_isolated_capacity_cannot_cover_ordinary_workflow_demand(db, capacity):
    await _queued_job(db)
    [isolated_id] = (await plan_capacity_launches(_policy(capacity), (capacity.pool,)))[capacity.pool]
    async with db.context() as session:
        worker = await session.get(ExecutionWorker, isolated_id)
        worker.state = "ready"
        worker.memory_budget_bytes = 512 * MIB
    run = await _accept_pool_run()
    await _set_request(db.sessions, run, 256 * MIB)

    [normal_id] = (await plan_capacity_launches(_policy(capacity), (capacity.pool,)))[capacity.pool]

    async with db.context() as session:
        normal = await session.get(ExecutionWorker, normal_id)
        assert normal.isolated_platform_job_id is None
        assert "workflow.execute" in normal.supported_handlers


async def test_cancel_before_prepare_seals_and_deletes_without_starting_an_image(db, capacity):
    job_id = await _queued_job(db)
    policy = _policy(capacity)
    [worker_id] = (await plan_capacity_launches(policy, (capacity.pool,)))[capacity.pool]
    async with db.context() as session:
        job = await session.get(PlatformJob, job_id)
        job.status = "cancelled"

    await capacity.pool.reconcile_worker(worker_id, policy)
    async with db.context() as session:
        assert (await session.get(ExecutionWorker, worker_id)).state == "retired"
    await capacity.pool.reconcile_worker(worker_id, policy)

    async with db.context() as session:
        worker = await session.get(ExecutionWorker, worker_id)
        job = await session.get(PlatformJob, job_id)
        assert worker.capacity_resource_deleted_at is not None
        assert worker.capacity_resource_uid is not None
        assert job.attempt == 0 and job.lease_token is None
    assert capacity.provider.starts == []
    assert capacity.provider.resources[str(worker_id)]["state"] == "deleted"


async def test_isolated_workers_share_the_provider_ceiling_with_the_warm_pool(db, capacity):
    await _queued_job(db)
    async with db.context() as session:
        session.add(ExecutionWorker(
            form="external", state="ready", capacity_scope=capacity.provider.profile.scope,
            capacity_resource_uid="existing-normal-resource", supported_handlers=["workflow.execute"],
            supported_compute=[], memory_budget_bytes=512 * MIB,
            memory_limit_bytes=capacity.provider.profile.memory_limit_bytes,
        ))
    policy = replace(_policy(capacity), warm_workers=2)

    [worker_id] = (await plan_capacity_launches(policy, (capacity.pool,)))[capacity.pool]

    async with db.context() as session:
        workers = (await session.scalars(select(ExecutionWorker))).all()
        assert len(workers) == capacity.provider.profile.max_workers
        assert (await session.get(ExecutionWorker, worker_id)).resource_class == "isolated"


async def test_older_isolated_request_precedes_newer_normal_demand(db, capacity):
    """The ordered demand pass cannot let a normal worker consume its profile."""
    job_id = await _queued_job(db)
    async with db.context() as session:
        job = await session.get(PlatformJob, job_id)
        job.created_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    run = await _accept_pool_run()
    await _set_request(db.sessions, run, 256 * MIB)

    profile = capacity.provider.profile.model_copy(update={"max_workers": 1})
    cloud = ExternalWorkerPoolController(capacity.client, capacity.pool.settings, profile)
    [worker_id] = (await plan_capacity_launches(_policy(capacity), (cloud,)))[cloud]

    async with db.context() as session:
        worker = await session.get(ExecutionWorker, worker_id)
        assert worker.isolated_platform_job_id == job_id
        assert worker.supported_handlers == []


async def test_older_normal_demand_precedes_newer_isolated_request(db, capacity):
    """The same ordered pass retains FIFO in the opposite direction too."""
    run = await _accept_pool_run()
    await _set_request(db.sessions, run, 256 * MIB, age=timedelta(minutes=1))
    await _queued_job(db)

    profile = capacity.provider.profile.model_copy(update={"max_workers": 1})
    cloud = ExternalWorkerPoolController(capacity.client, capacity.pool.settings, profile)
    [worker_id] = (await plan_capacity_launches(_policy(capacity), (cloud,)))[cloud]

    async with db.context() as session:
        worker = await session.get(ExecutionWorker, worker_id)
        assert worker.isolated_platform_job_id is None
        assert "workflow.execute" in worker.supported_handlers


async def test_isolated_reclamation_keeps_a_bin_consumed_by_older_work(db, capacity):
    """A later isolated request cannot drain capacity already planned for work."""
    profile = capacity.provider.profile.model_copy(update={"max_workers": 1})
    cloud = ExternalWorkerPoolController(capacity.client, capacity.pool.settings, profile)
    async with db.context() as session:
        session.add(ExecutionWorker(
            form="external", state="ready", resource_class="standard",
            capacity_scope=profile.scope, capacity_resource_uid="occupied-in-plan-resource",
            supported_handlers=["workflow.execute"], supported_compute=[],
            active_runs=0, active_handler_counts={}, memory_budget_bytes=512 * MIB,
            memory_limit_bytes=profile.memory_limit_bytes,
        ))
    run = await _accept_pool_run()
    await _set_request(db.sessions, run, 256 * MIB, age=timedelta(minutes=1))
    await _queued_job(db)

    assert (await plan_capacity_launches(_policy(capacity), (cloud,)))[cloud] == []
    async with db.context() as session:
        worker = await session.scalar(select(ExecutionWorker).where(
            ExecutionWorker.capacity_resource_uid == "occupied-in-plan-resource",
        ))
        assert worker is not None
        assert worker.state == "ready"
        assert worker.end_reason is None


async def test_isolated_capacity_drains_one_idle_external_worker_until_deleted(db, capacity):
    job_id = await _queued_job(db)
    profile = capacity.provider.profile.model_copy(update={"max_workers": 1})
    cloud = ExternalWorkerPoolController(capacity.client, capacity.pool.settings, profile)
    async with db.context() as session:
        normal = ExecutionWorker(
            form="external", state="ready", resource_class="standard",
            capacity_scope=profile.scope, capacity_resource_uid="idle-normal-resource",
            supported_handlers=["workflow.execute"], supported_compute=[],
            active_runs=0, active_handler_counts={}, memory_budget_bytes=512 * MIB,
            memory_limit_bytes=profile.memory_limit_bytes,
        )
        session.add(normal)

    assert (await plan_capacity_launches(_policy(capacity), (cloud,)))[cloud] == []
    async with db.context() as session:
        worker = await session.get(ExecutionWorker, normal.id)
        assert worker.state == "draining"
        assert worker.end_reason == "isolated_capacity"

    # The draining physical resource still fills the provider profile and
    # prevents a second reclamation before deletion is durable.
    assert (await plan_capacity_launches(_policy(capacity), (cloud,)))[cloud] == []
    async with db.context() as session:
        worker = await session.get(ExecutionWorker, normal.id)
        assert worker.state == "draining"
        worker.capacity_resource_deleted_at = datetime.now(timezone.utc)

    [worker_id] = (await plan_capacity_launches(_policy(capacity), (cloud,)))[cloud]
    async with db.context() as session:
        worker = await session.get(ExecutionWorker, worker_id)
        assert worker.isolated_platform_job_id == job_id


@pytest.mark.parametrize("job_type, expected_compute", [
    ("solution.deploy", ["app.compile.v2", "solution.archive.inspect.v1"]),
    ("solution.export", ["solution.archive.inspect.v1"]),
])
async def test_solution_reservation_grants_complete_code_owned_compute_set(db, capacity, job_type, expected_compute):
    job_id = await _queued_job(db)
    async with db.context() as session:
        job = await session.get(PlatformJob, job_id)
        job.job_type = job_type
    policy = replace(_policy(capacity), enabled_job_types=frozenset({job_type}))
    [worker_id] = (await plan_capacity_launches(policy, (capacity.pool,)))[capacity.pool]
    async with db.context() as session:
        worker = await session.get(ExecutionWorker, worker_id)
        assert worker.isolated_platform_job_id == job_id
        assert worker.supported_handlers == []
        assert worker.supported_compute == expected_compute
        job = await session.get(PlatformJob, job_id)
        assert job.attempt == 0 and job.status == "queued"
