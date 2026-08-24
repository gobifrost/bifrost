"""Boundary-aware Role assignment domain service."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from shared.role_cache import invalidate_user
from src.core.constants import PLATFORM_ADMIN_ROLE_ID
from src.core.principal import UserPrincipal
from src.models.orm.organization_groups import (
    OrganizationGroup,
    OrganizationGroupMembership,
)
from src.models.orm.organizations import Organization
from src.models.orm.role_assignments import RoleAssignment, RoleAssignmentBoundary
from src.models.orm.users import Role, User
from src.services.authorization import (
    AuthorizationBoundary,
    AuthorizationBoundaryKind,
    AuthorizationContext,
    authorize_capability,
)


@dataclass(frozen=True, slots=True)
class RoleAssignmentBoundarySpec:
    kind: str
    organization_id: UUID | None = None
    organization_group_id: UUID | None = None


def infer_legacy_role_assignment_boundaries(
    authorization: AuthorizationContext,
) -> list[RoleAssignmentBoundarySpec]:
    """Infer the boundary for old Role-assignment payloads without boundaries."""

    selected = authorization.selected_boundary
    if selected.kind is AuthorizationBoundaryKind.ORGANIZATION:
        return [
            RoleAssignmentBoundarySpec(
                kind="organization",
                organization_id=selected.organization_id,
            )
        ]
    if selected.kind is AuthorizationBoundaryKind.PLATFORM:
        return [RoleAssignmentBoundarySpec(kind="platform")]
    if selected.kind is AuthorizationBoundaryKind.MANAGED_ORGANIZATIONS:
        return [
            RoleAssignmentBoundarySpec(
                kind="managed_organizations",
            )
        ]
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=(
            "Role assignment boundaries are required for this authorization boundary"
        ),
    )


async def _authorization_boundaries_for_assignment(
    db: AsyncSession,
    boundary: RoleAssignmentBoundarySpec,
) -> tuple[AuthorizationBoundary, ...]:
    if boundary.kind == "organization":
        assert boundary.organization_id is not None
        return (AuthorizationBoundary.organization(boundary.organization_id),)
    if boundary.kind == "organization_group":
        assert boundary.organization_group_id is not None
        organization_ids = (
            (
                await db.execute(
                    select(OrganizationGroupMembership.organization_id).where(
                        OrganizationGroupMembership.organization_group_id
                        == boundary.organization_group_id
                    )
                )
            )
            .scalars()
            .all()
        )
        if not organization_ids:
            # An empty pod grants the assignee no customer authority. Managed
            # here is only the administrator's control boundary for creating
            # or removing the empty group selection; effective authorization
            # resolves membership independently in authorization.py.
            return (AuthorizationBoundary.managed_organizations(),)
        return tuple(
            AuthorizationBoundary.organization(organization_id)
            for organization_id in organization_ids
        )
    if boundary.kind == "managed_organizations":
        return (AuthorizationBoundary.managed_organizations(),)
    if boundary.kind == "platform":
        return (AuthorizationBoundary.platform(),)
    raise ValueError(f"Unknown assignment boundary: {boundary.kind}")


async def _validate_targets(
    db: AsyncSession,
    *,
    role: Role,
    user_id: UUID,
    boundaries: list[RoleAssignmentBoundarySpec],
) -> None:
    if not boundaries:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="A Role assignment requires at least one boundary",
        )
    if await db.get(User, user_id) is None:
        raise HTTPException(status_code=404, detail="User not found")

    allowed_kinds = {
        "organization",
        "organization_group",
        "managed_organizations",
        "platform",
    }
    for boundary in boundaries:
        valid_shape = (
            boundary.kind == "organization"
            and boundary.organization_id is not None
            and boundary.organization_group_id is None
        ) or (
            boundary.kind == "organization_group"
            and boundary.organization_group_id is not None
            and boundary.organization_id is None
        ) or (
            boundary.kind in {"managed_organizations", "platform"}
            and boundary.organization_id is None
            and boundary.organization_group_id is None
        )
        if boundary.kind not in allowed_kinds or not valid_shape:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Invalid {boundary.kind!r} Role assignment boundary",
            )

    identities = {
        (boundary.kind, boundary.organization_id, boundary.organization_group_id)
        for boundary in boundaries
    }
    if len(identities) != len(boundaries):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Duplicate boundary selections are not allowed",
        )
    if role.id == PLATFORM_ADMIN_ROLE_ID and identities != {
        ("platform", None, None)
    }:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Platform Admin must be assigned only at Global",
        )

    organization_ids = {
        boundary.organization_id
        for boundary in boundaries
        if boundary.organization_id is not None
    }
    if organization_ids:
        found = set(
            (
                await db.execute(
                    select(Organization.id).where(Organization.id.in_(organization_ids))
                )
            )
            .scalars()
            .all()
        )
        if found != organization_ids:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="One or more assignment organizations do not exist",
            )

    group_ids = {
        boundary.organization_group_id
        for boundary in boundaries
        if boundary.organization_group_id is not None
    }
    if group_ids:
        found_groups = set(
            (
                await db.execute(
                    select(OrganizationGroup.id).where(
                        OrganizationGroup.id.in_(group_ids)
                    )
                )
            )
            .scalars()
            .all()
        )
        if found_groups != group_ids:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="One or more assignment organization groups do not exist",
            )


async def replace_role_assignment(
    db: AsyncSession,
    *,
    requester: UserPrincipal,
    user_id: UUID,
    role_id: UUID,
    boundaries: list[RoleAssignmentBoundarySpec],
) -> RoleAssignment:
    """Create or atomically replace one user's boundary set for a Role."""

    role = await db.get(Role, role_id)
    if role is None:
        raise HTTPException(status_code=404, detail="Role not found")
    await _validate_targets(
        db,
        role=role,
        user_id=user_id,
        boundaries=boundaries,
    )

    assignment = await db.scalar(
        select(RoleAssignment)
        .options(selectinload(RoleAssignment.boundaries))
        .where(
            RoleAssignment.user_id == user_id,
            RoleAssignment.role_id == role_id,
        )
    )
    # Replacing an assignment can remove authority as well as add it. Require
    # coverage for every existing boundary too, otherwise an administrator for
    # one organization could erase the same Role's grant in another.
    authorization_specs = list(boundaries)
    if assignment is not None:
        existing_specs = [
            RoleAssignmentBoundarySpec(
                kind=boundary.boundary_kind,
                organization_id=boundary.organization_id,
                organization_group_id=boundary.organization_group_id,
            )
            for boundary in assignment.boundaries
        ]
        identities = {
            (boundary.kind, boundary.organization_id, boundary.organization_group_id)
            for boundary in authorization_specs
        }
        authorization_specs.extend(
            boundary
            for boundary in existing_specs
            if (
                boundary.kind,
                boundary.organization_id,
                boundary.organization_group_id,
            )
            not in identities
        )

    checked: set[AuthorizationBoundary] = set()
    for boundary in authorization_specs:
        for effective in await _authorization_boundaries_for_assignment(db, boundary):
            if effective in checked:
                continue
            checked.add(effective)
            await authorize_capability(
                db,
                requester=requester,
                selected_boundary=effective,
                capability="roles.readwrite",
            )

    persisted_boundaries = [
        RoleAssignmentBoundary(
            boundary_kind=boundary.kind,
            organization_id=boundary.organization_id,
            organization_group_id=boundary.organization_group_id,
        )
        for boundary in boundaries
    ]
    if assignment is None:
        assignment = RoleAssignment(
            user_id=user_id,
            role_id=role_id,
            assigned_by_user_id=requester.user_id,
            boundaries=persisted_boundaries,
        )
        db.add(assignment)
    else:
        assignment.assigned_by_user_id = requester.user_id
        assignment.boundaries = persisted_boundaries
    await db.flush()
    await invalidate_user(user_id)
    return assignment


async def delete_role_assignment(
    db: AsyncSession,
    *,
    requester: UserPrincipal,
    user_id: UUID,
    role_id: UUID,
) -> bool:
    """Delete one Role assignment after authorizing every covered boundary."""

    assignment = await db.scalar(
        select(RoleAssignment)
        .options(selectinload(RoleAssignment.boundaries))
        .where(
            RoleAssignment.user_id == user_id,
            RoleAssignment.role_id == role_id,
        )
    )
    if assignment is None:
        return False

    checked: set[AuthorizationBoundary] = set()
    for boundary in assignment.boundaries:
        spec = RoleAssignmentBoundarySpec(
            kind=boundary.boundary_kind,
            organization_id=boundary.organization_id,
            organization_group_id=boundary.organization_group_id,
        )
        for effective in await _authorization_boundaries_for_assignment(db, spec):
            if effective in checked:
                continue
            checked.add(effective)
            await authorize_capability(
                db,
                requester=requester,
                selected_boundary=effective,
                capability="roles.readwrite",
            )

    await db.execute(
        delete(RoleAssignment).where(RoleAssignment.id == assignment.id)
    )
    await db.flush()
    await invalidate_user(user_id)
    return True
