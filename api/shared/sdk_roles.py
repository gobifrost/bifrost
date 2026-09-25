"""Shared business service for role operations.

Single implementation used by both entry points:

- the HTTP handlers (``api/src/routers/roles.py``) serving SDK/CLI
  callers, and
- a future engine-local dispatcher serving workflow children through
  the parent-side local transport.

Both paths can share DTOs, response fields, HTTP statuses/error precedence,
side effects, audit attribution, cache invalidation, and assignment
transaction behavior. Each caller must enforce platform-admin authority
before invoking these operations.

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

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.log_safety import log_safe

if TYPE_CHECKING:
    from src.models import (
        RoleConsumerCounts,
        RoleFormsResponse,
        RolePublic,
        RoleUsersResponse,
    )

logger = logging.getLogger(__name__)


class RoleServiceError(Exception):
    """Role operation failure with an HTTP-style status.

    Raised by the shared service so the HTTP handler (``HTTPException``)
    and the future local dispatcher (``ok: false`` frames) can map the
    same failure to their own transport. Solution-managed 409s arrive as
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
    from src.models.orm.knowledge_sources import (
        KnowledgeNamespaceRole as KnowledgeNamespaceRoleORM,
    )
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
        ("knowledge", KnowledgeNamespaceRoleORM),
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


async def create_role(
    session: AsyncSession,
    *,
    name: str,
    description: str | None,
    permissions: dict | None,
    actor_email: str,
) -> RolePublic:
    """Create a role (shared by the HTTP handler and the local dispatcher)."""
    from src.models import Role as RoleORM
    from src.models import RolePublic
    from src.services.audit import emit_audit

    now = datetime.now(timezone.utc)

    role = RoleORM(
        name=name,
        description=description,
        permissions=permissions or {},
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
    public.consumer_counts = (await get_consumer_counts(session, [role.id]))[role.id]
    return public


async def get_role(
    session: AsyncSession,
    *,
    role_id: UUID,
) -> RolePublic:
    """Get a role by ID. Raises 404 when missing."""
    from src.models import Role as RoleORM
    from src.models import RolePublic

    result = await session.execute(select(RoleORM).where(RoleORM.id == role_id))
    role = result.scalar_one_or_none()

    if not role:
        raise RoleServiceError(404, "Role not found")

    public = RolePublic.model_validate(role)
    public.consumer_counts = (await get_consumer_counts(session, [role.id]))[role.id]
    return public


async def list_roles(
    session: AsyncSession,
    *,
    search: str | None = None,
    sort_by: Literal["name", "created"] = "name",
    sort_direction: Literal["asc", "desc"] = "asc",
    limit: int | None = None,
    offset: int = 0,
) -> tuple[list[RolePublic], int]:
    """List roles with inline consumer counts.

    Returns ``(items, total)`` — the caller sets the ``X-Total-Count``
    header from ``total``. Defaults match the historical handler (sort by
    name ascending, unbounded when ``limit`` is None).
    """
    from src.models import Role as RoleORM
    from src.models import RolePublic

    query = select(RoleORM)
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

    counts_by_role = await get_consumer_counts(session, [role.id for role in roles])

    out: list[RolePublic] = []
    for r in roles:
        public = RolePublic.model_validate(r)
        public.consumer_counts = counts_by_role[r.id]
        out.append(public)
    return out, total or 0


async def update_role(
    session: AsyncSession,
    *,
    role_id: UUID,
    name: str | None = None,
    description: str | None = None,
    permissions: dict | None = None,
    actor_email: str,
) -> RolePublic:
    """Update a role. Only non-None fields are applied.

    Raises 404 when missing. Like the historical handler, the returned
    payload carries no inline consumer counts.
    """
    from src.models import Role as RoleORM
    from src.models import RolePublic
    from src.services.audit import emit_audit

    result = await session.execute(select(RoleORM).where(RoleORM.id == role_id))
    role = result.scalar_one_or_none()

    if not role:
        raise RoleServiceError(404, "Role not found")

    if name is not None:
        role.name = name
    if description is not None:
        role.description = description
    if permissions is not None:
        role.permissions = permissions

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
            ("permissions", permissions),
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
    search: str | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> RoleUsersResponse:
    """Users assigned to a role.

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
    return RoleUsersResponse(
        user_ids=[str(assigned_user.id) for assigned_user in users],
        users=users,
        total=total or 0,
    )


async def assign_users_to_role(
    session: AsyncSession,
    *,
    role_id: UUID,
    user_ids: list[str],
    actor_email: str,
) -> None:
    """Assign users to a role (batch; skips unknown and already-assigned).

    Each entry is a user UUID or an email address (resolved to a UUID;
    unresolvable entries are skipped with a warning, exactly like the
    historical handler).
    """
    from src.models import User as UserORM
    from src.models import UserRole as UserRoleORM
    from src.services.audit import emit_audit

    now = datetime.now(timezone.utc)
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

        # Check if already assigned
        existing = await session.execute(
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
            assigned_by=actor_email,
            assigned_at=now,
        )
        session.add(user_role)
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
    """
    from src.models import Form as FormORM
    from src.models import FormRole as FormRoleORM
    from src.services.solutions.guard import (
        assert_entity_id_not_solution_managed,
    )

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
