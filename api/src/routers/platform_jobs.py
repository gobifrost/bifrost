"""Read and cancel durable scheduler-owned platform jobs."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from src.core.auth import Context, CurrentUser
from src.models.contracts.platform_jobs import (
    PlatformJobCancelResponse,
    PlatformJobListResponse,
    PlatformJobPublic,
)
from src.models.orm.platform_jobs import PlatformJob
from src.services.platform_jobs import (
    ACTIVE_PLATFORM_JOB_STATUSES,
    platform_job_to_public,
    request_platform_job_cancel,
)

router = APIRouter(prefix="/api/platform-jobs", tags=["Platform Jobs"])


def _can_read(job: PlatformJob, user: CurrentUser) -> bool:
    return (
        user.is_platform_admin
        or job.requested_by_user_id == str(user.user_id)
    )


async def _get_visible_job(
    ctx: Context,
    user: CurrentUser,
    job_id: UUID,
) -> PlatformJob:
    job = await ctx.db.get(PlatformJob, job_id)
    if job is None or not _can_read(job, user):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Platform job not found",
        )
    return job


@router.get(
    "",
    response_model=PlatformJobListResponse,
    summary="List the caller's platform jobs",
)
async def list_platform_jobs(
    ctx: Context,
    user: CurrentUser,
    active_only: bool = Query(default=True),
    limit: int = Query(default=50, ge=1, le=200),
) -> PlatformJobListResponse:
    query = select(PlatformJob).order_by(PlatformJob.created_at.desc()).limit(limit)
    if not user.is_platform_admin:
        query = query.where(
            PlatformJob.requested_by_user_id == str(user.user_id)
        )
    if active_only:
        query = query.where(
            PlatformJob.status.in_(ACTIVE_PLATFORM_JOB_STATUSES)
        )
    jobs = (await ctx.db.execute(query)).scalars().all()
    return PlatformJobListResponse(
        jobs=[platform_job_to_public(job) for job in jobs]
    )


@router.get(
    "/{job_id}",
    response_model=PlatformJobPublic,
    summary="Get durable platform-job status",
)
async def get_platform_job_status(
    job_id: UUID,
    ctx: Context,
    user: CurrentUser,
) -> PlatformJobPublic:
    return platform_job_to_public(await _get_visible_job(ctx, user, job_id))


@router.post(
    "/{job_id}/cancel",
    response_model=PlatformJobCancelResponse,
    summary="Request cancellation of a platform job",
)
async def cancel_platform_job(
    job_id: UUID,
    ctx: Context,
    user: CurrentUser,
) -> PlatformJobCancelResponse:
    job = await _get_visible_job(ctx, user, job_id)
    job, accepted = await request_platform_job_cancel(ctx.db, job)
    return PlatformJobCancelResponse(
        job=platform_job_to_public(job),
        accepted=accepted,
    )
