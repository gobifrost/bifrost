"""REST endpoints for private builder Solutions.

This is the first non-superuser Solution surface. ``/api/solutions`` stays
administrator-only; these routes let a user holding the ``solutions.build``
scope create and collaborate on private Solutions (2026-07-25
private-solution-builder spec, Work Package 1). Provider support access is
available only through the deliberate Builder support view.

Two gates apply to every route. The capability gate (``require_builder``) decides
whether the caller may use the builder at all and answers 403 when it denies. The
per-Solution gate is the central access service, and it answers **404** rather
than 403 when a caller has neither ownership, an explicit collaborator grant,
nor provider support authority. Support authority never widens ordinary
Solution catalogs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.authorization_scopes import PLATFORM_SUPERUSER_SCOPE
from src.core.db_deps import DbSession
from src.core.auth import CurrentActiveUser
from src.core.principal import UserPrincipal
from src.models.contracts.solution_builder import (
    BuilderProjectDTO,
    BuilderOrganizationTargetDTO,
    BuilderTargetsDTO,
    BuilderCollaboratorDTO,
    BuilderCollaboratorsList,
    BuilderCollaboratorUpsert,
    BuilderSessionDTO,
    BuilderSessionsList,
    BuilderTurnDTO,
    BuilderTurnsList,
    BuildJobPublic,
    CreateSessionRequest,
    GlobalOperationChangeDTO,
    GlobalOperationChangesListDTO,
    GlobalWorkspaceStatusDTO,
    GlobalWorkspaceValidationDTO,
    PrivateSolutionCreate,
    PrivateSolutionDTO,
    PrivateSolutionsList,
    RunTurnRequest,
    RunTurnResponse,
    RevisionDiffDTO,
    RevisionFileContentDTO,
    RevisionFilesList,
    SourceRevisionsList,
    UndoRequest,
)
from src.models.contracts.platform_jobs import PlatformJobAccepted
from src.models.contracts.solutions import (
    SolutionDeployJobStatus,
    SolutionRoleGrantCreate,
    SolutionRoleGrantPublic,
)
from src.models.orm.solution_build_jobs import SolutionBuildJob
from src.models.orm.solution_builder import (
    SolutionBuilderProject,
    SolutionGlobalOperationChange,
)
from src.models.orm.solution_deploy_jobs import SolutionDeployJob
from src.models.orm.solutions import Solution
from src.services.builder.agent_turns import (
    BuilderAgentTurnService,
    enqueue_builder_turn_deploy,
)
from src.services.builder.authorization_targets import (
    discover_builder_authorization_targets,
    organization_builder_tool_names,
)
from src.services.authorization import (
    AuthorizationBoundaryKind,
    AuthorizationContext,
    CurrentAuthorizationContext,
)
from src.services.builder.global_workspace import (
    GlobalWorkspaceConflict,
    GlobalWorkspaceError,
    GlobalWorkspaceInvalid,
    GlobalWorkspaceState,
    ensure_global_workspace,
    global_workspace_state,
    latest_global_workspace_source_apply,
    refresh_global_workspace,
    validate_global_workspace_revision,
)
from src.jobs.platform.builder_global_release import (
    BUILDER_GLOBAL_RELEASE_APPLY_DEFINITION,
    BUILDER_GLOBAL_RELEASE_ROLLBACK_DEFINITION,
    BuilderGlobalReleaseApplyPayload,
    BuilderGlobalReleaseRollbackPayload,
)
from src.services.builder.global_operation_changes import (
    GlobalOperationChangeError,
    discard_staged_global_operation_change,
    list_applied_global_operation_changes,
    list_staged_global_operation_changes,
    operation_change_applied_fingerprint,
    operation_change_review_fingerprint,
)
from src.services.builder.private_solutions import (
    CollaboratorNotEligible,
    CollaboratorNotFound,
    PrivateSolutionSlugTaken,
    RoleGrantNotEligible,
    RoleGrantNotFound,
    create_builder_session,
    create_private_solution,
    delete_private_solution,
    iter_revision_chunks,
    list_builder_sessions,
    list_builder_turns,
    list_collaborators,
    list_private_solutions,
    list_solution_role_grants,
    list_source_revisions,
    load_accessible_private_solution,
    load_revision_for_solution,
    private_solution_dto_context,
    remove_collaborator,
    remove_solution_role_grant,
    request_promotion,
    revision_download_filename,
    session_to_dto,
    to_dto,
    upsert_collaborator,
    upsert_solution_role_grant,
)
from src.services.builder.turns import (
    BuilderProjectMissing,
    BuilderTurnConflict,
    BuilderTurnService,
    WorkspaceInvalid,
)
from src.services.builder.fs_tools import WorkspaceViolation
from src.services.builder.revision_inspection import (
    RevisionArtifactMissing,
    diff_revisions,
    list_revision_files,
    read_revision_file,
)
from src.services.solutions.access import SolutionAction
from src.services.sandbox_runner_config import get_builder_readiness
from src.services.platform_jobs import (
    enqueue_platform_job,
    ensure_platform_job_notification,
    publish_platform_job_update,
)

router = APIRouter(prefix="/api/builder/solutions", tags=["builder"])

NOT_FOUND_DETAIL = "Solution not found"


@dataclass(frozen=True, slots=True)
class BuilderRequestContext:
    authorization: AuthorizationContext
    db: AsyncSession

    @property
    def user(self) -> UserPrincipal:
        return self.authorization.requester

    @property
    def org_id(self) -> UUID | None:
        boundary = self.authorization.selected_boundary
        if boundary.kind is AuthorizationBoundaryKind.ORGANIZATION:
            return boundary.organization_id
        return None


async def require_builder(
    authorization: CurrentAuthorizationContext,
    db: DbSession,
) -> BuilderRequestContext:
    """Resolve Builder access from the central boundary evaluator."""

    if authorization.requester.is_external or not (
        authorization.has_capability("builder.read")
        or authorization.has_capability("builder.execute")
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Builder access is required",
        )
    return BuilderRequestContext(authorization=authorization, db=db)


BuilderContext = Annotated[BuilderRequestContext, Depends(require_builder)]


@router.get(
    "/targets",
    response_model=BuilderTargetsDTO,
    summary="List the Builder boundaries available to the current person",
)
async def list_builder_targets(
    user: CurrentActiveUser,
    db: DbSession,
) -> BuilderTargetsDTO:
    """Discover selectable boundaries before the client chooses one.

    This endpoint returns only boundaries backed by a covering Role assignment.
    Every subsequent create, tool call, and turn rechecks the selected exact
    boundary; discovery is never accepted as execution authority.
    """

    if user.is_external:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Builder access is required",
        )
    targets = await discover_builder_authorization_targets(db, requester=user)
    if not targets.has_builder_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Builder access is required",
        )
    ai_configured, readiness = await get_builder_readiness(db)
    return BuilderTargetsDTO(
        organizations=[
            BuilderOrganizationTargetDTO(
                id=target.id,
                name=target.name,
                is_provider=target.is_provider,
                can_read=target.can_read,
                can_execute=target.can_execute,
                can_build_resources=target.can_build_resources,
            )
            for target in targets.organizations
        ],
        can_view_all=targets.can_view_all,
        can_open_global_workspace=targets.can_open_global_workspace,
        ai_configured=ai_configured,
        builder_ready=readiness.ready,
        builder_blockers=readiness.blockers,
        is_platform_admin=targets.is_platform_admin,
    )


def _can_support_solution(ctx: BuilderRequestContext) -> bool:
    return ctx.authorization.has_delegated_capability("builder.read")


def _require_platform_workspace(ctx: BuilderRequestContext) -> None:
    ctx.authorization.require("builder.execute")
    ctx.authorization.require("repository.readwrite")
    ctx.authorization.require_resource_boundary(None)


def _require_solution_lifecycle_execute(
    ctx: BuilderRequestContext,
    project: SolutionBuilderProject | None = None,
) -> None:
    """Require source-build and private-preview deployment capabilities."""

    if project is None or project.target_kind != "solution":
        return
    ctx.authorization.require("solutions.build.execute")
    ctx.authorization.require("solutions.deploy.execute")


def _require_solution_write_access(
    ctx: BuilderRequestContext,
    project: SolutionBuilderProject,
) -> None:
    if project.target_kind == "solution":
        ctx.authorization.require("solutions.readwrite")


async def _global_workspace_dto(
    db: AsyncSession,
    state: GlobalWorkspaceState | None,
) -> GlobalWorkspaceStatusDTO:
    if state is None:
        return GlobalWorkspaceStatusDTO(exists=False)
    current = state.project.current_revision_id
    deployed = state.project.deployed_revision_id
    pending_operation_count = int(
        await db.scalar(
            select(func.count(SolutionGlobalOperationChange.id)).where(
                SolutionGlobalOperationChange.solution_id == state.solution.id,
                SolutionGlobalOperationChange.state.in_(("staged", "failed")),
            )
        )
        or 0
    )
    return GlobalWorkspaceStatusDTO(
        exists=True,
        solution_id=state.solution.id,
        current_revision_id=current,
        deployed_revision_id=deployed,
        has_pending_proposal=(
            (current is not None and current != deployed)
            or pending_operation_count > 0
        ),
        pending_operation_count=pending_operation_count,
        can_rollback=state.can_rollback,
        last_applied_at=state.last_applied_at,
    )


def _raise_global_workspace_error(exc: GlobalWorkspaceError) -> NoReturn:
    if isinstance(exc, GlobalWorkspaceConflict):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    if isinstance(exc, GlobalWorkspaceInvalid):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": "Global workspace validation failed",
                "errors": exc.errors,
            },
        ) from exc
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=str(exc),
    ) from exc


def _operation_change_dto(result) -> GlobalOperationChangeDTO:
    return GlobalOperationChangeDTO(
        id=result.id,
        operation_id=result.operation_id,
        resource_type=result.resource_type,
        resource_id=result.resource_id,
        state=result.state,
        validation_errors=result.validation_errors,
        before_state=result.before_state,
        after_state=result.applied_state if result.state == "applied" else result.payload,
    )


async def _load_or_404(
    ctx: BuilderRequestContext, solution_id: UUID, action: SolutionAction
) -> tuple[Solution, SolutionBuilderProject]:
    """Load a private Solution the caller may act on, or raise 404."""
    if action is SolutionAction.VIEW:
        ctx.authorization.require("builder.read")
    else:
        ctx.authorization.require("builder.execute")
    solution = await ctx.db.get(Solution, solution_id)
    project = await ctx.db.get(SolutionBuilderProject, solution_id)
    if solution is None or project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND_DETAIL
        )
    if project.target_kind == "global_repo":
        try:
            _require_platform_workspace(ctx)
        except HTTPException:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=NOT_FOUND_DETAIL,
            ) from None
        return solution, project
    if project.target_kind == "organization":
        try:
            ctx.authorization.require_resource_boundary(solution.organization_id)
        except HTTPException:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=NOT_FOUND_DETAIL,
            ) from None
        return solution, project
    loaded = await load_accessible_private_solution(
        ctx.db,
        solution_id=solution_id,
        action=action,
        actor_user_id=ctx.user.user_id,
        is_platform_admin=ctx.authorization.has_capability("platform.superuser"),
        is_external=ctx.user.is_external,
        can_support=action is SolutionAction.VIEW and _can_support_solution(ctx),
        effective_role_ids=frozenset(ctx.authorization.role_ids),
    )
    if loaded is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND_DETAIL
        )
    project = loaded[1]
    if action is SolutionAction.VIEW:
        ctx.authorization.require("solutions.read")
    else:
        _require_solution_write_access(ctx, project)
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
    ctx.authorization.require("builder.execute")
    if body.target_kind == "solution":
        ctx.authorization.require("solutions.readwrite")
    elif not any(
        capability.endswith(".readwrite") or capability.endswith(".execute")
        for _tool_name, action_scopes in organization_builder_tool_names(
            ctx.authorization.effective_capabilities
        )
        for capability in action_scopes
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "An organization workspace requires at least one direct "
                "resource-authoring capability"
            ),
        )
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
            target_kind=body.target_kind,
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
async def list_solutions(
    user: CurrentActiveUser,
    db: DbSession,
    view: Literal["mine", "all"] = Query(default="mine"),
    organization_id: UUID | None = Query(default=None),
    owner_user_id: UUID | None = Query(default=None),
    search: str | None = Query(default=None, max_length=255),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> PrivateSolutionsList:
    if user.is_external:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Builder access is required",
        )
    targets = await discover_builder_authorization_targets(db, requester=user)
    if not targets.has_builder_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Builder access is required",
        )
    can_view_all = targets.can_view_all
    if view == "all" and not can_view_all:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Select Managed organizations with Builder support access",
        )
    solution_organization_ids = frozenset(
        target.id
        for target in targets.organizations
        if target.can_read
        and (
            PLATFORM_SUPERUSER_SCOPE in target.capabilities
            or "solutions.read" in target.capabilities
        )
    )
    workspace_organization_ids = frozenset(
        target.id for target in targets.organizations if target.can_read
    )
    page = await list_private_solutions(
        db,
        actor_user_id=user.user_id,
        is_external=user.is_external,
        view=view,
        can_support=can_view_all,
        organization_id=organization_id,
        owner_user_id=owner_user_id,
        search=search,
        limit=limit if view == "all" else None,
        offset=offset if view == "all" else 0,
        allowed_solution_organization_ids=solution_organization_ids,
        allowed_workspace_organization_ids=workspace_organization_ids,
    )
    items = [
        to_dto(
            row.solution,
            row.project,
            owner_name=row.owner_name,
            owner_email=row.owner_email,
            organization_name=row.organization_name,
            caller_access=(
                "owner"
                if row.solution.owner_user_id == user.user_id
                else "collaborator"
                if row.collaborator_access
                else "support"
            ),
            collaborator_access=row.collaborator_access,
        )
        for row in page.records
    ]
    ai_configured, readiness = await get_builder_readiness(db)
    return PrivateSolutionsList(
        solutions=items,
        total=page.total,
        limit=limit if view == "all" else None,
        offset=offset if view == "all" else 0,
        view=view,
        can_view_all=can_view_all,
        ai_configured=ai_configured,
        builder_ready=readiness.ready,
        builder_blockers=readiness.blockers,
        is_platform_admin=targets.is_platform_admin,
        can_open_global_workspace=targets.can_open_global_workspace,
    )


@router.get(
    "/global-workspace",
    response_model=GlobalWorkspaceStatusDTO,
    summary="Get the administrator global workspace state",
)
async def get_global_workspace(ctx: BuilderContext) -> GlobalWorkspaceStatusDTO:
    _require_platform_workspace(ctx)
    return await _global_workspace_dto(ctx.db, await global_workspace_state(ctx.db))


@router.post(
    "/global-workspace",
    response_model=GlobalWorkspaceStatusDTO,
    summary="Create or open the administrator global workspace",
)
async def create_global_workspace(ctx: BuilderContext) -> GlobalWorkspaceStatusDTO:
    _require_platform_workspace(ctx)
    state = await ensure_global_workspace(
        ctx.db,
        owner_user_id=ctx.user.user_id,
        organization_id=ctx.org_id,
    )
    return await _global_workspace_dto(ctx.db, state)


@router.post(
    "/global-workspace/refresh",
    response_model=GlobalWorkspaceStatusDTO,
    summary="Refresh the proposal baseline from live _repo",
)
async def refresh_global_workspace_route(
    ctx: BuilderContext,
) -> GlobalWorkspaceStatusDTO:
    _require_platform_workspace(ctx)
    state = await global_workspace_state(ctx.db)
    if state is None:
        raise HTTPException(status_code=404, detail="Global workspace not found")
    try:
        refreshed = await refresh_global_workspace(
            ctx.db,
            solution_id=state.solution.id,
            requested_by=ctx.user.user_id,
        )
    except GlobalWorkspaceError as exc:
        _raise_global_workspace_error(exc)
    return await _global_workspace_dto(ctx.db, refreshed)


@router.post(
    "/global-workspace/validate",
    response_model=GlobalWorkspaceValidationDTO,
    summary="Validate the current global workspace proposal without executing it",
)
async def validate_global_workspace_route(
    ctx: BuilderContext,
) -> GlobalWorkspaceValidationDTO:
    _require_platform_workspace(ctx)
    state = await global_workspace_state(ctx.db)
    revision_id = state.project.current_revision_id if state else None
    if state is None or revision_id is None:
        raise HTTPException(status_code=404, detail="Global workspace not found")
    try:
        errors = await validate_global_workspace_revision(
            ctx.db,
            solution_id=state.solution.id,
            revision_id=revision_id,
        )
    except GlobalWorkspaceError as exc:
        _raise_global_workspace_error(exc)
    return GlobalWorkspaceValidationDTO(
        revision_id=revision_id,
        valid=not errors,
        errors=errors,
    )


@router.get(
    "/global-workspace/operations",
    response_model=GlobalOperationChangesListDTO,
    summary="List staged Global operation changes for human review",
)
async def list_global_workspace_operations(
    ctx: BuilderContext,
) -> GlobalOperationChangesListDTO:
    _require_platform_workspace(ctx)
    state = await global_workspace_state(ctx.db)
    if state is None:
        raise HTTPException(status_code=404, detail="Global workspace not found")
    changes = await list_staged_global_operation_changes(
        ctx.db,
        solution_id=state.solution.id,
    )
    rollbackable_changes = await list_applied_global_operation_changes(
        ctx.db,
        solution_id=state.solution.id,
    )
    return GlobalOperationChangesListDTO(
        changes=[_operation_change_dto(change) for change in changes],
        rollbackable_changes=[
            _operation_change_dto(change) for change in rollbackable_changes
        ],
    )


@router.delete(
    "/global-workspace/operations/{change_id}",
    response_model=GlobalOperationChangeDTO,
    summary="Discard one staged Global operation change",
)
async def discard_global_workspace_operation(
    change_id: UUID,
    ctx: BuilderContext,
) -> GlobalOperationChangeDTO:
    _require_platform_workspace(ctx)
    state = await global_workspace_state(ctx.db)
    if state is None:
        raise HTTPException(status_code=404, detail="Global workspace not found")
    try:
        change = await discard_staged_global_operation_change(
            ctx.db,
            solution_id=state.solution.id,
            change_id=change_id,
            requested_by=ctx.user.user_id,
        )
    except GlobalOperationChangeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _operation_change_dto(change)


@router.post(
    "/global-workspace/apply",
    response_model=PlatformJobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue the reviewed global workspace release",
)
async def apply_global_workspace_route(
    response: Response,
    ctx: BuilderContext,
) -> PlatformJobAccepted:
    _require_platform_workspace(ctx)
    state = await global_workspace_state(ctx.db)
    if state is None:
        raise HTTPException(status_code=404, detail="Global workspace not found")
    project = state.project
    if project.current_revision_id is None or project.deployed_revision_id is None:
        raise HTTPException(status_code=409, detail="Global workspace revisions are missing")
    source_changed = project.current_revision_id != project.deployed_revision_id
    changes = await list_staged_global_operation_changes(
        ctx.db,
        solution_id=state.solution.id,
    )
    if any(change.state != "staged" for change in changes):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Resolve failed Global operation changes before applying",
        )
    errors = [error for change in changes for error in change.validation_errors]
    if errors:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": "Global operation changes are invalid",
                "errors": errors,
            },
        )
    if not source_changed and not changes:
        raise HTTPException(status_code=409, detail="There is no pending release to apply")
    actor = ctx.user
    job, reused = await enqueue_platform_job(
        ctx.db,
        BUILDER_GLOBAL_RELEASE_APPLY_DEFINITION,
        BuilderGlobalReleaseApplyPayload(
            solution_id=state.solution.id,
            from_revision_id=project.deployed_revision_id if source_changed else None,
            to_revision_id=project.current_revision_id if source_changed else None,
            approved_operation_changes={
                change.id: operation_change_review_fingerprint(change)
                for change in changes
            },
        ),
        dedupe_key=str(state.solution.id),
        resource_lock_key=f"builder-global-release:{state.solution.id}",
        organization_id=None,
        requested_by_user_id=actor.user_id,
        requested_by_email=actor.email,
        requested_by_name=actor.name or actor.email or "Unknown",
        resource_type="solution",
        resource_id=str(state.solution.id),
        title="Applying Global Builder release",
        action_url=f"/solutions/{state.solution.id}/builder?boundary=platform",
    )
    if reused and job.requested_by_user_id != str(actor.user_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A Global release apply is already in progress",
        )
    if job.notification_id is None:
        await ensure_platform_job_notification(ctx.db, job)
    await ctx.db.commit()
    await ctx.db.refresh(job)
    await publish_platform_job_update(job)
    response.headers["Location"] = f"/api/platform-jobs/{job.id}"
    return PlatformJobAccepted(
        job_id=job.id,
        notification_id=job.notification_id,
        status=job.status,
        reused=reused,
    )


@router.post(
    "/global-workspace/rollback",
    response_model=PlatformJobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue rollback of the latest global workspace release",
)
async def rollback_global_workspace_route(
    response: Response,
    ctx: BuilderContext,
) -> PlatformJobAccepted:
    _require_platform_workspace(ctx)
    state = await global_workspace_state(ctx.db)
    if state is None:
        raise HTTPException(status_code=404, detail="Global workspace not found")
    if state.project.current_revision_id != state.project.deployed_revision_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Apply or discard the pending proposal before rolling back",
        )
    latest_source = await latest_global_workspace_source_apply(ctx.db, state.solution.id)
    latest_operations = await list_applied_global_operation_changes(
        ctx.db,
        solution_id=state.solution.id,
    )
    source_apply_id: UUID | None = None
    operation_changes = []
    if latest_source is None and not latest_operations:
        raise HTTPException(
            status_code=409,
            detail="No applied Global release is available to roll back",
        )
    if latest_source is not None and not latest_operations:
        source_apply_id = latest_source.id
    elif latest_source is None:
        operation_changes = latest_operations
    else:
        operation_apply_job_id = latest_operations[0].apply_job_id
        operation_applied_at = max(
            change.applied_at
            for change in latest_operations
            if change.applied_at is not None
        )
        if (
            latest_source.apply_job_id is not None
            and latest_source.apply_job_id == operation_apply_job_id
            and all(
                change.apply_job_id == latest_source.apply_job_id
                for change in latest_operations
            )
        ):
            source_apply_id = latest_source.id
            operation_changes = latest_operations
        elif latest_source.applied_at >= operation_applied_at:
            source_apply_id = latest_source.id
        else:
            operation_changes = latest_operations
    actor = ctx.user
    job, reused = await enqueue_platform_job(
        ctx.db,
        BUILDER_GLOBAL_RELEASE_ROLLBACK_DEFINITION,
        BuilderGlobalReleaseRollbackPayload(
            solution_id=state.solution.id,
            source_apply_id=source_apply_id,
            approved_operation_changes={
                change.id: operation_change_applied_fingerprint(change)
                for change in operation_changes
            },
        ),
        dedupe_key=str(state.solution.id),
        resource_lock_key=f"builder-global-release:{state.solution.id}",
        organization_id=None,
        requested_by_user_id=actor.user_id,
        requested_by_email=actor.email,
        requested_by_name=actor.name or actor.email or "Unknown",
        resource_type="solution",
        resource_id=str(state.solution.id),
        title="Rolling back Global Builder release",
        action_url=f"/solutions/{state.solution.id}/builder?boundary=platform",
    )
    if reused and job.requested_by_user_id != str(actor.user_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A Global release rollback is already in progress",
        )
    if job.notification_id is None:
        await ensure_platform_job_notification(ctx.db, job)
    await ctx.db.commit()
    await ctx.db.refresh(job)
    await publish_platform_job_update(job)
    response.headers["Location"] = f"/api/platform-jobs/{job.id}"
    return PlatformJobAccepted(
        job_id=job.id,
        notification_id=job.notification_id,
        status=job.status,
        reused=reused,
    )


@router.get(
    "/{solution_id}",
    response_model=PrivateSolutionDTO,
    summary="Get one authorized private Builder Solution",
)
async def get_solution(solution_id: UUID, ctx: BuilderContext) -> PrivateSolutionDTO:
    solution, project = await _load_or_404(ctx, solution_id, SolutionAction.VIEW)
    dto_context = await private_solution_dto_context(
        ctx.db,
        solution=solution,
        actor_user_id=ctx.user.user_id,
        can_support=_can_support_solution(ctx),
        effective_role_ids=frozenset(ctx.authorization.role_ids),
    )
    return to_dto(
        solution,
        project,
        owner_name=dto_context.owner_name,
        owner_email=dto_context.owner_email,
        organization_name=dto_context.organization_name,
        caller_access=dto_context.caller_access,
        collaborator_access=dto_context.collaborator_access,
    )


@router.get(
    "/{solution_id}/collaborators",
    response_model=BuilderCollaboratorsList,
    summary="List explicit collaborators on a private Builder Solution",
)
async def get_collaborators(
    solution_id: UUID,
    ctx: BuilderContext,
) -> BuilderCollaboratorsList:
    await _load_or_404(ctx, solution_id, SolutionAction.VIEW)
    collaborators = await list_collaborators(ctx.db, solution_id=solution_id)
    return BuilderCollaboratorsList(
        collaborators=collaborators,
        total=len(collaborators),
    )


@router.put(
    "/{solution_id}/collaborators",
    response_model=BuilderCollaboratorDTO,
    summary="Invite or update one Builder collaborator",
)
async def put_collaborator(
    solution_id: UUID,
    body: BuilderCollaboratorUpsert,
    ctx: BuilderContext,
) -> BuilderCollaboratorDTO:
    solution, _project = await _load_or_404(
        ctx,
        solution_id,
        SolutionAction.MANAGE,
    )
    try:
        return await upsert_collaborator(
            ctx.db,
            solution=solution,
            email=body.email,
            access=body.access,
            invited_by=ctx.user.user_id,
        )
    except CollaboratorNotEligible as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc


@router.delete(
    "/{solution_id}/collaborators/{collaborator_user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove one Builder collaborator",
)
async def delete_collaborator(
    solution_id: UUID,
    collaborator_user_id: UUID,
    ctx: BuilderContext,
) -> None:
    await _load_or_404(ctx, solution_id, SolutionAction.MANAGE)
    try:
        await remove_collaborator(
            ctx.db,
            solution_id=solution_id,
            collaborator_user_id=collaborator_user_id,
        )
    except CollaboratorNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Collaborator not found",
        ) from exc


@router.get(
    "/{solution_id}/role-grants",
    response_model=list[SolutionRoleGrantPublic],
    summary="List Role grants on a private Builder Solution",
)
async def get_solution_role_grants(
    solution_id: UUID,
    ctx: BuilderContext,
) -> list[SolutionRoleGrantPublic]:
    await _load_or_404(ctx, solution_id, SolutionAction.VIEW)
    ctx.authorization.require("roles.read")
    grants = await list_solution_role_grants(ctx.db, solution_id=solution_id)
    return [SolutionRoleGrantPublic.model_validate(grant) for grant in grants]


@router.put(
    "/{solution_id}/role-grants",
    response_model=SolutionRoleGrantPublic,
    summary="Create or update one Role grant on a private Builder Solution",
)
async def put_solution_role_grant(
    solution_id: UUID,
    body: SolutionRoleGrantCreate,
    ctx: BuilderContext,
) -> SolutionRoleGrantPublic:
    if body.solution_id != solution_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Body solution_id must match the route",
        )
    await _load_or_404(ctx, solution_id, SolutionAction.MANAGE)
    ctx.authorization.require("roles.read")
    try:
        grant = await upsert_solution_role_grant(
            ctx.db,
            solution_id=solution_id,
            role_id=body.role_id,
            access=body.access,
            granted_by_user_id=ctx.user.user_id,
        )
    except RoleGrantNotEligible as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    return SolutionRoleGrantPublic.model_validate(grant)


@router.delete(
    "/{solution_id}/role-grants/{role_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove one Role grant from a private Builder Solution",
)
async def delete_solution_role_grant(
    solution_id: UUID,
    role_id: UUID,
    ctx: BuilderContext,
) -> None:
    await _load_or_404(ctx, solution_id, SolutionAction.MANAGE)
    ctx.authorization.require("roles.read")
    try:
        await remove_solution_role_grant(
            ctx.db,
            solution_id=solution_id,
            role_id=role_id,
        )
    except RoleGrantNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Role grant not found",
        ) from exc


@router.delete(
    "/{solution_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a private builder Solution and everything it owns (owner only)",
)
async def delete_solution(solution_id: UUID, ctx: BuilderContext) -> None:
    solution, project = await _load_or_404(ctx, solution_id, SolutionAction.MANAGE)
    if project.target_kind == "global_repo":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The global workspace cannot be deleted from the Builder",
        )
    await delete_private_solution(ctx.db, solution)


@router.post(
    "/{solution_id}/promotion-request",
    response_model=BuilderProjectDTO,
    summary="Ask a platform administrator to promote this Solution",
)
async def create_promotion_request(
    solution_id: UUID, ctx: BuilderContext
) -> BuilderProjectDTO:
    _solution, project = await _load_or_404(ctx, solution_id, SolutionAction.MANAGE)
    try:
        updated = await request_promotion(
            ctx.db,
            project,
            requested_by=ctx.user.user_id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    return BuilderProjectDTO.model_validate(updated)


@router.post(
    "/{solution_id}/sessions",
    response_model=BuilderSessionDTO,
    status_code=status.HTTP_201_CREATED,
    summary="Open an attributable Builder chat session for this Solution",
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
    sessions = await list_builder_sessions(ctx.db, solution_id=solution_id)
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
    summary="Download one authorized source revision as a zip",
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
        iter_revision_chunks(ctx.db, solution_id=solution_id, revision_id=revision.id),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/{solution_id}/revisions/{revision_id}/files",
    response_model=RevisionFilesList,
    summary="List files in one immutable source revision",
)
async def get_revision_files(
    solution_id: UUID,
    revision_id: UUID,
    ctx: BuilderContext,
) -> RevisionFilesList:
    await _load_or_404(ctx, solution_id, SolutionAction.VIEW)
    revision = await load_revision_for_solution(
        ctx.db, solution_id=solution_id, revision_id=revision_id
    )
    if revision is None:
        raise HTTPException(status_code=404, detail="Revision not found")
    try:
        return await list_revision_files(solution_id, revision_id)
    except RevisionArtifactMissing as exc:
        raise HTTPException(
            status_code=404, detail="Revision source is missing"
        ) from exc
    except WorkspaceViolation as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get(
    "/{solution_id}/revisions/{revision_id}/file",
    response_model=RevisionFileContentDTO,
    summary="Read one bounded text file from a source revision",
)
async def get_revision_file(
    solution_id: UUID,
    revision_id: UUID,
    ctx: BuilderContext,
    path: str = Query(min_length=1, max_length=2048),
) -> RevisionFileContentDTO:
    await _load_or_404(ctx, solution_id, SolutionAction.VIEW)
    revision = await load_revision_for_solution(
        ctx.db, solution_id=solution_id, revision_id=revision_id
    )
    if revision is None:
        raise HTTPException(status_code=404, detail="Revision not found")
    try:
        return await read_revision_file(solution_id, revision_id, path)
    except RevisionArtifactMissing as exc:
        raise HTTPException(
            status_code=404, detail="Revision source is missing"
        ) from exc
    except WorkspaceViolation as exc:
        detail = "File not found" if "file not found" in str(exc) else str(exc)
        raise HTTPException(status_code=404, detail=detail) from exc


@router.get(
    "/{solution_id}/revisions/{revision_id}/diff",
    response_model=RevisionDiffDTO,
    summary="Diff one source revision against its parent or another revision",
)
async def get_revision_diff(
    solution_id: UUID,
    revision_id: UUID,
    ctx: BuilderContext,
    against_revision_id: UUID | None = None,
) -> RevisionDiffDTO:
    await _load_or_404(ctx, solution_id, SolutionAction.VIEW)
    revision = await load_revision_for_solution(
        ctx.db, solution_id=solution_id, revision_id=revision_id
    )
    if revision is None:
        raise HTTPException(status_code=404, detail="Revision not found")
    against = against_revision_id or revision.parent_revision_id
    if against is not None:
        compared = await load_revision_for_solution(
            ctx.db, solution_id=solution_id, revision_id=against
        )
        if compared is None:
            raise HTTPException(status_code=404, detail="Compared revision not found")
    try:
        return await diff_revisions(solution_id, revision_id, against)
    except RevisionArtifactMissing as exc:
        raise HTTPException(
            status_code=404, detail="Revision source is missing"
        ) from exc
    except WorkspaceViolation as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post(
    "/{solution_id}/undo",
    response_model=BuilderTurnDTO,
    summary="Restore an earlier revision as a new revision (owner only)",
)
async def undo_to_revision(
    solution_id: UUID, body: UndoRequest, ctx: BuilderContext
) -> BuilderTurnDTO:
    _solution, project = await _load_or_404(ctx, solution_id, SolutionAction.EDIT)
    _require_solution_lifecycle_execute(ctx, project)
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
    if (
        turn.output_revision_id is not None
        and turn.output_revision_id != turn.base_revision_id
    ):
        if project.target_kind != "solution":
            await ctx.db.commit()
        else:
            await enqueue_builder_turn_deploy(
                ctx.db,
                solution_id,
                turn=turn,
                revision_id=turn.output_revision_id,
            )
    else:
        await ctx.db.commit()
    return BuilderTurnDTO.model_validate(turn)


@router.post(
    "/{solution_id}/turns",
    response_model=RunTurnResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue one durable builder agent turn (owner only)",
)
async def run_turn(
    solution_id: UUID, body: RunTurnRequest, ctx: BuilderContext
) -> RunTurnResponse:
    """Persist the message and queue an isolated Builder turn.

    The response returns as soon as the durable PlatformJob exists. Progress,
    completion, and restored conversation state arrive through the shared
    PlatformJob notification transport.
    """
    _solution, project = await _load_or_404(ctx, solution_id, SolutionAction.EDIT)
    _require_solution_lifecycle_execute(ctx, project)
    _ai_configured, readiness = await get_builder_readiness(ctx.db)
    if not readiness.ready:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "builder_not_ready",
                "message": "Builder has not been enabled by a platform administrator",
                "blockers": [
                    blocker.model_dump(mode="json") for blocker in readiness.blockers
                ],
            },
        )
    service = BuilderAgentTurnService(ctx.db)
    try:
        queued = await service.enqueue_agent_turn(
            solution_id,
            session_id=body.session_id,
            requested_by=ctx.user.user_id,
            user_message=body.message,
            attachment_ids=body.attachment_ids,
            resume_from_turn_id=body.resume_from_turn_id,
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
    return RunTurnResponse(
        turn=BuilderTurnDTO.model_validate(queued.turn),
        job_id=queued.platform_job.id,
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


@router.get(
    "/{solution_id}/build-jobs/{job_id}",
    response_model=BuildJobPublic,
    summary="Get one owner-scoped builder build job",
)
async def get_build_job(
    solution_id: UUID,
    job_id: UUID,
    ctx: BuilderContext,
) -> BuildJobPublic:
    await _load_or_404(ctx, solution_id, SolutionAction.VIEW)
    job = await ctx.db.get(SolutionBuildJob, job_id)
    if job is None or job.solution_id != solution_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Build job not found",
        )
    return BuildJobPublic.model_validate(job)


@router.get(
    "/{solution_id}/deploy-jobs/{job_id}",
    response_model=SolutionDeployJobStatus,
    summary="Get one owner-scoped builder deploy job",
)
async def get_deploy_job(
    solution_id: UUID,
    job_id: UUID,
    ctx: BuilderContext,
) -> SolutionDeployJobStatus:
    await _load_or_404(ctx, solution_id, SolutionAction.VIEW)
    job = await ctx.db.get(SolutionDeployJob, job_id)
    if job is None or job.install_id != solution_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Deploy job not found",
        )
    return SolutionDeployJobStatus.model_validate(job)
