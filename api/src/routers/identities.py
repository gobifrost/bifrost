"""
Identities Router

Identities are the accounts that run work no person started: each
organization's default, the global default, and custom ones admins add.
Roles are assigned through ``/api/users/{user_id}/role-assignments``.
"""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from src.core.auth import CurrentActiveUser
from src.core.db_deps import DbSession
from src.models.contracts.identities import (
    FederatedCredentialCreate,
    FederatedCredentialPublic,
    FederatedCredentialUpdate,
    IdentityCreate,
    IdentityPublic,
    IdentityUpdate,
)
from src.services.authorization.enforce import load_caller
from src.services.identities import (
    IdentityError,
    create_identity as create_identity_service,
    delete_identity as delete_identity_service,
    list_identities as list_identities_service,
    rename_identity as rename_identity_service,
)
from src.services.federated_credentials import (
    FederatedCredentialError,
    create_federated_credential,
    delete_federated_credential,
    list_federated_credentials,
    update_federated_credential,
)

router = APIRouter(prefix="/api/identities", tags=["Identities"])


@router.get(
    "",
    response_model=list[IdentityPublic],
    summary="List identities",
    description=(
        "The identities in the organizations the caller may read users in, with their roles and the "
        "number of workflows that run as each. The global identity comes first."
    ),
)
async def list_identities(
    user: CurrentActiveUser,
    db: DbSession,
    organization_id: UUID | None = Query(None, description="Only this organization's identities"),
) -> list[IdentityPublic]:
    try:
        return await list_identities_service(db, await load_caller(db, user), organization_id=organization_id)
    except IdentityError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from None


@router.post(
    "",
    response_model=IdentityPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Create a custom identity",
    description="A custom identity with an explicit or configured default base role, in an organization or Global.",
)
async def create_identity(request: IdentityCreate, user: CurrentActiveUser, db: DbSession) -> IdentityPublic:
    try:
        return await create_identity_service(db, await load_caller(db, user), request)
    except IdentityError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from None


@router.patch(
    "/{identity_id}",
    response_model=IdentityPublic,
    summary="Rename a custom identity",
    description="Rename a custom identity; default identities are all named Default Identity and can't be renamed.",
)
async def rename_identity(
    identity_id: UUID, request: IdentityUpdate, user: CurrentActiveUser, db: DbSession
) -> IdentityPublic:
    try:
        return await rename_identity_service(db, await load_caller(db, user), identity_id, request)
    except IdentityError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from None


@router.delete(
    "/{identity_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a custom identity",
    description="Only a custom identity no workflow runs as can be deleted; the 409 names the workflows.",
)
async def delete_identity(identity_id: UUID, user: CurrentActiveUser, db: DbSession) -> None:
    try:
        await delete_identity_service(db, await load_caller(db, user), identity_id)
    except IdentityError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from None


@router.get("/{identity_id}/federated-credentials", response_model=list[FederatedCredentialPublic])
async def list_identity_federated_credentials(
    identity_id: UUID, user: CurrentActiveUser, db: DbSession
) -> list[FederatedCredentialPublic]:
    try:
        return await list_federated_credentials(db, await load_caller(db, user), identity_id)
    except (IdentityError, FederatedCredentialError) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None


@router.post(
    "/{identity_id}/federated-credentials",
    response_model=FederatedCredentialPublic,
    status_code=status.HTTP_201_CREATED,
)
async def create_identity_federated_credential(
    identity_id: UUID, body: FederatedCredentialCreate, user: CurrentActiveUser, db: DbSession
) -> FederatedCredentialPublic:
    try:
        return await create_federated_credential(db, await load_caller(db, user), identity_id, body)
    except (IdentityError, FederatedCredentialError) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None


@router.patch("/{identity_id}/federated-credentials/{credential_id}", response_model=FederatedCredentialPublic)
async def update_identity_federated_credential(
    identity_id: UUID,
    credential_id: UUID,
    body: FederatedCredentialUpdate,
    user: CurrentActiveUser,
    db: DbSession,
) -> FederatedCredentialPublic:
    try:
        return await update_federated_credential(db, await load_caller(db, user), identity_id, credential_id, body)
    except (IdentityError, FederatedCredentialError) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None


@router.delete("/{identity_id}/federated-credentials/{credential_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_identity_federated_credential(
    identity_id: UUID, credential_id: UUID, user: CurrentActiveUser, db: DbSession
) -> None:
    try:
        await delete_federated_credential(db, await load_caller(db, user), identity_id, credential_id)
    except (IdentityError, FederatedCredentialError) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
