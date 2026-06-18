from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.models.orm.solution_deploy_jobs import SolutionDeployJob
from src.models.orm.solutions import Solution
from src.routers.solutions import reconcile_orphaned_deploy_jobs


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
