from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from shared.app_build_errors import APP_BUILD_JOB_TYPES, app_build_memory_message
from src.jobs.platform.application_deploy import (
    APPLICATION_DEPLOY_DEFINITION,
    ApplicationDeployPayload,
)
from src.jobs.platform.catalog import PlatformJobMetadata, get_platform_job_metadata
from src.jobs.platform.application_publish import (
    APPLICATION_PUBLISH_DEFINITION,
    ApplicationPublishPayload,
)
from src.jobs.platform.kubernetes_client import _lease_token_fingerprint
from src.jobs.schedulers import platform_jobs as scheduler
from src.models.orm.execution_workers import ExecutionWorker
from src.models.orm.platform_job_memory_profiles import PlatformJobMemoryProfile
from src.models.orm.platform_jobs import PlatformJob
from src.models.orm.solution_export_jobs import SolutionExportJob
from src.models.orm.solutions import Solution
from src.services.memory_requests import MIB, sized_job_limit_bytes
from src.services.platform_job_memory_profiles import MEMORY_PROFILE_SAFETY_MARGIN_BYTES
from src.services.platform_jobs import enqueue_platform_job
from src.services.worker_pool.sizing import JobSizing



# A free budget every test job fits.
AMPLE = 2**40
# Settings -> Kubernetes defaults: Worker memory 2 GiB, Largest worker 8 GiB.
SIZING = JobSizing(
    standard_limit_bytes=2048 * MIB,
    largest_limit_bytes=8192 * MIB,
    threshold=0.85,
    baseline_bytes=768 * MIB,
)
# The deployment's worker memory request, which a standard pod requests.
REQUEST = 512 * MIB


def _pods(max_pods: int = 2) -> scheduler.IsolatedPods:
    return scheduler.IsolatedPods(
        namespace="test", max_pods=max_pods, sizing=SIZING, standard_request_bytes=REQUEST
    )

async def _queued_job(db_session: AsyncSession) -> PlatformJob:
    await db_session.execute(delete(PlatformJob))
    await db_session.commit()
    app_id = uuid4()
    job, _ = await enqueue_platform_job(
        db_session,
        APPLICATION_PUBLISH_DEFINITION,
        ApplicationPublishPayload(application_id=app_id),
        dedupe_key=str(app_id),
        organization_id=None,
        requested_by_user_id=uuid4(),
        requested_by_email="dev@example.com",
        requested_by_name="Dev",
        resource_type="application",
        resource_id=str(app_id),
        title="Publishing Test",
        action_url="/apps/test/edit",
    )
    # Leave the row uncommitted so the live test scheduler cannot claim it
    # between creation and the scheduler function under test.
    return job


async def _queued_remote_build_job(
    db_session: AsyncSession,
    *,
    clear_existing: bool = True,
) -> PlatformJob:
    if clear_existing:
        await db_session.execute(delete(PlatformJob))
        await db_session.commit()
    app_id = uuid4()
    job, _ = await enqueue_platform_job(
        db_session,
        APPLICATION_DEPLOY_DEFINITION,
        ApplicationDeployPayload(
            application_id=app_id,
            deployment_id=uuid4(),
            input_sha256="a" * 64,
        ),
        dedupe_key=str(app_id),
        organization_id=None,
        requested_by_user_id=uuid4(),
        requested_by_email="dev@example.com",
        requested_by_name="Dev",
        resource_type="application",
        resource_id=str(app_id),
        title="Deploying Test",
        action_url=None,
    )
    job.execution_backend = "kubernetes"
    await db_session.flush()
    return job


def _unlimited_deploy() -> PlatformJobMetadata:
    """Deploy definition with the concurrency cap lifted.

    Production serializes deploys (max_concurrency=1). Tests exercising
    replica contention, lock fairness, and starvation need parallel
    same-type claims, so they lift the cap through the same lookup the
    scheduler uses instead of switching job types.
    """
    metadata = get_platform_job_metadata(APPLICATION_DEPLOY_DEFINITION.job_type)
    assert metadata is not None
    return replace(metadata, policy=replace(metadata.policy, max_concurrency=None))


def _lift_deploy_concurrency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_metadata = scheduler.get_platform_job_metadata
    monkeypatch.setattr(
        scheduler,
        "get_platform_job_metadata",
        lambda job_type: (
            _unlimited_deploy()
            if job_type == APPLICATION_DEPLOY_DEFINITION.job_type
            else real_metadata(job_type)
        ),
    )


async def _future_jobs(
    session_factory: async_sessionmaker[AsyncSession],
    count: int,
) -> list[PlatformJob]:
    available_at = datetime.now(timezone.utc) + timedelta(days=1)
    async with session_factory() as session:
        await session.execute(delete(PlatformJob))
        jobs = []
        for index in range(count):
            app_id = uuid4()
            job, _ = await enqueue_platform_job(
                session,
                APPLICATION_DEPLOY_DEFINITION,
                ApplicationDeployPayload(
                    application_id=app_id,
                    deployment_id=uuid4(),
                    input_sha256="a" * 64,
                ),
                dedupe_key=str(app_id),
                organization_id=None,
                requested_by_user_id=uuid4(),
                requested_by_email="dev@example.com",
                requested_by_name="Dev",
                resource_type="application",
                resource_id=str(app_id),
                title=f"Deploying Test {index}",
                action_url=None,
            )
            job.available_at = available_at
            jobs.append(job)
        await session.commit()
        return jobs


@pytest.fixture(autouse=True)
def patch_context(
    monkeypatch: pytest.MonkeyPatch,
    db_session: AsyncSession,
) -> None:
    @asynccontextmanager
    async def test_context() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    monkeypatch.setattr(scheduler, "get_db_context", test_context)
    monkeypatch.setattr(
        scheduler,
        "publish_platform_job_update",
        AsyncMock(),
    )
    monkeypatch.setattr(scheduler, "get_cgroup_memory", lambda: (-1, -1))


@pytest.mark.asyncio
async def test_default_claim_ignores_kubernetes_backend_jobs(
    db_session: AsyncSession,
) -> None:
    job = await _queued_remote_build_job(db_session)

    assert await scheduler.claim_platform_job(fits_bytes=AMPLE) is None
    await db_session.refresh(job)
    assert job.status == "queued"
    assert job.lease_token is None


@pytest.mark.asyncio
async def test_remote_claim_sets_kubernetes_launch_metadata(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = await _queued_remote_build_job(db_session)
    job.memory_required_bytes = 512 * 1024 * 1024
    monkeypatch.setattr(
        scheduler,
        "get_cgroup_memory",
        lambda: (900 * 1024 * 1024, 1024 * 1024 * 1024),
    )

    claim = await scheduler.claim_platform_job(backend="kubernetes", isolated=_pods())

    assert claim is not None
    assert claim.id == job.id
    assert claim.job_type == "application.deploy"
    await db_session.refresh(job)
    assert job.status == "running"
    assert job.phase == "Waiting for Kubernetes capacity"
    assert job.kubernetes_job_name == (
        f"bifrost-job-{job.id.hex}-{_lease_token_fingerprint(claim.lease_token)[:12]}"
    )
    assert claim.lease_token.hex not in (job.kubernetes_job_name or "")
    assert job.kubernetes_launch_started_at is not None
    assert job.kubernetes_namespace == "test"
    assert job.kubernetes_job_uid is None
    assert job.kubernetes_pod_uid is None
    assert job.runner_started_at is None
    # A standard pod: Worker memory, requesting the deployment's request.
    assert job.memory_limit_bytes == SIZING.standard_limit_bytes
    assert job.memory_request_bytes == REQUEST


@pytest.mark.asyncio
async def test_remote_claim_respects_global_remote_limit(
    db_session: AsyncSession,
) -> None:
    first = await _queued_remote_build_job(db_session)
    second = await _queued_remote_build_job(db_session, clear_existing=False)

    first_claim = await scheduler.claim_platform_job(backend="kubernetes", isolated=_pods(1))
    second_claim = await scheduler.claim_platform_job(backend="kubernetes", isolated=_pods(1))

    assert first_claim is not None
    assert first_claim.id in {first.id, second.id}
    assert second_claim is None
    await db_session.refresh(first)
    await db_session.refresh(second)
    running = [job for job in (first, second) if job.status == "running"]
    queued = [job for job in (first, second) if job.status == "queued"]
    assert len(running) == 1
    assert len(queued) == 1
    assert running[0].kubernetes_job_name is not None
    assert running[0].kubernetes_namespace == "test"
    assert queued[0].kubernetes_job_name is None
    assert queued[0].kubernetes_namespace is None


@pytest.mark.asyncio
async def test_type_concurrency_limit_counts_opposite_backend_jobs(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = await _queued_remote_build_job(db_session)
    active.status = "running"
    active.lease_token = uuid4()
    active.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=1)
    queued = await _queued_remote_build_job(db_session, clear_existing=False)
    queued.execution_backend = "local"
    limited = replace(
        APPLICATION_DEPLOY_DEFINITION,
        policy=replace(APPLICATION_DEPLOY_DEFINITION.policy, max_concurrency=1),
    )
    monkeypatch.setattr(scheduler, "get_platform_job_metadata", lambda _type: limited)

    assert await scheduler.claim_platform_job(backend="local", fits_bytes=AMPLE) is None
    await db_session.refresh(queued)
    assert queued.status == "queued"
    assert queued.lease_token is None


@pytest.mark.asyncio
async def test_resource_lock_counts_opposite_backend_jobs(
    db_session: AsyncSession,
) -> None:
    active = await _queued_remote_build_job(db_session)
    active.status = "running"
    active.resource_lock_key = "application:shared"
    active.lease_token = uuid4()
    active.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=1)
    queued = await _queued_remote_build_job(db_session, clear_existing=False)
    queued.execution_backend = "local"
    queued.resource_lock_key = "application:shared"

    assert await scheduler.claim_platform_job(backend="local", fits_bytes=AMPLE) is None
    await db_session.refresh(queued)
    assert queued.status == "queued"
    assert queued.lease_token is None


@pytest.mark.asyncio
async def test_a_job_a_standard_pod_cannot_hold_gets_a_sized_pod(
    db_session: AsyncSession,
) -> None:
    """The worker sizing rule: the smallest limit whose budget holds it."""
    job = await _queued_remote_build_job(db_session)
    job.memory_required_bytes = 1536 * MIB

    claim = await scheduler.claim_platform_job(backend="kubernetes", isolated=_pods())

    assert claim is not None
    await db_session.refresh(job)
    limit = sized_job_limit_bytes(
        1536 * MIB, SIZING.threshold, SIZING.baseline_bytes, SIZING.largest_limit_bytes
    )
    assert job.memory_limit_bytes == limit
    # It requests its whole limit, decided now, not when the Job is created.
    assert job.memory_request_bytes == limit
    assert limit > SIZING.standard_limit_bytes
    assert SIZING.budget_bytes(limit) >= 1536 * MIB


@pytest.mark.asyncio
async def test_a_job_no_pod_can_hold_is_never_launched(
    db_session: AsyncSession,
) -> None:
    """It stays queued, unchanged, for the unfit sweep to fail visibly."""
    job = await _queued_remote_build_job(db_session)
    job.memory_required_bytes = SIZING.largest_budget_bytes + 1

    assert await scheduler.claim_platform_job(backend="kubernetes", isolated=_pods()) is None
    await db_session.refresh(job)
    assert (job.status, job.phase, job.lease_token) == ("queued", "Queued", None)


@pytest.mark.asyncio
async def test_an_isolated_claim_and_only_one_needs_its_pods() -> None:
    with pytest.raises(ValueError):
        await scheduler.claim_platform_job(backend="kubernetes")
    with pytest.raises(ValueError):
        await scheduler.claim_platform_job(fits_bytes=AMPLE, isolated=_pods())


@pytest.mark.asyncio
async def test_an_oom_killed_pod_teaches_its_job_a_larger_pod(
    db_session: AsyncSession,
) -> None:
    """The retry and the workload's profile ask for at least the pod's limit
    plus the margin, so the next pod is sized larger."""
    profile_key = f"application.deploy.memory.v1:{uuid4()}"
    job = await _queued_remote_build_job(db_session)
    job.memory_profile_key = profile_key
    # Retried after losing its runner, as Solution jobs are.
    job.retry_on_runner_loss = True
    job.max_attempts = 2
    claim = await scheduler.claim_platform_job(backend="kubernetes", isolated=_pods())
    assert claim is not None
    await db_session.refresh(job)
    limit = SIZING.standard_limit_bytes
    assert job.memory_limit_bytes == limit

    retried = await scheduler._handle_runner_loss(
        claim.id,
        claim.lease_token,
        error_code="memory_pressure",
        error_message="The build pod exceeded its memory limit.",
        out_of_memory=True,
    )

    assert retried is True
    await db_session.refresh(job)
    profile = await db_session.get(PlatformJobMemoryProfile, profile_key)
    assert job.memory_required_bytes >= limit + MEMORY_PROFILE_SAFETY_MARGIN_BYTES
    assert profile is not None
    assert profile.memory_required_bytes >= limit + MEMORY_PROFILE_SAFETY_MARGIN_BYTES
    pod = SIZING.pod_limit_bytes(job.memory_required_bytes)
    assert pod is not None and pod > limit


@pytest.mark.asyncio
async def test_expired_remote_owner_cannot_claim_a_queued_platform_job(db_session: AsyncSession) -> None:
    from src.worker.remote.fencing import LeaseRef, remote_owner_context
    from src.worker.remote.ownership import RemoteOwnerLease

    job = await _queued_remote_build_job(db_session)
    job.execution_backend = "local"
    await db_session.flush()
    with remote_owner_context(LeaseRef(RemoteOwnerLease(uuid4(), uuid4(), uuid4(), 1))):
        assert await scheduler.claim_platform_job(fits_bytes=AMPLE) is None
    await db_session.refresh(job)
    assert job.status == "queued"
    assert job.lease_token is None


@pytest.mark.asyncio
async def test_stale_remote_owner_cannot_requeue_a_live_platform_job(
    db_session: AsyncSession,
) -> None:
    from src.worker.remote.fencing import LeaseRef, remote_owner_context
    from src.worker.remote.ownership import RemoteOwnerLease

    job = await _queued_job(db_session)
    claim = await scheduler.claim_platform_job(fits_bytes=AMPLE)
    assert claim is not None
    worker_id, current_owner, stale_owner, launcher = uuid4(), uuid4(), uuid4(), uuid4()
    db_session.add(
        ExecutionWorker(
            id=worker_id,
            form="static",
            state="ready",
            resource_class="standard",
            slots=0,
            supported_handlers=["workflow.execute"],
            remote_owner_id=current_owner,
            remote_launcher_id=launcher,
            remote_session_generation=2,
            remote_owner_lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
        )
    )
    await db_session.commit()
    original_expiry = job.lease_expires_at
    stale = LeaseRef(
        RemoteOwnerLease(
            worker_id=worker_id,
            owner_id=stale_owner,
            launcher_id=launcher,
            session_generation=1,
        )
    )

    with remote_owner_context(stale):
        assert await scheduler._heartbeat(claim.id, claim.lease_token) is None
        requeued = await scheduler._handle_runner_loss(
            claim.id,
            claim.lease_token,
            error_code="runner_lost",
            error_message="stale owner must not requeue this job",
        )

    assert not requeued
    await db_session.refresh(job)
    assert job.status == "running"
    assert job.lease_token == claim.lease_token
    assert job.lease_expires_at == original_expiry


@pytest.mark.asyncio
async def test_remote_claim_does_not_reclaim_pending_kubernetes_job(
    db_session: AsyncSession,
) -> None:
    job = await _queued_remote_build_job(db_session)
    job.kubernetes_job_name = "bifrost-job-existing"

    assert await scheduler.claim_platform_job(backend="kubernetes", isolated=_pods()) is None
    await db_session.refresh(job)
    assert job.status == "queued"
    assert job.lease_token is None


@pytest.mark.asyncio
async def test_expired_recovery_ignores_kubernetes_backend_jobs(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wall_clock_now = datetime.now(timezone.utc)
    monkeypatch.setattr(
        scheduler,
        "_now",
        lambda: wall_clock_now + timedelta(days=2),
    )
    job = await _queued_remote_build_job(db_session)
    job.status = "running"
    job.attempt = 1
    job.lease_token = uuid4()
    job.lease_expires_at = wall_clock_now + timedelta(days=1)
    await db_session.commit()

    recovered, failed = await scheduler.recover_expired_platform_jobs()

    assert (recovered, failed) == (0, 0)
    await db_session.refresh(job)
    assert job.status == "running"
    assert job.lease_token is not None


@pytest.mark.asyncio
async def test_expired_recovery_keeps_a_local_job_with_a_live_lease(
    db_session: AsyncSession,
) -> None:
    job = await _queued_job(db_session)
    now = datetime.now(timezone.utc)
    job.status = "running"
    job.attempt = 1
    job.lease_owner = "test-scheduler"
    job.lease_token = uuid4()
    job.heartbeat_at = now
    job.lease_expires_at = now + scheduler.LEASE_DURATION
    await db_session.commit()

    try:
        recovered, failed = await scheduler.recover_expired_platform_jobs()

        assert (recovered, failed) == (0, 0)
        await db_session.refresh(job)
        assert job.status == "running"
        assert job.lease_token is not None
    finally:
        await db_session.execute(delete(PlatformJob).where(PlatformJob.id == job.id))
        await db_session.commit()


@pytest.mark.asyncio
async def test_claim_assigns_fenced_lease(
    db_session: AsyncSession,
) -> None:
    job = await _queued_job(db_session)
    claim = await scheduler.claim_platform_job(fits_bytes=AMPLE)
    assert claim is not None
    assert claim.id == job.id
    await db_session.refresh(job)
    assert job.status == "running"
    assert job.attempt == 1
    assert job.lease_token == claim.lease_token
    assert job.lease_owner
    assert job.lease_expires_at is not None

    assert await scheduler.claim_platform_job(fits_bytes=AMPLE) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("job_count", [1, 2])
async def test_concurrent_replicas_claim_each_row_once(
    async_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    job_count: int,
) -> None:
    jobs = await _future_jobs(async_session_factory, job_count)
    _lift_deploy_concurrency(monkeypatch)

    @asynccontextmanager
    async def independent_context() -> AsyncGenerator[AsyncSession, None]:
        async with async_session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    monkeypatch.setattr(scheduler, "get_db_context", independent_context)
    monkeypatch.setattr(
        scheduler,
        "_now",
        lambda: datetime.now(timezone.utc) + timedelta(days=2),
    )

    claims = await asyncio.gather(
        scheduler.claim_platform_job(fits_bytes=AMPLE),
        scheduler.claim_platform_job(fits_bytes=AMPLE),
    )
    claimed = [claim for claim in claims if claim is not None]

    assert len(claimed) == job_count
    assert len({claim.id for claim in claimed}) == job_count
    assert {claim.id for claim in claimed} == {job.id for job in jobs}


@pytest.mark.asyncio
async def test_expired_lease_requeues_then_fails_at_attempt_limit(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Keep the lease valid to the live scheduler container while advancing only
    # this test process beyond it. Otherwise the live scheduler can recover the
    # deliberately expired row before the function under test sees it.
    wall_clock_now = datetime.now(timezone.utc)
    lease_deadline = wall_clock_now + timedelta(days=1)
    monkeypatch.setattr(
        scheduler,
        "_now",
        lambda: wall_clock_now + timedelta(days=2),
    )

    job = await _queued_job(db_session)
    job.status = "running"
    job.attempt = 1
    job.lease_token = uuid4()
    job.lease_expires_at = lease_deadline
    await db_session.commit()

    recovered, failed = await scheduler.recover_expired_platform_jobs()
    assert (recovered, failed) == (1, 0)
    await db_session.refresh(job)
    assert job.status == "queued"
    assert job.lease_token is None

    job.status = "running"
    job.attempt = job.max_attempts
    job.lease_token = uuid4()
    job.lease_expires_at = lease_deadline
    await db_session.commit()
    recovered, failed = await scheduler.recover_expired_platform_jobs()
    assert (recovered, failed) == (1, 1)
    await db_session.refresh(job)
    assert job.status == "failed"
    assert job.error_code == "runner_lost"


@pytest.mark.asyncio
async def test_terminal_runner_loss_fails_linked_solution_export_and_clears_options(
    db_session: AsyncSession,
) -> None:
    solution = Solution(
        id=uuid4(),
        slug=f"export-loss-{uuid4().hex[:8]}",
        name="Export loss",
        version="1.0.0",
        organization_id=None,
    )
    export = SolutionExportJob(
        id=uuid4(),
        solution_id=solution.id,
        status="running",
        progress_percent=5,
        message="Building backup",
        encrypted_options="opaque-encrypted-options",
    )
    lease_token = uuid4()
    platform = PlatformJob(
        id=uuid4(),
        job_type="solution.export",
        payload_version=1,
        payload={"protected": True},
        requested_by_user_id=str(uuid4()),
        requested_by_email="dev@example.com",
        requested_by_name="Dev",
        resource_type="solution_export",
        resource_id=str(export.id),
        title="Exporting Test",
        status="running",
        attempt=2,
        max_attempts=2,
        lease_token=lease_token,
    )
    db_session.add_all((solution, export, platform))
    await db_session.commit()

    requeued = await scheduler._handle_runner_loss(
        platform.id,
        lease_token,
        error_code="runner_lost",
        error_message="The platform-job runner stopped before the operation completed.",
    )

    assert not requeued
    await db_session.refresh(platform)
    await db_session.refresh(export)
    assert platform.status == "failed"
    assert export.status == "failed"
    assert export.progress_percent == 100
    assert export.message == "Backup failed"
    assert export.failure_message == "Backup export runner stopped before completion"
    assert export.encrypted_options is None
    assert export.completed_at is not None


@pytest.mark.asyncio
async def test_retrying_runner_loss_keeps_linked_solution_export_active(
    db_session: AsyncSession,
) -> None:
    solution = Solution(
        id=uuid4(),
        slug=f"export-retry-{uuid4().hex[:8]}",
        name="Export retry",
        version="1.0.0",
        organization_id=None,
    )
    export = SolutionExportJob(
        id=uuid4(),
        solution_id=solution.id,
        status="running",
        progress_percent=5,
        message="Building backup",
        encrypted_options="opaque-encrypted-options",
    )
    lease_token = uuid4()
    platform = PlatformJob(
        id=uuid4(),
        job_type="solution.export",
        payload_version=1,
        payload={"protected": True},
        requested_by_user_id=str(uuid4()),
        requested_by_email="dev@example.com",
        requested_by_name="Dev",
        resource_type="solution_export",
        resource_id=str(export.id),
        title="Exporting Test",
        status="running",
        attempt=1,
        max_attempts=2,
        lease_token=lease_token,
    )
    db_session.add_all((solution, export, platform))
    await db_session.commit()

    requeued = await scheduler._handle_runner_loss(
        platform.id,
        lease_token,
        error_code="runner_lost",
        error_message="The platform-job runner stopped before the operation completed.",
    )

    assert requeued
    await db_session.refresh(platform)
    await db_session.refresh(export)
    assert platform.status == "queued"
    assert export.status == "running"
    assert export.encrypted_options == "opaque-encrypted-options"
    assert export.completed_at is None


@pytest.mark.asyncio
async def test_final_expired_lease_fails_linked_solution_export_and_clears_options(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wall_clock_now = datetime.now(timezone.utc)
    monkeypatch.setattr(
        scheduler,
        "_now",
        lambda: wall_clock_now + timedelta(days=2),
    )
    solution = Solution(
        id=uuid4(),
        slug=f"export-expired-{uuid4().hex[:8]}",
        name="Expired export",
        version="1.0.0",
        organization_id=None,
    )
    export = SolutionExportJob(
        id=uuid4(),
        solution_id=solution.id,
        status="running",
        progress_percent=5,
        message="Building backup",
        encrypted_options="opaque-encrypted-options",
    )
    platform = PlatformJob(
        id=uuid4(),
        job_type="solution.export",
        payload_version=1,
        payload={"protected": True},
        requested_by_user_id=str(uuid4()),
        requested_by_email="dev@example.com",
        requested_by_name="Dev",
        resource_type="solution_export",
        resource_id=str(export.id),
        title="Exporting Test",
        status="running",
        attempt=2,
        max_attempts=2,
        lease_token=uuid4(),
        lease_expires_at=wall_clock_now + timedelta(days=1),
    )
    db_session.add_all((solution, export, platform))
    await db_session.commit()

    recovered, failed = await scheduler.recover_expired_platform_jobs()

    # The shared scheduler fixture can hold other deliberately expired rows;
    # assert this call's terminal export projection rather than global volume.
    assert recovered >= 1
    assert failed >= 1
    await db_session.refresh(export)
    assert export.status == "failed"
    assert export.progress_percent == 100
    assert export.encrypted_options is None
    assert export.completed_at is not None


@pytest.mark.asyncio
async def test_a_job_larger_than_the_free_budget_waits_for_executor_memory(
    db_session: AsyncSession,
) -> None:
    job = await _queued_job(db_session)
    mib = 1024 * 1024
    job.memory_required_bytes = 512 * mib

    assert await scheduler.claim_platform_job(fits_bytes=256 * mib) is None
    await db_session.refresh(job)
    assert job.status == "queued"
    assert job.phase == "Waiting for executor memory"
    assert job.lease_token is None


@pytest.mark.asyncio
async def test_a_job_that_fits_the_free_budget_is_claimed(
    db_session: AsyncSession,
) -> None:
    job = await _queued_job(db_session)
    mib = 1024 * 1024
    job.memory_required_bytes = 128 * mib

    claim = await scheduler.claim_platform_job(fits_bytes=128 * mib)

    assert claim is not None
    assert claim.id == job.id
    assert claim.memory_required_bytes == 128 * mib


@pytest.mark.asyncio
async def test_a_local_claim_needs_the_free_budget() -> None:
    with pytest.raises(ValueError):
        await scheduler.claim_platform_job(backend="local")


@pytest.mark.asyncio
async def test_claims_highest_priority_first(db_session: AsyncSession) -> None:
    low = await _queued_job(db_session)
    low.priority = 10
    high_id = uuid4()
    high, _ = await enqueue_platform_job(
        db_session,
        APPLICATION_PUBLISH_DEFINITION,
        ApplicationPublishPayload(application_id=high_id),
        dedupe_key=str(high_id),
        priority=1000,
        organization_id=None,
        requested_by_user_id=uuid4(),
        requested_by_email="dev@example.com",
        requested_by_name="Dev",
        resource_type="application",
        resource_id=str(high_id),
        title="High priority",
        action_url=None,
    )

    claim = await scheduler.claim_platform_job(fits_bytes=AMPLE)

    assert claim is not None
    assert claim.id == high.id


@pytest.mark.asyncio
async def test_resource_lock_serializes_matching_jobs(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _lift_deploy_concurrency(monkeypatch)
    first = await _queued_job(db_session)
    first.resource_lock_key = "solution:one"
    second_id = uuid4()
    second, _ = await enqueue_platform_job(
        db_session,
        APPLICATION_DEPLOY_DEFINITION,
        ApplicationDeployPayload(
            application_id=second_id,
            deployment_id=uuid4(),
            input_sha256="a" * 64,
        ),
        dedupe_key=str(second_id),
        resource_lock_key="solution:one",
        organization_id=None,
        requested_by_user_id=uuid4(),
        requested_by_email="dev@example.com",
        requested_by_name="Dev",
        resource_type="application",
        resource_id=str(second_id),
        title="Second",
        action_url=None,
    )

    first_claim = await scheduler.claim_platform_job(fits_bytes=AMPLE)
    second_claim = await scheduler.claim_platform_job(fits_bytes=AMPLE)

    assert first_claim is not None
    assert first_claim.id in {first.id, second.id}
    assert second_claim is None


@pytest.mark.asyncio
async def test_blocked_jobs_do_not_starve_runnable_job_beyond_first_twenty(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _lift_deploy_concurrency(monkeypatch)
    blocker = await _queued_job(db_session)
    blocker.status = "running"
    blocker.resource_lock_key = "solution:blocked"

    for index in range(20):
        app_id = uuid4()
        await enqueue_platform_job(
            db_session,
            APPLICATION_DEPLOY_DEFINITION,
            ApplicationDeployPayload(
                application_id=app_id,
                deployment_id=uuid4(),
                input_sha256="a" * 64,
            ),
            dedupe_key=str(app_id),
            resource_lock_key="solution:blocked",
            organization_id=None,
            requested_by_user_id=uuid4(),
            requested_by_email="dev@example.com",
            requested_by_name="Dev",
            resource_type="application",
            resource_id=str(app_id),
            title=f"Blocked {index}",
            action_url=None,
        )

    runnable_id = uuid4()
    runnable, _ = await enqueue_platform_job(
        db_session,
        APPLICATION_DEPLOY_DEFINITION,
        ApplicationDeployPayload(
            application_id=runnable_id,
            deployment_id=uuid4(),
            input_sha256="a" * 64,
        ),
        dedupe_key=str(runnable_id),
        resource_lock_key="solution:runnable",
        organization_id=None,
        requested_by_user_id=uuid4(),
        requested_by_email="dev@example.com",
        requested_by_name="Dev",
        resource_type="application",
        resource_id=str(runnable_id),
        title="Runnable after blocked queue",
        action_url=None,
    )

    claim = await scheduler.claim_platform_job(fits_bytes=AMPLE)

    assert claim is not None
    assert claim.id == runnable.id


@pytest.mark.asyncio
async def test_type_concurrency_limit_is_enforced(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _queued_job(db_session)
    second_id = uuid4()
    await enqueue_platform_job(
        db_session,
        APPLICATION_PUBLISH_DEFINITION,
        ApplicationPublishPayload(application_id=second_id),
        dedupe_key=str(second_id),
        organization_id=None,
        requested_by_user_id=uuid4(),
        requested_by_email="dev@example.com",
        requested_by_name="Dev",
        resource_type="application",
        resource_id=str(second_id),
        title="Second",
        action_url=None,
    )
    limited = replace(
        APPLICATION_PUBLISH_DEFINITION,
        policy=replace(APPLICATION_PUBLISH_DEFINITION.policy, max_concurrency=1),
    )
    monkeypatch.setattr(scheduler, "get_platform_job_metadata", lambda _type: limited)

    assert await scheduler.claim_platform_job(fits_bytes=AMPLE) is not None
    assert await scheduler.claim_platform_job(fits_bytes=AMPLE) is None


@pytest.mark.asyncio
async def test_publish_jobs_serialize_at_concurrency_one_by_default(
    db_session: AsyncSession,
) -> None:
    """Candidate heavy jobs serialize unless a policy says otherwise."""
    await db_session.execute(delete(PlatformJob))
    for index in range(2):
        app_id = uuid4()
        await enqueue_platform_job(
            db_session,
            APPLICATION_PUBLISH_DEFINITION,
            ApplicationPublishPayload(application_id=app_id),
            dedupe_key=str(app_id),
            organization_id=None,
            requested_by_user_id=uuid4(),
            requested_by_email="dev@example.com",
            requested_by_name="Dev",
            resource_type="application",
            resource_id=str(app_id),
            title=f"Publishing Test {index}",
            action_url=None,
        )

    assert await scheduler.claim_platform_job(fits_bytes=AMPLE) is not None
    assert await scheduler.claim_platform_job(fits_bytes=AMPLE) is None


@pytest.mark.asyncio
async def test_protected_payload_is_not_stored_in_plaintext(
    db_session: AsyncSession,
) -> None:
    await db_session.execute(delete(PlatformJob))
    protected = replace(APPLICATION_PUBLISH_DEFINITION, encrypt_payload=True)
    app_id = uuid4()

    job, _ = await enqueue_platform_job(
        db_session,
        protected,
        ApplicationPublishPayload(application_id=app_id),
        dedupe_key=str(app_id),
        organization_id=None,
        requested_by_user_id=uuid4(),
        requested_by_email="dev@example.com",
        requested_by_name="Dev",
        resource_type="application",
        resource_id=str(app_id),
        title="Protected",
        action_url=None,
    )

    assert job.payload == {"protected": True}
    assert job.encrypted_payload is not None
    assert str(app_id) not in job.encrypted_payload


@pytest.mark.asyncio
async def test_timeout_stops_child_and_requeues_fenced_attempt(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        scheduler,
        "RUNNER_RETRY_DELAY",
        timedelta(hours=1),
    )
    job = await _queued_job(db_session)
    claim = await scheduler.claim_platform_job(fits_bytes=AMPLE)
    assert claim is not None
    claim = scheduler.ClaimedPlatformJob(
        id=claim.id,
        lease_token=claim.lease_token,
        timeout_seconds=1,
        hard_memory_ratio=0.95,
        memory_required_bytes=0,
    )

    class NeverExits:
        pid = 999999
        returncode = None

        async def wait(self):
            await asyncio.Future()

    process = NeverExits()
    monkeypatch.setattr(
        scheduler.asyncio,
        "create_subprocess_exec",
        AsyncMock(return_value=process),
    )
    monkeypatch.setattr(scheduler, "HEARTBEAT_INTERVAL_SECONDS", 0.001)
    monkeypatch.setattr(scheduler, "_terminate_runner", AsyncMock())
    monkeypatch.setattr(
        scheduler,
        "_monotonic",
        MagicMock(side_effect=[0, 2]),
    )

    assert not await scheduler.run_claimed_platform_job(claim)
    await db_session.refresh(job)
    assert job.status == "queued"
    assert job.error_code == "timeout"
    assert job.error_retryable is True
    assert job.lease_token is None


@pytest.mark.asyncio
async def test_hard_memory_pressure_stops_child_and_requeues_attempt(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        scheduler,
        "RUNNER_RETRY_DELAY",
        timedelta(hours=1),
    )
    job = await _queued_job(db_session)
    claim = await scheduler.claim_platform_job(fits_bytes=AMPLE)
    assert claim is not None

    class NeverExits:
        pid = 999999
        returncode = None

        async def wait(self):
            await asyncio.Future()

    monkeypatch.setattr(
        scheduler.asyncio,
        "create_subprocess_exec",
        AsyncMock(return_value=NeverExits()),
    )
    monkeypatch.setattr(scheduler, "HEARTBEAT_INTERVAL_SECONDS", 0.001)
    monkeypatch.setattr(scheduler, "_terminate_runner", AsyncMock())
    monkeypatch.setattr(scheduler, "_monotonic", MagicMock(side_effect=[0, 0]))
    monkeypatch.setattr(
        scheduler,
        "get_cgroup_memory",
        lambda: (97, 100),
    )

    assert not await scheduler.run_claimed_platform_job(claim)
    await db_session.refresh(job)
    assert job.status == "queued"
    assert job.error_code == "memory_pressure"
    assert job.error_message == app_build_memory_message("pressure")
    assert job.error_retryable is True
    assert job.lease_token is None


@pytest.mark.parametrize("job_type", sorted(APP_BUILD_JOB_TYPES))
@pytest.mark.parametrize("out_of_memory", [False, True])
async def test_build_memory_failure_keeps_actionable_durable_error(
    db_session: AsyncSession, job_type: str, out_of_memory: bool,
) -> None:
    job = await _queued_job(db_session)
    claim = await scheduler.claim_platform_job(fits_bytes=AMPLE)
    assert claim is not None
    job.job_type = job_type
    job.attempt = job.max_attempts
    await db_session.flush()

    await scheduler._handle_runner_loss(
        job.id, claim.lease_token, error_code="memory_pressure",
        error_message="The build pod exceeded its memory limit.",
        out_of_memory=out_of_memory,
    )

    await db_session.refresh(job)
    assert job.status == "failed"
    assert job.error_message == app_build_memory_message("oom" if out_of_memory else "pressure")


@pytest.mark.parametrize("returncode", [-9, 137, 1])
async def test_unreported_build_runner_exit_does_not_claim_confirmed_oom(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, returncode: int,
) -> None:
    job = await _queued_job(db_session)
    claim = await scheduler.claim_platform_job(fits_bytes=AMPLE)
    assert claim is not None
    job.attempt = job.max_attempts
    await db_session.flush()
    process = SimpleNamespace(returncode=returncode)
    monkeypatch.setattr(scheduler.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))

    assert not await scheduler.run_claimed_platform_job(claim)
    await db_session.refresh(job)
    assert job.error_code == "runner_exited"
    if returncode in (-9, 137):
        assert job.error_message == app_build_memory_message("killed")
    else:
        assert "OOM" not in job.error_message


@pytest.mark.asyncio
async def test_cancelling_worker_stops_active_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    waiting = asyncio.Event()

    class NeverExits:
        pid = 999999
        returncode = None

        async def wait(self):
            waiting.set()
            await asyncio.Future()

    monkeypatch.setattr(
        scheduler.asyncio,
        "create_subprocess_exec",
        AsyncMock(return_value=NeverExits()),
    )
    terminate = AsyncMock()
    monkeypatch.setattr(scheduler, "_terminate_runner", terminate)
    handle_loss = AsyncMock()
    monkeypatch.setattr(scheduler, "_handle_runner_loss", handle_loss)
    claim = scheduler.ClaimedPlatformJob(
        id=uuid4(),
        lease_token=uuid4(),
        timeout_seconds=60,
        hard_memory_ratio=0.95,
        memory_required_bytes=0,
    )

    task = asyncio.create_task(scheduler.run_claimed_platform_job(claim))
    await waiting.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        _ = await task
    terminate.assert_awaited_once()
    handle_loss.assert_awaited_once()


@pytest.mark.asyncio
async def test_settings_concurrency_override_replaces_policy_default(
    db_session: AsyncSession,
) -> None:
    from src.jobs.platform.application_deploy import (
        ApplicationDeployPayload,
    )
    from src.services.kubernetes_execution import KubernetesExecutionService

    async def _enqueue_deploy() -> None:
        app_id = uuid4()
        await enqueue_platform_job(
            db_session,
            APPLICATION_DEPLOY_DEFINITION,
            ApplicationDeployPayload(
                application_id=app_id,
                deployment_id=uuid4(),
                input_sha256="a" * 64,
            ),
            dedupe_key=str(app_id),
            organization_id=None,
            requested_by_user_id=uuid4(),
            requested_by_email="dev@example.com",
            requested_by_name="Dev",
            resource_type="application",
            resource_id=str(app_id),
            title="Deploying Test",
            action_url=None,
        )

    await db_session.execute(delete(PlatformJob))
    await _enqueue_deploy()
    await _enqueue_deploy()

    service = KubernetesExecutionService(db_session)
    await service.set_job_type_concurrency(
        "application.deploy", 2, updated_by="test"
    )

    assert await scheduler.claim_platform_job(fits_bytes=AMPLE) is not None
    assert await scheduler.claim_platform_job(fits_bytes=AMPLE) is not None

    await db_session.execute(delete(PlatformJob))
    await _enqueue_deploy()
    await _enqueue_deploy()
    await service.set_job_type_concurrency(
        "application.deploy", 1, updated_by="test"
    )

    assert await scheduler.claim_platform_job(fits_bytes=AMPLE) is not None
    assert await scheduler.claim_platform_job(fits_bytes=AMPLE) is None

    # Restore the code default so later suites see a pristine state.
    await service.set_job_type_concurrency(
        "application.deploy", None, updated_by="test"
    )
    await db_session.commit()
