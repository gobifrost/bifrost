"""REST endpoints for private builder Solutions.

This is the first non-superuser Solution surface. ``/api/solutions`` stays
administrator-only; these owner-scoped routes let a user holding the
``solutions.build`` permission create and manage Solutions that are private to
them (2026-07-25 private-solution-builder spec, Work Package 1).

Two gates apply to every route. The capability gate (``require_builder``) decides
whether the caller may use the builder at all and answers 403 when it denies. The
per-Solution gate is the central access service, and it answers **404** rather
than 403 — a private Solution is invisible to everyone but its owner, including
platform admins.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from src.core.auth import Context, ExecutionContext
from src.models.contracts.solution_builder import (
    BuilderProjectDTO,
    BuilderSessionDTO,
    BuilderSessionsList,
    BuilderTurnDTO,
    BuilderTurnsList,
    CreateSessionRequest,
    PrivateSolutionCreate,
    PrivateSolutionDTO,
    PrivateSolutionsList,
    RunTurnRequest,
    RunTurnResponse,
    SourceRevisionsList,
    UndoRequest,
)
from src.models.orm.solution_builder import SolutionBuilderProject
from src.models.orm.solutions import Solution
from src.models.orm.users import Role, UserRole
from src.services.builder.agent_turns import BuilderAgentTurnService
from src.services.builder.model_gateway import BuilderModelUnavailable
from src.services.builder.private_solutions import (
    PrivateSolutionSlugTaken,
    create_builder_session,
    create_private_solution,
    delete_private_solution,
    iter_revision_chunks,
    list_builder_sessions,
    list_builder_turns,
    list_private_solutions,
    list_source_revisions,
    load_accessible_private_solution,
    load_revision_for_solution,
    request_promotion,
    revision_download_filename,
    session_to_dto,
    to_dto,
)
from src.services.builder.turns import (
    BuilderProjectMissing,
    BuilderTurnConflict,
    BuilderTurnService,
    WorkspaceInvalid,
)
from src.services.solutions.access import SolutionAction
from src.services.solutions.builder_authz import can_build

router = APIRouter(prefix="/api/builder/solutions", tags=["builder"])

NOT_FOUND_DETAIL = "Solution not found"


async def require_builder(ctx: Context) -> ExecutionContext:
    """Gate every builder route on the ``solutions.build`` capability.

    Loads the caller's ``Role.permissions`` rows and defers the decision to the
    single authorization service, so no route interprets the permission key
    itself.
    """
    permissions = (
        (
            await ctx.db.execute(
                select(Role.permissions)
                .join(UserRole, UserRole.role_id == Role.id)
                .where(UserRole.user_id == ctx.user.user_id)
            )
        )
        .scalars()
        .all()
    )
    if not can_build(
        is_platform_admin=ctx.user.is_platform_admin,
        is_external=ctx.user.is_external,
        role_permissions=[p for p in permissions if p],
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The solutions.build permission is required to use the builder",
        )
    return ctx


BuilderContext = Annotated[ExecutionContext, Depends(require_builder)]


async def _load_or_404(
    ctx: ExecutionContext, solution_id: UUID, action: SolutionAction
) -> tuple[Solution, SolutionBuilderProject]:
    """Load a private Solution the caller may act on, or raise 404."""
    loaded = await load_accessible_private_solution(
        ctx.db,
        solution_id=solution_id,
        action=action,
        actor_user_id=ctx.user.user_id,
        is_platform_admin=ctx.user.is_platform_admin,
        is_external=ctx.user.is_external,
    )
    if loaded is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND_DETAIL
        )
    return loaded


@router.post(
    "",
    response_model=PrivateSolutionDTO,
    status_code=status.HTTP_201_CREATED,
    summary="Create a private builder Solution owned by the caller",
)
async def create_solution(
    body: PrivateSolutionCreate, ctx: BuilderContext
) -> PrivateSolutionDTO:
    if ctx.org_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A private Solution requires an organization (caller has none)",
        )
    try:
        solution, project = await create_private_solution(
            ctx.db,
            slug=body.slug,
            name=body.name,
            owner_user_id=ctx.user.user_id,
            organization_id=ctx.org_id,
        )
    except PrivateSolutionSlugTaken as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"You already have a private Solution with slug '{body.slug}'",
        ) from exc
    return to_dto(solution, project)


@router.get(
    "",
    response_model=PrivateSolutionsList,
    summary="List the caller's own private builder Solutions",
)
async def list_solutions(ctx: BuilderContext) -> PrivateSolutionsList:
    rows = await list_private_solutions(
        ctx.db,
        owner_user_id=ctx.user.user_id,
        is_external=ctx.user.is_external,
    )
    items = [to_dto(solution, project) for solution, project in rows]
    return PrivateSolutionsList(solutions=items, total=len(items))


@router.get(
    "/{solution_id}",
    response_model=PrivateSolutionDTO,
    summary="Get one private builder Solution (owner only)",
)
async def get_solution(solution_id: UUID, ctx: BuilderContext) -> PrivateSolutionDTO:
    solution, project = await _load_or_404(ctx, solution_id, SolutionAction.VIEW)
    return to_dto(solution, project)


@router.delete(
    "/{solution_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a private builder Solution and everything it owns (owner only)",
)
async def delete_solution(solution_id: UUID, ctx: BuilderContext) -> None:
    solution, _project = await _load_or_404(ctx, solution_id, SolutionAction.EDIT)
    await delete_private_solution(ctx.db, solution)


@router.post(
    "/{solution_id}/promotion-request",
    response_model=BuilderProjectDTO,
    summary="Ask a platform administrator to promote this Solution (owner only)",
)
async def create_promotion_request(
    solution_id: UUID, ctx: BuilderContext
) -> BuilderProjectDTO:
    _solution, project = await _load_or_404(ctx, solution_id, SolutionAction.EDIT)
    updated = await request_promotion(ctx.db, project)
    return BuilderProjectDTO.model_validate(updated)


@router.post(
    "/{solution_id}/sessions",
    response_model=BuilderSessionDTO,
    status_code=status.HTTP_201_CREATED,
    summary="Open a builder chat session against this Solution (owner only)",
)
async def create_session(
    solution_id: UUID, body: CreateSessionRequest, ctx: BuilderContext
) -> BuilderSessionDTO:
    await _load_or_404(ctx, solution_id, SolutionAction.EDIT)
    session = await create_builder_session(
        ctx.db,
        solution_id=solution_id,
        user_id=ctx.user.user_id,
        title=body.title,
    )
    return session_to_dto(session)


@router.get(
    "/{solution_id}/sessions",
    response_model=BuilderSessionsList,
    summary="List the caller's builder chat sessions for this Solution",
)
async def list_sessions(solution_id: UUID, ctx: BuilderContext) -> BuilderSessionsList:
    await _load_or_404(ctx, solution_id, SolutionAction.VIEW)
    sessions = await list_builder_sessions(
        ctx.db, solution_id=solution_id, user_id=ctx.user.user_id
    )
    items = [session_to_dto(session) for session in sessions]
    return BuilderSessionsList(sessions=items, total=len(items))


@router.get(
    "/{solution_id}/revisions",
    response_model=SourceRevisionsList,
    summary="List this Solution's source revisions, newest first",
)
async def list_revisions(solution_id: UUID, ctx: BuilderContext) -> SourceRevisionsList:
    _solution, project = await _load_or_404(ctx, solution_id, SolutionAction.VIEW)
    items = await list_source_revisions(
        ctx.db, solution_id=solution_id, project=project
    )
    return SourceRevisionsList(revisions=items, total=len(items))


@router.get(
    "/{solution_id}/revisions/{revision_id}/download",
    summary="Download one source revision as a zip (owner only)",
)
async def download_revision(
    solution_id: UUID, revision_id: UUID, ctx: BuilderContext
) -> StreamingResponse:
    solution, _project = await _load_or_404(ctx, solution_id, SolutionAction.VIEW)
    revision = await load_revision_for_solution(
        ctx.db, solution_id=solution_id, revision_id=revision_id
    )
    if revision is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Revision not found"
        )

    filename = revision_download_filename(solution.slug, revision.id)
    return StreamingResponse(
        iter_revision_chunks(
            ctx.db, solution_id=solution_id, revision_id=revision.id
        ),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post(
    "/{solution_id}/undo",
    response_model=BuilderTurnDTO,
    summary="Restore an earlier revision as a new revision (owner only)",
)
async def undo_to_revision(
    solution_id: UUID, body: UndoRequest, ctx: BuilderContext
) -> BuilderTurnDTO:
    await _load_or_404(ctx, solution_id, SolutionAction.EDIT)
    service = BuilderTurnService(ctx.db)
    try:
        turn = await service.undo(
            solution_id,
            session_id=body.session_id,
            requested_by=ctx.user.user_id,
            to_revision_id=body.to_revision_id,
        )
    except BuilderTurnConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Another turn or deploy is already writing this Solution",
        ) from exc
    except BuilderProjectMissing as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except WorkspaceInvalid as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    await ctx.db.commit()
    return BuilderTurnDTO.model_validate(turn)


@router.post(
    "/{solution_id}/turns",
    response_model=RunTurnResponse,
    summary="Run one builder agent turn against this Solution (owner only)",
)
async def run_turn(
    solution_id: UUID, body: RunTurnRequest, ctx: BuilderContext
) -> RunTurnResponse:
    """Send a message to the builder agent and apply whatever it changes.

    The turn holds the per-Solution write lock for its duration, so a second
    concurrent turn is refused with 409 rather than queued. A model or tool
    failure still records a failed turn row and leaves the current revision
    pointer untouched.
    """
    await _load_or_404(ctx, solution_id, SolutionAction.EDIT)
    service = BuilderAgentTurnService(ctx.db)
    try:
        outcome = await service.run_agent_turn(
            solution_id,
            session_id=body.session_id,
            requested_by=ctx.user.user_id,
            user_message=body.message,
        )
    except BuilderTurnConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Another turn or deploy is already writing this Solution",
        ) from exc
    except BuilderProjectMissing as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except BuilderModelUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"The builder model is not available: {exc}",
        ) from exc
    await ctx.db.commit()
    return RunTurnResponse(
        turn=BuilderTurnDTO.model_validate(outcome.turn),
        final_text=outcome.final_text,
        tool_call_count=outcome.tool_call_count,
        revision_created=outcome.revision_created,
    )


@router.get(
    "/{solution_id}/turns",
    response_model=BuilderTurnsList,
    summary="List this Solution's builder turns, newest first",
)
async def list_turns(solution_id: UUID, ctx: BuilderContext) -> BuilderTurnsList:
    await _load_or_404(ctx, solution_id, SolutionAction.VIEW)
    items = await list_builder_turns(ctx.db, solution_id=solution_id)
    return BuilderTurnsList(turns=items, total=len(items))
