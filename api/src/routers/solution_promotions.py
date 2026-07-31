"""Explicit platform-admin review surface for private Solution promotions."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from src.core.auth import Context, CurrentSuperuser
from src.models.contracts.solution_builder import (
    PromotionResultDTO,
    PromotionReviewDTO,
    PromotionReviewsList,
    PromotionTargetRequest,
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


@router.get("", response_model=PromotionReviewsList)
async def list_reviews(
    ctx: Context,
    _user: CurrentSuperuser,
) -> PromotionReviewsList:
    reviews = await list_promotion_reviews(ctx.db)
    return PromotionReviewsList(promotions=reviews, total=len(reviews))


@router.get("/{solution_id}", response_model=PromotionReviewDTO)
async def get_review(
    solution_id: UUID,
    ctx: Context,
    _user: CurrentSuperuser,
) -> PromotionReviewDTO:
    try:
        return await promotion_review(ctx.db, solution_id)
    except PromotionNotFound as exc:
        raise _not_found() from exc


@router.post("/{solution_id}/promote", response_model=PromotionResultDTO)
async def promote(
    solution_id: UUID,
    body: PromotionTargetRequest,
    ctx: Context,
    user: CurrentSuperuser,
) -> PromotionResultDTO:
    try:
        return await promote_private_solution(
            ctx.db,
            solution_id,
            body,
            admin_user_id=user.user_id,
        )
    except PromotionNotFound as exc:
        raise _not_found() from exc
    except (PromotionBlocked, SolutionDeployConflict) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
