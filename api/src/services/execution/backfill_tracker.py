"""Helpers for tracking progress of summary backfill jobs.

When an admin triggers a backfill via ``POST /api/agent-runs/backfill-summaries``,
we enqueue one ``agent-summarization`` message per run with a ``backfill_job_id``
tag. The summarize worker calls :func:`refresh_backfill_progress` after each
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
from src.models.orm.agent_runs import AgentRun
from src.models.orm.summary_backfill_job import SummaryBackfillJob

logger = logging.getLogger(__name__)


async def refresh_backfill_progress(
    job_id: UUID,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Rebuild progress from durable outcomes; replay never counts a run twice."""
    try:
        async with session_factory() as db:
            # Locked: several executors count outcomes for one job at once.
            job = (
                await db.execute(
                    select(SummaryBackfillJob)
                    .where(SummaryBackfillJob.id == job_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if job is None:
                logger.warning(
                    "Backfill job %s not found when refreshing progress",
                    job_id,
                )
                return
            if job.status not in ("running", "complete"):
                # Admin cancelled the job — keep processing the per-run summary
                # (no harm in it) but don't touch the terminal counters.
                return

            if job.status == "running":
                counts = (await db.execute(select(
                    func.count().filter(AgentRun.summary_status == "completed"),
                    func.count().filter(AgentRun.summary_status == "failed"),
                ).where(AgentRun.summary_backfill_job_id == job_id))).one()
                job.succeeded, job.failed = counts
                # Completed runs predate the backfill; exclude their original
                # agent execution's usage from the backfill's actual cost.
                job.actual_cost_usd = await db.scalar(
                    select(func.coalesce(func.sum(AIUsage.cost), Decimal("0")))
                    .join(AgentRun, AgentRun.id == AIUsage.agent_run_id)
                    .where(
                        AgentRun.summary_backfill_job_id == job_id,
                        AIUsage.timestamp >= job.created_at,
                    )
                ) or Decimal("0")

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
        from src.services.platform_jobs import (
            finish_deferred_platform_job,
            update_deferred_platform_job_progress,
        )

        completed = payload["succeeded"] + payload["failed"]
        if payload["status"] == "complete":
            await finish_deferred_platform_job(
                job_id,
                status="succeeded",
                result=payload,
            )
        else:
            await update_deferred_platform_job_progress(
                job_id,
                phase=f"Summarized {completed}/{payload['total']} agent runs",
                current=completed,
                total=payload["total"],
            )
    except Exception:
        logger.exception(
            "Failed to refresh backfill progress for job %s",
            job_id,
        )
