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
from src.models.contracts.identities import IdentityCreate, IdentityPublic, IdentityUpdate
from src.services.authorization.enforce import load_caller
from src.services.identities import (
    IdentityError,
    create_identity as create_identity_service,
    delete_identity as delete_identity_service,
    list_identities as list_identities_service,
    rename_identity as rename_identity_service,
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
    description="A custom identity with the User base role, in an organization or (organization_id null) Global.",
)
async def create_identity(request: IdentityCreate, user: CurrentActiveUser, db: DbSession) -> IdentityPublic:
    try:
        return await create_identity_service(db, await load_caller(db, user), request)
    except IdentityError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from None


@router.patch(
    "/{identity_id}",
    response_model=IdentityPublic,
    summary="Rename an identity",
    description="Rename any identity, default or custom.",
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
