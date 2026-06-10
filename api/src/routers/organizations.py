"""
Organizations Router

CRUD operations for client organizations.
"""

import logging
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from src.core.auth import CurrentSuperuser
from src.core.db_deps import DbSession
from src.core.log_safety import log_safe
from src.services.audit import emit_audit
from src.models import Organization as OrganizationORM
from src.models import OrganizationCreate, OrganizationPublic, OrganizationUpdate

# Import cache functions
try:
    from src.core.cache import upsert_org, invalidate_org
    CACHE_AVAILABLE = True
except ImportError:
    CACHE_AVAILABLE = False
    upsert_org = None  # type: ignore
    invalidate_org = None  # type: ignore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/organizations", tags=["Organizations"])


@router.get(
    "",
    response_model=list[OrganizationPublic],
    summary="List all organizations",
    description="Get all organizations (Platform admin only)",
)
async def list_organizations(
    user: CurrentSuperuser,
    db: DbSession,
) -> list[OrganizationPublic]:
    """List all organizations.

    Provider organization is always listed first, followed by other orgs alphabetically.
    """
    query = (
        select(OrganizationORM)
        .where(OrganizationORM.is_active)
        .order_by(
            OrganizationORM.is_provider.desc(),  # Provider org first
            OrganizationORM.name,
        )
    )
    result = await db.execute(query)
    orgs = result.scalars().all()
    return [OrganizationPublic.model_validate(org) for org in orgs]


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
    now = datetime.now(timezone.utc)

    org = OrganizationORM(
        name=request.name,
        domain=request.domain.lower() if request.domain else None,
        is_active=request.is_active,
        settings=request.settings,
        created_by=user.email,
        created_at=now,
        updated_at=now,
    )

    db.add(org)
    await db.flush()
    await db.refresh(org)

    # Upsert to cache after successful write
    if CACHE_AVAILABLE and upsert_org:
        await upsert_org(
            org_id=str(org.id),
            name=org.name,
            domain=org.domain,
            is_active=org.is_active,
            is_provider=org.is_provider,
        )

    logger.info(f"Created organization {org.id}: {org.name}")
    await emit_audit(
        db,
        "organization.create",
        resource_type="organization",
        resource_id=org.id,
        details={"name": org.name, "domain": org.domain},
    )
    return OrganizationPublic.model_validate(org)


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
    result = await db.execute(
        select(OrganizationORM).where(OrganizationORM.id == org_id)
    )
    org = result.scalar_one_or_none()

    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )

    return OrganizationPublic.model_validate(org)


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
    result = await db.execute(
        select(OrganizationORM).where(OrganizationORM.id == org_id)
    )
    org = result.scalar_one_or_none()

    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )

    if request.name is not None:
        org.name = request.name
    if request.domain is not None:
        org.domain = request.domain.lower() if request.domain else None
    if request.is_active is not None:
        org.is_active = request.is_active
    if request.settings is not None:
        org.settings = request.settings

    org.updated_at = datetime.now(timezone.utc)

    await db.flush()
    await db.refresh(org)

    # Upsert to cache after successful update
    if CACHE_AVAILABLE and upsert_org:
        await upsert_org(
            org_id=str(org.id),
            name=org.name,
            domain=org.domain,
            is_active=org.is_active,
            is_provider=org.is_provider,
        )

    logger.info(f"Updated organization {log_safe(org_id)}")
    changed_fields = [
        k for k, v in request.model_dump(exclude_unset=True).items() if v is not None
    ]
    await emit_audit(
        db,
        "organization.update",
        resource_type="organization",
        resource_id=org.id,
        details={"name": org.name, "changed_fields": changed_fields},
    )
    return OrganizationPublic.model_validate(org)


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
    result = await db.execute(
        select(OrganizationORM).where(OrganizationORM.id == org_id)
    )
    org = result.scalar_one_or_none()

    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )

    # Provider organization cannot be deleted
    if org.is_provider:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Provider organization cannot be deleted",
        )

    org.is_active = False
    org.updated_at = datetime.now(timezone.utc)

    await db.flush()

    # Invalidate cache after soft delete
    if CACHE_AVAILABLE and invalidate_org:
        await invalidate_org(str(org_id))

    logger.info(f"Soft deleted organization {log_safe(org_id)}")
    await emit_audit(
        db,
        "organization.delete",
        resource_type="organization",
        resource_id=org.id,
        details={"name": org.name},
    )
