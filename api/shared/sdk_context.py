"""Shared business service for the SDK ``GET /api/sdk/context`` operation.

Single implementation for both entry points:

- the HTTP handler (``api/src/routers/cli.py::get_dev_context``) serving
  external SDK/CLI callers bootstrapping an execution context, and
- the same handler reached by workflow children over the worker-local
  engine socket.

Both paths resolve selected scope through the original caller's current
role reach. Actor flags never authorize a scope override.

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

    Returns actor facts and the selected original-user scope. An explicit
    organization must be in current role reach, including for runtime calls.

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
        SdkContextError: 403 outside original reach; 404 for an inactive or missing organization.
    """
    from shared.sdk_config import ScopeResolutionError, resolve_sdk_scope
    from src.services.authorization.enforce import load_caller

    caller = await load_caller(session, principal)
    try:
        target_org_id = await resolve_sdk_scope(str(org_id) if org_id is not None else None, caller=caller)
    except ScopeResolutionError as exc:
        raise SdkContextError(exc.status_code, exc.detail) from exc

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
