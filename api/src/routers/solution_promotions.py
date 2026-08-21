"""Boundary-aware review and publication of private Solution releases."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from src.core.db_deps import DbSession
from src.models.contracts.solution_builder import (
    PromotionResultDTO,
    PromotionReviewDTO,
    PromotionReviewsList,
    PromotionTargetRequest,
)
from src.models.orm.organizations import Organization
from src.services.authorization import (
    AuthorizationBoundary,
    AuthorizationBoundaryKind,
    AuthorizationContext,
    CurrentAuthorizationContext,
    resolve_authorization_context,
)
from src.services.builder.promotion import (
    PromotionBlocked,
    PromotionNotFound,
    list_promotion_reviews,
    promote_private_solution,
    promotion_review,
)
from src.services.solutions.deploy import SolutionDeployConflict

router = APIRouter(prefix="/api/solution-promotions", tags=["solution-promotions"])


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Promotion request not found",
    )


async def _source_organization_ids(
    authorization: AuthorizationContext,
    db: DbSession,
) -> set[UUID | None] | None:
    """Resolve source review reach without treating Platform as customer reach."""

    authorization.require("solutions.publish.read")
    if authorization.has_capability("platform.superuser"):
        return None
    boundary = authorization.selected_boundary
    if boundary.kind is AuthorizationBoundaryKind.ORGANIZATION:
        return {boundary.organization_id}
    if boundary.kind is AuthorizationBoundaryKind.MANAGED_ORGANIZATIONS:
        return set(
            (
                await db.execute(
                    select(Organization.id).where(Organization.is_provider.is_(False))
                )
            )
            .scalars()
            .all()
        )
    if boundary.kind is AuthorizationBoundaryKind.PLATFORM:
        return {None}
    return set()


async def _require_source_review(
    authorization: AuthorizationContext,
    db: DbSession,
    review: PromotionReviewDTO,
) -> None:
    visible_ids = await _source_organization_ids(authorization, db)
    if visible_ids is not None and review.organization_id not in visible_ids:
        raise _not_found()


async def _destination_authorization(
    authorization: AuthorizationContext,
    db: DbSession,
    review: PromotionReviewDTO,
    body: PromotionTargetRequest,
) -> AuthorizationContext:
    if body.target == "global":
        destination = AuthorizationBoundary.platform()
    else:
        destination_organization_id = (
            body.target_organization_id or review.organization_id
        )
        if destination_organization_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Company promotion requires an organization",
            )
        destination = AuthorizationBoundary.organization(
            destination_organization_id
        )
    destination_authorization = await resolve_authorization_context(
        db,
        requester=authorization.requester,
        selected_boundary=destination,
        request_id=authorization.request_id,
    )
    destination_authorization.require("solutions.publish.execute")
    if body.approve_role_creation or body.role_user_assignments:
        destination_authorization.require("roles.readwrite")
    if body.allow_global_repo_access:
        destination_authorization.require("repository.access.readwrite")
    if body.approved_connection_names:
        destination_authorization.require("integrations.readwrite")
    return destination_authorization


@router.get("", response_model=PromotionReviewsList)
async def list_reviews(
    authorization: CurrentAuthorizationContext,
    db: DbSession,
) -> PromotionReviewsList:
    reviews = await list_promotion_reviews(
        db,
        source_organization_ids=await _source_organization_ids(authorization, db),
    )
    return PromotionReviewsList(promotions=reviews, total=len(reviews))


@router.get("/{solution_id}", response_model=PromotionReviewDTO)
async def get_review(
    solution_id: UUID,
    authorization: CurrentAuthorizationContext,
    db: DbSession,
) -> PromotionReviewDTO:
    try:
        review = await promotion_review(db, solution_id)
        await _require_source_review(authorization, db, review)
        return review
    except PromotionNotFound as exc:
        raise _not_found() from exc


@router.post("/{solution_id}/promote", response_model=PromotionResultDTO)
async def promote(
    solution_id: UUID,
    body: PromotionTargetRequest,
    authorization: CurrentAuthorizationContext,
    db: DbSession,
) -> PromotionResultDTO:
    try:
        review = await promotion_review(db, solution_id)
        await _require_source_review(authorization, db, review)
        await _destination_authorization(authorization, db, review, body)
        return await promote_private_solution(
            db,
            solution_id,
            body,
            admin_user_id=authorization.effective_actor.user_id,
        )
    except PromotionNotFound as exc:
        raise _not_found() from exc
    except (PromotionBlocked, SolutionDeployConflict) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
