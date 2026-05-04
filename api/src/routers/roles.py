"""
Roles Router

Manage roles for organization users.
- Assign users to roles (UserRoles)
- Assign forms to roles (FormRoles)
"""

import logging
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select, delete

from src.core.auth import CurrentSuperuser
from src.core.database import DbSession
from src.core.log_safety import log_safe
from src.services.audit import emit_audit
from src.models import (
    Role as RoleORM,
    UserRole as UserRoleORM,
    FormRole as FormRoleORM,
    AgentRole as AgentRoleORM,
    Form as FormORM,
    User as UserORM,
    Agent as AgentORM,
)
from src.models import (
    RoleCreate,
    RolePublic,
    RoleUpdate,
    RoleUsersResponse,
    RoleFormsResponse,
    RoleAgentsResponse,
    AssignUsersToRoleRequest,
    AssignFormsToRoleRequest,
    AssignAgentsToRoleRequest,
)

# Per-user role cache (Redis-backed, used by table-policy `has_role` lookups
# in `get_execution_context` / WS `_populate_user_roles`). Aliased on import
# because `invalidate_role` collides with the same-named function in
# `src.core.cache.invalidation` (which clears the global roles list, a
# different cache).
from shared.role_cache import invalidate_role as invalidate_user_role_cache_for_role
from shared.role_cache import invalidate_user as invalidate_user_role_cache

# Import cache invalidation
try:
    from src.core.cache import (
        invalidate_role,
        invalidate_role_users,
        invalidate_role_forms,
    )

    CACHE_INVALIDATION_AVAILABLE = True
except ImportError:
    CACHE_INVALIDATION_AVAILABLE = False
    invalidate_role = None  # type: ignore
    invalidate_role_users = None  # type: ignore
    invalidate_role_forms = None  # type: ignore

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
    description="Get all roles (Platform admin only)",
)
async def list_roles(
    user: CurrentSuperuser,
    db: DbSession,
) -> list[RolePublic]:
    """List all roles."""
    query = select(RoleORM).order_by(RoleORM.name)
    result = await db.execute(query)
    roles = result.scalars().all()
    return [RolePublic.model_validate(r) for r in roles]


@router.post(
    "",
    response_model=RolePublic,
    status_code=status.HTTP_201_CREATED,
    summary="Create a role",
    description="Create a new role (Platform admin only)",
)
async def create_role(
    request: RoleCreate,
    user: CurrentSuperuser,
    db: DbSession,
) -> RolePublic:
    """Create a new role."""
    now = datetime.now(timezone.utc)

    role = RoleORM(
        name=request.name,
        description=request.description,
        permissions=request.permissions or {},
        created_by=user.email,
        created_at=now,
        updated_at=now,
    )

    db.add(role)
    await db.flush()
    await db.refresh(role)

    logger.info(f"Created role {role.id}: {log_safe(role.name)}")

    # Invalidate cache (roles are global, no org_id needed)
    if CACHE_INVALIDATION_AVAILABLE and invalidate_role:
        await invalidate_role(None, str(role.id))

    await emit_audit(
        db,
        "role.create",
        resource_type="role",
        resource_id=role.id,
        details={"name": role.name},
    )
    return RolePublic.model_validate(role)


@router.get(
    "/{role_id}",
    response_model=RolePublic,
    summary="Get a role",
    description="Get a role by ID (Platform admin only)",
)
async def get_role(
    role_id: UUID,
    user: CurrentSuperuser,
    db: DbSession,
) -> RolePublic:
    """Get a role by ID."""
    result = await db.execute(select(RoleORM).where(RoleORM.id == role_id))
    role = result.scalar_one_or_none()

    if not role:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Role not found",
        )

    return RolePublic.model_validate(role)


@router.patch(
    "/{role_id}",
    response_model=RolePublic,
    summary="Update a role",
    description="Update a role (Platform admin only)",
)
async def update_role(
    role_id: UUID,
    request: RoleUpdate,
    user: CurrentSuperuser,
    db: DbSession,
) -> RolePublic:
    """Update a role."""
    result = await db.execute(select(RoleORM).where(RoleORM.id == role_id))
    role = result.scalar_one_or_none()

    if not role:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Role not found",
        )

    if request.name is not None:
        role.name = request.name
    if request.description is not None:
        role.description = request.description
    if request.permissions is not None:
        role.permissions = request.permissions

    role.updated_at = datetime.now(timezone.utc)

    await db.flush()
    await db.refresh(role)

    logger.info(f"Updated role {log_safe(role_id)}")

    # Invalidate cache (roles are global, no org_id needed)
    if CACHE_INVALIDATION_AVAILABLE and invalidate_role:
        await invalidate_role(None, str(role_id))

    # Per-user role cache: a rename changes role_names for every user holding
    # this role, so sweep all entries containing role_id.
    await invalidate_user_role_cache_for_role(role_id)

    changed_fields = [
        k for k, v in request.model_dump(exclude_unset=True).items() if v is not None
    ]
    await emit_audit(
        db,
        "role.update",
        resource_type="role",
        resource_id=role.id,
        details={"name": role.name, "changed_fields": changed_fields},
    )
    return RolePublic.model_validate(role)


# Keep PUT for backwards compatibility
@router.put(
    "/{role_id}",
    response_model=RolePublic,
    summary="Update a role",
    description="Update a role (Platform admin only)",
    include_in_schema=False,  # Hide from OpenAPI, use PATCH instead
)
async def update_role_put(
    role_id: UUID,
    request: RoleUpdate,
    user: CurrentSuperuser,
    db: DbSession,
) -> RolePublic:
    """Update a role (PUT - for backwards compatibility)."""
    return await update_role(role_id, request, user, db)


@router.delete(
    "/{role_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a role",
    description="Delete a role (Platform admin only). CASCADE removes all role assignments.",
)
async def delete_role(
    role_id: UUID,
    user: CurrentSuperuser,
    db: DbSession,
) -> None:
    """Delete a role."""
    result = await db.execute(select(RoleORM).where(RoleORM.id == role_id))
    role = result.scalar_one_or_none()

    if not role:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Role not found",
        )

    deleted_name = role.name
    await db.delete(role)
    await db.flush()
    logger.info(f"Deleted role {log_safe(role_id)}")

    # Invalidate cache (roles are global, no org_id needed)
    if CACHE_INVALIDATION_AVAILABLE and invalidate_role:
        await invalidate_role(None, str(role_id))

    # Per-user role cache: deleting a role means every user holding it loses
    # the membership; clear all entries containing role_id.
    await invalidate_user_role_cache_for_role(role_id)

    await emit_audit(
        db,
        "role.delete",
        resource_type="role",
        resource_id=role_id,
        details={"name": deleted_name},
    )


# =============================================================================
# Role-User Assignments
# =============================================================================


@router.get(
    "/{role_id}/users",
    response_model=RoleUsersResponse,
    summary="Get role users",
    description="Get all users assigned to a role",
)
async def get_role_users(
    role_id: UUID,
    user: CurrentSuperuser,
    db: DbSession,
) -> RoleUsersResponse:
    """Get all users assigned to a role."""
    result = await db.execute(
        select(UserRoleORM.user_id).where(UserRoleORM.role_id == role_id)
    )
    user_ids = [str(uid) for uid in result.scalars().all()]
    return RoleUsersResponse(user_ids=user_ids)


@router.post(
    "/{role_id}/users",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Assign users to role",
    description="Assign users to a role (batch operation)",
)
async def assign_users_to_role(
    role_id: UUID,
    request: AssignUsersToRoleRequest,
    user: CurrentSuperuser,
    db: DbSession,
) -> None:
    """Assign users to a role."""
    now = datetime.now(timezone.utc)
    # Track newly-assigned users so we can invalidate the per-user role cache.
    # Skip users already assigned (no cache impact) and users that didn't resolve.
    affected_user_ids: list[UUID] = []

    for user_id_str in request.user_ids:
        # Try to parse as UUID, otherwise lookup by email
        try:
            user_uuid = UUID(user_id_str)
        except ValueError:
            result = await db.execute(
                select(UserORM.id).where(UserORM.email == user_id_str)
            )
            user_uuid = result.scalar_one_or_none()
            if not user_uuid:
                logger.warning(f"User {log_safe(user_id_str)} not found, skipping")
                continue

        # Check if already assigned
        existing = await db.execute(
            select(UserRoleORM).where(
                UserRoleORM.user_id == user_uuid,
                UserRoleORM.role_id == role_id,
            )
        )
        if existing.scalar_one_or_none():
            continue

        user_role = UserRoleORM(
            user_id=user_uuid,
            role_id=role_id,
            assigned_by=user.email,
            assigned_at=now,
        )
        db.add(user_role)
        affected_user_ids.append(user_uuid)

    await db.flush()
    logger.info(f"Assigned users to role {log_safe(role_id)}")

    # Invalidate cache (roles are global, no org_id needed)
    if CACHE_INVALIDATION_AVAILABLE and invalidate_role_users:
        await invalidate_role_users(None, str(role_id))

    # Per-user role cache: drop entries for each newly-assigned user so the
    # next read sees the new role membership.
    for affected in affected_user_ids:
        await invalidate_user_role_cache(affected)

    await emit_audit(
        db,
        "role.user_assigned",
        resource_type="role",
        resource_id=role_id,
        details={"user_ids": request.user_ids},
    )


@router.delete(
    "/{role_id}/users/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove user from role",
    description="Remove a user from a role",
)
async def remove_user_from_role(
    role_id: UUID,
    user_id: str,
    user: CurrentSuperuser,
    db: DbSession,
) -> None:
    """Remove a user from a role."""
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

    result = await db.execute(
        delete(UserRoleORM).where(
            UserRoleORM.user_id == user_uuid,
            UserRoleORM.role_id == role_id,
        )
    )

    if result.rowcount == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User-role assignment not found",
        )

    logger.info(f"Removed user {log_safe(user_id)} from role {log_safe(role_id)}")

    # Invalidate cache (roles are global, no org_id needed)
    if CACHE_INVALIDATION_AVAILABLE and invalidate_role_users:
        await invalidate_role_users(None, str(role_id))

    # Per-user role cache: drop this user's entry so the next read sees the
    # post-unassignment membership.
    await invalidate_user_role_cache(user_uuid)

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
)
async def get_role_forms(
    role_id: UUID,
    user: CurrentSuperuser,
    db: DbSession,
) -> RoleFormsResponse:
    """Get all forms assigned to a role."""
    result = await db.execute(
        select(FormRoleORM.form_id).where(FormRoleORM.role_id == role_id)
    )
    form_ids = [str(fid) for fid in result.scalars().all()]
    return RoleFormsResponse(form_ids=form_ids)


@router.post(
    "/{role_id}/forms",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Assign forms to role",
    description="Assign forms to a role (batch operation)",
)
async def assign_forms_to_role(
    role_id: UUID,
    request: AssignFormsToRoleRequest,
    user: CurrentSuperuser,
    db: DbSession,
) -> None:
    """Assign forms to a role."""
    now = datetime.now(timezone.utc)

    for form_id_str in request.form_ids:
        form_uuid = UUID(form_id_str)

        # Verify form exists before creating assignment
        form_result = await db.execute(
            select(FormORM.id).where(FormORM.id == form_uuid)
        )
        if not form_result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Form with ID '{form_id_str}' not found",
            )

        # Check if already assigned
        existing = await db.execute(
            select(FormRoleORM).where(
                FormRoleORM.form_id == form_uuid,
                FormRoleORM.role_id == role_id,
            )
        )
        if existing.scalar_one_or_none():
            continue

        form_role = FormRoleORM(
            form_id=form_uuid,
            role_id=role_id,
            assigned_by=user.email,
            assigned_at=now,
        )
        db.add(form_role)

    await db.flush()
    logger.info(f"Assigned forms to role {log_safe(role_id)}")

    # Invalidate cache (roles are global, no org_id needed)
    if CACHE_INVALIDATION_AVAILABLE and invalidate_role_forms:
        await invalidate_role_forms(None, str(role_id))


@router.delete(
    "/{role_id}/forms/{form_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove form from role",
    description="Remove a form from a role",
)
async def remove_form_from_role(
    role_id: UUID,
    form_id: UUID,
    user: CurrentSuperuser,
    db: DbSession,
) -> None:
    """Remove a form from a role."""
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
    if CACHE_INVALIDATION_AVAILABLE and invalidate_role_forms:
        await invalidate_role_forms(None, str(role_id))


# =============================================================================
# Role-Agent Assignments
# =============================================================================


@router.get(
    "/{role_id}/agents",
    response_model=RoleAgentsResponse,
    summary="Get role agents",
    description="Get all agents assigned to a role",
)
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
)
async def assign_agents_to_role(
    role_id: UUID,
    request: AssignAgentsToRoleRequest,
    user: CurrentSuperuser,
    db: DbSession,
) -> None:
    """Assign agents to a role."""
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
)
async def remove_agent_from_role(
    role_id: UUID,
    agent_id: UUID,
    user: CurrentSuperuser,
    db: DbSession,
) -> None:
    """Remove an agent from a role."""
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
