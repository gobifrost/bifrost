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

from src.models import BrandingSettings, BrandingUpdateRequest
from src.core.auth import Context, CurrentActiveUser
from src.core.database import AsyncSession, get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/branding", tags=["Branding"])

# Allowed image types for logo upload
ALLOWED_CONTENT_TYPES = {"image/png", "image/jpeg", "image/jpg", "image/svg+xml"}
MAX_LOGO_SIZE = 5 * 1024 * 1024  # 5MB


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

    if not branding:
        # Return defaults if no branding configured
        return BrandingSettings(
            square_logo_url=None,
            rectangle_logo_url=None,
            primary_color=None,
        )

    return BrandingSettings(
        primary_color=branding.primary_color,
        square_logo_url="/api/branding/logo/square" if branding.square_logo_data else None,
        rectangle_logo_url="/api/branding/logo/rectangle" if branding.rectangle_logo_data else None,
    )


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
    user: CurrentActiveUser,
) -> BrandingSettings:
    """
    Update primary color only.

    Only superusers can update global branding.
    Use POST /logo/{type} to upload logos.
    """
    if not user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only superusers can update branding",
        )

    from src.repositories.branding import BrandingRepository
    branding_repo = BrandingRepository(ctx.db)

    # Update primary color only
    branding = await branding_repo.set_branding(primary_color=request.primary_color)

    await ctx.db.commit()
    logger.info(f"Primary color updated by {user.email}")

    return BrandingSettings(
        primary_color=branding.primary_color,
        square_logo_url="/api/branding/logo/square" if branding.square_logo_data else None,
        rectangle_logo_url="/api/branding/logo/rectangle" if branding.rectangle_logo_data else None,
    )


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
    user: CurrentActiveUser,
) -> BrandingSettings:
    """
    Upload a logo file.

    Args:
        logo_type: 'square' or 'rectangle'
        file: Image file (PNG, JPEG, SVG)
    """
    if not user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only superusers can upload logos",
        )

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

    await ctx.db.commit()
    logger.info(f"Logo '{logo_type}' uploaded by {user.email}")

    return BrandingSettings(
        primary_color=branding.primary_color,
        square_logo_url="/api/branding/logo/square" if branding.square_logo_data else None,
        rectangle_logo_url="/api/branding/logo/rectangle" if branding.rectangle_logo_data else None,
    )


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
    user: CurrentActiveUser,
) -> BrandingSettings:
    """
    Reset a specific logo to default.

    Args:
        logo_type: 'square' or 'rectangle'
    """
    if not user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only superusers can reset logos",
        )

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

    await ctx.db.commit()
    logger.info(f"Logo '{logo_type}' reset to default by {user.email}")

    return BrandingSettings(
        primary_color=branding.primary_color,
        square_logo_url="/api/branding/logo/square" if branding.square_logo_data else None,
        rectangle_logo_url="/api/branding/logo/rectangle" if branding.rectangle_logo_data else None,
    )


@router.delete(
    "/color",
    response_model=BrandingSettings,
    summary="Reset primary color to default",
    description="Remove custom primary color and revert to default (superuser only)",
)
async def reset_color(
    ctx: Context,
    user: CurrentActiveUser,
) -> BrandingSettings:
    """Reset primary color to default."""
    if not user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only superusers can reset branding",
        )

    from src.repositories.branding import BrandingRepository
    branding_repo = BrandingRepository(ctx.db)

    # Reset primary color by setting it to None
    branding = await branding_repo.set_branding(primary_color=None)

    await ctx.db.commit()
    logger.info(f"Primary color reset to default by {user.email}")

    return BrandingSettings(
        primary_color=branding.primary_color,
        square_logo_url="/api/branding/logo/square" if branding.square_logo_data else None,
        rectangle_logo_url="/api/branding/logo/rectangle" if branding.rectangle_logo_data else None,
    )


@router.delete(
    "",
    response_model=BrandingSettings,
    summary="Reset all branding to defaults",
    description="Remove all custom branding (logos and color) and revert to defaults (superuser only)",
)
async def reset_all_branding(
    ctx: Context,
    user: CurrentActiveUser,
) -> BrandingSettings:
    """Reset all branding to defaults."""
    if not user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only superusers can reset branding",
        )

    from src.repositories.branding import BrandingRepository
    branding_repo = BrandingRepository(ctx.db)

    # Delete all branding - this will return defaults
    await branding_repo.delete_branding()

    await ctx.db.commit()
    logger.info(f"All branding reset to defaults by {user.email}")

    return BrandingSettings(
        primary_color=None,
        square_logo_url=None,
        rectangle_logo_url=None,
    )
