"""Shared authorization policy for App Builder control-plane changes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from fastapi import HTTPException, status

if TYPE_CHECKING:
    from src.models.contracts.applications import ApplicationUpdate


class PlatformAdminUser(Protocol):
    is_platform_admin: bool


def require_platform_admin(user: PlatformAdminUser) -> None:
    """Require platform-admin privileges for App Builder mutations."""
    if not user.is_platform_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Platform admin privileges required",
        )


def update_requires_platform_admin(data: ApplicationUpdate) -> bool:
    """Return true when a metadata patch changes routing or access control."""
    return (
        data.slug is not None
        or data.scope is not None
        or data.access_level is not None
        or data.role_ids is not None
    )
