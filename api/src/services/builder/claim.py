"""Atomic, fair claim of durable build-job rows."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.solution_build_jobs import SolutionBuildJob


async def claim_build_job(
    db: AsyncSession,
    job_id: UUID,
) -> SolutionBuildJob | None:
    """Claim the exact central job dispatched to this coordinator."""
    now = datetime.now(timezone.utc)
    claimed = (
        await db.execute(
            select(SolutionBuildJob)
            .where(
                SolutionBuildJob.id == job_id,
                SolutionBuildJob.status == "queued",
            )
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
