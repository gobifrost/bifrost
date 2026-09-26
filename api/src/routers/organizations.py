"""
Organizations Router

CRUD operations for client organizations.
"""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from src.core.auth import CurrentSuperuser
from src.core.db_deps import DbSession
from src.models import OrganizationCreate, OrganizationPublic, OrganizationUpdate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/organizations", tags=["Organizations"])


@router.get(
    "",
    response_model=list[OrganizationPublic],
    summary="List organizations",
    description="Get active organizations, optionally including inactive ones (Platform admin only)",
)
async def list_organizations(
    user: CurrentSuperuser,
    db: DbSession,
    include_inactive: Annotated[
        bool,
        Query(description="Include inactive (disabled) organizations"),
    ] = False,
) -> list[OrganizationPublic]:
    """List organizations.

    Provider organization is always listed first, followed by active and inactive
    organizations alphabetically.
    """
    from shared.sdk_organizations import list_organizations as list_organizations_service

    return await list_organizations_service(db, include_inactive=include_inactive)


@router.post(
    "",
    response_model=OrganizationPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new organization",
    description="Create a new client organization (Platform admin only)",
)
async def create_organization(
    request: OrganizationCreate,
    user: CurrentSuperuser,
    db: DbSession,
) -> OrganizationPublic:
    """Create a new client organization."""
    from shared.sdk_organizations import (
        OrganizationServiceError,
        create_organization as create_organization_service,
    )

    try:
        return await create_organization_service(
            db,
            name=request.name,
            domain=request.domain,
            is_active=request.is_active,
            settings=request.settings,
            actor_email=user.email,
        )
    except OrganizationServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from None


@router.get(
    "/{org_id}",
    response_model=OrganizationPublic,
    summary="Get organization by ID",
    description="Get a specific organization by ID (Platform admin only)",
)
async def get_organization(
    org_id: UUID,
    user: CurrentSuperuser,
    db: DbSession,
) -> OrganizationPublic:
    """Get a specific organization by ID."""
    from shared.sdk_organizations import (
        OrganizationServiceError,
        get_organization as get_organization_service,
    )

    try:
        return await get_organization_service(db, org_id=org_id)
    except OrganizationServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from None


@router.patch(
    "/{org_id}",
    response_model=OrganizationPublic,
    summary="Update an organization",
    description="Update an existing organization (Platform admin only)",
)
async def update_organization(
    org_id: UUID,
    request: OrganizationUpdate,
    user: CurrentSuperuser,
    db: DbSession,
) -> OrganizationPublic:
    """Update an organization."""
    from shared.sdk_organizations import (
        OrganizationServiceError,
        update_organization as update_organization_service,
    )

    try:
        return await update_organization_service(
            db,
            org_id=org_id,
            name=request.name,
            domain=request.domain,
            is_active=request.is_active,
            settings=request.settings,
        )
    except OrganizationServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from None


@router.delete(
    "/{org_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an organization",
    description="Soft delete an organization (sets is_active=False, Platform admin only)",
)
async def delete_organization(
    org_id: UUID,
    user: CurrentSuperuser,
    db: DbSession,
) -> None:
    """Soft delete an organization."""
    from shared.sdk_organizations import (
        OrganizationServiceError,
        delete_organization as delete_organization_service,
    )

    try:
        await delete_organization_service(db, org_id=org_id)
    except OrganizationServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from None
