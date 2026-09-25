"""Shared business service for organization operations.

Single implementation used by both entry points:

- the HTTP handlers (``api/src/routers/organizations.py``) serving SDK/CLI
  callers, and
- a future engine-local dispatcher serving workflow children through
  the parent-side local transport.

Both paths share DTOs, response fields, HTTP statuses/error precedence,
sorting/filter defaults, audit attribution, and cache updates. Each caller
must enforce platform-admin authority before invoking these operations.

Scope is the five SDK methods: ``create``, ``get``, ``list``,
``update``, ``delete``. Each maps 1:1 to one HTTP call, so there are no
composite facades to decompose here. Delete is a soft disable
(``is_active=False``); the HTTP handler returns 204 and the SDK maps
that to ``True``.

Parent-side only: imports SQLAlchemy models and the Redis-backed
caches. The child never imports this module.

The service takes an explicit trusted session, UUIDs, validated
parameters, and the actor email for creation — never a ``Request``, JWT,
or raw child frame. HTTP authentication and ``HTTPException`` mapping
stay in the router.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.log_safety import log_safe

if TYPE_CHECKING:
    from src.models import OrganizationPublic

logger = logging.getLogger(__name__)


class OrganizationServiceError(Exception):
    """Organization operation failure with an HTTP-style status.

    Raised by the shared service so the HTTP handler (``HTTPException``)
    and the future local dispatcher (``ok: false`` frames) can map the
    same failure to their own transport.
    """

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


async def list_organizations(
    session: AsyncSession,
    *,
    include_inactive: bool = False,
) -> list[OrganizationPublic]:
    """List organizations, provider first then active then alphabetical.

    Defaults match the historical handler (active only unless
    ``include_inactive`` is set).
    """
    from src.models import Organization as OrganizationORM
    from src.models import OrganizationPublic

    query = select(OrganizationORM)
    if not include_inactive:
        query = query.where(OrganizationORM.is_active)
    query = query.order_by(
        OrganizationORM.is_provider.desc(),
        OrganizationORM.is_active.desc(),
        OrganizationORM.name,
    )
    result = await session.execute(query)
    orgs = result.scalars().all()
    return [OrganizationPublic.model_validate(org) for org in orgs]


async def create_organization(
    session: AsyncSession,
    *,
    name: str,
    domain: str | None,
    is_active: bool = True,
    settings: dict | None = None,
    actor_email: str,
) -> OrganizationPublic:
    """Create an organization (shared by the HTTP handler and the local dispatcher)."""
    from src.models import Organization as OrganizationORM
    from src.models import OrganizationPublic
    from src.services.audit import emit_audit

    now = datetime.now(timezone.utc)

    org = OrganizationORM(
        name=name,
        domain=domain.lower() if domain else None,
        is_active=is_active,
        settings=settings if settings is not None else {},
        created_by=actor_email,
        created_at=now,
        updated_at=now,
    )

    session.add(org)
    await session.flush()
    await session.refresh(org)

    try:
        from src.core.cache import upsert_org

        await upsert_org(
            org_id=str(org.id),
            name=org.name,
            domain=org.domain,
            is_active=org.is_active,
            is_provider=org.is_provider,
        )
    except ImportError:
        logger.debug("cache module unavailable, skipping org cache upsert")

    logger.info(f"Created organization {org.id}: {org.name}")
    await emit_audit(
        session,
        "organization.create",
        resource_type="organization",
        resource_id=org.id,
        details={"name": org.name, "domain": org.domain},
    )
    return OrganizationPublic.model_validate(org)


async def get_organization(
    session: AsyncSession,
    *,
    org_id: UUID,
) -> OrganizationPublic:
    """Get an organization by ID. Raises 404 when missing."""
    from src.models import Organization as OrganizationORM
    from src.models import OrganizationPublic

    result = await session.execute(
        select(OrganizationORM).where(OrganizationORM.id == org_id)
    )
    org = result.scalar_one_or_none()

    if not org:
        raise OrganizationServiceError(404, "Organization not found")

    return OrganizationPublic.model_validate(org)


async def update_organization(
    session: AsyncSession,
    *,
    org_id: UUID,
    name: str | None = None,
    domain: str | None = None,
    is_active: bool | None = None,
    settings: dict | None = None,
) -> OrganizationPublic:
    """Update an organization. Only non-None fields are applied.

    Raises 404 when missing (checked before the provider guard, like the
    historical handler). Disabling the provider org raises 403.
    """
    from src.models import Organization as OrganizationORM
    from src.models import OrganizationPublic
    from src.services.audit import emit_audit

    result = await session.execute(
        select(OrganizationORM).where(OrganizationORM.id == org_id)
    )
    org = result.scalar_one_or_none()

    if not org:
        raise OrganizationServiceError(404, "Organization not found")

    if org.is_provider and is_active is False:
        raise OrganizationServiceError(
            403, "Provider organization cannot be disabled"
        )

    if name is not None:
        org.name = name
    if domain is not None:
        org.domain = domain.lower() if domain else None
    if is_active is not None:
        org.is_active = is_active
    if settings is not None:
        org.settings = settings

    org.updated_at = datetime.now(timezone.utc)

    await session.flush()
    await session.refresh(org)

    try:
        from src.core.cache import upsert_org

        await upsert_org(
            org_id=str(org.id),
            name=org.name,
            domain=org.domain,
            is_active=org.is_active,
            is_provider=org.is_provider,
        )
    except ImportError:
        logger.debug("cache module unavailable, skipping org cache upsert")

    logger.info(f"Updated organization {log_safe(org_id)}")
    changed_fields = [
        k
        for k, v in (
            ("name", name),
            ("domain", domain),
            ("is_active", is_active),
            ("settings", settings),
        )
        if v is not None
    ]
    await emit_audit(
        session,
        "organization.update",
        resource_type="organization",
        resource_id=org.id,
        details={"name": org.name, "changed_fields": changed_fields},
    )
    return OrganizationPublic.model_validate(org)


async def delete_organization(
    session: AsyncSession,
    *,
    org_id: UUID,
) -> str:
    """Soft-disable an organization (``is_active=False``).

    Returns the deleted org's name (for audit/tests). Raises 404 when
    missing (checked before the provider guard); the provider org
    raises 403.
    """
    from src.models import Organization as OrganizationORM
    from src.services.audit import emit_audit

    result = await session.execute(
        select(OrganizationORM).where(OrganizationORM.id == org_id)
    )
    org = result.scalar_one_or_none()

    if not org:
        raise OrganizationServiceError(404, "Organization not found")

    if org.is_provider:
        raise OrganizationServiceError(
            403, "Provider organization cannot be deleted"
        )

    deleted_name = org.name
    org.is_active = False
    org.updated_at = datetime.now(timezone.utc)

    await session.flush()

    try:
        from src.core.cache import invalidate_org

        await invalidate_org(str(org_id))
    except ImportError:
        logger.debug("cache module unavailable, skipping org cache invalidate")

    logger.info(f"Soft deleted organization {log_safe(org_id)}")
    await emit_audit(
        session,
        "organization.delete",
        resource_type="organization",
        resource_id=org.id,
        details={"name": deleted_name},
    )
    return deleted_name
