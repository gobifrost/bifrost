"""Identity provider connections, their tokens and group mappings, and the
organization domain claims they provision against."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query, Response

from src.core.auth import CurrentActiveUser
from src.core.db_deps import DbSession
from src.models.contracts.identity_providers import (
    IdentityProviderCreate,
    IdentityProviderCreated,
    IdentityProviderGroupMappingUpdate,
    IdentityProviderGroupResponse,
    IdentityProviderResponse,
    IdentityProviderTokenIssued,
    IdentityProviderTokenRotate,
    IdentityProviderUpdate,
    OrganizationDomainCreate,
    OrganizationDomainResponse,
)
from src.services import identity_providers as service
from src.services.authorization.enforce import load_caller
from src.services.identity_provisioning.expiry import clear_expiry_notifications
from src.services.operation_catalog import operation_route

router = APIRouter(prefix="/api/identity-providers", tags=["Identity Providers"])


# ---------------------------------------------------------------------------
# Domain claims (declared before /{identity_provider_id})
# ---------------------------------------------------------------------------


@router.get(
    "/domains",
    response_model=list[OrganizationDomainResponse],
    **operation_route("identityproviders.domains.list"),
)
async def list_domains(
    db: DbSession, user: CurrentActiveUser, organization_id: UUID = Query(...)
) -> list[OrganizationDomainResponse]:
    caller = await load_caller(db, user)
    return await service.list_domains(db, caller, organization_id)


@router.post(
    "/domains",
    response_model=OrganizationDomainResponse,
    status_code=201,
    **operation_route("identityproviders.domains.create"),
)
async def add_domain(
    request: OrganizationDomainCreate, db: DbSession, user: CurrentActiveUser
) -> OrganizationDomainResponse:
    caller = await load_caller(db, user)
    return await service.add_domain(db, caller, request.organization_id, request.domain)


@router.post(
    "/domains/{claim_id}/verify",
    response_model=OrganizationDomainResponse,
    **operation_route("identityproviders.domains.verify"),
)
async def verify_domain(
    claim_id: UUID, db: DbSession, user: CurrentActiveUser
) -> OrganizationDomainResponse:
    caller = await load_caller(db, user)
    return await service.verify_domain(db, caller, claim_id)


@router.delete(
    "/domains/{claim_id}",
    status_code=204,
    **operation_route("identityproviders.domains.delete"),
)
async def delete_domain(claim_id: UUID, db: DbSession, user: CurrentActiveUser) -> Response:
    caller = await load_caller(db, user)
    await service.delete_domain(db, caller, claim_id)
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Connections
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=list[IdentityProviderResponse],
    **operation_route("identityproviders.list"),
)
async def list_identity_providers(
    db: DbSession, user: CurrentActiveUser, organization_id: UUID | None = Query(None)
) -> list[IdentityProviderResponse]:
    caller = await load_caller(db, user)
    return await service.list_connections(db, caller, organization_id)


@router.post(
    "",
    response_model=IdentityProviderCreated,
    status_code=201,
    **operation_route("identityproviders.create"),
)
async def create_identity_provider(
    request: IdentityProviderCreate, db: DbSession, user: CurrentActiveUser
) -> IdentityProviderCreated:
    caller = await load_caller(db, user)
    return await service.create_connection(db, caller, request)


@router.get(
    "/{identity_provider_id}",
    response_model=IdentityProviderResponse,
    **operation_route("identityproviders.get"),
)
async def get_identity_provider(
    identity_provider_id: UUID, db: DbSession, user: CurrentActiveUser
) -> IdentityProviderResponse:
    caller = await load_caller(db, user)
    return await service.get_connection(db, caller, identity_provider_id)


@router.patch(
    "/{identity_provider_id}",
    response_model=IdentityProviderResponse,
    **operation_route("identityproviders.update"),
)
async def update_identity_provider(
    identity_provider_id: UUID,
    request: IdentityProviderUpdate,
    db: DbSession,
    user: CurrentActiveUser,
) -> IdentityProviderResponse:
    caller = await load_caller(db, user)
    return await service.update_connection(db, caller, identity_provider_id, request)


@router.delete(
    "/{identity_provider_id}",
    status_code=204,
    **operation_route("identityproviders.delete"),
)
async def delete_identity_provider(
    identity_provider_id: UUID, db: DbSession, user: CurrentActiveUser
) -> Response:
    caller = await load_caller(db, user)
    await service.delete_connection(db, caller, identity_provider_id)
    return Response(status_code=204)


@router.post(
    "/{identity_provider_id}/token",
    response_model=IdentityProviderTokenIssued,
    **operation_route("identityproviders.token.rotate"),
)
async def rotate_identity_provider_token(
    identity_provider_id: UUID,
    request: IdentityProviderTokenRotate,
    db: DbSession,
    user: CurrentActiveUser,
) -> IdentityProviderTokenIssued:
    caller = await load_caller(db, user)
    issued, name = await service.rotate_token(
        db, caller, identity_provider_id, request.lifetime_days
    )
    await db.commit()
    await clear_expiry_notifications(name)
    return issued


# ---------------------------------------------------------------------------
# Groups and mappings
# ---------------------------------------------------------------------------


@router.get(
    "/{identity_provider_id}/groups",
    response_model=list[IdentityProviderGroupResponse],
    **operation_route("identityproviders.groups.list"),
)
async def list_identity_provider_groups(
    identity_provider_id: UUID, db: DbSession, user: CurrentActiveUser
) -> list[IdentityProviderGroupResponse]:
    caller = await load_caller(db, user)
    return await service.list_groups(db, caller, identity_provider_id)


@router.put(
    "/{identity_provider_id}/groups/{group_id}/mapping",
    response_model=IdentityProviderGroupResponse,
    **operation_route("identityproviders.groups.map"),
)
async def map_identity_provider_group(
    identity_provider_id: UUID,
    group_id: UUID,
    request: IdentityProviderGroupMappingUpdate,
    db: DbSession,
    user: CurrentActiveUser,
) -> IdentityProviderGroupResponse:
    caller = await load_caller(db, user)
    response, provisioner = await service.set_group_mapping(
        db, caller, identity_provider_id, group_id, request.role_ids
    )
    await db.commit()
    await provisioner.after_commit()
    return response


@router.delete(
    "/{identity_provider_id}/groups/{group_id}/mapping",
    status_code=204,
    **operation_route("identityproviders.groups.unmap"),
)
async def unmap_identity_provider_group(
    identity_provider_id: UUID, group_id: UUID, db: DbSession, user: CurrentActiveUser
) -> Response:
    caller = await load_caller(db, user)
    provisioner = await service.delete_group_mapping(db, caller, identity_provider_id, group_id)
    await db.commit()
    await provisioner.after_commit()
    return Response(status_code=204)

