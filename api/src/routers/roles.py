"""
Roles Router

Manage roles for organization users.
- Assign users to roles (UserRoles)
- Assign forms to roles (FormRoles)
"""

import logging
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import select, delete

from src.core.auth import CurrentActiveUser, CurrentSuperuser
from src.core.db_deps import DbSession
from src.models.contracts.role_assignments import (
    RolePermissionsResponse,
    RolePermissionsUpdate,
)
from src.services.authorization.enforce import (
    GLOBAL,
    authorize_operation,
    load_caller,
    operation_reach,
)
from src.core.log_safety import log_safe
from src.services.solutions.guard import (
    assert_entity_id_not_solution_managed,
)
from src.services.audit import emit_audit
from src.models import (
    FormRole as FormRoleORM,
    AgentRole as AgentRoleORM,
    Form as FormORM,
    User as UserORM,
    Agent as AgentORM,
)
from src.models.orm.applications import Application as ApplicationORM
from src.models.orm.app_roles import AppRole as AppRoleORM
from src.models.orm.workflows import Workflow as WorkflowORM
from src.models.orm.workflow_roles import WorkflowRole as WorkflowRoleORM
from src.models import (
    RoleCreate,
    RolePublic,
    RoleUpdate,
    RoleUsersResponse,
    RoleFormsResponse,
    RoleAgentsResponse,
    RoleAppsResponse,
    RoleWorkflowsResponse,
    AssignUsersToRoleRequest,
    AssignFormsToRoleRequest,
    AssignAgentsToRoleRequest,
    AssignAppsToRoleRequest,
    AssignWorkflowsToRoleRequest,
    UnassignUsersFromRoleRequest,
    UnassignFormsFromRoleRequest,
    UnassignAgentsFromRoleRequest,
    UnassignAppsFromRoleRequest,
    UnassignWorkflowsFromRoleRequest,
)

# Import cache invalidation
from src.core.cache import invalidate_role_forms
from src.services.operation_catalog import operation_route

# Agent cache invalidation (optional, may not exist yet)
try:
    from src.core.cache import invalidate_role_agents

    AGENT_CACHE_INVALIDATION_AVAILABLE = True
except ImportError:
    AGENT_CACHE_INVALIDATION_AVAILABLE = False
    invalidate_role_agents = None  # type: ignore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/roles", tags=["Roles"])


@router.get(
    "",
    response_model=list[RolePublic],
    summary="List all roles",
    description="Get all roles",
**operation_route("roles.list"))
async def list_roles(
    user: CurrentActiveUser,
    db: DbSession,
    response: Response,
    search: str | None = Query(None, description="Search role name or description"),
    include_builtin: bool = Query(
        False,
        description="Include the builtin roles (Platform Admin, User, Platform Operator, Secrets Reader)",
    ),
    sort_by: Literal["name", "created"] = Query("name"),
    sort_direction: Literal["asc", "desc"] = Query("asc"),
    limit: int | None = Query(
        None,
        ge=1,
        le=100,
        description="Maximum rows to return; omit for the legacy unbounded response",
    ),
    offset: int = Query(0, ge=0, description="Rows to skip when limit is set"),
) -> list[RolePublic]:
    """List all roles with inline consumer counts (users/forms/agents/apps/workflows/knowledge).

    Consumer counts span every organization, so only a Platform Admin gets them.
    """
    from shared.sdk_roles import list_roles as list_roles_service

    caller = await authorize_operation(db, user, "roles.list", GLOBAL)
    items, total = await list_roles_service(
        db,
        search=search,
        sort_by=sort_by,
        sort_direction=sort_direction,
        limit=limit,
        offset=offset,
        include_builtin=include_builtin,
        include_counts=caller.is_platform_admin,
    )
    response.headers["X-Total-Count"] = str(total)
    return items


@router.post(
    "",
    response_model=RolePublic,
    status_code=status.HTTP_201_CREATED,
    summary="Create a role",
    description="Create a new role",
**operation_route("roles.create"))
async def create_role(
    request: RoleCreate,
    user: CurrentActiveUser,
    db: DbSession,
) -> RolePublic:
    """Create a new role."""
    from shared.sdk_roles import RoleServiceError, create_role as create_role_service

    await authorize_operation(db, user, "roles.create", GLOBAL)

    try:
        return await create_role_service(
            db,
            name=request.name,
            description=request.description,
            actor_email=user.email,
        )
    except RoleServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from None


@router.get(
    "/{role_id}",
    response_model=RolePublic,
    summary="Get a role",
    description="Get a role by ID",
**operation_route("roles.get"))
async def get_role(
    role_id: UUID,
    user: CurrentActiveUser,
    db: DbSession,
) -> RolePublic:
    """Get a role by ID (consumer counts for a Platform Admin only)."""
    from shared.sdk_roles import RoleServiceError, get_role as get_role_service

    caller = await authorize_operation(db, user, "roles.get", GLOBAL)
    try:
        return await get_role_service(
            db, role_id=role_id, include_counts=caller.is_platform_admin
        )
    except RoleServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from None


@router.patch(
    "/{role_id}",
    response_model=RolePublic,
    summary="Update a role",
    description="Update a role",
**operation_route("roles.update"))
async def update_role(
    role_id: UUID,
    request: RoleUpdate,
    user: CurrentActiveUser,
    db: DbSession,
) -> RolePublic:
    """Update a role."""
    from shared.sdk_roles import RoleServiceError, update_role as update_role_service

    await authorize_operation(db, user, "roles.update", GLOBAL)

    try:
        return await update_role_service(
            db,
            role_id=role_id,
            name=request.name,
            description=request.description,
            actor_email=user.email,
        )
    except RoleServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from None


# Keep PUT for backwards compatibility
@router.put(
    "/{role_id}",
    response_model=RolePublic,
    summary="Update a role",
    description="Update a role",
    include_in_schema=False,  # Hide from OpenAPI, use PATCH instead
)
async def update_role_put(
    role_id: UUID,
    request: RoleUpdate,
    user: CurrentActiveUser,
    db: DbSession,
) -> RolePublic:
    """Update a role (PUT - for backwards compatibility)."""
    await authorize_operation(db, user, "PUT /api/roles/{role_id}", GLOBAL)
    return await update_role(role_id, request, user, db)


@router.delete(
    "/{role_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a role",
    description=(
        "Delete a role. CASCADE removes all role assignments; a role that is "
        "anyone's base role can't be deleted."
    ),
**operation_route("roles.delete"))
async def delete_role(
    role_id: UUID,
    user: CurrentActiveUser,
    db: DbSession,
) -> None:
    """Delete a role."""
    from shared.sdk_roles import RoleServiceError, delete_role as delete_role_service

    await authorize_operation(db, user, "roles.delete", GLOBAL)

    try:
        await delete_role_service(db, role_id=role_id)
    except RoleServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from None


# =============================================================================
# Role-User Assignments
# =============================================================================


@router.get(
    "/{role_id}/users",
    response_model=RoleUsersResponse,
    summary="Get role users",
    description="Get all users assigned to a role",
**operation_route("roles.users.list"))
async def get_role_users(
    role_id: UUID,
    user: CurrentActiveUser,
    db: DbSession,
    search: str | None = Query(None, description="Search assigned user name or email"),
    limit: int | None = Query(
        None,
        ge=1,
        le=100,
        description="Maximum rows to return; omit for the legacy unbounded response",
    ),
    offset: int = Query(0, ge=0, description="Rows to skip when limit is set"),
) -> RoleUsersResponse:
    """Get users assigned to a role, limited to the organizations where the
    caller may read role assignments."""
    from shared.sdk_roles import list_role_users as list_role_users_service

    reach = operation_reach(await load_caller(db, user), "roles.users.list")
    return await list_role_users_service(
        db,
        role_id=role_id,
        reach=reach,
        search=search,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/{role_id}/users",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Assign users to role",
    description="Assign users to a role (batch operation)",
**operation_route("roles.users.assign"))
async def assign_users_to_role(
    role_id: UUID,
    request: AssignUsersToRoleRequest,
    user: CurrentActiveUser,
    db: DbSession,
) -> None:
    """Assign users to a role."""
    from shared.sdk_roles import (
        RoleServiceError,
        assign_users_to_role as assign_users_to_role_service,
    )

    try:
        await assign_users_to_role_service(
            db,
            await load_caller(db, user),
            role_id=role_id,
            user_ids=request.user_ids,
        )
    except RoleServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from None


@router.delete(
    "/{role_id}/users/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove user from role",
    description="Remove a user from a role",
**operation_route("roles.users.remove"))
async def remove_user_from_role(
    role_id: UUID,
    user_id: str,
    user: CurrentActiveUser,
    db: DbSession,
) -> None:
    """Remove a user from a role."""
    from shared.sdk_roles import RoleServiceError, remove_users_from_role

    operation = "roles.users.remove"
    caller = await load_caller(db, user)
    operation_reach(caller, operation)
    try:
        user_uuid = UUID(user_id)
    except ValueError:
        result = await db.execute(select(UserORM.id).where(UserORM.email == user_id))
        user_uuid = result.scalar_one_or_none()
        if not user_uuid:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
            )

    try:
        removed = await remove_users_from_role(
            db, caller, role_id=role_id, user_ids=[user_uuid], operation=operation
        )
    except RoleServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from None
    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User-role assignment not found",
        )

    logger.info(f"Removed user {log_safe(user_id)} from role {log_safe(role_id)}")

    await emit_audit(
        db,
        "role.user_unassigned",
        resource_type="role",
        resource_id=role_id,
        details={"user_id": str(user_uuid)},
    )


# =============================================================================
# Role-Form Assignments
# =============================================================================


@router.get(
    "/{role_id}/forms",
    response_model=RoleFormsResponse,
    summary="Get role forms",
    description="Get all forms assigned to a role",
**operation_route("roles.forms.list"))
async def get_role_forms(
    role_id: UUID,
    user: CurrentSuperuser,
    db: DbSession,
) -> RoleFormsResponse:
    """Get all forms assigned to a role."""
    from shared.sdk_roles import list_role_forms as list_role_forms_service

    return await list_role_forms_service(db, role_id=role_id)


@router.post(
    "/{role_id}/forms",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Assign forms to role",
    description="Assign forms to a role (batch operation)",
**operation_route("roles.forms.assign"))
async def assign_forms_to_role(
    role_id: UUID,
    request: AssignFormsToRoleRequest,
    user: CurrentSuperuser,
    db: DbSession,
) -> None:
    """Assign forms to a role."""
    from shared.sdk_roles import (
        RoleServiceError,
        assign_forms_to_role as assign_forms_to_role_service,
    )

    try:
        await assign_forms_to_role_service(
            db,
            role_id=role_id,
            form_ids=request.form_ids,
            actor_email=user.email,
        )
    except RoleServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from None


@router.delete(
    "/{role_id}/forms/{form_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove form from role",
    description="Remove a form from a role",
**operation_route("roles.forms.remove"))
async def remove_form_from_role(
    role_id: UUID,
    form_id: UUID,
    user: CurrentSuperuser,
    db: DbSession,
) -> None:
    """Remove a form from a role."""
    await assert_entity_id_not_solution_managed(db, FormORM, form_id)
    result = await db.execute(
        delete(FormRoleORM).where(
            FormRoleORM.form_id == form_id,
            FormRoleORM.role_id == role_id,
        )
    )

    if result.rowcount == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Form-role assignment not found",
        )

    logger.info(f"Removed form {log_safe(form_id)} from role {log_safe(role_id)}")

    # Invalidate cache (roles are global, no org_id needed)
    await invalidate_role_forms(None, str(role_id))


# =============================================================================
# Role-Agent Assignments
# =============================================================================


@router.get(
    "/{role_id}/agents",
    response_model=RoleAgentsResponse,
    summary="Get role agents",
    description="Get all agents assigned to a role",
**operation_route("roles.agents.list"))
async def get_role_agents(
    role_id: UUID,
    user: CurrentSuperuser,
    db: DbSession,
) -> RoleAgentsResponse:
    """Get all agents assigned to a role."""
    result = await db.execute(
        select(AgentRoleORM.agent_id).where(AgentRoleORM.role_id == role_id)
    )
    agent_ids = [str(aid) for aid in result.scalars().all()]
    return RoleAgentsResponse(agent_ids=agent_ids)


@router.post(
    "/{role_id}/agents",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Assign agents to role",
    description="Assign agents to a role (batch operation)",
**operation_route("roles.agents.assign"))
async def assign_agents_to_role(
    role_id: UUID,
    request: AssignAgentsToRoleRequest,
    user: CurrentSuperuser,
    db: DbSession,
) -> None:
    """Assign agents to a role."""
    from shared.builtin_roles import is_builtin_role_id

    if is_builtin_role_id(role_id):
        raise HTTPException(status.HTTP_409_CONFLICT, "Builtin roles cannot be assigned entities")

    now = datetime.now(timezone.utc)

    for agent_id_str in request.agent_ids:
        agent_uuid = UUID(agent_id_str)

        # Verify agent exists before creating assignment
        agent_result = await db.execute(
            select(AgentORM.id).where(AgentORM.id == agent_uuid)
        )
        if not agent_result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Agent with ID '{agent_id_str}' not found",
            )
        await assert_entity_id_not_solution_managed(db, AgentORM, agent_uuid)

        # Check if already assigned
        existing = await db.execute(
            select(AgentRoleORM).where(
                AgentRoleORM.agent_id == agent_uuid,
                AgentRoleORM.role_id == role_id,
            )
        )
        if existing.scalar_one_or_none():
            continue

        agent_role = AgentRoleORM(
            agent_id=agent_uuid,
            role_id=role_id,
            assigned_by=user.email,
            assigned_at=now,
        )
        db.add(agent_role)

    await db.flush()
    logger.info(f"Assigned agents to role {log_safe(role_id)}")

    # Invalidate cache if available (roles are global, no org_id needed)
    if AGENT_CACHE_INVALIDATION_AVAILABLE and invalidate_role_agents:
        await invalidate_role_agents(None, str(role_id))


@router.delete(
    "/{role_id}/agents/{agent_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove agent from role",
    description="Remove an agent from a role",
**operation_route("roles.agents.remove"))
async def remove_agent_from_role(
    role_id: UUID,
    agent_id: UUID,
    user: CurrentSuperuser,
    db: DbSession,
) -> None:
    """Remove an agent from a role."""
    await assert_entity_id_not_solution_managed(db, AgentORM, agent_id)
    result = await db.execute(
        delete(AgentRoleORM).where(
            AgentRoleORM.agent_id == agent_id,
            AgentRoleORM.role_id == role_id,
        )
    )

    if result.rowcount == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent-role assignment not found",
        )

    logger.info(f"Removed agent {log_safe(agent_id)} from role {log_safe(role_id)}")

    # Invalidate cache if available (roles are global, no org_id needed)
    if AGENT_CACHE_INVALIDATION_AVAILABLE and invalidate_role_agents:
        await invalidate_role_agents(None, str(role_id))


# =============================================================================
# Bulk Unassign — list-body shortcuts for existing surfaces (users/forms/agents)
# Kept alongside the per-id DELETE forms; the per-id paths stay for callers
# that already use them.
# =============================================================================


@router.delete(
    "/{role_id}/users",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Bulk unassign users from role",
    description=(
        "Bulk unassign N users from a role in one call. Pass the user UUIDs in the "
        "request body as {user_ids: [...]}. Unknown ids are silently skipped."
    ),
**operation_route("roles.users.bulk_remove"))
async def bulk_unassign_users(
    role_id: UUID,
    request: UnassignUsersFromRoleRequest,
    user: CurrentActiveUser,
    db: DbSession,
) -> None:
    """Remove multiple users from a role in one statement."""
    from shared.sdk_roles import RoleServiceError, remove_users_from_role

    caller = await load_caller(db, user)
    operation_reach(caller, "roles.users.bulk_remove")
    uuids: list[UUID] = []
    for uid in request.user_ids:
        try:
            uuids.append(UUID(uid))
        except ValueError:
            logger.warning(f"Invalid user id {log_safe(uid)} in bulk unassign — skipping")

    if not uuids:
        return

    try:
        await remove_users_from_role(
            db, caller, role_id=role_id, user_ids=uuids, operation="roles.users.bulk_remove"
        )
    except RoleServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from None
    logger.info(f"Bulk unassigned {len(uuids)} users from role {log_safe(role_id)}")

    await emit_audit(
        db,
        "role.users_bulk_unassigned",
        resource_type="role",
        resource_id=role_id,
        details={"user_ids": [str(u) for u in uuids]},
    )


@router.delete(
    "/{role_id}/forms",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Bulk unassign forms from role",
**operation_route("roles.forms.bulk_remove"))
async def bulk_unassign_forms(
    role_id: UUID,
    request: UnassignFormsFromRoleRequest,
    user: CurrentSuperuser,
    db: DbSession,
) -> None:
    """Remove multiple forms from a role in one statement."""
    uuids = [UUID(fid) for fid in request.form_ids]
    for fid in uuids:
        await assert_entity_id_not_solution_managed(db, FormORM, fid)
    await db.execute(
        delete(FormRoleORM).where(
            FormRoleORM.role_id == role_id,
            FormRoleORM.form_id.in_(uuids),
        )
    )
    await db.flush()
    logger.info(f"Bulk unassigned {len(uuids)} forms from role {log_safe(role_id)}")

    await invalidate_role_forms(None, str(role_id))

    await emit_audit(
        db,
        "role.forms_bulk_unassigned",
        resource_type="role",
        resource_id=role_id,
        details={"form_ids": [str(u) for u in uuids]},
    )


@router.delete(
    "/{role_id}/agents",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Bulk unassign agents from role",
**operation_route("roles.agents.bulk_remove"))
async def bulk_unassign_agents(
    role_id: UUID,
    request: UnassignAgentsFromRoleRequest,
    user: CurrentSuperuser,
    db: DbSession,
) -> None:
    """Remove multiple agents from a role in one statement."""
    uuids = [UUID(aid) for aid in request.agent_ids]
    for aid in uuids:
        await assert_entity_id_not_solution_managed(db, AgentORM, aid)
    await db.execute(
        delete(AgentRoleORM).where(
            AgentRoleORM.role_id == role_id,
            AgentRoleORM.agent_id.in_(uuids),
        )
    )
    await db.flush()
    logger.info(f"Bulk unassigned {len(uuids)} agents from role {log_safe(role_id)}")

    if AGENT_CACHE_INVALIDATION_AVAILABLE and invalidate_role_agents:
        await invalidate_role_agents(None, str(role_id))

    await emit_audit(
        db,
        "role.agents_bulk_unassigned",
        resource_type="role",
        resource_id=role_id,
        details={"agent_ids": [str(u) for u in uuids]},
    )


# =============================================================================
# Role-App Assignments
# =============================================================================


@router.get(
    "/{role_id}/apps",
    response_model=RoleAppsResponse,
    summary="Get role apps",
**operation_route("roles.apps.list"))
async def get_role_apps(
    role_id: UUID,
    user: CurrentSuperuser,
    db: DbSession,
) -> RoleAppsResponse:
    result = await db.execute(
        select(AppRoleORM.app_id).where(AppRoleORM.role_id == role_id)
    )
    return RoleAppsResponse(app_ids=[str(aid) for aid in result.scalars().all()])


@router.post(
    "/{role_id}/apps",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Assign apps to role",
**operation_route("roles.apps.assign"))
async def assign_apps_to_role(
    role_id: UUID,
    request: AssignAppsToRoleRequest,
    user: CurrentSuperuser,
    db: DbSession,
) -> None:
    from shared.builtin_roles import is_builtin_role_id

    if is_builtin_role_id(role_id):
        raise HTTPException(status.HTTP_409_CONFLICT, "Builtin roles cannot be assigned entities")

    now = datetime.now(timezone.utc)
    for app_id_str in request.app_ids:
        app_uuid = UUID(app_id_str)
        app_exists = await db.execute(
            select(ApplicationORM.id).where(ApplicationORM.id == app_uuid)
        )
        if not app_exists.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Application with ID '{app_id_str}' not found",
            )
        await assert_entity_id_not_solution_managed(db, ApplicationORM, app_uuid)
        existing = await db.execute(
            select(AppRoleORM).where(
                AppRoleORM.app_id == app_uuid,
                AppRoleORM.role_id == role_id,
            )
        )
        if existing.scalar_one_or_none():
            continue
        db.add(AppRoleORM(
            app_id=app_uuid,
            role_id=role_id,
            assigned_by=user.email,
            assigned_at=now,
        ))
    await db.flush()
    logger.info(f"Assigned apps to role {log_safe(role_id)}")
    await emit_audit(
        db,
        "role.apps_assigned",
        resource_type="role",
        resource_id=role_id,
        details={"app_ids": request.app_ids},
    )


@router.delete(
    "/{role_id}/apps",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Bulk unassign apps from role",
**operation_route("roles.apps.bulk_remove"))
async def bulk_unassign_apps(
    role_id: UUID,
    request: UnassignAppsFromRoleRequest,
    user: CurrentSuperuser,
    db: DbSession,
) -> None:
    uuids = [UUID(aid) for aid in request.app_ids]
    for aid in uuids:
        await assert_entity_id_not_solution_managed(db, ApplicationORM, aid)
    await db.execute(
        delete(AppRoleORM).where(
            AppRoleORM.role_id == role_id,
            AppRoleORM.app_id.in_(uuids),
        )
    )
    await db.flush()
    logger.info(f"Bulk unassigned {len(uuids)} apps from role {log_safe(role_id)}")
    await emit_audit(
        db,
        "role.apps_bulk_unassigned",
        resource_type="role",
        resource_id=role_id,
        details={"app_ids": [str(u) for u in uuids]},
    )


# =============================================================================
# Role-Workflow Assignments
# =============================================================================


@router.get(
    "/{role_id}/workflows",
    response_model=RoleWorkflowsResponse,
    summary="Get role workflows",
**operation_route("roles.workflows.list"))
async def get_role_workflows(
    role_id: UUID,
    user: CurrentSuperuser,
    db: DbSession,
) -> RoleWorkflowsResponse:
    result = await db.execute(
        select(WorkflowRoleORM.workflow_id).where(WorkflowRoleORM.role_id == role_id)
    )
    return RoleWorkflowsResponse(
        workflow_ids=[str(wid) for wid in result.scalars().all()]
    )


@router.post(
    "/{role_id}/workflows",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Assign workflows to role",
**operation_route("roles.workflows.assign"))
async def assign_workflows_to_role(
    role_id: UUID,
    request: AssignWorkflowsToRoleRequest,
    user: CurrentSuperuser,
    db: DbSession,
) -> None:
    from shared.builtin_roles import is_builtin_role_id

    if is_builtin_role_id(role_id):
        raise HTTPException(status.HTTP_409_CONFLICT, "Builtin roles cannot be assigned entities")

    now = datetime.now(timezone.utc)
    for wf_id_str in request.workflow_ids:
        wf_uuid = UUID(wf_id_str)
        wf_exists = await db.execute(
            select(WorkflowORM.id).where(WorkflowORM.id == wf_uuid)
        )
        if not wf_exists.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Workflow with ID '{wf_id_str}' not found",
            )
        await assert_entity_id_not_solution_managed(db, WorkflowORM, wf_uuid)
        existing = await db.execute(
            select(WorkflowRoleORM).where(
                WorkflowRoleORM.workflow_id == wf_uuid,
                WorkflowRoleORM.role_id == role_id,
            )
        )
        if existing.scalar_one_or_none():
            continue
        db.add(WorkflowRoleORM(
            workflow_id=wf_uuid,
            role_id=role_id,
            assigned_by=user.email,
            assigned_at=now,
        ))
    await db.flush()
    logger.info(f"Assigned workflows to role {log_safe(role_id)}")
    await emit_audit(
        db,
        "role.workflows_assigned",
        resource_type="role",
        resource_id=role_id,
        details={"workflow_ids": request.workflow_ids},
    )


@router.delete(
    "/{role_id}/workflows",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Bulk unassign workflows from role",
**operation_route("roles.workflows.bulk_remove"))
async def bulk_unassign_workflows(
    role_id: UUID,
    request: UnassignWorkflowsFromRoleRequest,
    user: CurrentSuperuser,
    db: DbSession,
) -> None:
    uuids = [UUID(wid) for wid in request.workflow_ids]
    for wid in uuids:
        await assert_entity_id_not_solution_managed(db, WorkflowORM, wid)
    await db.execute(
        delete(WorkflowRoleORM).where(
            WorkflowRoleORM.role_id == role_id,
            WorkflowRoleORM.workflow_id.in_(uuids),
        )
    )
    await db.flush()
    logger.info(f"Bulk unassigned {len(uuids)} workflows from role {log_safe(role_id)}")
    await emit_audit(
        db,
        "role.workflows_bulk_unassigned",
        resource_type="role",
        resource_id=role_id,
        details={"workflow_ids": [str(u) for u in uuids]},
    )



# =============================================================================
# Role permission sets
# =============================================================================


@router.get(
    "/{role_id}/permissions",
    response_model=RolePermissionsResponse,
    summary="Get a role's permissions",
    description=(
        "Every permission the role holds, marked editable (identity permissions on "
        "custom roles) and privileged, plus the identity permissions an editor offers."
    ),
)
async def get_role_permissions(
    role_id: UUID,
    user: CurrentActiveUser,
    db: DbSession,
) -> RolePermissionsResponse:
    from src.services.role_permissions import RolePermissionError, describe_role_permissions

    await authorize_operation(db, user, "GET /api/roles/{role_id}/permissions", GLOBAL)
    try:
        return await describe_role_permissions(db, role_id=role_id)
    except RolePermissionError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from None


@router.put(
    "/{role_id}/permissions",
    response_model=RolePermissionsResponse,
    summary="Set a role's identity permissions",
    description=(
        "Replace the role's identity permissions (users, users.lifecycle, organizations, "
        "roleassignments, roles); its other permissions are kept. Builtin roles can't be changed."
    ),
)
async def set_role_permissions(
    role_id: UUID,
    request: RolePermissionsUpdate,
    user: CurrentActiveUser,
    db: DbSession,
) -> RolePermissionsResponse:
    from src.services.role_permissions import RolePermissionError, replace_identity_permissions

    await authorize_operation(db, user, "PUT /api/roles/{role_id}/permissions", GLOBAL)
    try:
        return await replace_identity_permissions(
            db, role_id=role_id, permissions=request.permissions
        )
    except RolePermissionError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from None
