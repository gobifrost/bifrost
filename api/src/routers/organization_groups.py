"""Provider-owned organization groups used by authorization assignments."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

from shared.role_cache import invalidate_user
from src.core.auth import CurrentActiveUser
from src.core.db_deps import DbSession
from src.models import (
    Organization,
    OrganizationGroup,
    OrganizationGroupCreate,
    OrganizationGroupMembership,
    OrganizationGroupPublic,
    OrganizationGroupUpdate,
    RoleAssignment,
)
from src.models.orm.role_assignments import RoleAssignmentBoundary
from src.services.audit import emit_audit
from src.services.authorization import AuthorizationBoundary, authorize_capability


router = APIRouter(prefix="/api/organization-groups", tags=["Organization Groups"])


def _public(group: OrganizationGroup) -> OrganizationGroupPublic:
    return OrganizationGroupPublic(
        id=group.id,
        owner_organization_id=group.owner_organization_id,
        name=group.name,
        member_organization_ids=[
            membership.organization_id for membership in group.memberships
        ],
        created_at=group.created_at,
        updated_at=group.updated_at,
    )


async def _authorize(db: DbSession, user: CurrentActiveUser, capability: str) -> None:
    await authorize_capability(
        db,
        requester=user,
        selected_boundary=AuthorizationBoundary.managed_organizations(),
        capability=capability,
    )


async def _provider_organization_id(db: DbSession) -> UUID:
    provider_ids = list(
        (
            await db.execute(
                select(Organization.id).where(Organization.is_provider.is_(True))
            )
        )
        .scalars()
        .all()
    )
    if len(provider_ids) != 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Exactly one provider organization is required to manage "
                "organization groups"
            ),
        )
    return provider_ids[0]


async def _assignments_for_group(
    db: DbSession,
    group_id: UUID,
) -> list[RoleAssignment]:
    return list(
        (
            await db.execute(
                select(RoleAssignment)
                .join(RoleAssignmentBoundary)
                .options(selectinload(RoleAssignment.boundaries))
                .where(RoleAssignmentBoundary.organization_group_id == group_id)
            )
        )
        .scalars()
        .unique()
        .all()
    )


async def _invalidate_group_assignees(
    db: DbSession,
    group_id: UUID,
) -> None:
    for assignment in await _assignments_for_group(db, group_id):
        await invalidate_user(assignment.user_id)


async def _validated_organizations(
    db: DbSession,
    *,
    owner_organization_id: UUID,
    member_organization_ids: list[UUID],
) -> list[UUID]:
    owner = await db.get(Organization, owner_organization_id)
    if owner is None or not owner.is_provider:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Organization groups must be owned by the provider organization",
        )

    unique_members = list(dict.fromkeys(member_organization_ids))
    if not unique_members:
        return []
    organizations = (
        (
            await db.execute(
                select(Organization).where(Organization.id.in_(unique_members))
            )
        )
        .scalars()
        .all()
    )
    found = {organization.id for organization in organizations}
    missing = sorted(str(member) for member in set(unique_members) - found)
    if missing:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Unknown organization(s): " + ", ".join(missing),
        )
    provider_members = sorted(
        str(organization.id) for organization in organizations if organization.is_provider
    )
    if provider_members:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Provider organizations cannot be members of Managed organization "
                "groups: " + ", ".join(provider_members)
            ),
        )
    return unique_members


@router.get("", response_model=list[OrganizationGroupPublic])
async def list_organization_groups(
    user: CurrentActiveUser,
    db: DbSession,
) -> list[OrganizationGroupPublic]:
    await _authorize(db, user, "organizationgroups.read")
    groups = (
        (
            await db.execute(
                select(OrganizationGroup)
                .options(selectinload(OrganizationGroup.memberships))
                .order_by(OrganizationGroup.name)
            )
        )
        .scalars()
        .all()
    )
    return [_public(group) for group in groups]


@router.post(
    "",
    response_model=OrganizationGroupPublic,
    status_code=status.HTTP_201_CREATED,
)
async def create_organization_group(
    request: OrganizationGroupCreate,
    user: CurrentActiveUser,
    db: DbSession,
) -> OrganizationGroupPublic:
    await _authorize(db, user, "organizationgroups.readwrite")
    owner_organization_id = await _provider_organization_id(db)
    member_ids = await _validated_organizations(
        db,
        owner_organization_id=owner_organization_id,
        member_organization_ids=request.member_organization_ids,
    )
    group = OrganizationGroup(
        owner_organization_id=owner_organization_id,
        name=request.name,
        memberships=[
            OrganizationGroupMembership(organization_id=organization_id)
            for organization_id in member_ids
        ],
    )
    db.add(group)
    await db.flush()
    await db.refresh(group, attribute_names=["memberships"])
    await emit_audit(
        db,
        "organization_group.create",
        resource_type="organization_group",
        resource_id=group.id,
        details={"member_organization_ids": [str(member) for member in member_ids]},
    )
    return _public(group)


@router.patch("/{group_id}", response_model=OrganizationGroupPublic)
async def update_organization_group(
    group_id: UUID,
    request: OrganizationGroupUpdate,
    user: CurrentActiveUser,
    db: DbSession,
) -> OrganizationGroupPublic:
    await _authorize(db, user, "organizationgroups.readwrite")
    group = await db.scalar(
        select(OrganizationGroup)
        .options(selectinload(OrganizationGroup.memberships))
        .where(OrganizationGroup.id == group_id)
    )
    if group is None:
        raise HTTPException(status_code=404, detail="Organization group not found")

    if request.name is not None:
        group.name = request.name
    if request.member_organization_ids is not None:
        member_ids = await _validated_organizations(
            db,
            owner_organization_id=group.owner_organization_id,
            member_organization_ids=request.member_organization_ids,
        )
        group.memberships = [
            OrganizationGroupMembership(organization_id=organization_id)
            for organization_id in member_ids
        ]
    await db.flush()
    if request.member_organization_ids is not None:
        await _invalidate_group_assignees(db, group.id)
    await db.refresh(group, attribute_names=["memberships"])
    await emit_audit(
        db,
        "organization_group.update",
        resource_type="organization_group",
        resource_id=group.id,
        details={
            "changed_fields": sorted(request.model_dump(exclude_unset=True).keys())
        },
    )
    return _public(group)


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_organization_group(
    group_id: UUID,
    user: CurrentActiveUser,
    db: DbSession,
) -> None:
    await _authorize(db, user, "organizationgroups.readwrite")
    assignments = await _assignments_for_group(db, group_id)
    for assignment in assignments:
        remaining = [
            boundary
            for boundary in assignment.boundaries
            if boundary.organization_group_id != group_id
        ]
        if remaining:
            assignment.boundaries = remaining
        else:
            await db.delete(assignment)
    result = await db.execute(
        delete(OrganizationGroup).where(OrganizationGroup.id == group_id)
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Organization group not found")
    await db.flush()
    for assignment in assignments:
        await invalidate_user(assignment.user_id)
    await emit_audit(
        db,
        "organization_group.delete",
        resource_type="organization_group",
        resource_id=group_id,
    )
