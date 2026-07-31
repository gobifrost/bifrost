"""Atomic, fair claim of durable build-job rows."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from src.models.orm.solution_build_jobs import SolutionBuildJob


async def claim_next_build_job(db: AsyncSession) -> SolutionBuildJob | None:
    """Lock and claim the oldest per-user candidate with recent-claim fairness.

    PostgreSQL rejects ``DISTINCT ON ... FOR UPDATE``. Correlated ``NOT
    EXISTS`` selects the oldest queued row per user while leaving the outer
    build-job row lockable with ``SKIP LOCKED``.
    """
    now = datetime.now(timezone.utc)
    recent_cutoff = now - timedelta(minutes=10)
    job = aliased(SolutionBuildJob)
    earlier = aliased(SolutionBuildJob)
    recent = aliased(SolutionBuildJob)

    has_earlier = (
        select(1)
        .where(
            earlier.status == "queued",
            earlier.requested_by.is_not_distinct_from(job.requested_by),
            earlier.created_at < job.created_at,
        )
        .correlate(job)
        .exists()
    )
    recent_claims = (
        select(func.count(recent.id))
        .where(
            recent.requested_by.is_not_distinct_from(job.requested_by),
            recent.claimed_at.is_not(None),
            recent.claimed_at > recent_cutoff,
        )
        .correlate(job)
        .scalar_subquery()
    )
    claimed = (
        await db.execute(
            select(job)
            .where(job.status == "queued", ~has_earlier)
            .order_by(recent_claims.asc(), job.created_at.asc(), job.id.asc())
            .limit(1)
            .with_for_update(skip_locked=True)
        )
    ).scalar_one_or_none()
    if claimed is None:
        return None
    claimed.status = "running"
    claimed.claimed_at = now
    claimed.started_at = now
    claimed.last_progress_at = now
    await db.flush()
    return claimed
