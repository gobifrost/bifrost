"""The caller description the authorization evaluator decides against.

An ``AuthorizationContext`` is built from the person's base role, the roles
they hold beyond it, and where each of those roles applies (its boundaries).
It deliberately does NOT carry the provider-org scope-bypass flag: that
non-admin path is exactly what the role/boundary model replaces.

Nothing in request handling builds or reads a context yet; R3 wires it in
domain by domain.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.builtin_roles import PLATFORM_ADMIN_ROLE_ID
from src.models.orm.users import RolePermission, User, UserRole, UserRoleBoundary


class BoundaryKind(StrEnum):
    """Where a role assignment applies (mirrors ``user_role_boundaries.kind``)."""

    # Exactly one organization (``organization_id`` set).
    ORGANIZATION = "organization"
    # Every customer organization: not the provider org, not Global.
    MANAGED_ORGANIZATIONS = "managed_organizations"
    # Global / platform-level targets.
    PLATFORM = "platform"


@dataclass(frozen=True)
class Boundary:
    kind: BoundaryKind
    organization_id: UUID | None = None


@dataclass(frozen=True)
class RoleGrant:
    """One additional role the person holds, with where it applies."""

    role_id: UUID
    permissions: frozenset[str]
    boundaries: tuple[Boundary, ...]


@dataclass(frozen=True)
class AuthorizationContext:
    user_id: UUID
    home_organization_id: UUID | None
    base_role_id: UUID
    is_external: bool
    # Permissions of the base role. Platform Admin's set is empty here: it is
    # recognised by ``is_platform_admin``, not by a stored wildcard row.
    base_permissions: frozenset[str]
    role_grants: tuple[RoleGrant, ...] = ()

    @property
    def is_platform_admin(self) -> bool:
        return self.base_role_id == PLATFORM_ADMIN_ROLE_ID


async def build_authorization_context(
    db: AsyncSession, user_id: UUID
) -> AuthorizationContext:
    """Load the context for ``user_id``: two queries, no per-role lookups."""
    user_row = (
        await db.execute(
            select(User.organization_id, User.base_role_id, User.is_external).where(
                User.id == user_id
            )
        )
    ).one()
    home_organization_id, base_role_id, is_external = user_row

    boundary_rows = (
        await db.execute(
            select(UserRoleBoundary.role_id, UserRoleBoundary.kind, UserRoleBoundary.organization_id)
            .join(
                UserRole,
                (UserRole.user_id == UserRoleBoundary.user_id)
                & (UserRole.role_id == UserRoleBoundary.role_id),
            )
            .where(UserRoleBoundary.user_id == user_id)
        )
    ).all()
    boundaries_by_role: dict[UUID, list[Boundary]] = {}
    for role_id, kind, organization_id in boundary_rows:
        boundaries_by_role.setdefault(role_id, []).append(Boundary(BoundaryKind(kind), organization_id))

    permission_rows = (
        await db.execute(
            select(RolePermission.role_id, RolePermission.permission).where(
                RolePermission.role_id.in_([base_role_id, *boundaries_by_role])
            )
        )
    ).all()
    permissions_by_role: dict[UUID, set[str]] = {}
    for role_id, permission in permission_rows:
        permissions_by_role.setdefault(role_id, set()).add(permission)

    return AuthorizationContext(
        user_id=user_id,
        home_organization_id=home_organization_id,
        base_role_id=base_role_id,
        is_external=bool(is_external) and base_role_id != PLATFORM_ADMIN_ROLE_ID,
        base_permissions=frozenset(permissions_by_role.get(base_role_id, ())),
        role_grants=tuple(
            RoleGrant(
                role_id=role_id,
                permissions=frozenset(permissions_by_role.get(role_id, ())),
                boundaries=tuple(boundaries),
            )
            for role_id, boundaries in boundaries_by_role.items()
        ),
    )
