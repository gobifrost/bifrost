"""Administration of identity provider connections and domain claims.

Every function takes the evaluator ``Caller`` the router loaded and checks
the organization-boundary permission itself, so a caller only ever sees or
changes connections in organizations where they hold ``identityproviders``.
Mapping a group to a role is a role grant, so it also passes the same checks
a manual role assignment does.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.models.contracts.identity_providers import (
    IdentityProviderCreate,
    IdentityProviderCreated,
    IdentityProviderGroupResponse,
    IdentityProviderResponse,
    IdentityProviderTokenInfo,
    IdentityProviderTokenIssued,
    IdentityProviderUpdate,
    IdentitySource,
    OrganizationDomainResponse,
    MappedRole,
)
from src.models.orm.identity_providers import (
    IdentityProvider,
    IdentityProviderGroup,
    IdentityProviderGroupMapping,
    IdentityProviderGroupMember,
    IdentityProviderGroupRole,
    IdentityProviderToken,
    IdentityProviderUser,
    OrganizationDomain,
)
from src.models.orm.organizations import Organization
from src.models.orm.users import Role
from src.services.audit import emit_audit
from src.services.authorization.enforce import (
    Caller,
    cross_org,
    operation_reach,
    permitted_organizations,
    require_operation,
    require_permission,
)
from src.services.identity_provisioning import domains, tokens
from src.services.identity_provisioning.core import UNMAPPABLE_ROLE_IDS, Provisioner

def scim_base_url(identity_provider_id: UUID) -> str:
    return f"{get_settings().public_url.rstrip('/')}/api/scim/v2/{identity_provider_id}"


# ---------------------------------------------------------------------------
# Domain claims
# ---------------------------------------------------------------------------


def domain_response(claim: OrganizationDomain) -> OrganizationDomainResponse:
    return OrganizationDomainResponse(
        id=claim.id,
        organization_id=claim.organization_id,
        domain=claim.domain,
        verified_at=claim.verified_at,
        last_checked_at=claim.last_checked_at,
        txt_record_name=claim.domain,
        txt_record_value=domains.txt_record_value(claim),
        created_at=claim.created_at,
    )


async def _require_organization(session: AsyncSession, organization_id: UUID) -> Organization:
    organization = await session.get(Organization, organization_id)
    if organization is None:
        raise HTTPException(404, "Organization not found")
    return organization


async def list_domains(
    session: AsyncSession, caller: Caller, organization_id: UUID
) -> list[OrganizationDomainResponse]:
    require_operation(caller, "identityproviders.domains.list", cross_org(organization_id))
    return [domain_response(c) for c in await domains.list_claims(session, organization_id)]


async def add_domain(
    session: AsyncSession, caller: Caller, organization_id: UUID, domain: str
) -> OrganizationDomainResponse:
    require_operation(caller, "identityproviders.domains.create", cross_org(organization_id))
    await _require_organization(session, organization_id)
    try:
        claim = await domains.add_claim(
            session, organization_id, domain, created_by=caller.principal.email
        )
    except domains.DomainClaimError as exc:
        raise HTTPException(exc.status_code, exc.detail) from None
    await emit_audit(
        session,
        "identity_provider.domain_claimed",
        resource_type="organization",
        resource_id=organization_id,
        details={"domain": claim.domain},
    )
    return domain_response(claim)


async def _domain_claim(session: AsyncSession, claim_id: UUID) -> OrganizationDomain:
    claim = await session.get(OrganizationDomain, claim_id)
    if claim is None:
        raise HTTPException(404, "Domain claim not found")
    return claim


async def verify_domain(
    session: AsyncSession, caller: Caller, claim_id: UUID
) -> OrganizationDomainResponse:
    claim = await _domain_claim(session, claim_id)
    require_operation(caller, "identityproviders.domains.verify", cross_org(claim.organization_id))
    was_verified = claim.verified_at is not None
    try:
        claim = await domains.verify_claim(session, claim)
    except domains.DomainClaimError as exc:
        raise HTTPException(exc.status_code, exc.detail) from None
    if claim.verified_at is not None and not was_verified:
        await emit_audit(
            session,
            "identity_provider.domain_verified",
            resource_type="organization",
            resource_id=claim.organization_id,
            details={"domain": claim.domain},
        )
    return domain_response(claim)


async def delete_domain(session: AsyncSession, caller: Caller, claim_id: UUID) -> None:
    claim = await _domain_claim(session, claim_id)
    require_operation(caller, "identityproviders.domains.delete", cross_org(claim.organization_id))
    await session.delete(claim)
    await session.flush()
    await emit_audit(
        session,
        "identity_provider.domain_removed",
        resource_type="organization",
        resource_id=claim.organization_id,
        details={"domain": claim.domain},
    )


# ---------------------------------------------------------------------------
# Connections
# ---------------------------------------------------------------------------


def _token_info(token: IdentityProviderToken | None) -> IdentityProviderTokenInfo | None:
    if token is None:
        return None
    return IdentityProviderTokenInfo(
        hint=token.token_hint,
        expires_at=token.expires_at,
        created_at=token.created_at,
        last_used_at=token.last_used_at,
    )


async def _responses(
    session: AsyncSession, connections: list[IdentityProvider]
) -> list[IdentityProviderResponse]:
    if not connections:
        return []
    ids = [c.id for c in connections]
    org_ids = {c.organization_id for c in connections}
    org_names = dict(
        (await session.execute(
            select(Organization.id, Organization.name).where(Organization.id.in_(org_ids))
        )).all()
    )
    verified: dict[UUID, list[str]] = {}
    for org_id, domain in (
        await session.execute(
            select(OrganizationDomain.organization_id, OrganizationDomain.domain)
            .where(
                OrganizationDomain.organization_id.in_(org_ids),
                OrganizationDomain.verified_at.is_not(None),
            )
            .order_by(OrganizationDomain.domain)
        )
    ).all():
        verified.setdefault(org_id, []).append(domain)
    user_counts = dict(
        (await session.execute(
            select(IdentityProviderUser.identity_provider_id, func.count())
            .where(IdentityProviderUser.identity_provider_id.in_(ids))
            .group_by(IdentityProviderUser.identity_provider_id)
        )).all()
    )
    group_counts = dict(
        (await session.execute(
            select(IdentityProviderGroup.identity_provider_id, func.count())
            .where(IdentityProviderGroup.identity_provider_id.in_(ids))
            .group_by(IdentityProviderGroup.identity_provider_id)
        )).all()
    )
    mapped_counts = dict(
        (await session.execute(
            select(IdentityProviderGroup.identity_provider_id, func.count())
            .join(
                IdentityProviderGroupMapping,
                IdentityProviderGroupMapping.group_id == IdentityProviderGroup.id,
            )
            .where(IdentityProviderGroup.identity_provider_id.in_(ids))
            .group_by(IdentityProviderGroup.identity_provider_id)
        )).all()
    )
    pending_counts = dict(
        (await session.execute(
            select(IdentityProviderUser.identity_provider_id, func.count())
            .where(
                IdentityProviderUser.identity_provider_id.in_(ids),
                IdentityProviderUser.access_pending_since.is_not(None),
            )
            .group_by(IdentityProviderUser.identity_provider_id)
        )).all()
    )
    current_tokens = {c.id: await tokens.current_token(session, c.id) for c in connections}
    return [
        IdentityProviderResponse(
            id=c.id,
            organization_id=c.organization_id,
            organization_name=org_names.get(c.organization_id, ""),
            name=c.name,
            source_type=c.source_type,
            vendor=c.vendor,  # type: ignore[arg-type]  # DB check constraint
            is_enabled=c.is_enabled,
            provision_as_external=c.provision_as_external,
            scim_base_url=scim_base_url(c.id),
            token=_token_info(current_tokens[c.id]),
            verified_domains=verified.get(c.organization_id, []),
            user_count=user_counts.get(c.id, 0),
            group_count=group_counts.get(c.id, 0),
            mapped_group_count=mapped_counts.get(c.id, 0),
            pending_access_count=pending_counts.get(c.id, 0),
            last_request_at=c.last_request_at,
            last_sync_at=c.last_sync_at,
            created_at=c.created_at,
            updated_at=c.updated_at,
        )
        for c in connections
    ]


async def _connection(
    session: AsyncSession, caller: Caller, identity_provider_id: UUID, *, operation: str
) -> IdentityProvider:
    connection = await session.get(IdentityProvider, identity_provider_id)
    if connection is None:
        raise HTTPException(404, "Identity provider not found")
    require_operation(caller, operation, cross_org(connection.organization_id))
    return connection


async def list_connections(
    session: AsyncSession, caller: Caller, organization_id: UUID | None
) -> list[IdentityProviderResponse]:
    stmt = select(IdentityProvider).order_by(IdentityProvider.name)
    if organization_id is not None:
        require_operation(caller, "identityproviders.list", cross_org(organization_id))
        stmt = stmt.where(IdentityProvider.organization_id == organization_id)
    else:
        reach = operation_reach(caller, "identityproviders.list").where(
            IdentityProvider.organization_id
        )
        if reach is not None:
            stmt = stmt.where(reach)
    return await _responses(session, list((await session.scalars(stmt)).all()))


async def get_connection(
    session: AsyncSession, caller: Caller, identity_provider_id: UUID
) -> IdentityProviderResponse:
    connection = await _connection(
        session, caller, identity_provider_id, operation="identityproviders.get"
    )
    return (await _responses(session, [connection]))[0]


async def create_connection(
    session: AsyncSession, caller: Caller, request: IdentityProviderCreate
) -> IdentityProviderCreated:
    require_operation(caller, "identityproviders.create", cross_org(request.organization_id))
    await _require_organization(session, request.organization_id)
    connection = IdentityProvider(
        organization_id=request.organization_id,
        name=request.name.strip(),
        source_type=request.source_type,
        vendor=request.vendor,
        provision_as_external=request.provision_as_external,
        created_by=caller.principal.email,
    )
    session.add(connection)
    await session.flush()
    raw, token = await tokens.issue_token(
        session,
        connection,
        lifetime_days=request.token_lifetime_days,
        created_by=caller.principal.email,
    )
    await emit_audit(
        session,
        "identity_provider.create",
        resource_type="identity_provider",
        resource_id=connection.id,
        details={"name": connection.name, "organization_id": str(connection.organization_id)},
    )
    await session.refresh(connection)
    (response,) = await _responses(session, [connection])
    issued = IdentityProviderTokenIssued(
        token=raw, info=_token_info(token), previous_tokens_expire_at=None  # type: ignore[arg-type]
    )
    return IdentityProviderCreated(identity_provider=response, token=issued)


async def update_connection(
    session: AsyncSession,
    caller: Caller,
    identity_provider_id: UUID,
    request: IdentityProviderUpdate,
) -> IdentityProviderResponse:
    connection = await _connection(
        session, caller, identity_provider_id, operation="identityproviders.update"
    )
    changes = request.model_dump(exclude_unset=True, exclude_none=True)
    if "name" in changes:
        changes["name"] = changes["name"].strip()
    for key, value in changes.items():
        setattr(connection, key, value)
    await session.flush()
    await emit_audit(
        session,
        "identity_provider.update",
        resource_type="identity_provider",
        resource_id=connection.id,
        details={"changes": changes},
    )
    await session.refresh(connection)
    return (await _responses(session, [connection]))[0]


async def delete_connection(
    session: AsyncSession, caller: Caller, identity_provider_id: UUID
) -> None:
    """Remove a connection. Its users stay as they are but become Native, and
    the roles it granted become manual assignments (the FK sets NULL)."""
    connection = await _connection(
        session, caller, identity_provider_id, operation="identityproviders.delete"
    )
    await session.delete(connection)
    await session.flush()
    await emit_audit(
        session,
        "identity_provider.delete",
        resource_type="identity_provider",
        resource_id=identity_provider_id,
        details={"name": connection.name},
    )


async def rotate_token(
    session: AsyncSession, caller: Caller, identity_provider_id: UUID, lifetime_days: int
) -> tuple[IdentityProviderTokenIssued, str]:
    """Issue a new token; returns it with the connection's name, whose expiry
    warnings the caller clears after committing."""
    connection = await _connection(
        session, caller, identity_provider_id, operation="identityproviders.token.rotate"
    )
    previous = await tokens.current_token(session, connection.id)
    raw, token = await tokens.issue_token(
        session, connection, lifetime_days=lifetime_days, created_by=caller.principal.email
    )
    previous_expiry = None
    if previous is not None:
        await session.refresh(previous)
        previous_expiry = previous.expires_at
    await emit_audit(
        session,
        "identity_provider.token_rotated",
        resource_type="identity_provider",
        resource_id=connection.id,
        details={"lifetime_days": lifetime_days},
    )
    info = _token_info(token)
    assert info is not None
    issued = IdentityProviderTokenIssued(
        token=raw, info=info, previous_tokens_expire_at=previous_expiry
    )
    return issued, connection.name


# ---------------------------------------------------------------------------
# Groups and mappings
# ---------------------------------------------------------------------------


async def list_groups(
    session: AsyncSession, caller: Caller, identity_provider_id: UUID
) -> list[IdentityProviderGroupResponse]:
    connection = await _connection(
        session, caller, identity_provider_id, operation="identityproviders.groups.list"
    )
    groups = (
        await session.scalars(
            select(IdentityProviderGroup)
            .where(IdentityProviderGroup.identity_provider_id == connection.id)
            .order_by(IdentityProviderGroup.display_name)
        )
    ).all()
    group_ids = [g.id for g in groups]
    member_counts = dict(
        (await session.execute(
            select(IdentityProviderGroupMember.group_id, func.count())
            .where(IdentityProviderGroupMember.group_id.in_(group_ids))
            .group_by(IdentityProviderGroupMember.group_id)
        )).all()
    ) if group_ids else {}
    mapped = set(
        await session.scalars(
            select(IdentityProviderGroupMapping.group_id).where(
                IdentityProviderGroupMapping.group_id.in_(group_ids)
            )
        )
    ) if group_ids else set()
    roles: dict[UUID, list[MappedRole]] = {}
    if group_ids:
        for group_id, role_id, role_name in (
            await session.execute(
                select(IdentityProviderGroupRole.group_id, Role.id, Role.name)
                .join(Role, Role.id == IdentityProviderGroupRole.role_id)
                .where(IdentityProviderGroupRole.group_id.in_(group_ids))
                .order_by(Role.name)
            )
        ).all():
            roles.setdefault(group_id, []).append(MappedRole(id=role_id, name=role_name))
    return [
        IdentityProviderGroupResponse(
            id=g.id,
            display_name=g.display_name,
            external_id=g.external_id,
            member_count=member_counts.get(g.id, 0),
            mapped=g.id in mapped,
            roles=roles.get(g.id, []),
        )
        for g in groups
    ]


async def _group(
    session: AsyncSession, connection: IdentityProvider, group_id: UUID
) -> IdentityProviderGroup:
    group = await session.get(IdentityProviderGroup, group_id)
    if group is None or group.identity_provider_id != connection.id:
        raise HTTPException(404, "Group not found")
    return group


async def _authorize_mapped_roles(
    session: AsyncSession, caller: Caller, organization_id: UUID, role_ids: set[UUID]
) -> None:
    """The checks a manual assignment of each role at this org would pass."""
    from src.services.user_role_assignments import (
        RoleAssignmentError,
        check_boundaries,
        check_holder,
        check_role_change,
        default_boundaries,
        load_roles,
    )

    if not role_ids:
        return
    unmappable = role_ids & UNMAPPABLE_ROLE_IDS
    if unmappable:
        raise HTTPException(422, "Base roles and platform roles can't be mapped from a group")
    roles = await load_roles(session, role_ids)
    missing = role_ids - roles.keys()
    if missing:
        raise HTTPException(422, f"Role {sorted(missing, key=str)[0]} not found")
    require_permission(caller, "roleassignments.readwrite", organization_id)
    reach = permitted_organizations(caller, "roleassignments.readwrite")
    boundaries = default_boundaries(organization_id)
    try:
        for role in roles.values():
            check_role_change(caller, role, frozenset(), organization_id)
            check_holder(role, organization_id)
            check_boundaries(caller, reach, role, boundaries)
    except RoleAssignmentError as exc:
        raise HTTPException(exc.status_code, exc.detail) from None


async def set_group_mapping(
    session: AsyncSession,
    caller: Caller,
    identity_provider_id: UUID,
    group_id: UUID,
    role_ids: list[UUID],
) -> tuple[IdentityProviderGroupResponse, Provisioner]:
    """Map a group; returns the provisioner whose events the caller sends
    after committing."""
    connection = await _connection(
        session, caller, identity_provider_id, operation="identityproviders.groups.map"
    )
    group = await _group(session, connection, group_id)
    wanted = set(role_ids)
    await _authorize_mapped_roles(session, caller, connection.organization_id, wanted)
    provisioner = Provisioner(session, connection)
    await provisioner.hold_policy(exclusive=True)
    mapping = await session.get(IdentityProviderGroupMapping, group.id)
    if mapping is None:
        session.add(
            IdentityProviderGroupMapping(group_id=group.id, created_by=caller.principal.email)
        )
        await session.flush()
    current = set(
        await session.scalars(
            select(IdentityProviderGroupRole.role_id).where(
                IdentityProviderGroupRole.group_id == group.id
            )
        )
    )
    for role_id in current - wanted:
        await session.delete(await session.get(IdentityProviderGroupRole, (group.id, role_id)))
    for role_id in wanted - current:
        session.add(IdentityProviderGroupRole(group_id=group.id, role_id=role_id))
    await session.flush()
    await emit_audit(
        session,
        "identity_provider.group_mapped",
        resource_type="identity_provider",
        resource_id=connection.id,
        details={
            "group": group.display_name,
            "role_ids": sorted(str(r) for r in wanted),
        },
    )
    await provisioner.reconcile_all()
    (response,) = [g for g in await list_groups(session, caller, connection.id) if g.id == group.id]
    return response, provisioner


async def delete_group_mapping(
    session: AsyncSession, caller: Caller, identity_provider_id: UUID, group_id: UUID
) -> Provisioner:
    connection = await _connection(
        session, caller, identity_provider_id, operation="identityproviders.groups.unmap"
    )
    group = await _group(session, connection, group_id)
    provisioner = Provisioner(session, connection)
    await provisioner.hold_policy(exclusive=True)
    mapping = await session.get(IdentityProviderGroupMapping, group.id)
    if mapping is None:
        raise HTTPException(404, "This group is not mapped")
    await session.delete(mapping)
    await session.flush()
    await emit_audit(
        session,
        "identity_provider.group_unmapped",
        resource_type="identity_provider",
        resource_id=connection.id,
        details={"group": group.display_name},
    )
    await provisioner.reconcile_all()
    return provisioner


async def identity_sources(
    session: AsyncSession, user_ids: list[UUID]
) -> dict[UUID, IdentitySource]:
    """``user_id -> IdentitySource`` for linked users."""
    if not user_ids:
        return {}
    rows = await session.execute(
        select(
            IdentityProviderUser.user_id,
            IdentityProvider.id,
            IdentityProvider.name,
            IdentityProvider.vendor,
            IdentityProviderUser.access_pending_since,
        )
        .join(IdentityProvider, IdentityProvider.id == IdentityProviderUser.identity_provider_id)
        .where(IdentityProviderUser.user_id.in_(user_ids))
    )
    return {
        user_id: IdentitySource(
            identity_provider_id=idp_id,
            name=name,
            vendor=vendor,
            access_pending_since=pending_since,
        )
        for user_id, idp_id, name, vendor, pending_since in rows.all()
    }

