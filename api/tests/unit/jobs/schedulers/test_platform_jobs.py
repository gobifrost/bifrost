from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.jobs.platform.application_publish import (
    APPLICATION_PUBLISH_DEFINITION,
    ApplicationPublishPayload,
)
from src.jobs.schedulers import platform_jobs as scheduler
from src.models.orm.platform_jobs import PlatformJob
from src.services.platform_jobs import enqueue_platform_job


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
                action_url="/apps/test/edit",
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
async def test_claim_assigns_fenced_lease(
    db_session: AsyncSession,
) -> None:
    job = await _queued_job(db_session)
    claim = await scheduler.claim_platform_job()
    assert claim is not None
    assert claim.id == job.id
    await db_session.refresh(job)
    assert job.status == "running"
    assert job.attempt == 1
    assert job.lease_token == claim.lease_token
    assert job.lease_owner
    assert job.lease_expires_at is not None

    assert await scheduler.claim_platform_job() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("job_count", [1, 2])
async def test_concurrent_replicas_claim_each_row_once(
    async_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    job_count: int,
) -> None:
    jobs = await _future_jobs(async_session_factory, job_count)

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
        scheduler.claim_platform_job(),
        scheduler.claim_platform_job(),
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
async def test_memory_admission_defers_without_claiming(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = await _queued_job(db_session)
    mib = 1024 * 1024
    monkeypatch.setattr(
        scheduler,
        "get_cgroup_memory",
        lambda: (900 * mib, 1024 * mib),
    )
    assert await scheduler.claim_platform_job() is None
    await db_session.refresh(job)
    assert job.status == "queued"
    assert job.phase == "Waiting for scheduler memory"
    assert job.lease_token is None


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

    claim = await scheduler.claim_platform_job()

    assert claim is not None
    assert claim.id == high.id


@pytest.mark.asyncio
async def test_resource_lock_serializes_matching_jobs(db_session: AsyncSession) -> None:
    first = await _queued_job(db_session)
    first.resource_lock_key = "solution:one"
    second_id = uuid4()
    second, _ = await enqueue_platform_job(
        db_session,
        APPLICATION_PUBLISH_DEFINITION,
        ApplicationPublishPayload(application_id=second_id),
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

    first_claim = await scheduler.claim_platform_job()
    second_claim = await scheduler.claim_platform_job()

    assert first_claim is not None
    assert first_claim.id in {first.id, second.id}
    assert second_claim is None


@pytest.mark.asyncio
async def test_blocked_jobs_do_not_starve_runnable_job_beyond_first_twenty(
    db_session: AsyncSession,
) -> None:
    blocker = await _queued_job(db_session)
    blocker.status = "running"
    blocker.resource_lock_key = "solution:blocked"

    for index in range(20):
        app_id = uuid4()
        await enqueue_platform_job(
            db_session,
            APPLICATION_PUBLISH_DEFINITION,
            ApplicationPublishPayload(application_id=app_id),
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
        APPLICATION_PUBLISH_DEFINITION,
        ApplicationPublishPayload(application_id=runnable_id),
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

    claim = await scheduler.claim_platform_job()

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
    monkeypatch.setattr(scheduler, "get_platform_job_definition", lambda _type: limited)

    assert await scheduler.claim_platform_job() is not None
    assert await scheduler.claim_platform_job() is None


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
    claim = await scheduler.claim_platform_job()
    assert claim is not None
    claim = scheduler.ClaimedPlatformJob(
        id=claim.id,
        lease_token=claim.lease_token,
        timeout_seconds=1,
        hard_memory_ratio=0.95,
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
    claim = await scheduler.claim_platform_job()
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
    assert job.error_retryable is True
    assert job.lease_token is None


@pytest.mark.asyncio
async def test_worker_loop_drains_jobs_before_waiting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shutdown = asyncio.Event()
    calls = 0

    async def process_once() -> tuple[int, int]:
        nonlocal calls
        calls += 1
        if calls == 3:
            shutdown.set()
            return 0, 0
        return 1, 0

    monkeypatch.setattr(scheduler, "process_platform_jobs", process_once)

    await scheduler.platform_job_worker_loop(shutdown, idle_seconds=0.001)

    assert calls == 3


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
    )

    task = asyncio.create_task(scheduler.run_claimed_platform_job(claim))
    await waiting.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        _ = await task
    terminate.assert_awaited_once()
    handle_loss.assert_awaited_once()
