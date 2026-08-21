"""Boundary-aware access helpers for Application authoring surfaces."""

from uuid import UUID

from fastapi import HTTPException, status

from src.core.auth import Context
from src.core.exceptions import AccessDeniedError
from src.models.orm.applications import Application
from src.repositories.applications import ApplicationRepository
from src.services.authorization import (
    AuthorizationBoundaryKind,
    AuthorizationContext,
)


def selected_application_organization_id(
    authorization: AuthorizationContext,
) -> UUID | None:
    """Resolve one executable Application target from the selected boundary."""

    boundary = authorization.selected_boundary
    if boundary.kind is AuthorizationBoundaryKind.MANAGED_ORGANIZATIONS:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Select one organization before working with Applications",
        )
    return (
        boundary.organization_id
        if boundary.kind is AuthorizationBoundaryKind.ORGANIZATION
        else None
    )


def authorized_application_repository(
    ctx: Context,
    authorization: AuthorizationContext,
) -> ApplicationRepository:
    """Build an Application repository bound to the selected exact target."""

    user = authorization.requester
    return ApplicationRepository(
        ctx.db,
        selected_application_organization_id(authorization),
        user_id=user.user_id,
        bypass_resource_roles=authorization.has_capability("platform.superuser"),
        is_external=user.is_external,
    )


async def authorized_application_by_id(
    ctx: Context,
    authorization: AuthorizationContext,
    app_id: UUID,
) -> Application:
    """Return one visible Application without leaking inaccessible IDs."""

    repo = authorized_application_repository(ctx, authorization)
    try:
        return await repo.can_access(id=app_id)
    except AccessDeniedError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Application '{app_id}' not found",
        )


async def authorized_application_by_slug(
    ctx: Context,
    authorization: AuthorizationContext,
    slug: str,
) -> Application:
    """Return one visible Application by slug in the selected target."""

    repo = authorized_application_repository(ctx, authorization)
    try:
        return await repo.can_access(slug=slug, include_solution_managed=True)
    except AccessDeniedError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Application '{slug}' not found",
        )


def require_application_mutation(
    authorization: AuthorizationContext,
    application: Application,
) -> None:
    """Require Application authoring authority in the resource's boundary."""

    authorization.require("apps.readwrite")
    authorization.require_resource_boundary(application.organization_id)
