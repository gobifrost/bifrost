"""
Branding Router

Global platform branding configuration.

Branding settings (colors, fonts, CSS) and logo binary data are stored
in the global_branding table. Logo images are served via GET /logo/{type} endpoints.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import Response

from shared.svg_sanitizer import SvgSanitizationError, sanitize_svg

from src.models import BrandingSettings, BrandingTerminology, BrandingUpdateRequest, GlobalBranding
from src.core.auth import Context
from src.core.database import AsyncSession, get_db
from src.services.audit import emit_audit
from src.services.authorization import CurrentAuthorizationContext

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/branding", tags=["Branding"])

# Allowed image types for logo upload
ALLOWED_CONTENT_TYPES = {"image/png", "image/jpeg", "image/jpg", "image/svg+xml"}
MAX_LOGO_SIZE = 5 * 1024 * 1024  # 5MB


def _branding_response(branding: GlobalBranding | None) -> BrandingSettings:
    if not branding:
        return BrandingSettings(
            application_name=None,
            square_logo_url=None,
            rectangle_logo_url=None,
            primary_color=None,
            terminology=BrandingTerminology(),
        )

    return BrandingSettings(
        application_name=branding.application_name,
        primary_color=branding.primary_color,
        terminology=BrandingTerminology.model_validate(branding.terminology or {}),
        square_logo_url="/api/branding/logo/square" if branding.square_logo_data else None,
        rectangle_logo_url="/api/branding/logo/rectangle" if branding.rectangle_logo_data else None,
    )


def _require_platform_branding_write(
    authorization: CurrentAuthorizationContext,
) -> None:
    authorization.require("configs.readwrite")
    authorization.require_resource_boundary(None)


# =============================================================================
# Public Endpoints (no auth required for branding display)
# =============================================================================


@router.get(
    "",
    response_model=BrandingSettings,
    summary="Get branding settings",
    description="Get platform branding settings. Public endpoint for login page display.",
)
async def get_branding(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> BrandingSettings:
    """
    Get branding settings (public endpoint).

    Returns global branding settings for the platform.
    Used on login page before authentication.
    """
    from src.repositories.branding import BrandingRepository
    branding_repo = BrandingRepository(db)
    branding = await branding_repo.get_branding()
    return _branding_response(branding)


# =============================================================================
# Authenticated Endpoints
# =============================================================================


@router.put(
    "",
    response_model=BrandingSettings,
    summary="Update primary color",
    description="Update platform primary color (superuser only)",
)
async def update_branding(
    request: BrandingUpdateRequest,
    ctx: Context,
    authorization: CurrentAuthorizationContext,
) -> BrandingSettings:
    """
    Update primary color only.

    Only superusers can update global branding.
    Use POST /logo/{type} to upload logos.
    """
    _require_platform_branding_write(authorization)

    from src.repositories.branding import BrandingRepository
    branding_repo = BrandingRepository(ctx.db)

    terminology = request.terminology.model_dump(exclude_none=True) if request.terminology else None
    # application_name defaults to None in the request DTO ("leave unchanged");
    # only forward it to the repo when a value was provided. Clearing is done via
    # DELETE /application-name.
    extra: dict = {}
    if request.application_name is not None:
        extra["application_name"] = request.application_name
    branding = await branding_repo.set_branding(
        primary_color=request.primary_color,
        terminology=terminology,
        **extra,
    )

    await emit_audit(
        ctx.db,
        "branding.update",
        resource_type="branding",
        details={
            "primary_color_set": request.primary_color is not None,
            "application_name_set": request.application_name is not None,
            "terminology_set": request.terminology is not None,
        },
    )
    await ctx.db.commit()
    logger.info("Branding updated by %s", authorization.effective_actor.email)

    return _branding_response(branding)


@router.post(
    "/logo/{logo_type}",
    response_model=BrandingSettings,
    summary="Upload logo",
    description="Upload a square or rectangle logo (superuser only)",
)
async def upload_logo(
    logo_type: str,
    file: Annotated[UploadFile, File(description="Logo image file")],
    ctx: Context,
    authorization: CurrentAuthorizationContext,
) -> BrandingSettings:
    """
    Upload a logo file.

    Args:
        logo_type: 'square' or 'rectangle'
        file: Image file (PNG, JPEG, SVG)
    """
    _require_platform_branding_write(authorization)

    if logo_type not in ("square", "rectangle"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="logo_type must be 'square' or 'rectangle'",
        )

    # Validate content type
    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid file type. Allowed: {', '.join(ALLOWED_CONTENT_TYPES)}",
        )

    # Read file content
    content = await file.read()
    if len(content) > MAX_LOGO_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File too large. Maximum size: {MAX_LOGO_SIZE // 1024 // 1024}MB",
        )

    if file.content_type == "image/svg+xml":
        try:
            content = sanitize_svg(content)
        except SvgSanitizationError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid SVG: {exc}",
            )

    # Save logo binary data to database
    from src.repositories.branding import BrandingRepository
    branding_repo = BrandingRepository(ctx.db)

    if logo_type == "square":
        branding = await branding_repo.set_branding(
            square_logo_data=content,
            square_logo_content_type=file.content_type,
        )
    else:  # rectangle
        branding = await branding_repo.set_branding(
            rectangle_logo_data=content,
            rectangle_logo_content_type=file.content_type,
        )

    await emit_audit(
        ctx.db,
        "branding.logo.upload",
        resource_type="branding",
        details={
            "logo_type": logo_type,
            "content_type": file.content_type,
            "size": len(content),
        },
    )
    await ctx.db.commit()
    logger.info(
        "Logo '%s' uploaded by %s",
        logo_type,
        authorization.effective_actor.email,
    )

    return _branding_response(branding)


@router.get(
    "/logo/{logo_type}",
    summary="Get logo image",
    description="Serve the uploaded logo image",
    responses={
        200: {"content": {"image/png": {}, "image/svg+xml": {}, "image/jpeg": {}}},
        404: {"description": "Logo not found"},
    },
)
async def get_logo(logo_type: str, db: AsyncSession = Depends(get_db)):
    """Serve logo image from database."""
    if logo_type not in ("square", "rectangle"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="logo_type must be 'square' or 'rectangle'",
        )

    from src.repositories.branding import BrandingRepository
    branding_repo = BrandingRepository(db)
    branding = await branding_repo.get_branding()

    if not branding:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Logo '{logo_type}' not found",
        )

    if logo_type == "square":
        if not branding.square_logo_data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Logo '{logo_type}' not found",
            )
        return Response(
            content=branding.square_logo_data,
            media_type=branding.square_logo_content_type or "application/octet-stream",
        )
    else:  # rectangle
        if not branding.rectangle_logo_data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Logo '{logo_type}' not found",
            )
        return Response(
            content=branding.rectangle_logo_data,
            media_type=branding.rectangle_logo_content_type or "application/octet-stream",
        )


@router.delete(
    "/logo/{logo_type}",
    response_model=BrandingSettings,
    summary="Reset logo to default",
    description="Remove custom logo and revert to default (superuser only)",
)
async def reset_logo(
    logo_type: str,
    ctx: Context,
    authorization: CurrentAuthorizationContext,
) -> BrandingSettings:
    """
    Reset a specific logo to default.

    Args:
        logo_type: 'square' or 'rectangle'
    """
    _require_platform_branding_write(authorization)

    if logo_type not in ("square", "rectangle"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="logo_type must be 'square' or 'rectangle'",
        )

    from src.repositories.branding import BrandingRepository
    branding_repo = BrandingRepository(ctx.db)

    # Reset the specific logo by setting it to None
    if logo_type == "square":
        branding = await branding_repo.set_branding(
            square_logo_data=None,
            square_logo_content_type=None,
        )
    else:  # rectangle
        branding = await branding_repo.set_branding(
            rectangle_logo_data=None,
            rectangle_logo_content_type=None,
        )

    await emit_audit(
        ctx.db,
        "branding.logo.reset",
        resource_type="branding",
        details={"logo_type": logo_type},
    )
    await ctx.db.commit()
    logger.info(
        "Logo '%s' reset to default by %s",
        logo_type,
        authorization.effective_actor.email,
    )

    return _branding_response(branding)


@router.delete(
    "/color",
    response_model=BrandingSettings,
    summary="Reset primary color to default",
    description="Remove custom primary color and revert to default (superuser only)",
)
async def reset_color(
    ctx: Context,
    authorization: CurrentAuthorizationContext,
) -> BrandingSettings:
    """Reset primary color to default."""
    _require_platform_branding_write(authorization)

    from src.repositories.branding import BrandingRepository
    branding_repo = BrandingRepository(ctx.db)

    # Reset primary color by setting it to None
    branding = await branding_repo.set_branding(primary_color=None)

    await emit_audit(
        ctx.db,
        "branding.color.reset",
        resource_type="branding",
    )
    await ctx.db.commit()
    logger.info(
        "Primary color reset to default by %s",
        authorization.effective_actor.email,
    )

    return _branding_response(branding)


@router.delete(
    "/application-name",
    response_model=BrandingSettings,
    summary="Reset application name to default",
    description="Remove custom application name and revert to default (superuser only)",
)
async def reset_application_name(
    ctx: Context,
    authorization: CurrentAuthorizationContext,
) -> BrandingSettings:
    """Reset application name to default."""
    _require_platform_branding_write(authorization)
    from src.repositories.branding import BrandingRepository
    branding_repo = BrandingRepository(ctx.db)

    # Clear application name (pass explicit None to clear, not the unchanged sentinel)
    branding = await branding_repo.set_branding(application_name=None)

    await emit_audit(
        ctx.db,
        "branding.application_name.reset",
        resource_type="branding",
    )
    await ctx.db.commit()
    logger.info(
        "Application name reset to default by %s",
        authorization.effective_actor.email,
    )

    return _branding_response(branding)


@router.delete(
    "",
    response_model=BrandingSettings,
    summary="Reset all branding to defaults",
    description="Remove all custom branding (logos and color) and revert to defaults (superuser only)",
)
async def reset_all_branding(
    ctx: Context,
    authorization: CurrentAuthorizationContext,
) -> BrandingSettings:
    """Reset all branding to defaults."""
    _require_platform_branding_write(authorization)

    from src.repositories.branding import BrandingRepository
    branding_repo = BrandingRepository(ctx.db)

    # Delete all branding - this will return defaults
    await branding_repo.delete_branding()

    await emit_audit(
        ctx.db,
        "branding.reset",
        resource_type="branding",
    )
    await ctx.db.commit()
    logger.info(
        "All branding reset to defaults by %s",
        authorization.effective_actor.email,
    )

    return BrandingSettings(
        application_name=None,
        primary_color=None,
        square_logo_url=None,
        rectangle_logo_url=None,
        terminology=BrandingTerminology(),
    )
