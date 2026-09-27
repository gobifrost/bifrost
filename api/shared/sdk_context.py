"""Shared business service for the SDK ``GET /api/sdk/context`` operation.

Single implementation for both entry points:

- the HTTP handler (``api/src/routers/cli.py::get_dev_context``) serving
  external SDK/CLI callers bootstrapping an execution context, and
- the same handler reached by workflow children over the worker-local
  engine socket.

Both paths can share authenticated org input (an explicit trusted principal
plus an optional org override) and the C2 gate — platform admins and
provider-org members may target another org; everyone else resolves to
their own auth-verified org — so HTTP and worker-local results are identical by
construction.

Parent-side only: imports SQLAlchemy models. A workflow child never
imports this module (it stays DB-free and reaches it over the engine
socket).
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.principal import UserPrincipal
from src.models import Organization

logger = logging.getLogger(__name__)


class SdkContextError(Exception):
    """SDK context failure with an HTTP-style status.

    Raised by the shared service so the HTTP handler (``HTTPException``)
    and callers reached over the worker-local engine socket read the same status/detail. Unauthorized cross-org targeting is
    403; a missing/inactive org is 404 — matching the historical handler
    responses exactly.
    """

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


async def get_sdk_context(
    session: AsyncSession,
    principal: UserPrincipal,
    org_id: UUID | None = None,
) -> dict[str, Any]:
    """Build the developer context payload for an SDK bootstrap.

    Returns the authenticated user and their ``organization_id``-resolved
    org. The optional ``org_id`` override lets platform admins and
    provider-org members target another org for the session — gated by
    the same C2 rule the scope resolver applies elsewhere.

    Args:
        session: Database session.
        principal: The auth-verified trusted principal.
        org_id: Optional explicit org override (the ``?org_id=`` query
            parameter on the HTTP route).

    Returns:
        Mapping with ``user``, ``organization`` (or None for callers
        without an org), ``default_parameters``, and
        ``track_executions`` — the exact fields of
        ``DeveloperContextResponse``.

    Raises:
        SdkContextError: 403 when a non-bypass caller targets another
            org; 404 when the resolved org is missing or inactive.
    """
    if org_id is not None and org_id != principal.organization_id:
        is_provider_org = False
        if not principal.is_superuser and principal.organization_id is not None:
            row = await session.execute(
                select(Organization.is_provider).where(
                    Organization.id == principal.organization_id
                )
            )
            is_provider_org = bool(row.scalar_one_or_none())
        if not (principal.is_superuser or is_provider_org):
            raise SdkContextError(
                403,
                "Only platform admins or provider-org members can target another organization",
            )
        target_org_id = org_id
    elif org_id is not None:
        target_org_id = org_id
    else:
        target_org_id = principal.organization_id

    org_data = None
    if target_org_id is not None:
        stmt = select(Organization).where(Organization.id == target_org_id)
        result = await session.execute(stmt)
        org = result.scalar_one_or_none()
        if org is None or not org.is_active:
            raise SdkContextError(
                404,
                f"Organization {target_org_id} not found or inactive",
            )
        org_data = {
            "id": str(org.id),
            "name": org.name,
            "is_active": org.is_active,
            "is_provider": org.is_provider,
        }

    return {
        "user": {
            "id": str(principal.user_id),
            "email": principal.email,
            "name": principal.name,
            "is_superuser": principal.is_superuser,
        },
        "organization": org_data,
        "default_parameters": {},
        "track_executions": True,
    }
