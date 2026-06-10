"""
MFA Router

Provides endpoints for Multi-Factor Authentication:
- MFA setup and verification (simplified routes)
- Recovery codes management
- Trusted devices management
"""

import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from src.core.cache import get_shared_redis
from src.core.cache.keys import refresh_token_jti_key, TTL_REFRESH_TOKEN
from src.core.auth import CurrentActiveUser, get_current_user_from_db
from src.core.db_deps import DbSession
from src.core.log_safety import log_safe
from src.core.security import (
    create_access_token,
    create_refresh_token,
    verify_password,
)
from src.services.mfa_service import MFAService
from src.services.user_provisioning import get_user_roles

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth/mfa", tags=["mfa"])


# =============================================================================
# Request/Response Models
# =============================================================================

class MFAStatusResponse(BaseModel):
    """MFA status response."""
    mfa_enabled: bool
    mfa_required: bool
    enforcement_deadline: str | None = None
    enrolled_methods: list[str]
    recovery_codes_remaining: int


class MFASetupResponse(BaseModel):
    """MFA setup response with secret."""
    secret: str
    qr_code_uri: str
    provisioning_uri: str
    issuer: str
    account_name: str


class MFAVerifyRequest(BaseModel):
    """Request to verify MFA code."""
    code: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")


class MFAVerifyResponse(BaseModel):
    """
    MFA verification response.

    For enrollment completion: includes recovery_codes and tokens (auto-login)
    For subsequent verifications: just success status
    """
    success: bool
    recovery_codes: list[str] | None = None
    # Token fields for auto-login after enrollment
    access_token: str | None = None
    refresh_token: str | None = None
    token_type: str = "bearer"


class MFARemoveRequest(BaseModel):
    """Request to remove MFA method."""
    password: str | None = None
    mfa_code: str | None = None


class RecoveryCodesResponse(BaseModel):
    """Recovery codes response."""
    recovery_codes: list[str]
    count: int


class RecoveryCodesCountResponse(BaseModel):
    """Recovery codes count response."""
    total: int
    remaining: int


class RegenerateRecoveryCodesRequest(BaseModel):
    """Request to regenerate recovery codes."""
    mfa_code: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")


class TrustedDeviceResponse(BaseModel):
    """Trusted device info."""
    id: str
    device_name: str | None
    created_at: str
    expires_at: str
    last_used_at: str | None
    is_current: bool = False


class TrustedDevicesResponse(BaseModel):
    """List of trusted devices."""
    devices: list[TrustedDeviceResponse]


# =============================================================================
# MFA Status
# =============================================================================

@router.get("/status", response_model=MFAStatusResponse)
async def get_mfa_status(
    current_user: CurrentActiveUser,
    db: DbSession = None,
) -> MFAStatusResponse:
    """
    Get MFA status for current user.

    Returns:
        MFA status including enabled state, enrolled methods, and recovery code count
    """
    # Get the actual user from DB to access relationships
    user = await get_current_user_from_db(current_user, db)
    mfa_service = MFAService(db)

    status = await mfa_service.get_mfa_status(user)

    return MFAStatusResponse(
        mfa_enabled=status["mfa_enabled"],
        mfa_required=status["mfa_required"],
        enforcement_deadline=status["enforcement_deadline"].isoformat() if status["enforcement_deadline"] else None,
        enrolled_methods=status["enrolled_methods"],
        recovery_codes_remaining=status["recovery_codes_remaining"],
    )


# =============================================================================
# MFA Setup and Verification (Simplified Routes)
# =============================================================================

@router.post("/totp/setup", response_model=MFASetupResponse)
async def setup_mfa(
    current_user: CurrentActiveUser,
    db: DbSession = None,
) -> MFASetupResponse:
    """
    Initialize MFA enrollment for an authenticated user.

    This endpoint is for users who are already logged in and want to add/reset TOTP.
    For initial MFA enrollment during login, use POST /auth/mfa/setup instead.

    Generates a new TOTP secret and returns the provisioning URI for QR code generation.
    The MFA method is created in PENDING status until verified.

    Returns:
        MFA setup data including secret and QR code URI
    """
    user = await get_current_user_from_db(current_user, db)
    mfa_service = MFAService(db)

    setup_data = await mfa_service.setup_totp(user)
    await db.commit()

    logger.info(
        f"MFA setup initiated for user: {user.email}",
        extra={"user_id": str(user.id)}
    )

    return MFASetupResponse(**setup_data)


@router.post("/totp/verify", response_model=MFAVerifyResponse)
async def verify_mfa(
    request: MFAVerifyRequest,
    current_user: CurrentActiveUser,
    db: DbSession = None,
) -> MFAVerifyResponse:
    """
    Verify MFA code to complete enrollment for an authenticated user.

    This endpoint is for users who are already logged in and want to complete TOTP setup.
    For initial MFA enrollment during login, use POST /auth/mfa/verify instead.

    On success:
    - Activates the MFA method
    - Generates recovery codes (shown only once!)
    - Returns access tokens for auto-login

    Args:
        request: MFA verification request with 6-digit code

    Returns:
        Success status, recovery codes, and access tokens

    Raises:
        HTTPException: If verification fails
    """
    user = await get_current_user_from_db(current_user, db)
    mfa_service = MFAService(db)

    try:
        recovery_codes = await mfa_service.verify_totp_enrollment(user, request.code)
        await db.commit()
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    logger.info(
        f"MFA enrollment completed for user: {user.email}",
        extra={"user_id": str(user.id)}
    )

    # Generate tokens for auto-login after MFA enrollment
    db_roles = await get_user_roles(db, user.id)
    roles = ["authenticated", *db_roles]

    token_data = {
        "sub": str(user.id),
        "email": user.email,
        "name": user.name or user.email.split("@")[0],
        "is_superuser": user.is_superuser,
        "org_id": str(user.organization_id) if user.organization_id else None,
        "roles": roles,
    }

    access_token = create_access_token(data=token_data)
    refresh_token, jti = create_refresh_token(data={"sub": str(user.id)})

    # Store JTI in Redis for revocation support
    r = await get_shared_redis()
    await r.setex(refresh_token_jti_key(str(user.id), jti), TTL_REFRESH_TOKEN, "1")

    return MFAVerifyResponse(
        success=True,
        recovery_codes=recovery_codes,
        access_token=access_token,
        refresh_token=refresh_token,
    )


@router.delete("", status_code=status.HTTP_200_OK)
async def remove_mfa(
    request: MFARemoveRequest,
    current_user: CurrentActiveUser,
    db: DbSession = None,
) -> dict:
    """
    Remove MFA enrollment.

    Requires either current password or MFA code for verification.

    Args:
        request: Removal request with password or MFA code

    Returns:
        Success message

    Raises:
        HTTPException: If verification fails
    """
    if not request.password and not request.mfa_code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either password or mfa_code is required",
        )

    user = await get_current_user_from_db(current_user, db)
    mfa_service = MFAService(db)

    # Verify authorization
    if request.password:
        if not user.hashed_password:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Account does not have password authentication",
            )
        if not verify_password(request.password, user.hashed_password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid password",
            )
    elif request.mfa_code:
        if not await mfa_service.verify_totp_code(user.id, request.mfa_code):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid MFA code",
            )

    await mfa_service.remove_totp(user)
    await db.commit()

    logger.info(
        f"MFA removed for user: {user.email}",
        extra={"user_id": str(user.id)}
    )

    return {"message": "MFA removed successfully"}


# =============================================================================
# Recovery Codes
# =============================================================================

@router.post("/recovery-codes/regenerate", response_model=RecoveryCodesResponse)
async def regenerate_recovery_codes(
    request: RegenerateRecoveryCodesRequest,
    current_user: CurrentActiveUser,
    db: DbSession = None,
) -> RecoveryCodesResponse:
    """
    Regenerate recovery codes.

    Requires MFA code for verification. Invalidates all existing recovery codes.

    Args:
        request: Request with current MFA code

    Returns:
        New set of recovery codes

    Raises:
        HTTPException: If MFA verification fails
    """
    user = await get_current_user_from_db(current_user, db)
    mfa_service = MFAService(db)

    # Verify MFA code
    if not await mfa_service.verify_totp_code(user.id, request.mfa_code):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid MFA code",
        )

    recovery_codes = await mfa_service.regenerate_recovery_codes(user.id)
    await db.commit()

    logger.info(
        f"Recovery codes regenerated for user: {user.email}",
        extra={"user_id": str(user.id)}
    )

    return RecoveryCodesResponse(
        recovery_codes=recovery_codes,
        count=len(recovery_codes),
    )


@router.get("/recovery-codes/count", response_model=RecoveryCodesCountResponse)
async def get_recovery_codes_count(
    current_user: CurrentActiveUser,
    db: DbSession = None,
) -> RecoveryCodesCountResponse:
    """
    Get count of remaining unused recovery codes.

    Returns:
        Total and remaining recovery code counts
    """
    mfa_service = MFAService(db)
    counts = await mfa_service.get_recovery_codes_count(current_user.user_id)

    return RecoveryCodesCountResponse(**counts)


# =============================================================================
# Trusted Devices
# =============================================================================

@router.get("/trusted-devices", response_model=TrustedDevicesResponse)
async def list_trusted_devices(
    request: Request,
    current_user: CurrentActiveUser,
    db: DbSession = None,
) -> TrustedDevicesResponse:
    """
    List all trusted devices for current user.

    Returns:
        List of trusted devices with their status
    """
    mfa_service = MFAService(db)
    devices = await mfa_service.get_trusted_devices(current_user.user_id)

    # Get current device fingerprint for comparison
    user_agent = request.headers.get("user-agent", "")
    current_fingerprint = MFAService.generate_device_fingerprint(user_agent)

    device_list = [
        TrustedDeviceResponse(
            id=str(d.id),
            device_name=d.device_name,
            created_at=d.created_at.isoformat(),
            expires_at=d.expires_at.isoformat(),
            last_used_at=d.last_used_at.isoformat() if d.last_used_at else None,
            is_current=d.device_fingerprint == current_fingerprint,
        )
        for d in devices
    ]

    return TrustedDevicesResponse(devices=device_list)


@router.delete("/trusted-devices/{device_id}")
async def revoke_trusted_device(
    device_id: UUID,
    current_user: CurrentActiveUser,
    db: DbSession = None,
) -> dict:
    """
    Revoke trust for a specific device.

    Args:
        device_id: Device ID to revoke

    Returns:
        Success message

    Raises:
        HTTPException: If device not found
    """
    mfa_service = MFAService(db)
    revoked = await mfa_service.revoke_trusted_device(current_user.user_id, device_id)
    await db.commit()

    if not revoked:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Device not found",
        )

    logger.info(
        "Trusted device revoked for user",
        extra={
            "user_id": str(current_user.user_id),
            "device_id": log_safe(str(device_id)),
        }
    )

    return {"message": "Device trust revoked"}


@router.delete("/trusted-devices")
async def revoke_all_trusted_devices(
    current_user: CurrentActiveUser,
    db: DbSession = None,
) -> dict:
    """
    Revoke trust for all devices.

    Returns:
        Count of revoked devices
    """
    mfa_service = MFAService(db)
    count = await mfa_service.revoke_all_trusted_devices(current_user.user_id)
    await db.commit()

    logger.info(
        "All trusted devices revoked for user",
        extra={
            "user_id": str(current_user.user_id),
            "count": count,
        }
    )

    return {"message": f"Revoked {count} trusted devices"}
