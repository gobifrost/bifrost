"""Boundary-aware authorization helpers for human user administration."""

from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.organizations import Organization
from src.models.orm.users import User
from src.services.authorization import (
    AuthorizationBoundaryKind,
    AuthorizationContext,
)


async def require_user_visible(
    db: AsyncSession,
    *,
    authorization: AuthorizationContext,
    user: User,
) -> None:
    """Require the selected collection boundary to contain ``user``."""

    if authorization.has_capability("platform.superuser"):
        return

    boundary = authorization.selected_boundary
    if boundary.kind is AuthorizationBoundaryKind.ORGANIZATION:
        admitted = user.organization_id == boundary.organization_id
    elif boundary.kind is AuthorizationBoundaryKind.PLATFORM:
        admitted = user.organization_id is None
    else:
        admitted = False
        if user.organization_id is not None:
            is_provider = await db.scalar(
                select(Organization.is_provider).where(
                    Organization.id == user.organization_id
                )
            )
            admitted = is_provider is False

    if not admitted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )


def require_exact_user_boundary(
    *,
    authorization: AuthorizationContext,
    organization_id: UUID | None,
) -> None:
    """Require an exact organization/Platform boundary for a user mutation."""

    authorization.require_resource_boundary(organization_id)


__all__ = ["require_exact_user_boundary", "require_user_visible"]
