"""Agent review definition, admission, result, and usage routes."""

from __future__ import annotations

from typing import Annotated, Awaitable, TypeVar
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response, status

from shared.agent_review_admission import (
    admit_agent_review_run,
    create_review_definition,
    create_review_version,
    get_review_definition,
    get_review_results,
    get_review_run,
    list_review_definitions,
    list_review_versions,
    update_review_definition,
)
from shared.agent_reviews import AgentReviewServiceError, assert_review_sources_readable
from shared.models import (
    AgentReviewDefinitionCreate,
    AgentReviewDefinitionPublic,
    AgentReviewDefinitionUpdate,
    AgentReviewDefinitionsPage,
    AgentReviewRunAccepted,
    AgentReviewRunCreate,
    AgentReviewRunPublic,
    AgentReviewRunResults,
    AgentReviewVersionCreate,
    AgentReviewVersionPublic,
    AgentReviewVersionsPage,
    QualityUsageBreakdownResponse,
)
from shared.quality_usage_reporting import UsageReportPagination, summarize_quality_operation_usage
from src.core.auth import CurrentActiveUser
from src.core.db_deps import DbSession, ReadSnapshotDbSession
from src.core.principal import UserPrincipal
from src.models.orm.agent_reviews import AgentReviewRun
from src.services.platform_jobs import publish_platform_job_update

router = APIRouter(prefix="/api/agent-reviews", tags=["agent-reviews"])
T = TypeVar("T")


def _principal(user: CurrentActiveUser) -> UserPrincipal:
    return UserPrincipal(
        user_id=user.user_id,
        email=user.email,
        organization_id=user.organization_id,
        name=user.name or "",
        is_active=user.is_active,
        is_superuser=user.is_superuser,
        is_verified=user.is_verified,
        is_external=user.is_external,
    )


async def _domain(call: Awaitable[T]) -> T:
    try:
        return await call
    except AgentReviewServiceError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.public_detail) from exc


@router.post("", response_model=AgentReviewDefinitionPublic, status_code=status.HTTP_201_CREATED)
async def create_review(body: AgentReviewDefinitionCreate, db: DbSession, user: CurrentActiveUser) -> AgentReviewDefinitionPublic:
    result = await _domain(create_review_definition(db, user=_principal(user), body=body))
    await db.commit()
    return result


@router.get("", response_model=AgentReviewDefinitionsPage)
async def list_reviews(
    agent_id: UUID,
    db: DbSession,
    user: CurrentActiveUser,
    status_filter: Annotated[str, Query(alias="status", pattern="^(active|disabled|all)$")] = "active",
    organization_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AgentReviewDefinitionsPage:
    return await _domain(list_review_definitions(
        db,
        user=_principal(user),
        agent_id=agent_id,
        status_filter=status_filter,
        organization_id=organization_id,
        limit=limit,
        offset=offset,
    ))


# Literal run routes must be registered before /{review_id}.
@router.get("/runs/{review_run_id}", response_model=AgentReviewRunPublic)
async def get_run(review_run_id: UUID, db: ReadSnapshotDbSession, user: CurrentActiveUser) -> AgentReviewRunPublic:
    return await _domain(get_review_run(db, user=_principal(user), review_run_id=review_run_id))


@router.get("/runs/{review_run_id}/results", response_model=AgentReviewRunResults)
async def get_results(review_run_id: UUID, db: ReadSnapshotDbSession, user: CurrentActiveUser) -> AgentReviewRunResults:
    return await _domain(get_review_results(db, user=_principal(user), review_run_id=review_run_id))


@router.get("/runs/{review_run_id}/usage", response_model=QualityUsageBreakdownResponse)
async def get_usage(
    review_run_id: UUID,
    db: ReadSnapshotDbSession,
    user: CurrentActiveUser,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> QualityUsageBreakdownResponse:
    principal = _principal(user)
    run = await db.get(AgentReviewRun, review_run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Review run not found.")
    try:
        await assert_review_sources_readable(db, principal, review_run=run)
    except AgentReviewServiceError as exc:
        raise HTTPException(status_code=404, detail="Review run not found.") from exc
    report = await summarize_quality_operation_usage(
        db,
        scope="organization" if run.org_id else "platform",
        organization_id=run.org_id,
        operation_type="agent_review",
        operation_id=run.id,
        pagination=UsageReportPagination(limit=limit, offset=offset),
    )
    return QualityUsageBreakdownResponse.model_validate(report)


@router.get("/{review_id}", response_model=AgentReviewDefinitionPublic)
async def get_review(review_id: UUID, db: DbSession, user: CurrentActiveUser) -> AgentReviewDefinitionPublic:
    return await _domain(get_review_definition(db, user=_principal(user), review_id=review_id))


@router.patch("/{review_id}", response_model=AgentReviewDefinitionPublic)
async def update_review(review_id: UUID, body: AgentReviewDefinitionUpdate, db: DbSession, user: CurrentActiveUser) -> AgentReviewDefinitionPublic:
    result = await _domain(update_review_definition(db, user=_principal(user), review_id=review_id, body=body))
    await db.commit()
    return result


@router.post("/{review_id}/versions", response_model=AgentReviewVersionPublic, status_code=status.HTTP_201_CREATED)
async def create_version(review_id: UUID, body: AgentReviewVersionCreate, db: DbSession, user: CurrentActiveUser) -> AgentReviewVersionPublic:
    result = await _domain(create_review_version(db, user=_principal(user), review_id=review_id, body=body))
    await db.commit()
    return result


@router.get("/{review_id}/versions", response_model=AgentReviewVersionsPage)
async def list_versions(review_id: UUID, db: DbSession, user: CurrentActiveUser, limit: Annotated[int, Query(ge=1, le=200)] = 50, offset: Annotated[int, Query(ge=0)] = 0) -> AgentReviewVersionsPage:
    return await _domain(list_review_versions(db, user=_principal(user), review_id=review_id, limit=limit, offset=offset))


@router.post("/{review_id}/runs", response_model=AgentReviewRunAccepted, status_code=status.HTTP_202_ACCEPTED)
async def create_run(review_id: UUID, body: AgentReviewRunCreate, response: Response, db: DbSession, user: CurrentActiveUser) -> AgentReviewRunAccepted:
    run, job, reused = await _domain(admit_agent_review_run(db, user=_principal(user), review_id=review_id, body=body))
    await db.commit()
    await publish_platform_job_update(job)
    response.headers["Location"] = f"/api/platform-jobs/{job.id}"
    response.headers["X-Agent-Review-Run-Id"] = str(run.id)
    return AgentReviewRunAccepted(review_run_id=run.id, job_id=job.id, reused=reused, notification_id=job.notification_id)
