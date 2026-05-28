"""Helpers for tracking progress of summary backfill jobs.

When an admin triggers a backfill via ``POST /api/agent-runs/backfill-summaries``,
we enqueue one ``agent-summarization`` message per run with a ``backfill_job_id``
tag. The summarize worker calls :func:`record_backfill_outcome` after each
run's summarizer finishes so we can update the orchestration row, emit a
progress broadcast, and flip the job to ``complete`` on the last one.
"""
import logging
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.core.pubsub import publish_summary_backfill_update
from src.models.orm.ai_usage import AIUsage
from src.models.orm.summary_backfill_job import SummaryBackfillJob

logger = logging.getLogger(__name__)


async def record_backfill_outcome(
    job_id: UUID,
    run_id: UUID,
    succeeded: bool,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Increment the job's counters, accumulate cost, and broadcast progress.

    Idempotency guarantee: if the job row is missing (deleted / bad id) we
    swallow and log — the summarizer itself already persisted the per-run
    outcome, and losing a progress counter is strictly better than failing
    the worker message.
    """
    try:
        async with session_factory() as db:
            job = (
                await db.execute(
                    select(SummaryBackfillJob)
                    .where(SummaryBackfillJob.id == job_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if job is None:
                logger.warning(
                    "Backfill job %s not found when recording outcome for run %s",
                    job_id,
                    run_id,
                )
                return
            if job.status == "cancelled":
                # Admin cancelled the job — keep processing the per-run summary
                # (no harm in it) but don't touch the terminal counters.
                return
            processed = set(job.processed_run_ids or [])
            run_key = str(run_id)
            if run_key in processed:
                logger.info(
                    "Ignoring duplicate backfill outcome",
                    extra={"job_id": str(job_id), "run_id": run_key},
                )
                return

            if succeeded:
                job.succeeded = (job.succeeded or 0) + 1
                cost = await _sum_run_cost(run_id, db)
                if cost is not None:
                    job.actual_cost_usd = (
                        job.actual_cost_usd or Decimal("0")
                    ) + cost
            else:
                job.failed = (job.failed or 0) + 1
            job.processed_run_ids = sorted(processed | {run_key})

            finished = (job.succeeded + job.failed) >= job.total
            if finished and job.status == "running":
                job.status = "complete"
                from datetime import datetime, timezone
                job.completed_at = datetime.now(timezone.utc)

            payload = {
                "total": job.total,
                "succeeded": job.succeeded,
                "failed": job.failed,
                "status": job.status,
                "actual_cost_usd": str(job.actual_cost_usd),
                "estimated_cost_usd": str(job.estimated_cost_usd),
            }
            await db.commit()

        # Broadcast after commit so subscribers see the latest committed state.
        await publish_summary_backfill_update(job_id, payload)
    except Exception:
        logger.exception(
            "Failed to record backfill outcome for job %s run %s",
            job_id,
            run_id,
        )


async def _sum_run_cost(run_id: UUID, db: AsyncSession) -> Decimal | None:
    """Sum ``AIUsage.cost`` rows linked to the run. None if no cost recorded."""
    result = await db.execute(
        select(func.coalesce(func.sum(AIUsage.cost), Decimal("0"))).where(
            AIUsage.agent_run_id == run_id
        )
    )
    total = result.scalar() or Decimal("0")
    return total if total > 0 else None
