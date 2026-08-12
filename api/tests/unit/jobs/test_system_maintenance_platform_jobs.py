from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.jobs.platform import system_maintenance
from src.models.orm.platform_jobs import PlatformJob


@pytest.mark.asyncio
async def test_automatic_maintenance_jobs_are_durable_prioritized_singletons(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await db_session.execute(delete(PlatformJob))
    await db_session.commit()

    @asynccontextmanager
    async def context() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    monkeypatch.setattr(system_maintenance, "get_db_context", context)
    monkeypatch.setattr(
        system_maintenance,
        "publish_platform_job_update",
        lambda _job: _completed(),
    )

    first = await system_maintenance.enqueue_automatic_oauth_refresh()
    second = await system_maintenance.enqueue_automatic_oauth_refresh()
    await db_session.commit()

    jobs = (
        await db_session.execute(
            select(PlatformJob).where(PlatformJob.job_type == "oauth.refresh")
        )
    ).scalars().all()
    assert len(jobs) == 1
    assert first.platform_job_id == second.platform_job_id == jobs[0].id
    assert first.summary == "Durable job enqueued"
    assert second.summary == "Reused active job"
    assert jobs[0].priority == 1000
    assert jobs[0].resource_lock_key == "oauth.refresh"
    assert system_maintenance.OAUTH_REFRESH_DEFINITION.policy.max_concurrency == 1


@pytest.mark.asyncio
async def test_file_index_reconciliation_is_a_durable_singleton(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await db_session.execute(delete(PlatformJob))
    await db_session.commit()

    @asynccontextmanager
    async def context() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    monkeypatch.setattr(system_maintenance, "get_db_context", context)
    monkeypatch.setattr(
        system_maintenance,
        "publish_platform_job_update",
        lambda _job: _completed(),
    )

    outcome = await system_maintenance.enqueue_automatic_file_index_reconciliation()
    await db_session.commit()
    job = await db_session.get(PlatformJob, outcome.platform_job_id)

    assert job is not None
    assert job.job_type == "workspace.file_index_reconcile"
    assert job.priority == 1000
    assert job.resource_lock_key == "workspace.file_index_reconcile"
    assert (
        system_maintenance.FILE_INDEX_RECONCILIATION_DEFINITION.policy.max_concurrency
        == 1
    )


async def _completed() -> None:
    return None
