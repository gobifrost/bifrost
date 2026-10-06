"""Shared business service for role operations.

Single implementation used by both entry points:

- the HTTP handlers (``api/src/routers/roles.py``) serving SDK/CLI
  callers, and
- the same handlers reached by workflow children over the worker-local
  engine socket.

Both paths can share DTOs, response fields, HTTP statuses/error precedence,
side effects, audit attribution, cache invalidation, and assignment
transaction behavior. The handlers decide role-definition authority
(roles.read / roles.readwrite at Platform) before invoking these
operations; the user-assignment operations take the ``Caller`` and decide
roleassignments.read / roleassignments.readwrite per target user, with the
grant ceiling (``src.services.user_role_assignments``).

Scope is the nine SDK methods: ``create``, ``get``, ``list``,
``update``, ``delete``, ``list_users``, ``list_forms``,
``assign_users``, ``assign_forms``. Each maps 1:1 to one HTTP call, so
there are no composite facades to decompose here.

Parent-side only: imports SQLAlchemy models and the Redis-backed
caches. The child never imports this module.

The service takes an explicit trusted session, UUIDs, validated
parameters, and the actor email — never a ``Request``, JWT, or raw
child frame. HTTP authentication, ``HTTPException`` mapping, and the
``X-Total-Count`` header stay in the router.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from sqlalchemy import delete, func, select, union
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.log_safety import log_safe

if TYPE_CHECKING:
    from src.models import (
        RoleConsumerCounts,
        RoleFormsResponse,
        RolePlacementSummary,
        RolePublic,
        RoleUsersResponse,
    )
    from src.services.authorization.enforce import Caller
    from src.services.authorization.reach import OrgReach

logger = logging.getLogger(__name__)


class RoleServiceError(Exception):
    """Role operation failure with an HTTP-style status.

    Raised by the shared service so the HTTP handler (``HTTPException``)
    and callers reached over the worker-local engine socket read the same status/detail. Solution-managed 409s arrive as
    the guard's own exception and propagate unchanged.
    """

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


async def get_consumer_counts(
    session: AsyncSession, role_ids: list[UUID]
) -> dict[UUID, RoleConsumerCounts]:
    """Inline consumer counts (users/forms/agents/apps/workflows/knowledge)."""
    from src.models import RoleConsumerCounts
    from src.models import AgentRole as AgentRoleORM
    from src.models import FormRole as FormRoleORM
    from src.models import UserRole as UserRoleORM
    from src.models.orm.app_roles import AppRole as AppRoleORM
    from src.models.orm.workflow_roles import WorkflowRole as WorkflowRoleORM

    counts_by_role = {role_id: RoleConsumerCounts() for role_id in role_ids}
    if not counts_by_role:
        return counts_by_role

    aggregates: list[tuple[str, object]] = [
        ("users", UserRoleORM),
        ("forms", FormRoleORM),
        ("agents", AgentRoleORM),
        ("apps", AppRoleORM),
        ("workflows", WorkflowRoleORM),
    ]
    for field, orm in aggregates:
        aggregate = await session.execute(
            select(orm.role_id, func.count())  # type: ignore[attr-defined]
            .where(orm.role_id.in_(counts_by_role))  # type: ignore[attr-defined]
            .group_by(orm.role_id)  # type: ignore[attr-defined]
        )
        for role_id, count in aggregate.all():
            entry = counts_by_role.get(role_id)
            if entry is not None:
                setattr(entry, field, int(count))
    return counts_by_role


async def get_role_holders(
    session: AsyncSession, role_ids: list[UUID]
) -> dict[UUID, int]:
    """Distinct users holding each role as their base role or an additional role."""
    from src.models import User as UserORM
    from src.models import UserRole as UserRoleORM

    held = union(
        select(UserORM.base_role_id.label("role_id"), UserORM.id.label("user_id")),
        select(UserRoleORM.role_id, UserRoleORM.user_id),
    ).subquery()
    rows = await session.execute(
        select(held.c.role_id, func.count())
        .where(held.c.role_id.in_(role_ids))
        .group_by(held.c.role_id)
    )
    return {role_id: int(count) for role_id, count in rows.all()}


async def get_role_permissions(
    session: AsyncSession, role_ids: list[UUID]
) -> dict[UUID, list[str]]:
    """Each role's granted permissions, sorted."""
    from src.models.orm.users import RolePermission as RolePermissionORM

    rows = await session.execute(
        select(RolePermissionORM.role_id, RolePermissionORM.permission)
        .where(RolePermissionORM.role_id.in_(role_ids))
        .order_by(RolePermissionORM.permission)
    )
    permissions: dict[UUID, list[str]] = {role_id: [] for role_id in role_ids}
    for role_id, permission in rows.all():
        permissions[role_id].append(permission)
    return permissions


async def get_role_placements(
    session: AsyncSession, role_ids: list[UUID]
) -> dict[UUID, RolePlacementSummary]:
    """Where each role's assignments are placed, from its assignment boundaries."""
    from src.models import RolePlacementSummary
    from src.models.orm.users import UserRoleBoundary

    rows = await session.execute(
        select(
            UserRoleBoundary.role_id,
            func.count(func.distinct(UserRoleBoundary.organization_id)),
            func.bool_or(UserRoleBoundary.kind == "managed_organizations"),
            func.bool_or(UserRoleBoundary.kind == "platform"),
        )
        .where(UserRoleBoundary.role_id.in_(role_ids))
        .group_by(UserRoleBoundary.role_id)
    )
    placements = {
        role_id: RolePlacementSummary(organizations=0, managed=False, platform=False)
        for role_id in role_ids
    }
    for role_id, organizations, managed, platform in rows.all():
        placements[role_id] = RolePlacementSummary(
            organizations=int(organizations), managed=bool(managed), platform=bool(platform)
        )
    return placements


async def attach_role_summaries(session: AsyncSession, roles: list[RolePublic]) -> None:
    """Fill the consumer counts, holders, grants and placements of ``roles``."""
    if not roles:
        return
    role_ids = [role.id for role in roles]
    counts = await get_consumer_counts(session, role_ids)
    holders = await get_role_holders(session, role_ids)
    permissions = await get_role_permissions(session, role_ids)
    placements = await get_role_placements(session, role_ids)
    for role in roles:
        role.consumer_counts = counts[role.id]
        role.holders = holders.get(role.id, 0)
        role.grants = permissions[role.id]
        role.placements = placements[role.id]


async def create_role(
    session: AsyncSession,
    *,
    name: str,
    description: str | None,
    actor_email: str,
) -> RolePublic:
    """Create a role (shared by the HTTP handler and worker-local calls)."""
    from src.models import Role as RoleORM
    from src.models import RolePublic
    from src.services.audit import emit_audit

    now = datetime.now(timezone.utc)

    role = RoleORM(
        name=name,
        description=description,
        created_by=actor_email,
        created_at=now,
        updated_at=now,
    )

    session.add(role)
    await session.flush()
    await session.refresh(role)

    logger.info(f"Created role {role.id}: {log_safe(role.name)}")

    from src.core.cache import invalidate_role

    # Invalidate cache (roles are global, no org_id needed)
    await invalidate_role(None, str(role.id))

    await emit_audit(
        session,
        "role.create",
        resource_type="role",
        resource_id=role.id,
        details={"name": role.name},
    )
    public = RolePublic.model_validate(role)
    await attach_role_summaries(session, [public])
    return public


async def get_role(
    session: AsyncSession,
    *,
    role_id: UUID,
    include_counts: bool = True,
    include_builtin: bool = False,
) -> RolePublic:
    """Get a role by ID. Raises 404 when missing, or builtin unless
    ``include_builtin``.

    Builtin roles (Platform Admin, User, Platform Operator, Secrets Reader)
    are hidden from this surface unless asked for (the Roles UI shows them
    read-only) — see `shared.builtin_roles`; their permission sets are
    readable at ``/api/roles/{role_id}/permissions``.
    ``include_counts=False`` omits the consumer counts (they count users in
    every organization, which only a Platform Admin may see).
    """
    from src.models import Role as RoleORM
    from src.models import RolePublic

    result = await session.execute(select(RoleORM).where(RoleORM.id == role_id))
    role = result.scalar_one_or_none()

    if not role or (role.is_builtin and not include_builtin):
        raise RoleServiceError(404, "Role not found")

    public = RolePublic.model_validate(role)
    if include_counts:
        await attach_role_summaries(session, [public])
    return public


async def list_roles(
    session: AsyncSession,
    *,
    search: str | None = None,
    sort_by: Literal["name", "created"] = "name",
    sort_direction: Literal["asc", "desc"] = "asc",
    limit: int | None = None,
    offset: int = 0,
    include_builtin: bool = False,
    include_counts: bool = True,
) -> tuple[list[RolePublic], int]:
    """List roles with inline consumer counts.

    Returns ``(items, total)`` — the caller sets the ``X-Total-Count``
    header from ``total``. Defaults match the historical handler (sort by
    name ascending, unbounded when ``limit`` is None, builtin roles hidden).
    ``include_builtin`` adds Platform Admin, User, Platform Operator and
    Secrets Reader (read-only; for the roles UI). ``include_counts=False``
    omits the consumer counts (see ``get_role``).
    """
    from src.models import Role as RoleORM
    from src.models import RolePublic

    query = select(RoleORM)
    if not include_builtin:
        query = query.where(RoleORM.is_builtin.is_(False))
    if search and (term := search.strip()):
        pattern = f"%{term}%"
        query = query.where(
            RoleORM.name.ilike(pattern) | RoleORM.description.ilike(pattern)
        )

    total = await session.scalar(
        select(func.count()).select_from(query.order_by(None).subquery())
    )

    sort_expression = RoleORM.name if sort_by == "name" else RoleORM.created_at
    if sort_direction == "desc":
        sort_expression = sort_expression.desc()
    else:
        sort_expression = sort_expression.asc()
    query = query.order_by(sort_expression, RoleORM.id.asc())
    if limit is not None:
        query = query.offset(offset).limit(limit)
    result = await session.execute(query)
    roles = result.scalars().all()

    out = [RolePublic.model_validate(r) for r in roles]
    if include_counts:
        await attach_role_summaries(session, out)
    return out, total or 0


async def update_role(
    session: AsyncSession,
    *,
    role_id: UUID,
    name: str | None = None,
    description: str | None = None,
    actor_email: str,
) -> RolePublic:
    """Update a role. Only non-None fields are applied.

    Raises 404 when missing, 409 when builtin (Platform Admin, User,
    Platform Operator can't be renamed/described until R3a ships their UI —
    see `shared.builtin_roles`). Like the historical handler, the returned
    payload carries no inline consumer counts.
    """
    from src.models import Role as RoleORM
    from src.models import RolePublic
    from src.services.audit import emit_audit

    result = await session.execute(select(RoleORM).where(RoleORM.id == role_id))
    role = result.scalar_one_or_none()

    if not role:
        raise RoleServiceError(404, "Role not found")
    if role.is_builtin:
        raise RoleServiceError(409, "Builtin roles cannot be modified")

    if name is not None:
        role.name = name
    if description is not None:
        role.description = description

    role.updated_at = datetime.now(timezone.utc)

    await session.flush()
    await session.refresh(role)

    logger.info(f"Updated role {log_safe(role_id)}")

    from src.core.cache import invalidate_role
    from shared.role_cache import (
        invalidate_role as invalidate_user_role_cache_for_role,
    )

    # Invalidate cache (roles are global, no org_id needed)
    await invalidate_role(None, str(role_id))

    # Per-user role cache: a rename changes role_names for every user holding
    # this role, so sweep all entries containing role_id.
    await invalidate_user_role_cache_for_role(role_id)

    changed_fields = [
        k
        for k, v in (
            ("name", name),
            ("description", description),
        )
        if v is not None
    ]
    await emit_audit(
        session,
        "role.update",
        resource_type="role",
        resource_id=role.id,
        details={"name": role.name, "changed_fields": changed_fields},
    )
    return RolePublic.model_validate(role)


async def delete_role(
    session: AsyncSession,
    *,
    role_id: UUID,
) -> str:
    """Delete a role, cascading assignments.

    Returns the deleted role's name (for audit/tests). Raises 404 when
    missing; a role bound to any solution-managed entity is refused by
    the solution guard (409 propagates unchanged).
    """
    from src.models import Role as RoleORM
    from src.services.audit import emit_audit
    from src.services.solutions.guard import (
        assert_role_not_bound_to_solution_managed,
    )

    result = await session.execute(select(RoleORM).where(RoleORM.id == role_id))
    role = result.scalar_one_or_none()

    if not role:
        raise RoleServiceError(404, "Role not found")
    if role.is_builtin:
        raise RoleServiceError(409, "Builtin roles cannot be deleted")
    from src.models import User as UserORM

    base_users = await session.scalar(
        select(func.count()).select_from(UserORM).where(UserORM.base_role_id == role_id)
    )
    if base_users:
        raise RoleServiceError(
            409,
            f"This role is the base role of {base_users} user(s); give them another base role first",
        )

    # A role assigned to a solution-managed entity has deploy-owned bindings;
    # deleting it would cascade-strip them outside deploy (Codex R4). Refuse.
    await assert_role_not_bound_to_solution_managed(session, role_id)

    deleted_name = role.name
    await session.delete(role)
    await session.flush()
    logger.info(f"Deleted role {log_safe(role_id)}")

    from src.core.cache import invalidate_role
    from shared.role_cache import (
        invalidate_role as invalidate_user_role_cache_for_role,
    )

    # Invalidate cache (roles are global, no org_id needed)
    await invalidate_role(None, str(role_id))

    # Per-user role cache: deleting a role means every user holding it loses
    # the membership; clear all entries containing role_id.
    await invalidate_user_role_cache_for_role(role_id)

    await emit_audit(
        session,
        "role.delete",
        resource_type="role",
        resource_id=role_id,
        details={"name": deleted_name},
    )
    return deleted_name


async def list_role_users(
    session: AsyncSession,
    *,
    role_id: UUID,
    reach: OrgReach,
    search: str | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> RoleUsersResponse:
    """Users assigned to a role, limited to the organizations in ``reach``
    (the caller's roleassignments.read) before counting and paging.

    Like the historical handler, a role with no assignments (or an
    unknown role id) returns an empty response, not a 404.
    """
    from src.models import Organization as OrganizationORM
    from src.models import RoleUsersResponse, RoleUserSummary
    from src.models import User as UserORM
    from src.models import UserRole as UserRoleORM

    query = (
        select(
            UserORM,
            OrganizationORM.name,
            OrganizationORM.is_provider,
        )
        .join(UserRoleORM, UserRoleORM.user_id == UserORM.id)
        .outerjoin(OrganizationORM, OrganizationORM.id == UserORM.organization_id)
        .where(
            UserRoleORM.role_id == role_id,
            UserORM.is_system.is_(False),
        )
    )
    reach_filter = reach.where(UserORM.organization_id)
    if reach_filter is not None:
        query = query.where(reach_filter)
    if search and (term := search.strip()):
        pattern = f"%{term}%"
        query = query.where(
            UserORM.name.ilike(pattern) | UserORM.email.ilike(pattern)
        )

    total = await session.scalar(
        select(func.count()).select_from(query.order_by(None).subquery())
    )
    query = query.order_by(
        func.coalesce(UserORM.name, UserORM.email), UserORM.email
    )
    if limit is not None:
        query = query.offset(offset).limit(limit)

    result = await session.execute(query)
    users = [
        RoleUserSummary(
            id=assigned_user.id,
            name=assigned_user.name,
            email=assigned_user.email,
            organization_id=assigned_user.organization_id,
            organization_name=organization_name,
            organization_is_provider=bool(organization_is_provider),
        )
        for assigned_user, organization_name, organization_is_provider in result.all()
    ]
    await _attach_boundaries(session, role_id=role_id, users=users)
    return RoleUsersResponse(
        user_ids=[str(assigned_user.id) for assigned_user in users],
        users=users,
        total=total or 0,
    )


async def _attach_boundaries(session: AsyncSession, *, role_id: UUID, users) -> None:
    """Fill in where ``role_id`` applies for each listed user (two queries)."""
    from src.models import Organization as OrganizationORM
    from src.models.contracts.role_assignments import RoleBoundaryPublic
    from src.models.orm.users import UserRoleBoundary

    if not users:
        return
    rows = (
        await session.execute(
            select(
                UserRoleBoundary.user_id,
                UserRoleBoundary.kind,
                UserRoleBoundary.organization_id,
                OrganizationORM.name,
            )
            .outerjoin(OrganizationORM, OrganizationORM.id == UserRoleBoundary.organization_id)
            .where(
                UserRoleBoundary.role_id == role_id,
                UserRoleBoundary.user_id.in_([user.id for user in users]),
            )
            .order_by(UserRoleBoundary.kind, OrganizationORM.name)
        )
    ).all()
    by_user: dict[UUID, list[RoleBoundaryPublic]] = {}
    for user_id, kind, organization_id, organization_name in rows:
        by_user.setdefault(user_id, []).append(
            RoleBoundaryPublic(
                kind=kind, organization_id=organization_id, organization_name=organization_name
            )
        )
    for user in users:
        user.boundaries = by_user.get(user.id, [])


async def _require_assignment_target(
    session: AsyncSession, caller: "Caller", operation: str, role, user_id: UUID
):
    """roleassignments.readwrite at the user's organization, a Platform
    Admin for a privileged user, and the grant ceiling for ``role``.
    Returns the user, or None when they don't exist."""
    from src.models import User as UserORM
    from src.services.authorization.enforce import (
        held_permissions_by_user,
        org_target,
        require_operation,
        require_unprotected,
    )
    from src.services.authorization.privilege import is_privileged_principal
    from src.services.user_role_assignments import RoleAssignmentError, check_role_change

    user = await session.get(UserORM, user_id)
    if user is None:
        return None
    require_operation(caller, operation, org_target(user.organization_id))
    held = (await held_permissions_by_user(session, [user_id])).get(user_id, frozenset())
    require_unprotected(caller, is_privileged_principal(held))
    try:
        check_role_change(caller, role, held)
    except RoleAssignmentError as exc:
        raise RoleServiceError(exc.status_code, exc.detail) from None
    return user


async def _load_role_info(session: AsyncSession, role_id: UUID):
    from src.services.user_role_assignments import load_roles

    role = (await load_roles(session, [role_id])).get(role_id)
    if role is None:
        raise RoleServiceError(404, "Role not found")
    return role


async def assign_users_to_role(
    session: AsyncSession,
    caller: "Caller",
    *,
    role_id: UUID,
    user_ids: list[str],
) -> None:
    """Assign users to a role (batch; skips unknown and already-assigned).

    Each entry is a user UUID or an email address (resolved to a UUID;
    unresolvable entries are skipped with a warning, exactly like the
    historical handler). Raises 409 for a builtin role id. Each user is
    authorized (roleassignments.readwrite at their organization, the grant
    ceiling, protected users) and gets the default boundary: their home
    organization, or Platform for a Global user. One refused user refuses
    the whole batch.
    """
    from src.models import User as UserORM
    from src.models import UserRole as UserRoleORM
    from src.services.audit import emit_audit
    from src.services.authorization.enforce import operation_reach
    from src.services.user_role_assignments import (
        RoleAssignmentError,
        check_boundaries,
        default_boundaries,
        insert_assignment,
    )
    from shared.builtin_roles import is_builtin_role_id
    from shared.system_account_guard import (
        SYSTEM_ACCOUNT_ROLE_MESSAGE,
        is_system_account,
    )

    operation = "roles.users.assign"
    reach = operation_reach(caller, operation)
    if is_builtin_role_id(role_id):
        raise RoleServiceError(409, "Builtin roles cannot be assigned entities")
    role = await _load_role_info(session, role_id)

    # Track newly-assigned users so we can invalidate the per-user role cache.
    # Skip users already assigned (no cache impact) and users that didn't resolve.
    affected_user_ids: list[UUID] = []

    for user_id_str in user_ids:
        # Try to parse as UUID, otherwise lookup by email
        try:
            user_uuid = UUID(user_id_str)
        except ValueError:
            result = await session.execute(
                select(UserORM.id).where(UserORM.email == user_id_str)
            )
            user_uuid = result.scalar_one_or_none()
            if not user_uuid:
                logger.warning(f"User {log_safe(user_id_str)} not found, skipping")
                continue

        if is_system_account(user_uuid):
            raise RoleServiceError(422, SYSTEM_ACCOUNT_ROLE_MESSAGE)

        # Check if already assigned
        existing = await session.execute(
            select(UserRoleORM).where(
                UserRoleORM.user_id == user_uuid,
                UserRoleORM.role_id == role_id,
            )
        )
        if existing.scalar_one_or_none():
            continue

        user = await _require_assignment_target(session, caller, operation, role, user_uuid)
        if user is None:
            logger.warning(f"User {log_safe(user_id_str)} not found, skipping")
            continue
        boundaries = default_boundaries(user.organization_id)
        try:
            check_boundaries(caller, reach, role, boundaries)
        except RoleAssignmentError as exc:
            raise RoleServiceError(exc.status_code, exc.detail) from None
        await insert_assignment(
            session,
            user_id=user_uuid,
            role_id=role_id,
            boundaries=boundaries,
            assigned_by=caller.principal.email,
        )
        affected_user_ids.append(user_uuid)

    await session.flush()
    logger.info(f"Assigned users to role {log_safe(role_id)}")

    from src.core.cache import invalidate_role_users
    from shared.role_cache import invalidate_user as invalidate_user_role_cache

    # Invalidate cache (roles are global, no org_id needed)
    await invalidate_role_users(None, str(role_id))

    # Per-user role cache: drop entries for each newly-assigned user so the
    # next read sees the new role membership.
    for affected in affected_user_ids:
        await invalidate_user_role_cache(affected)

    await emit_audit(
        session,
        "role.user_assigned",
        resource_type="role",
        resource_id=role_id,
        details={"user_ids": user_ids},
    )


async def remove_users_from_role(
    session: AsyncSession,
    caller: "Caller",
    *,
    role_id: UUID,
    user_ids: list[UUID],
    operation: str,
) -> list[UUID]:
    """Remove ``role_id`` from each user that holds it; returns who lost it.

    Each user who holds the role is authorized like an assignment
    (roleassignments.readwrite at their organization, the grant ceiling,
    protected users); one refusal refuses the whole call. The assignment's
    boundary rows go with it (database cascade).
    """
    from src.core.cache import invalidate_role_users
    from shared.role_cache import invalidate_user as invalidate_user_role_cache
    from src.models import UserRole as UserRoleORM
    from src.services.authorization.enforce import operation_reach

    operation_reach(caller, operation)
    holders = list(
        (
            await session.execute(
                select(UserRoleORM.user_id).where(
                    UserRoleORM.role_id == role_id, UserRoleORM.user_id.in_(user_ids)
                )
            )
        ).scalars()
    )
    if not holders:
        return []
    role = await _load_role_info(session, role_id)
    for user_id in holders:
        await _require_assignment_target(session, caller, operation, role, user_id)

    await session.execute(
        delete(UserRoleORM).where(
            UserRoleORM.role_id == role_id, UserRoleORM.user_id.in_(holders)
        )
    )
    await session.flush()
    await invalidate_role_users(None, str(role_id))
    for user_id in holders:
        await invalidate_user_role_cache(user_id)
    return holders


async def list_role_forms(
    session: AsyncSession,
    *,
    role_id: UUID,
) -> RoleFormsResponse:
    """Form IDs assigned to a role (empty, not 404, when none)."""
    from src.models import FormRole as FormRoleORM
    from src.models import RoleFormsResponse

    result = await session.execute(
        select(FormRoleORM.form_id).where(FormRoleORM.role_id == role_id)
    )
    form_ids = [str(fid) for fid in result.scalars().all()]
    return RoleFormsResponse(form_ids=form_ids)


async def assign_forms_to_role(
    session: AsyncSession,
    *,
    role_id: UUID,
    form_ids: list[str],
    actor_email: str,
) -> None:
    """Assign forms to a role (batch; skips already-assigned).

    Raises 404 when a form does not exist. A malformed form id raises
    ``ValueError`` (historical behavior — surfaced as a 500 on the HTTP
    path). Solution-managed forms are refused by the guard (409
    propagates unchanged). No audit row, like the historical handler.
    Raises 409 for a builtin role id.
    """
    from src.models import Form as FormORM
    from src.models import FormRole as FormRoleORM
    from src.services.solutions.guard import (
        assert_entity_id_not_solution_managed,
    )
    from shared.builtin_roles import is_builtin_role_id

    if is_builtin_role_id(role_id):
        raise RoleServiceError(409, "Builtin roles cannot be assigned entities")

    now = datetime.now(timezone.utc)

    for form_id_str in form_ids:
        form_uuid = UUID(form_id_str)

        # Verify form exists before creating assignment
        form_result = await session.execute(
            select(FormORM.id).where(FormORM.id == form_uuid)
        )
        if not form_result.scalar_one_or_none():
            raise RoleServiceError(
                404, f"Form with ID '{form_id_str}' not found"
            )
        # Role bindings are portable + solution-owned: locked for managed forms.
        await assert_entity_id_not_solution_managed(session, FormORM, form_uuid)

        # Check if already assigned
        existing = await session.execute(
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
            assigned_by=actor_email,
            assigned_at=now,
        )
        session.add(form_role)

    await session.flush()
    logger.info(f"Assigned forms to role {log_safe(role_id)}")

    from src.core.cache import invalidate_role_forms

    # Invalidate cache (roles are global, no org_id needed)
    await invalidate_role_forms(None, str(role_id))
