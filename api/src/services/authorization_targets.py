"""Discover the exact authorization contexts a human may select."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.authorization_scopes import PLATFORM_SUPERUSER_SCOPE, implied_scopes
from src.core.principal import UserPrincipal
from src.models.orm.organization_groups import OrganizationGroupMembership
from src.models.orm.organizations import Organization
from src.models.orm.role_assignments import RoleAssignment, RoleAssignmentBoundary
from src.models.orm.users import Role


@dataclass(frozen=True, slots=True)
class AuthorizationOrganizationTarget:
    id: UUID
    name: str
    is_provider: bool
    capabilities: frozenset[str]
    role_ids: frozenset[UUID]


@dataclass(frozen=True, slots=True)
class AuthorizationTargets:
    organizations: tuple[AuthorizationOrganizationTarget, ...]
    platform_capabilities: frozenset[str]
    managed_capabilities: frozenset[str]


@dataclass(frozen=True, slots=True)
class _AssignmentBoundary:
    assignment_id: UUID
    role_id: UUID
    capabilities: frozenset[str]
    kind: str
    organization_id: UUID | None
    organization_group_id: UUID | None


async def discover_authorization_targets(
    db: AsyncSession,
    *,
    requester: UserPrincipal,
) -> AuthorizationTargets:
    """Expand Role-assignment selectors into executable request contexts.

    Organization groups and Managed organizations are assignment selectors.
    The response therefore expands them into exact Organization contexts while
    also retaining Managed as the explicit cross-customer collection context.
    Discovery is only a UI/bootstrap aid; every subsequent request resolves and
    re-authorizes its selected ``X-Bifrost-Boundary``.
    """

    rows = (
        await db.execute(
            select(RoleAssignment, Role, RoleAssignmentBoundary)
            .join(Role, Role.id == RoleAssignment.role_id)
            .join(
                RoleAssignmentBoundary,
                RoleAssignmentBoundary.role_assignment_id == RoleAssignment.id,
            )
            .where(RoleAssignment.user_id == requester.user_id)
        )
    ).all()
    boundaries = tuple(
        _AssignmentBoundary(
            assignment_id=assignment.id,
            role_id=role.id,
            capabilities=frozenset(role.capabilities or ()),
            kind=boundary.boundary_kind,
            organization_id=boundary.organization_id,
            organization_group_id=boundary.organization_group_id,
        )
        for assignment, role, boundary in rows
    )
    wildcard_assignments = {
        row.assignment_id
        for row in boundaries
        if row.kind == "platform"
        and PLATFORM_SUPERUSER_SCOPE in row.capabilities
    }

    organizations = tuple(
        (
            await db.execute(
                select(Organization)
                .where(Organization.is_active.is_(True))
                .order_by(Organization.is_provider.desc(), Organization.name)
            )
        )
        .scalars()
        .all()
    )
    group_memberships = {
        (group_id, organization_id)
        for group_id, organization_id in (
            await db.execute(
                select(
                    OrganizationGroupMembership.organization_group_id,
                    OrganizationGroupMembership.organization_id,
                )
            )
        ).all()
    }

    organization_targets: list[AuthorizationOrganizationTarget] = []
    for organization in organizations:
        capabilities: set[str] = set()
        role_ids: set[UUID] = set()
        for boundary in boundaries:
            covers = boundary.assignment_id in wildcard_assignments
            if not covers and boundary.kind == "organization":
                covers = boundary.organization_id == organization.id
            elif not covers and boundary.kind == "organization_group":
                covers = (
                    boundary.organization_group_id,
                    organization.id,
                ) in group_memberships
            elif not covers and boundary.kind == "managed_organizations":
                covers = not organization.is_provider
            if not covers:
                continue
            capabilities.update(boundary.capabilities)
            role_ids.add(boundary.role_id)
        effective = implied_scopes(capabilities)
        if not effective:
            continue
        organization_targets.append(
            AuthorizationOrganizationTarget(
                id=organization.id,
                name=organization.name,
                is_provider=organization.is_provider,
                capabilities=effective,
                role_ids=frozenset(role_ids),
            )
        )

    def boundary_capabilities(kind: str) -> frozenset[str]:
        return implied_scopes(
            capability
            for boundary in boundaries
            if boundary.assignment_id in wildcard_assignments or boundary.kind == kind
            for capability in boundary.capabilities
        )

    return AuthorizationTargets(
        organizations=tuple(organization_targets),
        platform_capabilities=boundary_capabilities("platform"),
        managed_capabilities=boundary_capabilities("managed_organizations"),
    )


__all__ = [
    "AuthorizationOrganizationTarget",
    "AuthorizationTargets",
    "discover_authorization_targets",
]
