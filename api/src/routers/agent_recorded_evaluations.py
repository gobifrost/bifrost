"""Recorded evaluation API surface backed by shared PlatformJobs."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response, status

from shared.agent_recorded_admission import (
    admit_recorded_evaluation,
    assert_recorded_evaluation_readable,
    get_recorded_results_page,
)
from shared.models import (
    QualityUsageBreakdownResponse,
    RecordedEvaluationCreate,
    RecordedEvaluationResultsPage,
)
from shared.quality_usage_reporting import (
    UsageReportPagination,
    summarize_quality_operation_usage,
)
from src.core.auth import CurrentActiveUser
from src.core.db_deps import DbSession, ReadSnapshotDbSession
from src.models.orm.agent_recorded_evaluations import AgentRecordedEvaluation
from src.models.contracts.platform_jobs import PlatformJobAccepted
from src.services.platform_jobs import publish_platform_job_update

router = APIRouter(prefix="/api/agent-evaluations", tags=["agent-evaluations"])


@router.post(
    "/recorded-evaluations",
    response_model=PlatformJobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_recorded_evaluation(
    body: RecordedEvaluationCreate,
    response: Response,
    db: DbSession,
    user: CurrentActiveUser,
) -> PlatformJobAccepted:
    evaluation, job, reused = await admit_recorded_evaluation(
        db, user=user, body=body
    )
    await db.commit()
    await publish_platform_job_update(job)
    response.headers["Location"] = f"/api/platform-jobs/{job.id}"
    response.headers["X-Recorded-Evaluation-Id"] = str(evaluation.id)
    return PlatformJobAccepted(
        job_id=job.id,
        status=job.status,
        reused=reused,
        notification_id=job.notification_id,
    )


@router.get(
    "/recorded-evaluations/{evaluation_id}/results",
    response_model=RecordedEvaluationResultsPage,
)
async def get_recorded_evaluation_results(
    evaluation_id: UUID,
    db: DbSession,
    user: CurrentActiveUser,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> RecordedEvaluationResultsPage:
    return await get_recorded_results_page(
        db,
        evaluation_id=evaluation_id,
        user=user,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/recorded-evaluations/{evaluation_id}/usage",
    response_model=QualityUsageBreakdownResponse,
)
async def get_recorded_evaluation_usage(
    evaluation_id: UUID,
    db: ReadSnapshotDbSession,
    user: CurrentActiveUser,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> QualityUsageBreakdownResponse:
    evaluation = await db.get(AgentRecordedEvaluation, evaluation_id)
    if evaluation is None:
        raise HTTPException(status_code=404, detail="Recorded evaluation not found.")
    await assert_recorded_evaluation_readable(db, evaluation=evaluation, user=user)
    report = await summarize_quality_operation_usage(
        db,
        scope="organization" if evaluation.org_id else "platform",
        organization_id=evaluation.org_id,
        operation_type="recorded_evaluation",
        operation_id=evaluation.id,
        pagination=UsageReportPagination(limit=limit, offset=offset),
    )
    return QualityUsageBreakdownResponse.model_validate(report)
