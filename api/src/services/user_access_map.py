"""The access map: what a person can do, and where.

Built from the same ``AuthorizationContext`` the evaluator decides with, so it
cannot say more or less than the platform enforces: the base role's permissions
at the person's home organization and each additional role's permissions at
each of its boundaries, grouped by place. A Platform Admin's wildcard covers
everything but ``WILDCARD_EXCLUDED_PERMISSIONS``, so it is shown as one
``*`` grant and only explicit grants outside its cover are listed besides.
"""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.builtin_roles import WILDCARD_PERMISSION
from src.models.contracts.permissions import (
    WILDCARD_EXCLUDED_PERMISSIONS,
    PermissionCatalogEntry,
    parse_permission,
)
from src.models.contracts.user_access import (
    AccessGrant,
    AccessGrantSource,
    AccessRow,
    GrantScope,
    GrantVia,
    OrganizationRef,
    Place,
    UserAccessMap,
)
from src.models.orm.organizations import Organization
from src.models.orm.users import Role
from src.services.authorization.context import (
    AuthorizationContext,
    Boundary,
    BoundaryKind,
    build_authorization_context,
)
from src.services.authorization.enforce import Caller
from src.services.authorization.privilege import is_privileged_principal
from src.services.permission_catalog import build_catalog
from src.services.user_role_assignments import ACCESS_OPERATION, require_assignment_target

_PlaceKey = tuple[str, UUID | None]

_REACH_RANK = {"home": 0, "organization": 1, "managed_organizations": 2, "platform": 3}


def _place_for(ctx: AuthorizationContext, boundary: Boundary, names: Mapping[UUID, str]) -> Place:
    if boundary.kind == BoundaryKind.PLATFORM:
        return Place(kind="platform", label="All organizations" if ctx.is_platform_admin else "Global")
    if boundary.kind == BoundaryKind.MANAGED_ORGANIZATIONS:
        return Place(kind="managed_organizations", label="All customer organizations")
    assert boundary.organization_id is not None
    name = names[boundary.organization_id]
    if boundary.organization_id == ctx.home_organization_id:
        return Place(
            kind="home",
            organization_id=boundary.organization_id,
            organization_name=name,
            label=f"{name} (home)",
        )
    return Place(
        kind="organization",
        organization_id=boundary.organization_id,
        organization_name=name,
        label=name,
    )


def _place_key(place: Place) -> _PlaceKey:
    return place.kind, place.organization_id


def _new_grant(permission: str, scope_by_domain: Mapping[str, GrantScope]) -> AccessGrant:
    if permission == WILDCARD_PERMISSION:
        return AccessGrant(
            permission=permission, domain="*", action="*", scope="platform_wide", sources=[]
        )
    parsed = parse_permission(permission)
    return AccessGrant(
        permission=permission,
        domain=parsed.domain,
        action=f"{parsed.action}.all" if parsed.extended else parsed.action,
        scope=scope_by_domain[parsed.domain],
        sources=[],
    )


def build_access_map(
    ctx: AuthorizationContext,
    *,
    user_name: str | None,
    user_email: str,
    held: frozenset[str],
    names: Mapping[UUID, str],
    role_names: Mapping[UUID, str],
    catalog: Mapping[str, PermissionCatalogEntry],
) -> UserAccessMap:
    """``held`` is everything the person holds at any boundary (what decides
    whether they are protected); ``names`` maps organization ids and
    ``role_names`` role ids to their names; ``catalog`` is keyed by domain."""
    scope_by_domain = {domain: entry.scope for domain, entry in catalog.items()}
    is_platform_admin = ctx.is_platform_admin
    platform = Boundary(BoundaryKind.PLATFORM)
    home = (
        platform
        if ctx.home_organization_id is None
        else Boundary(BoundaryKind.ORGANIZATION, ctx.home_organization_id)
    )

    # Global is in everyone's reach; the home place and every placed
    # boundary are too, whether or not the role there carries permissions.
    places: dict[_PlaceKey, Place] = {}
    for boundary in (home, platform, *(b for g in ctx.role_grants for b in g.boundaries)):
        place = _place_for(ctx, boundary, names)
        places[_place_key(place)] = place

    grants: dict[_PlaceKey, dict[str, AccessGrant]] = {}

    def hold(permission: str, boundary: Boundary, role_id: UUID, via: GrantVia) -> None:
        # The wildcard covers everything it does not exclude: listing those
        # again beside it would only repeat it.
        if (
            is_platform_admin
            and permission != WILDCARD_PERMISSION
            and permission not in WILDCARD_EXCLUDED_PERMISSIONS
        ):
            return
        row = grants.setdefault(_place_key(_place_for(ctx, boundary, names)), {})
        grant = row.setdefault(permission, _new_grant(permission, scope_by_domain))
        grant.sources.append(
            AccessGrantSource(role_id=role_id, role_name=role_names[role_id], via=via)
        )

    for permission in ctx.base_permissions:
        hold(permission, home, ctx.base_role_id, "base")
    for role_grant in ctx.role_grants:
        for boundary in role_grant.boundaries:
            for permission in role_grant.permissions:
                hold(permission, boundary, role_grant.role_id, "additional")

    reach = sorted(places.values(), key=lambda p: (_REACH_RANK[p.kind], p.label.lower()))
    rows = []
    for place in reach:
        row = grants.get(_place_key(place))
        if row is None:
            continue
        for grant in row.values():
            grant.sources.sort(key=lambda s: (s.via != "base", s.role_name.lower()))
        rows.append(AccessRow(place=place, grants=sorted(row.values(), key=lambda g: g.permission)))

    return UserAccessMap(
        user_id=ctx.user_id,
        name=user_name,
        email=user_email,
        home_organization=None
        if ctx.home_organization_id is None
        else OrganizationRef(id=ctx.home_organization_id, name=names[ctx.home_organization_id]),
        is_platform_admin=is_platform_admin,
        is_protected=is_privileged_principal(held),
        privileged_permissions=[p for p in sorted(held) if is_privileged_principal([p])],
        reach=reach,
        rows=rows,
    )


async def get_user_access_map(
    db: AsyncSession, caller: Caller, *, user_id: UUID
) -> UserAccessMap:
    """The access map of ``user_id``, gated like reading their role
    assignments: ``roleassignments.read`` at the user's organization."""
    target = await require_assignment_target(db, caller, user_id, ACCESS_OPERATION)
    user = target.user
    ctx = await build_authorization_context(db, user_id)

    role_ids = {ctx.base_role_id, *(grant.role_id for grant in ctx.role_grants)}
    role_names = dict(
        (await db.execute(select(Role.id, Role.name).where(Role.id.in_(role_ids)))).all()
    )
    organization_ids = {
        boundary.organization_id
        for grant in ctx.role_grants
        for boundary in grant.boundaries
        if boundary.organization_id is not None
    }
    if ctx.home_organization_id is not None:
        organization_ids.add(ctx.home_organization_id)
    names = dict(
        (
            await db.execute(
                select(Organization.id, Organization.name).where(
                    Organization.id.in_(organization_ids)
                )
            )
        ).all()
    )
    return build_access_map(
        ctx,
        user_name=user.name,
        user_email=user.email,
        held=target.held,
        names=names,
        role_names=role_names,
        catalog={entry.domain: entry for entry in build_catalog()},
    )
