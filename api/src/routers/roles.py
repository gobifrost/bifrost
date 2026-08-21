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
from sqlalchemy import delete, func, or_, select, update

from src.core.auth import CurrentActiveUser
from src.core.constants import PLATFORM_ADMIN_ROLE_ID
from src.core.db_deps import DbSession
from src.core.log_safety import log_safe
from src.services.solutions.guard import (
    assert_entity_id_not_solution_managed,
    assert_role_not_bound_to_solution_managed,
)
from src.services.audit import emit_audit
from src.services.operation_catalog import operation_route
from src.services.authorization import CurrentAuthorizationContext
from src.services.authorization import AuthorizationBoundaryKind
from src.services.role_assignments import (
    RoleAssignmentBoundarySpec,
    delete_role_assignment,
    infer_legacy_role_assignment_boundaries,
    replace_role_assignment,
)
from src.services.user_provisioning import validate_platform_admin_removal
from src.models import (
    Role as RoleORM,
    RoleAssignment as RoleAssignmentORM,
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
from src.models.orm.knowledge_sources import (
    KnowledgeNamespaceRole as KnowledgeNamespaceRoleORM,
)
from src.models.orm.organization_groups import OrganizationGroupMembership
from src.models.orm.organizations import Organization
from src.models.orm.role_assignments import RoleAssignmentBoundary
from src.models import (
    RoleCreate,
    RolePublic,
    RoleUpdate,
    RoleUsersResponse,
    RoleFormsResponse,
    RoleAgentsResponse,
    RoleAppsResponse,
    RoleWorkflowsResponse,
    RoleKnowledgeResponse,
    RoleKnowledgeEntry,
    RoleConsumerCounts,
    AuthorizationCapabilityPublic,
    AssignUsersToRoleRequest,
    AssignFormsToRoleRequest,
    AssignAgentsToRoleRequest,
    AssignAppsToRoleRequest,
    AssignWorkflowsToRoleRequest,
    AssignKnowledgeToRoleRequest,
    UnassignUsersFromRoleRequest,
    UnassignFormsFromRoleRequest,
    UnassignAgentsFromRoleRequest,
    UnassignAppsFromRoleRequest,
    UnassignWorkflowsFromRoleRequest,
    UnassignKnowledgeFromRoleRequest,
)

# Per-user role cache (Redis-backed, used by table-policy `has_role` lookups
# in `get_execution_context` / WS `_populate_user_roles`). Aliased on import
# because `invalidate_role` collides with the same-named function in
# `src.core.cache.invalidation` (which clears the global roles list, a
# different cache).
from shared.role_cache import invalidate_role as invalidate_user_role_cache_for_role
from shared.role_cache import invalidate_user as invalidate_user_role_cache

# Import cache invalidation
from src.core.cache import (
    invalidate_role,
    invalidate_role_users,
    invalidate_role_forms,
)

# Agent cache invalidation (optional, may not exist yet)
try:
    from src.core.cache import invalidate_role_agents

    AGENT_CACHE_INVALIDATION_AVAILABLE = True
except ImportError:
    AGENT_CACHE_INVALIDATION_AVAILABLE = False
    invalidate_role_agents = None  # type: ignore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/roles", tags=["Roles"])


async def _visible_role_user_ids(
    db: DbSession,
    *,
    role_id: UUID,
    authorization: CurrentAuthorizationContext,
) -> list[str]:
    """Return Role holders whose assignment intersects the active boundary."""

    query = (
        select(RoleAssignmentORM.user_id)
        .join(RoleAssignmentORM.boundaries)
        .where(RoleAssignmentORM.role_id == role_id)
    )
    if not authorization.has_capability("platform.superuser"):
        boundary = authorization.selected_boundary
        boundary_row = RoleAssignmentBoundary
        if boundary.kind is AuthorizationBoundaryKind.PLATFORM:
            query = query.where(boundary_row.boundary_kind == "platform")
        elif boundary.kind is AuthorizationBoundaryKind.MANAGED_ORGANIZATIONS:
            managed_org_ids = select(Organization.id).where(
                Organization.is_provider.is_(False)
            )
            managed_group_ids = select(
                OrganizationGroupMembership.organization_group_id
            ).where(
                OrganizationGroupMembership.organization_id.in_(managed_org_ids)
            )
            query = query.where(
                or_(
                    boundary_row.boundary_kind == "managed_organizations",
                    boundary_row.organization_id.in_(managed_org_ids),
                    boundary_row.organization_group_id.in_(managed_group_ids),
                )
            )
        else:
            assert boundary.organization_id is not None
            group_ids = select(
                OrganizationGroupMembership.organization_group_id
            ).where(
                OrganizationGroupMembership.organization_id
                == boundary.organization_id
            )
            is_managed = await db.scalar(
                select(Organization.is_provider).where(
                    Organization.id == boundary.organization_id
                )
            )
            predicates = [
                boundary_row.organization_id == boundary.organization_id,
                boundary_row.organization_group_id.in_(group_ids),
            ]
            if is_managed is False:
                predicates.append(
                    boundary_row.boundary_kind == "managed_organizations"
                )
            query = query.where(or_(*predicates))

    rows = (await db.execute(query.distinct())).scalars().all()
    return [str(user_id) for user_id in rows]


def _assert_role_mutable(role: RoleORM) -> None:
    """Reject public mutation of a Bifrost-managed role definition."""

    if role.is_builtin:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Built-in roles are managed by Bifrost and cannot be modified",
        )


def _require_role_assignment_admin_access(
    authorization: CurrentAuthorizationContext,
    operation_id: str,
) -> None:
    """Require the catalogued Role capability in the selected boundary."""

    authorization.require_operation(operation_id)


def _role_resource_boundary_clause(
    authorization: CurrentAuthorizationContext,
    organization_column,
):
    """Limit Role-consumer collection reads to the selected boundary."""

    if authorization.has_capability("platform.superuser"):
        return None
    boundary = authorization.selected_boundary
    if boundary.kind is AuthorizationBoundaryKind.PLATFORM:
        return organization_column.is_(None)
    if boundary.kind is AuthorizationBoundaryKind.MANAGED_ORGANIZATIONS:
        return organization_column.in_(
            select(Organization.id).where(Organization.is_provider.is_(False))
        )
    assert boundary.organization_id is not None
    return organization_column == boundary.organization_id


async def _require_role_resource_mutation(
    db: DbSession,
    *,
    authorization: CurrentAuthorizationContext,
    model,
    resource_id: UUID,
    resource_label: str,
):
    """Load one consumer and require Role authority in its exact boundary."""

    resource = await db.get(model, resource_id)
    if resource is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{resource_label} with ID '{resource_id}' not found",
        )
    authorization.require_resource_boundary(resource.organization_id)
    await assert_entity_id_not_solution_managed(db, model, resource_id)
    return resource


async def _assert_role_assignable_to_resources(db: DbSession, role_id: UUID) -> RoleORM:
    """Reject capability-only roles at resource-assignment boundaries."""

    role = await db.get(RoleORM, role_id)
    if role is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Role not found",
        )
    if not role.assignable_to_resources:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This built-in role grants platform capabilities and cannot be "
                "assigned to resources"
            ),
        )
    return role


@router.get(
    "",
    response_model=list[RolePublic],
    summary="List all roles",
    description="Get all roles (Platform admin only)",
    **operation_route("roles.list"),
)
async def list_roles(
    authorization: CurrentAuthorizationContext,
    db: DbSession,
) -> list[RolePublic]:
    """List Roles visible in the caller's explicitly selected boundary."""

    authorization.require_operation("roles.list")
    query = select(RoleORM).order_by(RoleORM.name)
    result = await db.execute(query)
    roles = result.scalars().all()

    counts_by_role: dict[UUID, RoleConsumerCounts] = {
        r.id: RoleConsumerCounts() for r in roles
    }

    for role in roles:
        counts_by_role[role.id].users = len(
            await _visible_role_user_ids(
                db,
                role_id=role.id,
                authorization=authorization,
            )
        )

    # Five grouped COUNT queries for resource consumers. Counts are scoped to
    # the selected authorization boundary so a scoped role manager cannot infer
    # cross-tenant usage from the global Role list.
    aggregates: list[tuple[str, "object", "object", "object"]] = [
        ("forms", FormRoleORM, FormORM, FormORM.id == FormRoleORM.form_id),
        ("agents", AgentRoleORM, AgentORM, AgentORM.id == AgentRoleORM.agent_id),
        ("apps", AppRoleORM, ApplicationORM, ApplicationORM.id == AppRoleORM.app_id),
        (
            "workflows",
            WorkflowRoleORM,
            WorkflowORM,
            WorkflowORM.id == WorkflowRoleORM.workflow_id,
        ),
        (
            "knowledge",
            KnowledgeNamespaceRoleORM,
            KnowledgeNamespaceRoleORM,
            KnowledgeNamespaceRoleORM.id == KnowledgeNamespaceRoleORM.id,
        ),
    ]
    for field, orm, resource_orm, join_clause in aggregates:
        query = select(orm.role_id, func.count()).group_by(orm.role_id)  # type: ignore[attr-defined]
        if resource_orm is not orm:
            query = query.join(resource_orm, join_clause)
        boundary_clause = _role_resource_boundary_clause(
            authorization,
            resource_orm.organization_id,
        )
        if boundary_clause is not None:
            query = query.where(boundary_clause)
        agg = await db.execute(query)
        for role_id, count in agg.all():
            entry = counts_by_role.get(role_id)
            if entry is None:
                continue
            setattr(entry, field, int(count))

    out: list[RolePublic] = []
    for r in roles:
        public = RolePublic.model_validate(r)
        public.consumer_counts = counts_by_role[r.id]
        out.append(public)
    return out


@router.get(
    "/capabilities",
    response_model=list[AuthorizationCapabilityPublic],
    summary="List authorization capabilities",
    description="Get the code-owned authorization capability catalog",
)
async def list_authorization_capabilities(
    authorization: CurrentAuthorizationContext,
) -> list[AuthorizationCapabilityPublic]:
    """Return the capability catalog used by role-management surfaces."""

    authorization.require_operation("roles.list")

    from shared.authorization_scopes import AUTHORIZATION_SCOPE_CATALOG

    return [
        AuthorizationCapabilityPublic(
            key=scope.key,
            display_name=scope.display_name,
            description=scope.description,
            category=scope.category,
            is_privileged=scope.is_privileged,
            assignable_to_custom_roles=scope.assignable_to_custom_roles,
        )
        for scope in AUTHORIZATION_SCOPE_CATALOG
    ]


@router.get(
    "/scopes",
    response_model=list[AuthorizationCapabilityPublic],
    summary="List authorization capabilities (deprecated scopes alias)",
    description="Deprecated compatibility alias for /api/roles/capabilities.",
)
async def list_authorization_scopes(
    authorization: CurrentAuthorizationContext,
) -> list[AuthorizationCapabilityPublic]:
    return await list_authorization_capabilities(authorization)


@router.post(
    "",
    response_model=RolePublic,
    status_code=status.HTTP_201_CREATED,
    summary="Create a role",
    description="Create a new role (Platform admin only)",
    **operation_route("roles.create"),
)
async def create_role(
    request: RoleCreate,
    authorization: CurrentAuthorizationContext,
    db: DbSession,
) -> RolePublic:
    """Create a new role."""
    authorization.require_operation("roles.create")
    authorization.require_resource_boundary(None)
    now = datetime.now(timezone.utc)

    role = RoleORM(
        name=request.name,
        description=request.description,
        capabilities=request.capabilities,
        permissions=request.permissions,
        created_by=authorization.effective_actor.email,
        created_at=now,
        updated_at=now,
    )

    db.add(role)
    await db.flush()
    await db.refresh(role)

    logger.info(f"Created role {role.id}: {log_safe(role.name)}")

    # Invalidate cache (roles are global, no org_id needed)
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
    **operation_route("roles.get"),
)
async def get_role(
    role_id: UUID,
    authorization: CurrentAuthorizationContext,
    db: DbSession,
) -> RolePublic:
    """Get a role by ID."""
    authorization.require_operation("roles.get")
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
    **operation_route("roles.update"),
)
async def update_role(
    role_id: UUID,
    request: RoleUpdate,
    authorization: CurrentAuthorizationContext,
    db: DbSession,
) -> RolePublic:
    """Update a role."""
    authorization.require_operation("roles.update")
    authorization.require_resource_boundary(None)
    result = await db.execute(select(RoleORM).where(RoleORM.id == role_id))
    role = result.scalar_one_or_none()

    if not role:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Role not found",
        )

    _assert_role_mutable(role)

    if request.name is not None:
        role.name = request.name
    if request.description is not None:
        role.description = request.description
    if request.capabilities is not None:
        role.capabilities = request.capabilities
    if request.permissions is not None:
        role.permissions = request.permissions

    role.updated_at = datetime.now(timezone.utc)

    await db.flush()
    await db.refresh(role)

    logger.info(f"Updated role {log_safe(role_id)}")

    # Invalidate cache (roles are global, no org_id needed)
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
    authorization: CurrentAuthorizationContext,
    db: DbSession,
) -> RolePublic:
    """Update a role (PUT - for backwards compatibility)."""
    return await update_role(role_id, request, authorization, db)


@router.delete(
    "/{role_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a role",
    description="Delete a role (Platform admin only). CASCADE removes all role assignments.",
    **operation_route("roles.delete"),
)
async def delete_role(
    role_id: UUID,
    authorization: CurrentAuthorizationContext,
    db: DbSession,
) -> None:
    """Delete a role."""
    authorization.require_operation("roles.delete")
    authorization.require_resource_boundary(None)
    result = await db.execute(select(RoleORM).where(RoleORM.id == role_id))
    role = result.scalar_one_or_none()

    if not role:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Role not found",
        )

    _assert_role_mutable(role)

    # A role assigned to a solution-managed entity has deploy-owned bindings;
    # deleting it would cascade-strip them outside deploy (Codex R4). Refuse.
    await assert_role_not_bound_to_solution_managed(db, role_id)

    deleted_name = role.name
    await db.delete(role)
    await db.flush()
    logger.info(f"Deleted role {log_safe(role_id)}")

    # Invalidate cache (roles are global, no org_id needed)
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
    **operation_route("roles.users.list"),
)
async def get_role_users(
    role_id: UUID,
    authorization: CurrentAuthorizationContext,
    db: DbSession,
) -> RoleUsersResponse:
    """Get Role holders admitted by the caller's selected boundary."""
    authorization.require_operation("roles.users.list")
    return RoleUsersResponse(
        user_ids=await _visible_role_user_ids(
            db,
            role_id=role_id,
            authorization=authorization,
        )
    )


@router.post(
    "/{role_id}/users",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Assign users to role",
    description="Assign users to a role (batch operation)",
    **operation_route("roles.users.assign"),
)
async def assign_users_to_role(
    role_id: UUID,
    request: AssignUsersToRoleRequest,
    user: CurrentActiveUser,
    authorization: CurrentAuthorizationContext,
    db: DbSession,
) -> None:
    """Assign users to a role."""
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

        boundaries = (
            [
                RoleAssignmentBoundarySpec(
                    kind=boundary.boundary_kind,
                    organization_id=boundary.organization_id,
                    organization_group_id=boundary.organization_group_id,
                )
                for boundary in request.boundaries
            ]
            if request.boundaries is not None
            else infer_legacy_role_assignment_boundaries(authorization)
        )
        await replace_role_assignment(
            db,
            requester=user,
            user_id=user_uuid,
            role_id=role_id,
            boundaries=boundaries,
        )
        if role_id == PLATFORM_ADMIN_ROLE_ID:
            target_user = await db.get(UserORM, user_uuid)
            if target_user is not None:
                # Keep the legacy boolean synchronized until all human routes
                # use Role assignments. A Role grant must not rewrite tenancy.
                target_user.is_superuser = True
        affected_user_ids.append(user_uuid)

    await db.flush()
    logger.info(f"Assigned users to role {log_safe(role_id)}")

    # Invalidate cache (roles are global, no org_id needed)
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
    **operation_route("roles.users.remove"),
)
async def remove_user_from_role(
    role_id: UUID,
    user_id: str,
    user: CurrentActiveUser,
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

    if role_id == PLATFORM_ADMIN_ROLE_ID:
        try:
            await validate_platform_admin_removal(
                db,
                user_ids=[user_uuid],
                actor_user_id=user.user_id,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc

    removed = await delete_role_assignment(
        db,
        requester=user,
        user_id=user_uuid,
        role_id=role_id,
    )
    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User-role assignment not found",
        )

    if role_id == PLATFORM_ADMIN_ROLE_ID:
        target_user = await db.get(UserORM, user_uuid)
        if target_user is not None:
            target_user.is_superuser = False

    logger.info(f"Removed user {log_safe(user_id)} from role {log_safe(role_id)}")

    # Invalidate cache (roles are global, no org_id needed)
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
    **operation_route("roles.forms.list"),
)
async def get_role_forms(
    role_id: UUID,
    authorization: CurrentAuthorizationContext,
    db: DbSession,
) -> RoleFormsResponse:
    """Get all forms assigned to a role."""
    _require_role_assignment_admin_access(authorization, "roles.forms.list")
    query = (
        select(FormRoleORM.form_id)
        .join(FormORM, FormORM.id == FormRoleORM.form_id)
        .where(FormRoleORM.role_id == role_id)
    )
    boundary_clause = _role_resource_boundary_clause(
        authorization, FormORM.organization_id
    )
    if boundary_clause is not None:
        query = query.where(boundary_clause)
    result = await db.execute(query)
    form_ids = [str(fid) for fid in result.scalars().all()]
    return RoleFormsResponse(form_ids=form_ids)


@router.post(
    "/{role_id}/forms",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Assign forms to role",
    description="Assign forms to a role (batch operation)",
    **operation_route("roles.forms.assign"),
)
async def assign_forms_to_role(
    role_id: UUID,
    request: AssignFormsToRoleRequest,
    authorization: CurrentAuthorizationContext,
    db: DbSession,
) -> None:
    """Assign forms to a role."""
    _require_role_assignment_admin_access(authorization, "roles.forms.assign")
    await _assert_role_assignable_to_resources(db, role_id)
    now = datetime.now(timezone.utc)

    for form_id_str in request.form_ids:
        form_uuid = UUID(form_id_str)
        await _require_role_resource_mutation(
            db,
            authorization=authorization,
            model=FormORM,
            resource_id=form_uuid,
            resource_label="Form",
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
            assigned_by=authorization.requester.email,
            assigned_at=now,
        )
        db.add(form_role)

    await db.flush()
    logger.info(f"Assigned forms to role {log_safe(role_id)}")

    # Invalidate cache (roles are global, no org_id needed)
    await invalidate_role_forms(None, str(role_id))


@router.delete(
    "/{role_id}/forms/{form_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove form from role",
    description="Remove a form from a role",
    **operation_route("roles.forms.remove"),
)
async def remove_form_from_role(
    role_id: UUID,
    form_id: UUID,
    authorization: CurrentAuthorizationContext,
    db: DbSession,
) -> None:
    """Remove a form from a role."""
    _require_role_assignment_admin_access(authorization, "roles.forms.remove")
    await _require_role_resource_mutation(
        db,
        authorization=authorization,
        model=FormORM,
        resource_id=form_id,
        resource_label="Form",
    )
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
    **operation_route("roles.agents.list"),
)
async def get_role_agents(
    role_id: UUID,
    authorization: CurrentAuthorizationContext,
    db: DbSession,
) -> RoleAgentsResponse:
    """Get all agents assigned to a role."""
    _require_role_assignment_admin_access(authorization, "roles.agents.list")
    query = (
        select(AgentRoleORM.agent_id)
        .join(AgentORM, AgentORM.id == AgentRoleORM.agent_id)
        .where(AgentRoleORM.role_id == role_id)
    )
    boundary_clause = _role_resource_boundary_clause(
        authorization, AgentORM.organization_id
    )
    if boundary_clause is not None:
        query = query.where(boundary_clause)
    result = await db.execute(query)
    agent_ids = [str(aid) for aid in result.scalars().all()]
    return RoleAgentsResponse(agent_ids=agent_ids)


@router.post(
    "/{role_id}/agents",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Assign agents to role",
    description="Assign agents to a role (batch operation)",
    **operation_route("roles.agents.assign"),
)
async def assign_agents_to_role(
    role_id: UUID,
    request: AssignAgentsToRoleRequest,
    authorization: CurrentAuthorizationContext,
    db: DbSession,
) -> None:
    """Assign agents to a role."""
    _require_role_assignment_admin_access(authorization, "roles.agents.assign")
    await _assert_role_assignable_to_resources(db, role_id)
    now = datetime.now(timezone.utc)

    for agent_id_str in request.agent_ids:
        agent_uuid = UUID(agent_id_str)
        await _require_role_resource_mutation(
            db,
            authorization=authorization,
            model=AgentORM,
            resource_id=agent_uuid,
            resource_label="Agent",
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
            assigned_by=authorization.requester.email,
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
    **operation_route("roles.agents.remove"),
)
async def remove_agent_from_role(
    role_id: UUID,
    agent_id: UUID,
    authorization: CurrentAuthorizationContext,
    db: DbSession,
) -> None:
    """Remove an agent from a role."""
    _require_role_assignment_admin_access(authorization, "roles.agents.remove")
    await _require_role_resource_mutation(
        db,
        authorization=authorization,
        model=AgentORM,
        resource_id=agent_id,
        resource_label="Agent",
    )
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
    **operation_route("roles.users.bulk_remove"),
)
async def bulk_unassign_users(
    role_id: UUID,
    request: UnassignUsersFromRoleRequest,
    user: CurrentActiveUser,
    db: DbSession,
) -> None:
    """Remove multiple users from a Role after checking every boundary."""
    uuids: list[UUID] = []
    for uid in request.user_ids:
        try:
            uuids.append(UUID(uid))
        except ValueError:
            logger.warning(
                f"Invalid user id {log_safe(uid)} in bulk unassign — skipping"
            )

    if not uuids:
        return

    if role_id == PLATFORM_ADMIN_ROLE_ID:
        try:
            await validate_platform_admin_removal(
                db,
                user_ids=uuids,
                actor_user_id=user.user_id,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc

    removed_ids: list[UUID] = []
    for user_uuid in uuids:
        if await delete_role_assignment(
            db,
            requester=user,
            user_id=user_uuid,
            role_id=role_id,
        ):
            removed_ids.append(user_uuid)
    if role_id == PLATFORM_ADMIN_ROLE_ID:
        await db.execute(
            update(UserORM)
            .where(UserORM.id.in_(removed_ids))
            .values(is_superuser=False)
        )
    await db.flush()
    logger.info(
        f"Bulk unassigned {len(removed_ids)} users from role {log_safe(role_id)}"
    )

    await invalidate_role_users(None, str(role_id))
    for uid_u in removed_ids:
        await invalidate_user_role_cache(uid_u)

    await emit_audit(
        db,
        "role.users_bulk_unassigned",
        resource_type="role",
        resource_id=role_id,
        details={"user_ids": [str(u) for u in removed_ids]},
    )


@router.delete(
    "/{role_id}/forms",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Bulk unassign forms from role",
    **operation_route("roles.forms.bulk_remove"),
)
async def bulk_unassign_forms(
    role_id: UUID,
    request: UnassignFormsFromRoleRequest,
    authorization: CurrentAuthorizationContext,
    db: DbSession,
) -> None:
    """Remove multiple forms from a role in one statement."""
    _require_role_assignment_admin_access(
        authorization, "roles.forms.bulk_remove"
    )
    uuids = [UUID(fid) for fid in request.form_ids]
    for fid in uuids:
        await _require_role_resource_mutation(
            db,
            authorization=authorization,
            model=FormORM,
            resource_id=fid,
            resource_label="Form",
        )
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
    **operation_route("roles.agents.bulk_remove"),
)
async def bulk_unassign_agents(
    role_id: UUID,
    request: UnassignAgentsFromRoleRequest,
    authorization: CurrentAuthorizationContext,
    db: DbSession,
) -> None:
    """Remove multiple agents from a role in one statement."""
    _require_role_assignment_admin_access(
        authorization, "roles.agents.bulk_remove"
    )
    uuids = [UUID(aid) for aid in request.agent_ids]
    for aid in uuids:
        await _require_role_resource_mutation(
            db,
            authorization=authorization,
            model=AgentORM,
            resource_id=aid,
            resource_label="Agent",
        )
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
    **operation_route("roles.apps.list"),
)
async def get_role_apps(
    role_id: UUID,
    authorization: CurrentAuthorizationContext,
    db: DbSession,
) -> RoleAppsResponse:
    _require_role_assignment_admin_access(authorization, "roles.apps.list")
    query = (
        select(AppRoleORM.app_id)
        .join(ApplicationORM, ApplicationORM.id == AppRoleORM.app_id)
        .where(AppRoleORM.role_id == role_id)
    )
    boundary_clause = _role_resource_boundary_clause(
        authorization, ApplicationORM.organization_id
    )
    if boundary_clause is not None:
        query = query.where(boundary_clause)
    result = await db.execute(query)
    return RoleAppsResponse(app_ids=[str(aid) for aid in result.scalars().all()])


@router.post(
    "/{role_id}/apps",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Assign apps to role",
    **operation_route("roles.apps.assign"),
)
async def assign_apps_to_role(
    role_id: UUID,
    request: AssignAppsToRoleRequest,
    authorization: CurrentAuthorizationContext,
    db: DbSession,
) -> None:
    _require_role_assignment_admin_access(authorization, "roles.apps.assign")
    await _assert_role_assignable_to_resources(db, role_id)
    now = datetime.now(timezone.utc)
    for app_id_str in request.app_ids:
        app_uuid = UUID(app_id_str)
        await _require_role_resource_mutation(
            db,
            authorization=authorization,
            model=ApplicationORM,
            resource_id=app_uuid,
            resource_label="Application",
        )
        existing = await db.execute(
            select(AppRoleORM).where(
                AppRoleORM.app_id == app_uuid,
                AppRoleORM.role_id == role_id,
            )
        )
        if existing.scalar_one_or_none():
            continue
        db.add(
            AppRoleORM(
                app_id=app_uuid,
                role_id=role_id,
                assigned_by=authorization.requester.email,
                assigned_at=now,
            )
        )
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
    **operation_route("roles.apps.bulk_remove"),
)
async def bulk_unassign_apps(
    role_id: UUID,
    request: UnassignAppsFromRoleRequest,
    authorization: CurrentAuthorizationContext,
    db: DbSession,
) -> None:
    _require_role_assignment_admin_access(authorization, "roles.apps.bulk_remove")
    uuids = [UUID(aid) for aid in request.app_ids]
    for aid in uuids:
        await _require_role_resource_mutation(
            db,
            authorization=authorization,
            model=ApplicationORM,
            resource_id=aid,
            resource_label="Application",
        )
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
    **operation_route("roles.workflows.list"),
)
async def get_role_workflows(
    role_id: UUID,
    authorization: CurrentAuthorizationContext,
    db: DbSession,
) -> RoleWorkflowsResponse:
    _require_role_assignment_admin_access(authorization, "roles.workflows.list")
    query = (
        select(WorkflowRoleORM.workflow_id)
        .join(WorkflowORM, WorkflowORM.id == WorkflowRoleORM.workflow_id)
        .where(WorkflowRoleORM.role_id == role_id)
    )
    boundary_clause = _role_resource_boundary_clause(
        authorization, WorkflowORM.organization_id
    )
    if boundary_clause is not None:
        query = query.where(boundary_clause)
    result = await db.execute(query)
    return RoleWorkflowsResponse(
        workflow_ids=[str(wid) for wid in result.scalars().all()]
    )


@router.post(
    "/{role_id}/workflows",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Assign workflows to role",
    **operation_route("roles.workflows.assign"),
)
async def assign_workflows_to_role(
    role_id: UUID,
    request: AssignWorkflowsToRoleRequest,
    authorization: CurrentAuthorizationContext,
    db: DbSession,
) -> None:
    _require_role_assignment_admin_access(authorization, "roles.workflows.assign")
    await _assert_role_assignable_to_resources(db, role_id)
    now = datetime.now(timezone.utc)
    for wf_id_str in request.workflow_ids:
        wf_uuid = UUID(wf_id_str)
        await _require_role_resource_mutation(
            db,
            authorization=authorization,
            model=WorkflowORM,
            resource_id=wf_uuid,
            resource_label="Workflow",
        )
        existing = await db.execute(
            select(WorkflowRoleORM).where(
                WorkflowRoleORM.workflow_id == wf_uuid,
                WorkflowRoleORM.role_id == role_id,
            )
        )
        if existing.scalar_one_or_none():
            continue
        db.add(
            WorkflowRoleORM(
                workflow_id=wf_uuid,
                role_id=role_id,
                assigned_by=authorization.requester.email,
                assigned_at=now,
            )
        )
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
    **operation_route("roles.workflows.bulk_remove"),
)
async def bulk_unassign_workflows(
    role_id: UUID,
    request: UnassignWorkflowsFromRoleRequest,
    authorization: CurrentAuthorizationContext,
    db: DbSession,
) -> None:
    _require_role_assignment_admin_access(
        authorization, "roles.workflows.bulk_remove"
    )
    uuids = [UUID(wid) for wid in request.workflow_ids]
    for wid in uuids:
        await _require_role_resource_mutation(
            db,
            authorization=authorization,
            model=WorkflowORM,
            resource_id=wid,
            resource_label="Workflow",
        )
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
# Role-Knowledge Assignments
# =============================================================================


@router.get(
    "/{role_id}/knowledge",
    response_model=RoleKnowledgeResponse,
    summary="Get role knowledge-namespace assignments",
    **operation_route("roles.knowledge.list"),
)
async def get_role_knowledge(
    role_id: UUID,
    authorization: CurrentAuthorizationContext,
    db: DbSession,
) -> RoleKnowledgeResponse:
    _require_role_assignment_admin_access(authorization, "roles.knowledge.list")
    query = select(KnowledgeNamespaceRoleORM).where(
        KnowledgeNamespaceRoleORM.role_id == role_id
    )
    boundary_clause = _role_resource_boundary_clause(
        authorization, KnowledgeNamespaceRoleORM.organization_id
    )
    if boundary_clause is not None:
        query = query.where(boundary_clause)
    result = await db.execute(query)
    return RoleKnowledgeResponse(
        entries=[
            RoleKnowledgeEntry(
                id=row.id,
                namespace=row.namespace,
                organization_id=row.organization_id,
            )
            for row in result.scalars().all()
        ]
    )


@router.post(
    "/{role_id}/knowledge",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Assign knowledge namespaces to role",
    **operation_route("roles.knowledge.assign"),
)
async def assign_knowledge_to_role(
    role_id: UUID,
    request: AssignKnowledgeToRoleRequest,
    authorization: CurrentAuthorizationContext,
    db: DbSession,
) -> None:
    _require_role_assignment_admin_access(authorization, "roles.knowledge.assign")
    await _assert_role_assignable_to_resources(db, role_id)
    now = datetime.now(timezone.utc)
    for entry in request.entries:
        authorization.require_resource_boundary(entry.organization_id)
        existing = await db.execute(
            select(KnowledgeNamespaceRoleORM).where(
                KnowledgeNamespaceRoleORM.namespace == entry.namespace,
                KnowledgeNamespaceRoleORM.organization_id == entry.organization_id,
                KnowledgeNamespaceRoleORM.role_id == role_id,
            )
        )
        if existing.scalar_one_or_none():
            continue
        db.add(
            KnowledgeNamespaceRoleORM(
                namespace=entry.namespace,
                organization_id=entry.organization_id,
                role_id=role_id,
                assigned_by=authorization.requester.email,
                assigned_at=now,
            )
        )
    await db.flush()
    logger.info(f"Assigned knowledge namespaces to role {log_safe(role_id)}")
    await emit_audit(
        db,
        "role.knowledge_assigned",
        resource_type="role",
        resource_id=role_id,
        details={
            "entries": [
                {
                    "namespace": e.namespace,
                    "organization_id": str(e.organization_id)
                    if e.organization_id
                    else None,
                }
                for e in request.entries
            ]
        },
    )


@router.delete(
    "/{role_id}/knowledge",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Bulk unassign knowledge namespaces from role",
    **operation_route("roles.knowledge.bulk_remove"),
)
async def bulk_unassign_knowledge(
    role_id: UUID,
    request: UnassignKnowledgeFromRoleRequest,
    authorization: CurrentAuthorizationContext,
    db: DbSession,
) -> None:
    _require_role_assignment_admin_access(
        authorization, "roles.knowledge.bulk_remove"
    )
    assignments = (
        (
            await db.execute(
                select(KnowledgeNamespaceRoleORM).where(
                    KnowledgeNamespaceRoleORM.role_id == role_id,
                    KnowledgeNamespaceRoleORM.id.in_(request.assignment_ids),
                )
            )
        )
        .scalars()
        .all()
    )
    for assignment in assignments:
        authorization.require_resource_boundary(assignment.organization_id)
    await db.execute(
        delete(KnowledgeNamespaceRoleORM).where(
            KnowledgeNamespaceRoleORM.role_id == role_id,
            KnowledgeNamespaceRoleORM.id.in_(request.assignment_ids),
        )
    )
    await db.flush()
    logger.info(
        f"Bulk unassigned {len(request.assignment_ids)} knowledge assignments from role {log_safe(role_id)}"
    )
    await emit_audit(
        db,
        "role.knowledge_bulk_unassigned",
        resource_type="role",
        resource_id=role_id,
        details={"assignment_ids": [str(a) for a in request.assignment_ids]},
    )
