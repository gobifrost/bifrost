"""Read and cancel durable scheduler-owned platform jobs."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import case, func, or_, select

from src.core.auth import Context, CurrentUser
from src.models.contracts.platform_jobs import (
    PlatformJobCancelResponse,
    PlatformJobListResponse,
    PlatformJobPublic,
    PlatformJobStatus,
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
    offset: int = Query(default=0, ge=0),
    job_status: PlatformJobStatus | None = Query(default=None, alias="status"),
    search: str | None = Query(default=None, min_length=1, max_length=200),
) -> PlatformJobListResponse:
    filters = []
    if not user.is_platform_admin:
        filters.append(
            PlatformJob.requested_by_user_id == str(user.user_id)
        )
    if job_status is not None:
        filters.append(PlatformJob.status == job_status.value)
    elif active_only:
        filters.append(
            PlatformJob.status.in_(ACTIVE_PLATFORM_JOB_STATUSES)
        )
    normalized_search = search.strip() if search else ""
    if normalized_search:
        pattern = f"%{normalized_search}%"
        filters.append(
            or_(
                PlatformJob.title.ilike(pattern),
                PlatformJob.job_type.ilike(pattern),
                PlatformJob.requested_by_name.ilike(pattern),
                PlatformJob.resource_type.ilike(pattern),
            )
        )

    total_query = select(func.count()).select_from(PlatformJob).where(*filters)
    total = (await ctx.db.execute(total_query)).scalar_one()
    active_first = case(
        (PlatformJob.status.in_(ACTIVE_PLATFORM_JOB_STATUSES), 0),
        else_=1,
    )
    jobs_query = (
        select(PlatformJob)
        .where(*filters)
        .order_by(active_first, PlatformJob.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    jobs = (await ctx.db.execute(jobs_query)).scalars().all()
    return PlatformJobListResponse(
        jobs=[platform_job_to_public(job) for job in jobs],
        total=total,
        limit=limit,
        offset=offset,
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
