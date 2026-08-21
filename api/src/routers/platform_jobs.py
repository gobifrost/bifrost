"""Read and cancel durable scheduler-owned platform jobs."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import case, func, or_, select

from src.core.auth import Context
from src.models.contracts.platform_jobs import (
    PlatformJobCancelResponse,
    PlatformJobListResponse,
    PlatformJobPublic,
    PlatformJobStatus,
)
from src.models.orm.platform_jobs import PlatformJob
from src.models.orm.organizations import Organization
from src.services.audit import emit_audit
from src.services.authorization import (
    AuthorizationBoundaryKind,
    CurrentAuthorizationContext,
)
from src.services.operation_catalog import operation_route
from src.services.platform_jobs import (
    ACTIVE_PLATFORM_JOB_STATUSES,
    platform_job_to_public,
    request_platform_job_cancel,
)

router = APIRouter(prefix="/api/platform-jobs", tags=["Platform Jobs"])


async def _boundary_admits_job(
    ctx: Context,
    authorization: CurrentAuthorizationContext,
    job: PlatformJob,
) -> bool:
    boundary = authorization.selected_boundary
    if boundary.kind is AuthorizationBoundaryKind.PLATFORM:
        return job.organization_id is None
    if boundary.kind is AuthorizationBoundaryKind.ORGANIZATION:
        return job.organization_id == boundary.organization_id
    if job.organization_id is None:
        return False
    return (
        await ctx.db.scalar(
            select(Organization.is_provider).where(
                Organization.id == job.organization_id
            )
        )
        is False
    )


async def _get_visible_job(
    ctx: Context,
    authorization: CurrentAuthorizationContext,
    job_id: UUID,
    *,
    capability: str = "platformjobs.read",
) -> PlatformJob:
    job = await ctx.db.get(PlatformJob, job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Platform job not found",
        )
    owns_job = job.requested_by_user_id == str(authorization.effective_actor.user_id)
    admitted_by_role = authorization.has_capability(
        capability
    ) and await _boundary_admits_job(ctx, authorization, job)
    if not owns_job and not admitted_by_role:
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
    authorization: CurrentAuthorizationContext,
    active_only: bool = Query(default=True),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    job_status: PlatformJobStatus | None = Query(default=None, alias="status"),
    search: str | None = Query(default=None, min_length=1, max_length=200),
) -> PlatformJobListResponse:
    owner_filter = PlatformJob.requested_by_user_id == str(
        authorization.effective_actor.user_id
    )
    admitted_filter = None
    if authorization.has_capability("platformjobs.read"):
        boundary = authorization.selected_boundary
        if boundary.kind is AuthorizationBoundaryKind.PLATFORM:
            admitted_filter = PlatformJob.organization_id.is_(None)
        elif boundary.kind is AuthorizationBoundaryKind.ORGANIZATION:
            admitted_filter = PlatformJob.organization_id == boundary.organization_id
        else:
            managed_org_ids = select(Organization.id).where(
                Organization.is_provider.is_(False)
            )
            admitted_filter = PlatformJob.organization_id.in_(managed_org_ids)

    filters = [
        owner_filter if admitted_filter is None else or_(owner_filter, admitted_filter)
    ]
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
    **operation_route("platform.jobs.get"),
)
async def get_platform_job_status(
    job_id: UUID,
    ctx: Context,
    authorization: CurrentAuthorizationContext,
) -> PlatformJobPublic:
    return platform_job_to_public(await _get_visible_job(ctx, authorization, job_id))


@router.post(
    "/{job_id}/cancel",
    response_model=PlatformJobCancelResponse,
    summary="Request cancellation of a platform job",
)
async def cancel_platform_job(
    job_id: UUID,
    ctx: Context,
    authorization: CurrentAuthorizationContext,
) -> PlatformJobCancelResponse:
    job = await _get_visible_job(
        ctx,
        authorization,
        job_id,
        capability="platformjobs.execute",
    )
    job, accepted = await request_platform_job_cancel(ctx.db, job)
    if accepted:
        await emit_audit(
            ctx.db,
            "platform_job.cancel",
            resource_type="platform_job",
            resource_id=job.id,
            details={"job_type": job.job_type},
        )
        await ctx.db.commit()
    return PlatformJobCancelResponse(
        job=platform_job_to_public(job),
        accepted=accepted,
    )
