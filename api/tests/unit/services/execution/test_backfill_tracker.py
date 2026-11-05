"""Backfill outcomes counted by concurrent executors are never lost."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

from sqlalchemy import delete

from src.models.orm.summary_backfill_job import SummaryBackfillJob
from src.models.orm.agent_runs import AgentRun
from src.services.execution import backfill_tracker
from src.services.execution.backfill_tracker import refresh_backfill_progress


async def test_concurrent_and_replayed_outcomes_are_counted_once(async_session_factory, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(backfill_tracker, "publish_summary_backfill_update", AsyncMock())
    monkeypatch.setattr("src.services.platform_jobs.finish_deferred_platform_job", AsyncMock())
    monkeypatch.setattr(
        "src.services.platform_jobs.update_deferred_platform_job_progress", AsyncMock()
    )
    job_id = uuid4()
    run_ids = [uuid4() for _ in range(8)]
    async with async_session_factory() as db:
        db.add(SummaryBackfillJob(id=job_id, requested_by=uuid4(), status="running", total=8))
        await db.flush()
        db.add_all([
            AgentRun(
                id=run_id, trigger_type="test", status="completed",
                summary_backfill_job_id=job_id,
                summary_status="completed" if index % 2 == 0 else "failed",
            ) for index, run_id in enumerate(run_ids)
        ])
        await db.commit()
    try:
        await asyncio.gather(
            *[
                refresh_backfill_progress(
                    job_id=job_id,
                    session_factory=async_session_factory,
                )
                for _ in range(16)
            ]
        )

        async with async_session_factory() as db:
            job = await db.get(SummaryBackfillJob, job_id)
            assert job is not None
            assert (job.succeeded, job.failed, job.status) == (4, 4, "complete")
    finally:
        async with async_session_factory() as db:
            await db.execute(delete(AgentRun).where(AgentRun.id.in_(run_ids)))
            await db.execute(delete(SummaryBackfillJob).where(SummaryBackfillJob.id == job_id))
            await db.commit()
