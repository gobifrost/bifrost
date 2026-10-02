"""The caller description the authorization evaluator decides against.

An ``AuthorizationContext`` is built from the person's base role, the roles
they hold beyond it, and where each of those roles applies (its boundaries).
Everything is decided from one list of effective grants, (permission,
boundary) pairs; no role is recognised by its identity. Platform Admin is
whoever holds the wildcard permission at the platform boundary, which only
the built-in Platform Admin role carries (as a stored ``role_permissions``
row, never assignable to a custom role).

It deliberately does NOT carry the provider-org scope-bypass flag: that
non-admin path is exactly what the role/boundary model replaces.

``src.services.authorization.enforce`` builds one per request on the routes
cut over to the evaluator.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.builtin_roles import WILDCARD_PERMISSION
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


def _effective_grants(
    home_organization_id: UUID | None,
    base_permissions: Iterable[str],
    role_grants: Iterable[RoleGrant],
) -> tuple[tuple[str, Boundary], ...]:
    """Every (permission, boundary) a person holds: the base role at their
    home boundary, then each additional role at its boundaries."""
    home = (
        Boundary(BoundaryKind.PLATFORM)
        if home_organization_id is None
        else Boundary(BoundaryKind.ORGANIZATION, home_organization_id)
    )
    grants = [(permission, home) for permission in sorted(base_permissions)]
    for grant in role_grants:
        grants.extend(
            (permission, boundary)
            for permission in sorted(grant.permissions)
            for boundary in grant.boundaries
        )
    return tuple(grants)


def _holds_platform_wildcard(grants: Iterable[tuple[str, Boundary]]) -> bool:
    return any(
        permission == WILDCARD_PERMISSION and boundary.kind == BoundaryKind.PLATFORM
        for permission, boundary in grants
    )


@dataclass(frozen=True)
class AuthorizationContext:
    user_id: UUID
    home_organization_id: UUID | None
    base_role_id: UUID
    is_external: bool
    # Permissions of the base role (User or a custom role), held at the
    # person's home organization.
    base_permissions: frozenset[str]
    role_grants: tuple[RoleGrant, ...] = ()

    @property
    def effective_grants(self) -> tuple[tuple[str, Boundary], ...]:
        return _effective_grants(
            self.home_organization_id, self.base_permissions, self.role_grants
        )

    @property
    def is_platform_admin(self) -> bool:
        """Whether the person holds the wildcard at the platform boundary."""
        return _holds_platform_wildcard(self.effective_grants)

    @property
    def held_permissions(self) -> frozenset[str]:
        """Everything the person holds at any boundary. For deciding whether
        they are a protected target, not for deciding an operation (that is
        boundary-aware; see ``decide``)."""
        return frozenset(permission for permission, _ in self.effective_grants)


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

    base_permissions = frozenset(permissions_by_role.get(base_role_id, ()))
    role_grants = tuple(
        RoleGrant(
            role_id=role_id,
            permissions=frozenset(permissions_by_role.get(role_id, ())),
            boundaries=tuple(boundaries),
        )
        for role_id, boundaries in boundaries_by_role.items()
    )
    return AuthorizationContext(
        user_id=user_id,
        home_organization_id=home_organization_id,
        base_role_id=base_role_id,
        is_external=bool(is_external)
        and not _holds_platform_wildcard(
            _effective_grants(home_organization_id, base_permissions, role_grants)
        ),
        base_permissions=base_permissions,
        role_grants=role_grants,
    )
