"""Durable executor attribution is fenced to one PlatformJob attempt."""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncGenerator
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import delete

from src.jobs.schedulers import platform_jobs as scheduler
from src.models.orm.execution_workers import ExecutionWorker
from src.models.orm.platform_jobs import PlatformJob
from src.services import platform_jobs as service
from src.worker.remote.fencing import LeaseRef, remote_owner_context
from src.worker.remote.ownership import RemoteOwnerLease


def _job(*, job_type: str = "application.publish") -> PlatformJob:
    return PlatformJob(
        job_type=job_type,
        payload_version=1,
        payload={},
        requested_by_user_id=str(uuid4()),
        requested_by_email="dev@example.com",
        requested_by_name="Dev",
        title="Publishing App",
    )


def _worker(*, capability: str = "app.compile.v2") -> ExecutionWorker:
    return ExecutionWorker(
        id=uuid4(),
        form="external",
        state="ready",
        capacity_scope="cloudflare",
        capacity_resource_uid="provider-private-resource",
        supported_compute=[capability],
    )


@pytest.fixture
def platform_context(monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession):
    @asynccontextmanager
    async def context() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    monkeypatch.setattr(service, "get_db_context", context)
    monkeypatch.setattr(scheduler, "get_db_context", context)
    monkeypatch.setattr(service, "publish_platform_job_update", AsyncMock())
    monkeypatch.setattr(scheduler, "publish_platform_job_update", AsyncMock())
    monkeypatch.setattr(scheduler, "get_cgroup_memory", lambda: (0, 2**40))


async def _running_job(db_session: AsyncSession) -> tuple[PlatformJob, UUID]:
    job = _job(job_type="application.sdk_update")
    lease = uuid4()
    job.status = "running"
    job.attempt = 1
    job.lease_token = lease
    job.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=1)
    db_session.add(job)
    await db_session.commit()
    return job, lease


@pytest.mark.asyncio
async def test_executor_attribution_survives_terminal_result_and_hides_provider_uid(
    db_session: AsyncSession, platform_context
):
    job, lease = await _running_job(db_session)
    worker = _worker()
    db_session.add(worker)
    await db_session.commit()

    assert await service.record_platform_job_executor_attribution(
        job.id,
        lease,
        worker_id=worker.id,
        compute_capability="app.compile.v2",
        stage_id=uuid4(),
        input_sha256="a" * 64,
        phase="Compiling App",
    )
    await db_session.refresh(job)
    stage_id = job.executor_stage_id
    assert job.executor_attempt == 1
    assert job.executor_worker_id == worker.id
    assert job.executor_capacity_resource_uid == "provider-private-resource"

    assert await service.finish_platform_job(
        job.id, lease, status="succeeded", result={"deployment_id": str(uuid4())}
    )
    await db_session.refresh(job)
    assert job.executor_stage_id == stage_id
    public = service.platform_job_to_public(job)
    assert public.executor is not None
    assert public.executor.worker_id == worker.id
    assert public.executor.input_sha256 == "a" * 64
    assert "provider-private-resource" not in public.model_dump_json()


@pytest.mark.asyncio
async def test_stale_lease_or_remote_owner_cannot_record_attribution(
    db_session: AsyncSession, platform_context
):
    job, lease = await _running_job(db_session)
    worker = _worker()
    db_session.add(worker)
    await db_session.commit()

    assert not await service.record_platform_job_executor_attribution(
        job.id, uuid4(), worker_id=worker.id
    )

    owner = uuid4()
    launcher = uuid4()
    worker.remote_owner_id = owner
    worker.remote_launcher_id = launcher
    worker.remote_session_generation = 1
    worker.remote_owner_lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=1)
    await db_session.commit()
    stale = RemoteOwnerLease(worker.id, uuid4(), launcher, 1)
    with remote_owner_context(LeaseRef(stale)):
        assert not await service.record_platform_job_executor_attribution(
            job.id, lease, worker_id=worker.id
        )

    await db_session.refresh(job)
    assert job.executor_worker_id is None


@pytest.mark.asyncio
async def test_retry_claim_clears_old_stage_and_rebinds_worker(
    db_session: AsyncSession, platform_context
):
    old_worker = _worker(capability="app.bundle.v1")
    new_worker = _worker(capability="app.bundle.v1")
    job = _job(job_type="application.publish")
    job.attempt = 1
    job.executor_attempt = 1
    job.executor_worker_id = old_worker.id
    job.executor_stage_id = uuid4()
    job.executor_input_sha256 = "a" * 64
    job.executor_compute_capability = "app.bundle.v1"
    db_session.add_all((old_worker, new_worker, job))
    await db_session.commit()

    claim = await scheduler.claim_platform_job(
        fits_bytes=2**40,
        job_types=frozenset((job.job_type,)),
        executor_worker_id=new_worker.id,
    )

    assert claim is not None
    assert claim.id == job.id
    await db_session.refresh(job)
    assert job.attempt == 2
    assert job.executor_attempt == 2
    assert job.executor_worker_id == new_worker.id
    assert job.executor_stage_id is None
    assert job.executor_input_sha256 is None
    assert job.executor_compute_capability is None


@pytest.mark.asyncio
@pytest.mark.parametrize("replay", ["identical", "different_stage", "erase_stage"])
async def test_recorded_compute_identity_only_accepts_identical_replay(
    db_session: AsyncSession, platform_context, replay,
):
    job, lease = await _running_job(db_session)
    worker = _worker()
    db_session.add(worker)
    await db_session.commit()
    stage = uuid4()
    original: dict[str, Any] = dict(worker_id=worker.id, compute_capability="app.compile.v2", stage_id=stage, input_sha256="a" * 64)
    assert await service.record_platform_job_executor_attribution(job.id, lease, **original)
    if replay == "identical":
        assert await service.record_platform_job_executor_attribution(job.id, lease, **original)
    else:
        replacement = dict(original)
        if replay == "different_stage":
            replacement["stage_id"] = uuid4()
        else:
            replacement["stage_id"] = replacement["input_sha256"] = replacement["compute_capability"] = None
        with pytest.raises(service.PlatformJobExecutorAttributionConflict):
            await service.record_platform_job_executor_attribution(job.id, lease, **replacement)
    await db_session.refresh(job)
    assert job.executor_stage_id == stage
    assert job.executor_input_sha256 == "a" * 64


@pytest.mark.asyncio
async def test_file_compute_stage_advances_only_from_its_exact_predecessor(
    db_session: AsyncSession, platform_context,
):
    job, lease = await _running_job(db_session)
    worker, other_worker = _worker(), _worker()
    db_session.add_all((worker, other_worker))
    await db_session.commit()
    first_stage, second_stage = uuid4(), uuid4()
    first = dict(
        worker_id=worker.id,
        compute_capability="app.compile.v2",
        stage_id=first_stage,
        input_sha256="a" * 64,
    )
    assert await service.record_platform_job_executor_attribution(job.id, lease, **first)

    assert await service.record_platform_job_executor_attribution(
        job.id, lease, worker_id=worker.id, compute_capability="app.compile.v2",
        stage_id=second_stage, input_sha256="b" * 64,
        predecessor_stage_id=first_stage,
    )
    await db_session.refresh(job)
    assert job.executor_stage_id == second_stage
    assert job.executor_input_sha256 == "b" * 64

    with pytest.raises(service.PlatformJobExecutorAttributionConflict):
        await service.record_platform_job_executor_attribution(
            job.id, lease, worker_id=worker.id, compute_capability="app.compile.v2",
            stage_id=uuid4(), input_sha256="c" * 64,
            predecessor_stage_id=first_stage,
        )
    with pytest.raises(service.PlatformJobExecutorAttributionConflict):
        await service.record_platform_job_executor_attribution(
            job.id, lease, worker_id=other_worker.id, compute_capability="app.compile.v2",
            stage_id=uuid4(), input_sha256="d" * 64,
            predecessor_stage_id=second_stage,
        )


@pytest.mark.parametrize("next_capability,accepted", [
    ("audit.archive.v1", True), ("audit.export.v1", False),
])
async def test_next_stage_requires_a_code_declared_capability_even_on_a_capable_image(
    db_session: AsyncSession, platform_context, monkeypatch, next_capability, accepted,
):
    from dataclasses import replace
    from src.jobs.platform import catalog

    job, lease = await _running_job(db_session)
    metadata = catalog.get_platform_job_metadata(job.job_type)
    assert metadata is not None
    monkeypatch.setattr(catalog, "_METADATA", {
        **catalog._METADATA,
        metadata.job_type: replace(metadata, additional_compute_capabilities=("audit.archive.v1",)),
    })
    worker = _worker()
    worker.supported_compute = ["app.compile.v2", "audit.archive.v1", "audit.export.v1"]
    db_session.add(worker)
    await db_session.commit()
    first, following = uuid4(), uuid4()
    assert await service.record_platform_job_executor_attribution(
        job.id, lease, worker_id=worker.id, compute_capability="app.compile.v2",
        stage_id=first, input_sha256="a" * 64,
    )
    assert await service.record_platform_job_executor_attribution(
        job.id, lease, worker_id=worker.id, compute_capability=next_capability,
        stage_id=following, input_sha256="b" * 64, predecessor_stage_id=first,
    ) is accepted
    await db_session.refresh(job)
    assert job.executor_stage_id == (following if accepted else first)


@pytest.mark.parametrize("complete", [False, True])
async def test_solution_claim_requires_both_inspection_and_compilation(
    db_session: AsyncSession, platform_context, complete,
):
    worker = _worker(capability="solution.archive.inspect.v1")
    if complete:
        worker.supported_compute = ["solution.archive.inspect.v1", "app.compile.v2"]
    job = _job(job_type="solution.deploy")
    db_session.add_all((worker, job))
    await db_session.commit()
    try:
        claim = await scheduler.claim_platform_job(
            fits_bytes=2**40, job_types=frozenset({job.job_type}), executor_worker_id=worker.id,
        )
        assert (claim is not None) is complete
        await db_session.refresh(job)
        assert job.attempt == int(complete)
        assert job.executor_worker_id == (worker.id if complete else None)
        if claim is not None:
            assert claim.id == job.id
    finally:
        await db_session.execute(delete(PlatformJob).where(PlatformJob.id == job.id))
        await db_session.execute(delete(ExecutionWorker).where(ExecutionWorker.id == worker.id))
        await db_session.commit()
