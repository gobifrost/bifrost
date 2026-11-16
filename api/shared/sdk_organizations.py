"""Shared business service for organization operations.

Single implementation used by both entry points:

- the HTTP handlers (``api/src/routers/organizations.py``) serving SDK/CLI
  callers, and
- the same handlers reached by workflow children over the worker-local
  engine socket.

Both paths share DTOs, response fields, HTTP statuses/error precedence,
sorting/filter defaults, audit attribution, and cache updates. The handlers
decide authorization with the evaluator before invoking these operations
(organizations.read / organizations.readwrite at the organization, creating
one at Platform); the list is filtered here to the caller's reach.

Scope is the five SDK methods: ``create``, ``get``, ``list``,
``update``, ``delete``. Each maps 1:1 to one HTTP call, so there are no
composite facades to decompose here. ``bulk_update_organization_tags``
serves the administrative bulk tag route only. Delete is a soft disable
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
    from src.services.authorization.reach import OrgReach

logger = logging.getLogger(__name__)


class OrganizationServiceError(Exception):
    """Organization operation failure with an HTTP-style status.

    Raised by the shared service so the HTTP handler (``HTTPException``)
    and callers reached over the worker-local engine socket read the same status/detail.
    """

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


async def list_organizations(
    session: AsyncSession,
    *,
    reach: OrgReach,
    include_inactive: bool = False,
) -> list[OrganizationPublic]:
    """List the organizations in ``reach``, provider first then active then
    alphabetical.

    Defaults match the historical handler (active only unless
    ``include_inactive`` is set).
    """
    from src.models import Organization as OrganizationORM
    from src.models import OrganizationPublic

    query = select(OrganizationORM)
    reach_filter = reach.where(OrganizationORM.id)
    if reach_filter is not None:
        query = query.where(reach_filter)
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
    tags: list[str] | None = None,
    actor_email: str,
) -> OrganizationPublic:
    """Create an organization (shared by the HTTP handler and worker-local calls)."""
    from shared.identities import ensure_default_identity
    from src.models import Organization as OrganizationORM
    from src.models import OrganizationPublic
    from src.services.audit import emit_audit

    if settings and "default_base_role_id" in settings:
        raise OrganizationServiceError(422, "Set the default base role through Base Role Defaults")
    now = datetime.now(timezone.utc)

    org = OrganizationORM(
        name=name,
        domain=domain.lower() if domain else None,
        is_active=is_active,
        settings=settings if settings is not None else {},
        tags=tags if tags is not None else [],
        created_by=actor_email,
        created_at=now,
        updated_at=now,
    )

    session.add(org)
    await session.flush()
    await session.refresh(org)
    await ensure_default_identity(session, org)

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
    tags: list[str] | None = None,
) -> OrganizationPublic:
    """Update an organization. Only non-None fields are applied.

    Raises 404 when missing (checked before the provider guard, like the
    historical handler). Disabling the provider org raises 403.
    """
    from src.models import Organization as OrganizationORM
    from src.models import OrganizationPublic
    from src.services.audit import emit_audit

    result = await session.execute(
        select(OrganizationORM).where(OrganizationORM.id == org_id).with_for_update()
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
        if settings.get("default_base_role_id") != (org.settings or {}).get("default_base_role_id"):
            raise OrganizationServiceError(422, "Set the default base role through Base Role Defaults")
        org.settings = settings
    if tags is not None:
        org.tags = tags

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
            ("tags", tags),
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


async def bulk_update_organization_tags(
    session: AsyncSession,
    *,
    organization_ids: list[UUID],
    add: list[str],
    remove: list[str],
) -> list[OrganizationPublic]:
    """Add and remove tags on several organizations in one transaction.

    ``add``/``remove`` are already normalized by the request contract.
    Removal is case-insensitive; an added tag that an organization already
    carries in another case keeps the existing spelling. Raises 404 when any
    organization is missing, so nothing changes. The caller authorizes every
    organization before calling.
    """
    from src.models import Organization as OrganizationORM
    from src.models import OrganizationPublic
    from src.models.contracts.organizations import normalize_organization_tags
    from src.services.audit import emit_audit

    unique_ids = list(dict.fromkeys(organization_ids))
    result = await session.execute(
        select(OrganizationORM)
        .where(OrganizationORM.id.in_(unique_ids))
        .order_by(OrganizationORM.id)
        .with_for_update()
    )
    orgs = list(result.scalars().all())
    if len(orgs) != len(unique_ids):
        raise OrganizationServiceError(404, "Organization not found")

    removed = {tag.casefold() for tag in remove}
    now = datetime.now(timezone.utc)
    changed = 0
    for org in orgs:
        current = list(org.tags or [])
        kept = [tag for tag in current if tag.casefold() not in removed]
        try:
            updated = normalize_organization_tags(kept + add)
        except ValueError as exc:
            raise OrganizationServiceError(422, f"{org.name}: {exc}") from None
        if updated != current:
            org.tags = updated
            org.updated_at = now
            changed += 1

    await session.flush()
    logger.info(f"Bulk-updated tags on {changed} of {len(orgs)} organizations")
    await emit_audit(
        session,
        "organization.tags_bulk_update",
        resource_type="organization",
        resource_id=None,
        details={
            "requested": len(unique_ids),
            "changed": changed,
            "added": add,
            "removed": remove,
        },
    )
    by_id = {org.id: org for org in orgs}
    return [OrganizationPublic.model_validate(by_id[org_id]) for org_id in unique_ids]


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
