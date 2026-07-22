from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.models.orm.solution_deploy_jobs import SolutionDeployJob
from src.models.orm.solutions import Solution
from src.routers.solutions import (
    DEPLOY_JOB_TIMEOUT,
    _run_deploy_job,
    expire_deploy_job_if_timed_out,
    reconcile_orphaned_deploy_jobs,
)


@pytest.mark.asyncio
async def test_reconcile_orphaned_deploy_jobs_fails_stale_non_terminal_jobs(db_session):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    sol = Solution(slug="demo", name="Demo")
    db_session.add(sol)
    await db_session.flush()

    stale_queued = SolutionDeployJob(
        install_id=sol.id,
        status="queued",
        created_at=now - timedelta(minutes=30),
        updated_at=now - timedelta(minutes=30),
    )
    stale_running = SolutionDeployJob(
        install_id=sol.id,
        status="running",
        created_at=now - timedelta(minutes=30),
        updated_at=now - timedelta(minutes=30),
    )
    fresh_queued = SolutionDeployJob(
        install_id=sol.id,
        status="queued",
        created_at=now,
        updated_at=now,
    )
    succeeded = SolutionDeployJob(
        install_id=sol.id,
        status="succeeded",
        created_at=now - timedelta(minutes=30),
        updated_at=now - timedelta(minutes=30),
    )
    db_session.add_all([stale_queued, stale_running, fresh_queued, succeeded])
    await db_session.flush()

    changed = await reconcile_orphaned_deploy_jobs(
        db_session,
        older_than=timedelta(minutes=10),
        now=now,
    )

    assert changed == 2
    assert stale_queued.status == "failed"
    assert stale_running.status == "failed"
    assert "API restarted" in (stale_queued.error or "")
    assert "API restarted" in (stale_running.error or "")
    assert fresh_queued.status == "queued"
    assert fresh_queued.error is None
    assert succeeded.status == "succeeded"


@pytest.mark.asyncio
async def test_expire_deploy_job_if_timed_out_marks_running_job_failed(db_session):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    job = SolutionDeployJob(
        install_id=None,
        status="running",
        result={"phase": "building app dist"},
        created_at=now - DEPLOY_JOB_TIMEOUT - timedelta(seconds=1),
        updated_at=now - DEPLOY_JOB_TIMEOUT - timedelta(seconds=1),
    )
    db_session.add(job)
    await db_session.flush()

    changed = expire_deploy_job_if_timed_out(job, now=now)

    assert changed is True
    assert job.status == "failed"
    assert job.result is None
    assert "15-minute" in (job.error or "")


def test_expire_deploy_job_if_timed_out_leaves_terminal_job_unchanged():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    job = SolutionDeployJob(
        install_id=None,
        status="succeeded",
        result={"solution_id": "abc"},
        created_at=now - DEPLOY_JOB_TIMEOUT - timedelta(seconds=1),
        updated_at=now,
    )

    changed = expire_deploy_job_if_timed_out(job, now=now)

    assert changed is False
    assert job.status == "succeeded"
    assert job.result == {"solution_id": "abc"}


@pytest.mark.asyncio
async def test_run_deploy_job_does_not_start_after_job_is_terminal(
    tmp_path, monkeypatch
):
    job = SolutionDeployJob(id=uuid4(), install_id=None, status="failed")

    class FakeDB:
        async def get(self, model, row_id):  # noqa: ANN001, ANN201
            assert model is SolutionDeployJob
            assert row_id == job.id
            return job

    @asynccontextmanager
    async def fake_db_context():
        yield FakeDB()

    from src.core import database
    from src.services.solutions import zip_install

    deploy = AsyncMock()
    monkeypatch.setattr(database, "get_db_context", fake_db_context)
    monkeypatch.setattr(zip_install, "deploy_zip_to_solution_path", deploy)
    zip_path = tmp_path / "deploy.zip"
    zip_path.write_bytes(b"not used")

    await _run_deploy_job(job.id, uuid4(), zip_path, force=False)

    deploy.assert_not_awaited()
    assert not zip_path.exists()
