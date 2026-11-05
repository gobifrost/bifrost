"""Cold-start capacity follows canonical PlatformJob eligibility and limits."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from src.models.orm.execution_workers import ExecutionWorker
from src.models.orm.platform_jobs import PlatformJob
from src.services.worker_pool import controller
from tests.unit.services.worker_pool.test_worker_capacity_domains import _executor
from tests.unit.services.worker_pool.test_worker_pool import (
    GIB, MIB, POLICY, SETTINGS, SPEC, FakeCluster,
    _clean as _clean, db as db_fixture,
)

db = db_fixture


@pytest_asyncio.fixture
async def jobs(db):
    ids = []

    async def create(**overrides):
        values = {
            "job_type": "application.deploy", "payload": {},
            "requested_by_user_id": str(uuid4()), "requested_by_email": "test@example.com",
            "requested_by_name": "Test", "title": "Capacity fixture",
            "memory_required_bytes": GIB,
        }
        values.update(overrides)
        async with db.context() as session:
            row = PlatformJob(**values)
            session.add(row)
            await session.flush()
            ids.append(row.id)
            return row.id

    try:
        yield create
    finally:
        async with db.context() as session:
            await session.execute(delete(PlatformJob).where(PlatformJob.id.in_(ids)))


async def test_queued_job_starts_a_zero_worker_pool_without_memory_wait_phase(db, jobs):
    job_id = await jobs(phase=None)
    pool = controller.WorkerPoolController(FakeCluster(), SETTINGS, SPEC)

    assert len(await pool.launch_for_demand(POLICY)) == 1
    assert await pool.launch_for_demand(POLICY) == []
    async with db.context() as session:
        job = await session.get(PlatformJob, job_id)
        assert job.status == "queued" and job.phase is None and job.lease_token is None


async def test_queued_job_uses_available_worker_memory(db, jobs):
    await jobs(memory_required_bytes=128 * MIB)
    await _executor(db, "launcher", ["workflow.execute"], GIB)
    pool = controller.WorkerPoolController(FakeCluster(), SETTINGS, SPEC)

    assert await pool.launch_for_demand(POLICY) == []


@pytest.mark.parametrize("change", [
    {"available_at": datetime.now(timezone.utc) + timedelta(hours=1)},
    {"execution_backend": "kubernetes"},
    {"status": "waiting"},
    {"status": "succeeded"},
    {"job_type": "unknown.handler"},
])
async def test_non_runnable_job_does_not_create_capacity(db, jobs, change):
    await jobs(**change)
    pool = controller.WorkerPoolController(FakeCluster(), SETTINGS, SPEC)

    assert await pool.launch_for_demand(POLICY) == []


@pytest.mark.parametrize("state", ["running", "cancel_requested"])
async def test_type_limit_counts_active_jobs_on_other_backends(db, jobs, state):
    await jobs(status=state, execution_backend="kubernetes")
    await jobs()
    pool = controller.WorkerPoolController(FakeCluster(), SETTINGS, SPEC)

    assert await pool.launch_for_demand(POLICY) == []


async def test_serial_backlog_only_plans_one_runnable_job(db, jobs):
    await jobs()
    await jobs()
    pool = controller.WorkerPoolController(FakeCluster(), SETTINGS, SPEC)

    assert len(await pool.launch_for_demand(POLICY)) == 1


async def test_type_override_adds_parallel_capacity(db, jobs):
    await jobs()
    await jobs()
    pool = controller.WorkerPoolController(FakeCluster(), SETTINGS, SPEC)
    policy = replace(POLICY, max_concurrency={"application.deploy": 2})

    assert len(await pool.launch_for_demand(policy)) == 2


async def test_busy_resource_blocks_other_job_types_and_backends(db, jobs):
    await jobs(status="running", execution_backend="kubernetes", resource_lock_key="fixture")
    await jobs(job_type="application.sdk_update", resource_lock_key="fixture")
    pool = controller.WorkerPoolController(FakeCluster(), SETTINGS, SPEC)

    assert await pool.launch_for_demand(POLICY) == []


async def test_pending_resource_only_plans_one_parallel_job(db, jobs):
    await jobs(resource_lock_key="fixture")
    await jobs(job_type="application.sdk_update", resource_lock_key="fixture")
    pool = controller.WorkerPoolController(FakeCluster(), SETTINGS, SPEC)

    assert len(await pool.launch_for_demand(POLICY)) == 1


async def test_claim_priority_selects_the_memory_request_used_for_capacity(db, jobs):
    await jobs(priority=1, memory_required_bytes=4 * GIB)
    await jobs(priority=100, memory_required_bytes=128 * MIB)
    pool = controller.WorkerPoolController(FakeCluster(), SETTINGS, SPEC)

    [worker_id] = await pool.launch_for_demand(POLICY)
    async with db.context() as session:
        worker = await session.scalar(select(ExecutionWorker).where(ExecutionWorker.id == worker_id))
        assert worker.resource_class == "standard"
