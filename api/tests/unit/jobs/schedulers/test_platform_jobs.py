from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

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
async def test_expired_lease_requeues_then_fails_at_attempt_limit(
    db_session: AsyncSession,
) -> None:
    job = await _queued_job(db_session)
    job.status = "running"
    job.attempt = 1
    job.lease_token = uuid4()
    job.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await db_session.commit()

    recovered, failed = await scheduler.recover_expired_platform_jobs()
    assert (recovered, failed) == (1, 0)
    await db_session.refresh(job)
    assert job.status == "queued"
    assert job.lease_token is None

    job.status = "running"
    job.attempt = job.max_attempts
    job.lease_token = uuid4()
    job.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
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
