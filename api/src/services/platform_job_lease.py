"""Fence destructive platform-job work on the job's current lease."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm import PlatformJob


class LeaseLost(Exception):
    """The job no longer holds its lease; another runner owns the work."""


async def hold_lease(db: AsyncSession, job_id: UUID, lease_token: UUID) -> None:
    # Holding the job row lock fences lease recovery until this transaction ends.
    held = await db.execute(
        select(PlatformJob.id)
        .where(
            PlatformJob.id == job_id,
            PlatformJob.lease_token == lease_token,
            PlatformJob.status == "running",
        )
        .with_for_update()
    )
    if held.scalar_one_or_none() is None:
        raise LeaseLost(str(job_id))
