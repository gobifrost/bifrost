"""REST endpoints for Solutions — installable surfaces (success-criteria §3).

An install is created here, then deployed via ``POST /{id}/deploy`` (the single
writer for a disconnected install). Deploy is a full replace by contract and is
non-interactive — it always applies the whole bundle.

Solution-management itself is an admin operation; the deployed *entities* are
what end users see (the Solution is invisible to them — criterion 16).
"""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.core.auth import Context, CurrentSuperuser
from src.models.contracts.solutions import (
    Solution as SolutionDTO,
    SolutionCreate,
    SolutionDeployRequest,
    SolutionDeployResponse,
    SolutionsList,
)
from src.models.orm.solutions import Solution as SolutionORM
from src.services.solutions.deploy import SolutionBundle, SolutionDeployer

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/solutions", tags=["Solutions"])


@router.post("", response_model=SolutionDTO, status_code=status.HTTP_201_CREATED, summary="Create a Solution install (admin only)")
async def create_solution(body: SolutionCreate, ctx: Context, user: CurrentSuperuser) -> SolutionDTO:
    # Scope: global → org NULL; org → explicit organization_id or caller's org.
    if body.scope == "global":
        org_id: UUID | None = None
    else:
        org_id = body.organization_id or ctx.org_id
        if org_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="org-scoped install requires an organization_id",
            )

    row = SolutionORM(
        slug=body.slug,
        name=body.name,
        organization_id=org_id,
        global_repo_access=body.global_repo_access,
        git_connected=body.git_connected,
        git_repo_url=body.git_repo_url,
    )
    ctx.db.add(row)
    try:
        await ctx.db.flush()
    except IntegrityError as exc:
        await ctx.db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await ctx.db.commit()
    await ctx.db.refresh(row)
    return SolutionDTO.model_validate(row)


@router.get("", response_model=SolutionsList, summary="List Solution installs (admin only)")
async def list_solutions(ctx: Context, user: CurrentSuperuser) -> SolutionsList:
    rows = (await ctx.db.execute(select(SolutionORM).order_by(SolutionORM.slug))).scalars().all()
    return SolutionsList(solutions=[SolutionDTO.model_validate(r) for r in rows])


@router.get("/{solution_id}", response_model=SolutionDTO, summary="Get a Solution install (admin only)")
async def get_solution(solution_id: UUID, ctx: Context, user: CurrentSuperuser) -> SolutionDTO:
    row = await ctx.db.get(SolutionORM, solution_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Solution not found")
    return SolutionDTO.model_validate(row)


@router.post(
    "/{solution_id}/deploy",
    response_model=SolutionDeployResponse,
    summary="Deploy a bundle to an install (full replace, non-interactive, admin only)",
)
async def deploy_solution(
    solution_id: UUID, body: SolutionDeployRequest, ctx: Context, user: CurrentSuperuser
) -> SolutionDeployResponse:
    solution = await ctx.db.get(SolutionORM, solution_id)
    if solution is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Solution not found")

    # One-writer invariant: a git-connected install is written only by auto-pull
    # (Sub-plan 5); deploy is refused for it.
    if solution.git_connected:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This install is git-connected; deploy is disabled (auto-pull is the only writer).",
        )

    deployer = SolutionDeployer(ctx.db)
    result = await deployer.deploy(
        SolutionBundle(
            solution=solution,
            python_files=body.python_files,
            workflows=body.workflows,
            tables=body.tables,
        )
    )
    await ctx.db.commit()
    return SolutionDeployResponse(
        solution_id=solution_id,
        workflows_upserted=result.workflows_upserted,
        workflows_deleted=result.workflows_deleted,
        tables_upserted=result.tables_upserted,
        tables_deleted=result.tables_deleted,
    )


@router.post(
    "/{solution_id}/sync",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Auto-pull a git-connected install from its repo (admin only)",
)
async def sync_solution(solution_id: UUID, ctx: Context, user: CurrentSuperuser) -> dict:
    """Pull the connected install's repo ``main`` and deploy it (criterion 13).

    This is the auto-pull entry point (webhook/poll/manual). It is the ONLY
    writer for a connected install — the deploy endpoint is refused for it. For a
    disconnected install there is nothing to pull, so this is refused in turn.
    """
    solution = await ctx.db.get(SolutionORM, solution_id)
    if solution is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Solution not found")
    if not solution.git_connected:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This install is not git-connected; use deploy instead.",
        )

    from src.services.solutions.git_sync import sync as git_sync

    await git_sync(ctx.db, solution)
    await ctx.db.commit()
    return {"solution_id": str(solution_id), "status": "synced"}
