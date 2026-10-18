"""Platform-admin workflow permission conversion endpoints."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response, status

from src.core.auth import CurrentActiveUser
from src.core.db_deps import DbSession
from src.jobs.platform.workflow_conversion import (
    ANALYZE_JOB_DEFINITION,
    APPLY_JOB_DEFINITION,
    CUTOVER_JOB_DEFINITION,
)
from src.models.contracts.platform_jobs import PlatformJobAccepted
from shared.models import (
    WorkflowConversionAnalyzeRequest,
    WorkflowConversionApplyRequest,
    WorkflowConversionCutoverRequest,
    WorkflowConversionReport,
)
from src.models.orm.platform_jobs import PlatformJob
from src.services.authorization.enforce import load_caller, require_operation
from src.services.authorization.evaluator import GLOBAL
from src.services.operation_catalog import operation_route
from shared.workflow_conversions import enqueue_analysis, enqueue_followup, get_report

router = APIRouter(prefix="/api/workflow-conversions", tags=["Workflow Conversions"])


async def _admin_caller(db: DbSession, user: CurrentActiveUser, operation: str):
    caller = await load_caller(db, user)
    # This deliberate second guard keeps the product initially limited to
    # current Platform Admins even if a future operation binding broadens.
    if not caller.is_platform_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Platform administrator permission is required",
        )
    require_operation(caller, operation, GLOBAL)
    return caller


@router.post(
    "",
    response_model=PlatformJobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    **operation_route("workflows.conversions.create"),
)
async def analyze_workflows(
    body: WorkflowConversionAnalyzeRequest,
    response: Response,
    user: CurrentActiveUser,
    db: DbSession,
) -> PlatformJobAccepted:
    await _admin_caller(db, user, operation="workflows.conversions.create")
    job, reused = await enqueue_analysis(db, user, body, ANALYZE_JOB_DEFINITION)
    response.headers["Location"] = f"/api/platform-jobs/{job.id}"
    return PlatformJobAccepted(
        job_id=job.id,
        status=job.status,
        reused=reused,
        notification_id=job.notification_id,
    )


@router.get(
    "/{job_id}/report",
    response_model=WorkflowConversionReport,
    **operation_route("workflows.conversions.get"),
)
async def workflow_conversion_report(
    job_id: UUID,
    user: CurrentActiveUser,
    db: DbSession,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
) -> WorkflowConversionReport:
    caller = await _admin_caller(db, user, operation="workflows.conversions.get")
    return await get_report(db, caller, job_id, offset=offset, limit=limit)


async def _analysis_job(db: DbSession, analysis_job_id: UUID) -> PlatformJob:
    job = await db.get(PlatformJob, analysis_job_id)
    if job is None or job.job_type != ANALYZE_JOB_DEFINITION.job_type:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversion analysis not found",
        )
    return job


@router.post(
    "/{job_id}/apply",
    response_model=PlatformJobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    **operation_route("workflows.conversions.update"),
)
async def apply_workflow_conversion(
    job_id: UUID,
    body: WorkflowConversionApplyRequest,
    response: Response,
    user: CurrentActiveUser,
    db: DbSession,
) -> PlatformJobAccepted:
    await _admin_caller(db, user, operation="workflows.conversions.update")
    analysis = await _analysis_job(db, job_id)
    job, reused = await enqueue_followup(
        db,
        user,
        analysis,
        body,
        APPLY_JOB_DEFINITION,
        title="Apply workflow permission conversion",
    )
    response.headers["Location"] = f"/api/platform-jobs/{job.id}"
    return PlatformJobAccepted(
        job_id=job.id,
        status=job.status,
        reused=reused,
        notification_id=job.notification_id,
    )


@router.post(
    "/{job_id}/cutover",
    response_model=PlatformJobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    **operation_route("workflows.permission_default.update"),
)
async def cutover_workflow_permissions(
    job_id: UUID,
    body: WorkflowConversionCutoverRequest,
    response: Response,
    user: CurrentActiveUser,
    db: DbSession,
) -> PlatformJobAccepted:
    await _admin_caller(db, user, operation="workflows.permission_default.update")
    analysis = await _analysis_job(db, job_id)
    if not bool(analysis.payload.get("platform_cutover")):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This analysis was not started for platform cutover",
        )
    job, reused = await enqueue_followup(
        db,
        user,
        analysis,
        body,
        CUTOVER_JOB_DEFINITION,
        title="Set declared permissions as the workflow default",
    )
    response.headers["Location"] = f"/api/platform-jobs/{job.id}"
    return PlatformJobAccepted(
        job_id=job.id,
        status=job.status,
        reused=reused,
        notification_id=job.notification_id,
    )
