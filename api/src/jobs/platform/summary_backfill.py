"""Platform-job parent for fan-out agent summary backfills."""

from uuid import UUID

from pydantic import BaseModel

from src.jobs.platform.base import (
    PlatformJobContext,
    PlatformJobDeferred,
    define_platform_job,
)


async def reconcile_summary_backfill_jobs() -> int:
    """Heal the narrow child-completion-before-parent-defer race."""
    from sqlalchemy import select

    from src.core.database import get_db_context
    from src.models.orm.platform_jobs import PlatformJob
    from src.models.orm.summary_backfill_job import SummaryBackfillJob
    from src.services.platform_jobs import finish_deferred_platform_job

    async with get_db_context() as db:
        rows = (
            await db.execute(
                select(SummaryBackfillJob)
                .join(PlatformJob, PlatformJob.id == SummaryBackfillJob.id)
                .where(
                    PlatformJob.status == "waiting",
                    SummaryBackfillJob.status.in_(("complete", "cancelled")),
                )
            )
        ).scalars().all()
    for row in rows:
        await finish_deferred_platform_job(
            row.id,
            status="cancelled" if row.status == "cancelled" else "succeeded",
            result={
                "total": row.total,
                "succeeded": row.succeeded,
                "failed": row.failed,
                "actual_cost_usd": str(row.actual_cost_usd),
            },
        )
    return len(rows)


class SummaryBackfillPayload(BaseModel):
    backfill_job_id: UUID
    run_ids: list[UUID]


async def run_summary_backfill(
    context: PlatformJobContext,
    payload: SummaryBackfillPayload,
) -> dict:
    from src.core.database import get_db_context
    from src.services.execution.summary_dispatch import (
        hand_off_summary,
        stage_backfill_summaries,
    )

    await context.report(
        "Dispatching summary work",
        current=0,
        total=len(payload.run_ids),
        percent=0,
    )
    # Durable before any hand-off: a lost hand-off is the reconciler's, and a
    # retried attempt keeps the dispatches already staged.
    async with get_db_context() as db:
        staged = await stage_backfill_summaries(db, payload.run_ids)
    for dispatch in staged:
        await hand_off_summary(dispatch)
    raise PlatformJobDeferred(
        "Waiting for summary workers",
        {"backfill_job_id": str(payload.backfill_job_id)},
    )


SUMMARY_BACKFILL_DEFINITION = define_platform_job(
    "agent.summary_backfill", SummaryBackfillPayload, run_summary_backfill
)
