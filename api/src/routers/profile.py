"""
Profile Router

User profile management endpoints for viewing and updating user profile,
avatar, and password.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import select, update

from src.core.auth import CurrentUser
from src.core.security import verify_password, get_password_hash
from src.core.database import AsyncSession, get_db
from src.models import PasswordChange, ProfileResponse, ProfileUpdate
from src.models.orm.users import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/profile", tags=["Profile"])

# Allowed image types for avatar upload
ALLOWED_CONTENT_TYPES = {"image/png", "image/jpeg", "image/jpg"}
MAX_AVATAR_SIZE = 2 * 1024 * 1024  # 2MB


# =============================================================================
# Profile Endpoints
# =============================================================================


@router.get(
    "",
    response_model=ProfileResponse,
    summary="Get current user profile",
    description="Get the authenticated user's profile information",
)
async def get_profile(
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
) -> ProfileResponse:
    """Get current user's profile."""
    # Load user from DB to get latest data including avatar info
    stmt = select(User).where(User.id == current_user.user_id)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    return ProfileResponse(
        id=user.id,
        email=user.email,
        name=user.name,
        has_avatar=user.avatar_data is not None,
        has_password=user.hashed_password is not None,
        organization_id=user.organization_id,
        is_superuser=user.is_superuser,
    )


@router.patch(
    "",
    response_model=ProfileResponse,
    summary="Update user profile",
    description="Update the authenticated user's profile information",
)
async def update_profile(
    request: ProfileUpdate,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
) -> ProfileResponse:
    """Update current user's profile."""
    # Build update values
    update_values = {}
    if request.name is not None:
        update_values["name"] = request.name

    if not update_values:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fields to update",
        )

    # Update user
    await db.execute(
        update(User)
        .where(User.id == current_user.user_id)
        .values(**update_values)
    )
    await db.commit()

    # Return updated profile
    stmt = select(User).where(User.id == current_user.user_id)
    result = await db.execute(stmt)
    user = result.scalar_one()

    return ProfileResponse(
        id=user.id,
        email=user.email,
        name=user.name,
        has_avatar=user.avatar_data is not None,
        has_password=user.hashed_password is not None,
        organization_id=user.organization_id,
        is_superuser=user.is_superuser,
    )


# =============================================================================
# Avatar Endpoints
# =============================================================================


@router.get(
    "/avatar",
    summary="Get user avatar",
    description="Get the authenticated user's avatar image",
    responses={
        200: {"content": {"image/png": {}, "image/jpeg": {}}},
        404: {"description": "No avatar set"},
    },
)
async def get_avatar(
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
) -> Response:
    """Get current user's avatar image."""
    stmt = select(User).where(User.id == current_user.user_id)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if not user or not user.avatar_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No avatar set",
        )

    return Response(
        content=user.avatar_data,
        media_type=user.avatar_content_type or "image/png",
    )


@router.post(
    "/avatar",
    response_model=ProfileResponse,
    summary="Upload avatar",
    description="Upload a new avatar image (PNG or JPEG, max 2MB)",
)
async def upload_avatar(
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    file: UploadFile = File(...),
) -> ProfileResponse:
    """Upload avatar image."""
    # Validate content type
    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid file type. Allowed: {', '.join(ALLOWED_CONTENT_TYPES)}",
        )

    # Read and validate file size
    content = await file.read()
    if len(content) > MAX_AVATAR_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File too large. Maximum size: {MAX_AVATAR_SIZE // (1024 * 1024)}MB",
        )

    # Update user avatar
    await db.execute(
        update(User)
        .where(User.id == current_user.user_id)
        .values(
            avatar_data=content,
            avatar_content_type=file.content_type,
        )
    )
    await db.commit()

    # Return updated profile
    stmt = select(User).where(User.id == current_user.user_id)
    result = await db.execute(stmt)
    user = result.scalar_one()

    return ProfileResponse(
        id=user.id,
        email=user.email,
        name=user.name,
        has_avatar=True,
        has_password=user.hashed_password is not None,
        organization_id=user.organization_id,
        is_superuser=user.is_superuser,
    )


@router.delete(
    "/avatar",
    response_model=ProfileResponse,
    summary="Delete avatar",
    description="Remove the user's avatar",
)
async def delete_avatar(
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
) -> ProfileResponse:
    """Remove user's avatar."""
    await db.execute(
        update(User)
        .where(User.id == current_user.user_id)
        .values(
            avatar_data=None,
            avatar_content_type=None,
        )
    )
    await db.commit()

    # Return updated profile
    stmt = select(User).where(User.id == current_user.user_id)
    result = await db.execute(stmt)
    user = result.scalar_one()

    return ProfileResponse(
        id=user.id,
        email=user.email,
        name=user.name,
        has_avatar=False,
        has_password=user.hashed_password is not None,
        organization_id=user.organization_id,
        is_superuser=user.is_superuser,
    )


# =============================================================================
# Password Endpoints
# =============================================================================


@router.post(
    "/password",
    summary="Change or set password",
    description="Change the authenticated user's password, or set one if they don't have one (passkey/OAuth users)",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def change_password(
    request: PasswordChange,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
) -> None:
    """Change or set user's password."""
    # Load user to verify current password
    stmt = select(User).where(User.id == current_user.user_id)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # If user has a password, require current_password and verify it
    if user.hashed_password:
        if not request.current_password:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Current password is required",
            )
        if not verify_password(request.current_password, user.hashed_password):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Current password is incorrect",
            )

    # Hash and update new password
    new_hashed = get_password_hash(request.new_password)
    await db.execute(
        update(User)
        .where(User.id == current_user.user_id)
        .values(hashed_password=new_hashed)
    )
    await db.commit()

    if user.hashed_password:
        logger.info(f"Password changed for user {user.email}")
    else:
        logger.info(f"Password set for user {user.email}")
