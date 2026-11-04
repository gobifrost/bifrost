"""API-side lease recovery must not need a physical worker claim loop."""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from src.jobs.schedulers import platform_jobs
from src.models.orm.platform_jobs import PlatformJob
from src.scheduler.control import ControlPlane
from src.services import work_dispatch
from src.services.worker_pool import unfit


@pytest.mark.asyncio
async def test_api_reconciliation_recovers_expired_job_without_an_executor(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(timezone.utc)
    jobs = [
        PlatformJob(
            job_type="audit.query", payload={}, title="Lease recovery regression",
            requested_by_user_id=str(uuid4()), requested_by_email="test@example.invalid",
            requested_by_name="Test", status="running", execution_backend="local",
            attempt=1, max_attempts=2, retry_on_runner_loss=True,
            lease_owner=owner, lease_token=uuid4(), lease_expires_at=expiry,
        )
        for owner, expiry in [("lost", now-timedelta(seconds=1)), ("live", now+timedelta(minutes=5))]
    ]
    db_session.add_all(jobs)
    await db_session.commit()
    expired, live = jobs
    live_lease = live.lease_token
    control = ControlPlane()

    async def listen(*_args: object) -> None:
        await asyncio.Event().wait()

    async def finish_pass(*_args: object, **_kwargs: object) -> None:
        control._stop_event.set()

    # No PoolWorker or handler runs. Keep this one API reconciliation pass
    # focused on real database lease recovery, not unrelated dispatch/Redis I/O.
    monkeypatch.setattr(work_dispatch, "listen_for_pool_wake", listen)
    monkeypatch.setattr(work_dispatch, "wait_for_next_pass", finish_pass)
    monkeypatch.setattr(work_dispatch, "reconcile_work_dispatches", AsyncMock())
    monkeypatch.setattr(unfit, "settle_work_that_never_fits", AsyncMock())
    monkeypatch.setattr(platform_jobs, "publish_platform_job_update", AsyncMock())
    try:
        await control._dispatch_reconcile_loop()
        await db_session.refresh(expired)
        await db_session.refresh(live)
        assert expired.status == "queued"
        assert expired.attempt == 1
        assert expired.max_attempts == 2 and expired.retry_on_runner_loss
        assert expired.lease_token is None and expired.lease_owner is None
        assert live.status == "running"
        assert live.lease_token == live_lease
    finally:
        await db_session.execute(delete(PlatformJob).where(PlatformJob.id.in_([job.id for job in jobs])))
        await db_session.commit()
