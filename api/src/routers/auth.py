"""
Authentication Router

Provides endpoints for user authentication:
- Login (JWT token generation with user provisioning)
- Token refresh with rotation
- Token revocation (logout, revoke-all)
- Current user info

Key Features:
- First user login auto-promotes to PlatformAdmin
- Subsequent users auto-join organizations by email domain
- JWT tokens include is_superuser, org_id, and roles
- Refresh tokens use JTI for revocation support
"""

import logging
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr

from src.core.cache import get_shared_redis
from src.core.cache.keys import (
    device_code_key,
    device_user_code_index_key,
    refresh_token_jti_key,
    user_refresh_tokens_pattern,
    TTL_DEVICE_CODE,
    TTL_REFRESH_TOKEN,
)
from src.models import (
    AuthStatusResponse,
    DeviceAuthorizeRequest,
    DeviceCodeResponse,
    DeviceTokenErrorResponse,
    DeviceTokenRequest,
    DeviceTokenResponse,
    OAuthProviderInfo,
)
from src.models.contracts.passkeys import (
    InvitePasskeyOptionsRequest,
    InvitePasskeyVerifyRequest,
    SetupPasskeyOptionsRequest,
    SetupPasskeyOptionsResponse,
    SetupPasskeyVerifyRequest,
    SetupPasskeyVerifyResponse,
)
from src.config import get_settings
from src.core.auth import CurrentActiveUser
from src.core.db_deps import DbSession
from src.core.log_safety import log_safe
from src.core.rate_limit import auth_limiter, mfa_limiter, get_client_ip
from src.services.audit import emit_audit
from src.services.audit_context import ActorContext, current_actor
from src.core.security import (
    create_access_token,
    create_mfa_token,
    create_refresh_token,
    decode_mfa_token,
    decode_token,
    generate_csrf_token,
    get_password_hash,
    verify_password,
)
from src.repositories.users import UserRepository
from src.services.user_provisioning import ensure_user_provisioned, get_user_roles
from shared.external_access import resolve_external_claim

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


# =============================================================================
# Cookie Configuration
# =============================================================================

def set_auth_cookies(response: Response, access_token: str, refresh_token: str):
    """
    Set HttpOnly authentication cookies and CSRF token.

    Cookies are secure, SameSite=Lax, and HttpOnly for XSS protection.
    This provides automatic auth for browser clients while still allowing
    service-to-service auth via Authorization header.

    Also sets a CSRF token cookie that JavaScript can read and send as a header.
    """
    settings = get_settings()

    # Determine if we're in production (HTTPS)
    # Only use secure cookies in production - dev and testing use HTTP
    secure = settings.is_production

    # Access token cookie (short-lived)
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=30 * 60,  # 30 minutes (matches JWT expiry)
        path="/",
    )

    # Refresh token cookie (long-lived)
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=7 * 24 * 60 * 60,  # 7 days (matches JWT expiry)
        path="/",
    )

    # CSRF token cookie (readable by JavaScript for X-CSRF-Token header)
    csrf_token = generate_csrf_token()
    response.set_cookie(
        key="csrf_token",
        value=csrf_token,
        httponly=False,  # JS needs to read this
        secure=secure,
        samesite="strict",  # Stricter for CSRF protection
        max_age=30 * 60,  # Match access token expiry
        path="/",
    )


def clear_auth_cookies(response: Response):
    """Clear authentication cookies on logout."""
    response.delete_cookie(key="access_token", path="/")
    response.delete_cookie(key="refresh_token", path="/")
    response.delete_cookie(key="csrf_token", path="/")


# =============================================================================
# Refresh Token JTI Management
# =============================================================================


async def store_refresh_token_jti(user_id: str, jti: str) -> None:
    """
    Store a refresh token JTI in Redis for validation/revocation.

    Args:
        user_id: User ID the token belongs to
        jti: JWT ID to store
    """
    r = await get_shared_redis()
    key = refresh_token_jti_key(user_id, jti)
    await r.setex(key, TTL_REFRESH_TOKEN, "1")


async def validate_and_revoke_refresh_token_jti(user_id: str, jti: str) -> bool:
    """
    Validate a refresh token JTI exists and revoke it (single use).

    Args:
        user_id: User ID the token belongs to
        jti: JWT ID to validate

    Returns:
        True if JTI was valid and has been revoked, False if invalid
    """
    r = await get_shared_redis()
    key = refresh_token_jti_key(user_id, jti)

    # Atomically check and delete
    result = await r.delete(key)
    return result > 0


async def revoke_all_user_refresh_tokens(user_id: str) -> int:
    """
    Revoke all refresh tokens for a user.

    Args:
        user_id: User ID to revoke tokens for

    Returns:
        Number of tokens revoked
    """
    r = await get_shared_redis()
    pattern = user_refresh_tokens_pattern(user_id)

    # Find all keys matching pattern
    keys = []
    async for key in r.scan_iter(match=pattern):
        keys.append(key)

    if keys:
        return await r.delete(*keys)
    return 0


# =============================================================================
# Request/Response Models
# =============================================================================

class Token(BaseModel):
    """Token response model."""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class MFARequiredResponse(BaseModel):
    """Response when MFA verification is required."""
    mfa_required: bool = True
    mfa_token: str
    available_methods: list[str]
    expires_in: int = 300  # 5 minutes


class MFASetupRequiredResponse(BaseModel):
    """Response when MFA enrollment is required."""
    mfa_setup_required: bool = True
    mfa_token: str
    expires_in: int = 300  # 5 minutes


class MFAVerifyRequest(BaseModel):
    """Request to verify MFA code during login."""
    mfa_token: str
    code: str
    trust_device: bool = False
    device_name: str | None = None


class LoginResponse(BaseModel):
    """Unified login response that can be Token or MFA response."""
    # Token fields (when MFA not required or after MFA verification)
    access_token: str | None = None
    refresh_token: str | None = None
    token_type: str = "bearer"
    # MFA fields (when MFA required)
    mfa_required: bool = False
    mfa_setup_required: bool = False
    mfa_token: str | None = None
    available_methods: list[str] | None = None
    expires_in: int | None = None


class TokenRefresh(BaseModel):
    """Token refresh request model."""
    refresh_token: str


class UserResponse(BaseModel):
    """User response model."""
    id: str
    email: str
    name: str
    is_active: bool
    is_superuser: bool
    is_verified: bool
    organization_id: str | None
    roles: list[str] = []


class UserCreate(BaseModel):
    """User creation request model."""
    email: EmailStr
    password: str
    name: str | None = None


# =============================================================================
# Endpoints
# =============================================================================

@router.post("/login", response_model=LoginResponse)
async def login(
    response: Response,
    form_data: OAuth2PasswordRequestForm = Depends(),
    request: Request = None,
    db: DbSession = None,
) -> LoginResponse:
    """
    Login with email and password.

    For email/password authentication, MFA is REQUIRED:
    - If user has MFA enrolled: returns mfa_required=True with mfa_token
    - If user has no MFA: returns mfa_setup_required=True to redirect to enrollment

    Performs user provisioning on each login:
    - First user in system becomes PlatformAdmin
    - Subsequent users are matched to organizations by email domain
    - JWT tokens include is_superuser, org_id, and roles for authorization

    Rate limited: 10 requests per minute per IP address.

    Args:
        form_data: OAuth2 password form with username (email) and password
        request: FastAPI request object
        db: Database session

    Returns:
        LoginResponse with either tokens (MFA bypass for trusted device) or MFA requirements

    Raises:
        HTTPException: If credentials are invalid or provisioning fails
    """
    from src.services.mfa_service import MFAService

    # Rate limiting
    client_ip = get_client_ip(request)
    await auth_limiter.check("login", client_ip)

    user_repo = UserRepository(db)
    user = await user_repo.get_by_email(form_data.username)

    if not user:
        await emit_audit(
            db,
            "auth.login.failed",
            outcome="failure",
            details={"email": form_data.username, "reason": "user_not_found"},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.hashed_password:
        await emit_audit(
            db,
            "auth.login.failed",
            resource_type="user",
            resource_id=user.id,
            outcome="failure",
            details={"email": user.email, "reason": "no_password"},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account does not have password authentication enabled",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not verify_password(form_data.password, user.hashed_password):
        await emit_audit(
            db,
            "auth.login.failed",
            resource_type="user",
            resource_id=user.id,
            outcome="failure",
            details={"email": user.email, "reason": "wrong_password"},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        await emit_audit(
            db,
            "auth.login.failed",
            resource_type="user",
            resource_id=user.id,
            outcome="failure",
            details={"email": user.email, "reason": "inactive"},
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is inactive",
        )

    # Check MFA status. MFA is required for password login when the global
    # BIFROST_MFA_ENABLED setting is true (default). When it's disabled (dev
    # environments), password login skips MFA setup and verification entirely.
    settings = get_settings()
    mfa_service = MFAService(db)
    mfa_status = await mfa_service.get_mfa_status(user)

    if not settings.mfa_enabled:
        # MFA disabled globally — issue tokens directly.
        return await _generate_login_tokens(user, db, response)

    if not user.mfa_enabled or not mfa_status["enrolled_methods"]:
        # User has no MFA enrolled - require setup
        mfa_token = create_mfa_token(str(user.id), purpose="mfa_setup")

        logger.info(
            f"MFA setup required for user: {user.email}",
            extra={"user_id": str(user.id)}
        )

        return LoginResponse(
            mfa_setup_required=True,
            mfa_token=mfa_token,
            expires_in=300,
        )

    # User has MFA - check for trusted device
    if request:
        user_agent = request.headers.get("user-agent", "")
        fingerprint = MFAService.generate_device_fingerprint(user_agent)
        client_ip = request.client.host if request.client else None

        if await mfa_service.is_device_trusted(user.id, fingerprint, client_ip):
            # Trusted device - skip MFA verification
            logger.info(
                f"Trusted device login for user: {user.email}",
                extra={"user_id": str(user.id)}
            )
            return await _generate_login_tokens(user, db, response)

    # MFA verification required
    mfa_token = create_mfa_token(str(user.id), purpose="mfa_verify")

    logger.info(
        f"MFA verification required for user: {user.email}",
        extra={"user_id": str(user.id)}
    )

    return LoginResponse(
        mfa_required=True,
        mfa_token=mfa_token,
        available_methods=mfa_status["enrolled_methods"],
        expires_in=300,
    )


class MFASetupTokenRequest(BaseModel):
    """Request with MFA token for initial setup."""
    mfa_token: str


class MFASetupResponse(BaseModel):
    """MFA setup response with secret."""
    secret: str
    qr_code_uri: str
    provisioning_uri: str
    issuer: str
    account_name: str
    is_existing: bool = False


class MFAEnrollVerifyRequest(BaseModel):
    """Request to verify MFA during initial enrollment."""
    mfa_token: str
    code: str


class MFASetupRequest(BaseModel):
    """Optional request body for MFA setup."""
    force_new: bool = False


class MFAEnrollVerifyResponse(BaseModel):
    """Response after completing MFA enrollment."""
    success: bool
    recovery_codes: list[str]
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


@router.post("/mfa/setup", response_model=MFASetupResponse)
async def mfa_initial_setup(
    setup_request: MFASetupRequest | None = None,
    db: DbSession = None,
    request: Request = None,
) -> MFASetupResponse:
    """
    Initialize MFA enrollment during first-time setup.

    This endpoint is for users who just logged in with password for the first time
    and need to enroll in MFA. Requires an mfa_token with purpose "mfa_setup"
    in the Authorization header.

    By default, returns an existing pending setup if valid (to prevent QR invalidation
    on page refresh). Set force_new=true to generate a new secret.

    Returns:
        MFA setup data including secret and QR code URI

    Raises:
        HTTPException: If MFA token is invalid or expired
    """
    from src.services.mfa_service import MFAService

    # Get mfa_token from Authorization header
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header",
        )

    mfa_token = auth_header.replace("Bearer ", "")

    # Validate MFA token with purpose "mfa_setup"
    payload = decode_mfa_token(mfa_token, "mfa_setup")
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired MFA setup token",
        )

    user_id = UUID(payload["sub"])

    # Get user from database
    user_repo = UserRepository(db)
    user = await user_repo.get_by_id(user_id)

    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
        )

    force_new = setup_request.force_new if setup_request else False

    mfa_service = MFAService(db)
    setup_data = await mfa_service.setup_totp(user, force_new=force_new)
    await db.commit()

    logger.info(
        f"MFA setup initiated for user: {user.email} (force_new={force_new})",
        extra={"user_id": str(user.id)}
    )

    return MFASetupResponse(**setup_data)


@router.post("/mfa/verify", response_model=MFAEnrollVerifyResponse)
async def mfa_initial_verify(
    response: Response,
    db: DbSession = None,
    request: Request = None,
) -> MFAEnrollVerifyResponse:
    """
    Verify MFA code to complete initial enrollment.

    This endpoint is for users completing their first MFA setup after password login.
    Requires an mfa_token with purpose "mfa_setup" in the Authorization header.

    On success:
    - Activates the MFA method
    - Generates recovery codes (shown only once!)
    - Returns access tokens for auto-login

    Returns:
        Success status, recovery codes, and access tokens

    Raises:
        HTTPException: If MFA token is invalid or code verification fails
    """
    from src.services.mfa_service import MFAService

    logger.info("MFA initial verify endpoint called")

    # Get mfa_token from Authorization header
    auth_header = request.headers.get("Authorization", "")
    logger.info(f"Auth header present: {bool(auth_header)}, starts with Bearer: {auth_header.startswith('Bearer ')}")

    if not auth_header.startswith("Bearer "):
        logger.warning("Missing or invalid Authorization header")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header",
        )

    mfa_token = auth_header.replace("Bearer ", "")
    logger.info(f"MFA token length: {len(mfa_token)}")

    # Validate MFA token with purpose "mfa_setup"
    payload = decode_mfa_token(mfa_token, "mfa_setup")
    logger.info(f"MFA token decode result: {payload is not None}")
    if not payload:
        logger.warning("Invalid or expired MFA setup token")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired MFA setup token",
        )

    user_id = UUID(payload["sub"])

    # Get code from request body
    body = await request.json()
    code = body.get("code", "")

    if not code or len(code) != 6:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid MFA code format",
        )

    # Get user from database
    user_repo = UserRepository(db)
    user = await user_repo.get_by_id(user_id)

    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
        )

    mfa_service = MFAService(db)

    try:
        recovery_codes = await mfa_service.verify_totp_enrollment(user, code)
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
        "is_external": await resolve_external_claim(db, user),
        "org_id": str(user.organization_id) if user.organization_id else None,
        "roles": roles,
    }

    access_token = create_access_token(data=token_data)
    refresh_token_str, jti = create_refresh_token(data={"sub": str(user.id)})

    # Store JTI in Redis for revocation support
    await store_refresh_token_jti(str(user.id), jti)

    # Set cookies for browser clients
    set_auth_cookies(response, access_token, refresh_token_str)

    return MFAEnrollVerifyResponse(
        success=True,
        recovery_codes=recovery_codes,
        access_token=access_token,
        refresh_token=refresh_token_str,
    )


@router.post("/mfa/login", response_model=LoginResponse)
async def verify_mfa_login(
    response: Response,
    mfa_request: MFAVerifyRequest,
    request: Request = None,
    db: DbSession = None,
) -> LoginResponse:
    """
    Complete MFA verification during login to get access tokens.

    This is used when an existing user with MFA logs in and needs to verify their code.
    For initial MFA enrollment verification, use POST /auth/mfa/verify.

    Rate limited: 5 requests per minute per IP address.

    Args:
        mfa_request: MFA verification request with token, code, and trust options
        request: FastAPI request object
        db: Database session

    Returns:
        LoginResponse with access and refresh tokens

    Raises:
        HTTPException: If MFA token is invalid or code verification fails
    """
    from src.services.mfa_service import MFAService

    # Rate limiting (stricter for MFA to prevent brute force)
    client_ip = get_client_ip(request)
    await mfa_limiter.check("mfa_verify", client_ip)

    # Validate MFA token
    payload = decode_mfa_token(mfa_request.mfa_token, "mfa_verify")
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired MFA token",
        )

    user_id = UUID(payload["sub"])

    # Get user from database
    user_repo = UserRepository(db)
    user = await user_repo.get_by_id(user_id)

    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
        )

    mfa_service = MFAService(db)

    # Check if code is a recovery code (longer format)
    code = mfa_request.code.replace("-", "").upper()
    is_recovery = len(code) == 8 and not code.isdigit()

    if is_recovery:
        # Verify recovery code
        client_ip = request.client.host if request and request.client else None
        if not await mfa_service.verify_recovery_code(user.id, mfa_request.code, client_ip):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid recovery code",
            )
    else:
        # Verify TOTP code
        if not await mfa_service.verify_totp_code(user.id, mfa_request.code):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid MFA code",
            )

    # Optionally trust this device
    if mfa_request.trust_device and request:
        user_agent = request.headers.get("user-agent", "")
        fingerprint = MFAService.generate_device_fingerprint(user_agent)
        client_ip = request.client.host if request.client else None

        await mfa_service.create_trusted_device(
            user_id=user.id,
            fingerprint=fingerprint,
            device_name=mfa_request.device_name,
            ip_address=client_ip,
        )

    await db.commit()

    logger.info(
        f"MFA verification successful for user: {user.email}",
        extra={"user_id": str(user.id)}
    )

    return await _generate_login_tokens(user, db, response)


async def _generate_login_tokens(user, db, response: Response | None = None) -> LoginResponse:
    """
    Generate login response with access and refresh tokens.

    Sets HttpOnly cookies for browser clients when response is provided.
    Also returns tokens in response body for service-to-service auth.

    Args:
        user: User model
        db: Database session
        response: FastAPI Response object (optional, for setting cookies)

    Returns:
        LoginResponse with tokens
    """
    # Update last login (use naive datetime for DB compatibility)
    user.last_login = datetime.now(timezone.utc)
    await db.commit()

    # Get user roles from database
    db_roles = await get_user_roles(db, user.id)
    roles = ["authenticated", *db_roles]

    # Build JWT claims with user info
    token_data = {
        "sub": str(user.id),
        "email": user.email,
        "name": user.name or user.email.split("@")[0],
        "is_superuser": user.is_superuser,
        "is_external": await resolve_external_claim(db, user),
        "org_id": str(user.organization_id) if user.organization_id else None,
        "roles": roles,
    }

    # Generate tokens
    access_token = create_access_token(data=token_data)
    refresh_token_str, jti = create_refresh_token(data={"sub": str(user.id)})

    # Store JTI in Redis for revocation support
    await store_refresh_token_jti(str(user.id), jti)

    # Set cookies for browser clients
    if response:
        set_auth_cookies(response, access_token, refresh_token_str)

    logger.info(
        f"User logged in: {user.email}",
        extra={
            "user_id": str(user.id),
                "is_superuser": user.is_superuser,
            "org_id": str(user.organization_id) if user.organization_id else None,
        }
    )

    # Emit with an actor_override built from the freshly-authenticated user,
    # since the middleware couldn't populate the contextvar (no token yet).
    base_actor = current_actor()
    login_actor = ActorContext(
        user_id=user.id,
        organization_id=user.organization_id,
        email=user.email,
        name=user.name,
        ip_address=base_actor.ip_address if base_actor else None,
        user_agent=base_actor.user_agent if base_actor else None,
        source="http",
    )
    await emit_audit(
        db,
        "auth.login.success",
        resource_type="user",
        resource_id=user.id,
        details={"email": user.email, "is_superuser": user.is_superuser},
        actor_override=login_actor,
    )

    return LoginResponse(
        access_token=access_token,
        refresh_token=refresh_token_str,
    )


@router.post("/refresh", response_model=Token)
async def refresh_token(
    request: Request,
    response: Response,
    token_data: TokenRefresh | None = None,
    db: DbSession = None,
) -> Token:
    """
    Refresh access token using refresh token with rotation.

    Implements refresh token rotation: the old token is invalidated
    and a new one is issued. This limits the window for token theft.

    Fetches fresh user data and roles from database to ensure
    the new token has up-to-date claims.

    The refresh token can be provided in two ways:
    1. Request body (API clients): {"refresh_token": "..."}
    2. HttpOnly cookie (browser clients): Automatically sent

    Rate limited: 10 requests per minute per IP address.

    Args:
        request: FastAPI request object
        token_data: Optional refresh token in body (API clients)
        db: Database session

    Returns:
        New access and refresh tokens with updated claims

    Raises:
        HTTPException: If refresh token is invalid or revoked
    """
    # Rate limiting
    client_ip = get_client_ip(request)
    await auth_limiter.check("refresh", client_ip)

    # Get refresh token from body (API clients) or cookie (browser clients)
    refresh_token_value = None
    if token_data and token_data.refresh_token:
        refresh_token_value = token_data.refresh_token
    else:
        refresh_token_value = request.cookies.get("refresh_token")

    if not refresh_token_value:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Decode with type validation
    payload = decode_token(refresh_token_value, expected_type="refresh")

    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = payload.get("sub")
    jti = payload.get("jti")

    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Validate JTI exists and revoke it (single use - rotation)
    if not jti:
        # Legacy token without JTI - reject
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token format",
            headers={"WWW-Authenticate": "Bearer"},
        )

    jti_valid = await validate_and_revoke_refresh_token_jti(user_id, jti)
    if not jti_valid:
        # JTI not found - token was already used or revoked
        logger.warning(f"Refresh token reuse attempt for user {log_safe(user_id)}, JTI {log_safe(jti)}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has been revoked or already used",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Verify user still exists and is active
    user_repo = UserRepository(db)
    user = await user_repo.get_by_id(UUID(user_id))

    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Get fresh user roles from database
    db_roles = await get_user_roles(db, user.id)
    roles = ["authenticated", *db_roles]

    # Build JWT claims with fresh user info
    new_token_data = {
        "sub": str(user.id),
        "email": user.email,
        "name": user.name or user.email.split("@")[0],
        "is_superuser": user.is_superuser,
        "is_external": await resolve_external_claim(db, user),
        "org_id": str(user.organization_id) if user.organization_id else None,
        "roles": roles,
    }

    # Generate new tokens with rotation
    access_token = create_access_token(data=new_token_data)
    new_refresh_token, new_jti = create_refresh_token(data={"sub": str(user.id)})

    # Store new JTI in Redis
    await store_refresh_token_jti(str(user.id), new_jti)

    # Set cookies for browser clients
    set_auth_cookies(response, access_token, new_refresh_token)

    return Token(
        access_token=access_token,
        refresh_token=new_refresh_token,
    )


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(
    current_user: CurrentActiveUser,
) -> UserResponse:
    """
    Get current authenticated user information.

    Returns user info including type, organization, and roles from the JWT token.

    Args:
        current_user: Current authenticated user (from JWT)

    Returns:
        User information with roles
    """
    return UserResponse(
        id=str(current_user.user_id),
        email=current_user.email,
        name=current_user.name,
        is_active=current_user.is_active,
        is_superuser=current_user.is_superuser,
        is_verified=current_user.is_verified,
        organization_id=str(current_user.organization_id) if current_user.organization_id else None,
        roles=current_user.roles,
    )


class LogoutResponse(BaseModel):
    """Logout response model."""
    message: str = "Logged out successfully"


class RevokeAllResponse(BaseModel):
    """Revoke all sessions response model."""
    message: str
    sessions_revoked: int


class LogoutRequest(BaseModel):
    """Logout request with optional refresh token."""
    refresh_token: str | None = None


@router.post("/logout", response_model=LogoutResponse)
async def logout(
    request: Request,
    response: Response,
    current_user: CurrentActiveUser,
    db: DbSession,
    body: LogoutRequest | None = None,
) -> LogoutResponse:
    """
    Logout current user and revoke refresh token.

    Clears authentication cookies and revokes the current refresh token.
    The access token will remain valid until expiry (30 minutes max).

    For API clients using Bearer auth, the refresh_token should be passed
    in the request body. For browser clients using cookies, the token is
    read from the refresh_token cookie automatically.

    Args:
        request: FastAPI request (to get refresh token cookie)
        response: FastAPI response (to clear cookies)
        current_user: Current authenticated user
        body: Optional request body containing refresh_token for API clients

    Returns:
        Logout confirmation
    """
    # Get refresh token from body (API clients) or cookie (browser clients)
    refresh_token = None
    if body and body.refresh_token:
        refresh_token = body.refresh_token
    else:
        refresh_token = request.cookies.get("refresh_token")

    if refresh_token:
        payload = decode_token(refresh_token, expected_type="refresh")
        if payload and payload.get("jti"):
            await validate_and_revoke_refresh_token_jti(
                str(current_user.user_id),
                payload["jti"]
            )

    # Clear cookies
    clear_auth_cookies(response)

    logger.info(f"User logged out: {current_user.email}")
    await emit_audit(
        db,
        "auth.logout",
        resource_type="user",
        resource_id=current_user.user_id,
        details={"email": current_user.email},
    )

    return LogoutResponse()


@router.post("/revoke-all", response_model=RevokeAllResponse)
async def revoke_all_sessions(
    response: Response,
    current_user: CurrentActiveUser,
) -> RevokeAllResponse:
    """
    Revoke all refresh tokens for the current user.

    This logs out all sessions across all devices. Useful when:
    - User suspects account compromise
    - User wants to sign out everywhere
    - Password has been changed

    Note: Access tokens will remain valid until expiry (30 minutes max).
    For immediate revocation, consider also changing the password.

    Args:
        response: FastAPI response (to clear current cookies)
        current_user: Current authenticated user

    Returns:
        Number of sessions revoked
    """
    count = await revoke_all_user_refresh_tokens(str(current_user.user_id))

    # Clear cookies for current session
    clear_auth_cookies(response)

    logger.info(
        f"User revoked all sessions: {current_user.email}",
        extra={"sessions_revoked": count}
    )

    return RevokeAllResponse(
        message="All sessions have been revoked",
        sessions_revoked=count,
    )


class AdminRevokeRequest(BaseModel):
    """Admin revocation request."""
    user_id: str


@router.post("/admin/revoke-user", response_model=RevokeAllResponse)
async def admin_revoke_user_sessions(
    revoke_data: AdminRevokeRequest,
    current_user: CurrentActiveUser,
    db: DbSession,
) -> RevokeAllResponse:
    """
    Revoke all refresh tokens for a specific user (admin only).

    Allows platform administrators to forcibly log out a user from all
    devices. Useful for security incidents or account compromises.

    Requires platform admin (superuser) privileges.

    Args:
        revoke_data: Target user ID to revoke
        current_user: Current authenticated user (must be admin)
        db: Database session

    Returns:
        Number of sessions revoked

    Raises:
        HTTPException: If not admin or user not found
    """
    # Require platform admin
    if not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Platform admin privileges required",
        )

    # Verify target user exists
    user_repo = UserRepository(db)
    target_user = await user_repo.get_by_id(revoke_data.user_id)
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Revoke all sessions for target user
    count = await revoke_all_user_refresh_tokens(revoke_data.user_id)

    logger.warning(
        f"Admin revoked all sessions for user: {log_safe(target_user.email)}",
        extra={
            "admin_user": current_user.email,
            "target_user": log_safe(target_user.email),
            "target_user_id": log_safe(revoke_data.user_id),
            "sessions_revoked": count,
        }
    )

    return RevokeAllResponse(
        message=f"All sessions have been revoked for user {target_user.email}",
        sessions_revoked=count,
    )


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register_user(
    request: Request,
    user_data: UserCreate,
    db: DbSession = None,
) -> UserResponse:
    """
    Register a new user with auto-provisioning.

    Handles three scenarios:
    1. Pre-created user (is_registered=False): Admin created the user, user completes registration
    2. First user in system: Becomes PlatformAdmin
    3. New user with matching domain: Auto-joined to organization

    Note: In production, this should be restricted or require admin approval.

    Rate limited: 10 requests per minute per IP address.

    Args:
        request: FastAPI request object
        user_data: User registration data
        db: Database session

    Returns:
        Created user information with roles

    Raises:
        HTTPException: If email already registered or provisioning fails
    """
    # Rate limiting
    client_ip = get_client_ip(request)
    await auth_limiter.check("register", client_ip)

    settings = get_settings()
    user_repo = UserRepository(db)

    # Check if this is first-time setup (no users exist)
    has_users = await user_repo.has_any_users()

    # Allow registration if:
    # 1. First user (system bootstrap) - always allowed
    # 2. Development or testing mode - always allowed
    # 3. Production with existing users - disabled (use admin invite flow)
    if has_users and not (settings.is_development or settings.is_testing):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User registration is disabled. Contact an administrator for access.",
        )

    # Check if email already exists
    existing_user = await user_repo.get_by_email(user_data.email)

    if existing_user:
        # Check if this is a pre-created user completing registration
        if not existing_user.is_registered:
            # Pre-created user - complete their registration
            existing_user.hashed_password = get_password_hash(user_data.password)
            existing_user.is_registered = True
            if user_data.name:
                existing_user.name = user_data.name
            await db.commit()

            logger.info(
                f"Pre-created user completed registration: {existing_user.email}",
                extra={
                    "user_id": str(existing_user.id),
                    "is_superuser": existing_user.is_superuser,
                }
            )

            return UserResponse(
                id=str(existing_user.id),
                email=existing_user.email,
                name=existing_user.name or "",
                is_active=existing_user.is_active,
                is_superuser=existing_user.is_superuser,
                is_verified=existing_user.is_verified,
                organization_id=str(existing_user.organization_id) if existing_user.organization_id else None,
                roles=["authenticated"],
            )
        else:
            # Already registered user
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already registered",
            )

    # New user - use provisioning logic to determine user type and org assignment
    try:
        result = await ensure_user_provisioned(
            db=db,
            email=user_data.email,
            name=user_data.name,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    # Set password and mark as registered
    user = result.user
    user.hashed_password = get_password_hash(user_data.password)
    user.is_registered = True
    await db.commit()

    logger.info(
        f"User registered: {user.email}",
        extra={
            "user_id": str(user.id),
            "is_superuser": user.is_superuser,
            "was_created": result.was_created,
        }
    )

    return UserResponse(
        id=str(user.id),
        email=user.email,
        name=user.name or "",
        is_active=user.is_active,
        is_superuser=user.is_superuser,
        is_verified=user.is_verified,
        organization_id=str(user.organization_id) if user.organization_id else None,
        roles=result.roles,
    )


@router.get("/status", response_model=AuthStatusResponse)
async def get_auth_status(db: DbSession = None) -> AuthStatusResponse:
    """
    Get authentication system status for the login page.

    Public endpoint that returns everything needed to render the login UI:
    - Whether this is first-time setup (no users exist)
    - Available authentication methods (password, OAuth providers)
    - MFA requirements

    Returns:
        AuthStatusResponse with complete login page configuration
    """
    from src.services.oauth_config_service import OAuthConfigService

    settings = get_settings()
    user_repo = UserRepository(db)
    has_users = await user_repo.has_any_users()

    # Get available OAuth providers from database config
    oauth_config_service = OAuthConfigService(db)
    available_providers = await oauth_config_service.get_available_providers()

    # Get OIDC display name if configured
    oidc_display_name = "SSO"
    if "oidc" in available_providers:
        oidc_config = await oauth_config_service.get_provider_config("oidc")
        if oidc_config and oidc_config.display_name:
            oidc_display_name = oidc_config.display_name

    # Provider display info
    provider_info_map = {
        "microsoft": {"display_name": "Microsoft", "icon": "microsoft"},
        "google": {"display_name": "Google", "icon": "google"},
        "oidc": {"display_name": oidc_display_name, "icon": "key"},
    }

    oauth_providers = [
        OAuthProviderInfo(
            name=name,
            display_name=provider_info_map.get(name, {}).get("display_name", name.title()),
            icon=provider_info_map.get(name, {}).get("icon"),
        )
        for name in available_providers
    ]

    return AuthStatusResponse(
        needs_setup=not has_users,
        password_login_enabled=True,  # Always enabled for now
        mfa_required_for_password=settings.mfa_enabled,
        oauth_providers=oauth_providers,
    )


# NOTE: /auth/oauth/login endpoint was removed for security reasons.
# It accepted unverified email claims which allowed account takeover.
# Use /auth/oauth/callback flow instead which properly validates OAuth tokens.


# =============================================================================
# Device Authorization Flow (RFC 8628)
# =============================================================================


def _generate_user_code() -> str:
    """
    Generate a human-readable user code for device authorization.

    Format: XXXX-YYYY (8 uppercase letters/digits, no ambiguous chars)
    Avoids: O/0, I/1, S/5, Z/2 for better readability
    """
    import random
    import string

    # Remove ambiguous characters
    chars = string.ascii_uppercase.replace("O", "").replace("I", "").replace("S", "").replace("Z", "")
    chars += string.digits.replace("0", "").replace("1", "").replace("5", "").replace("2", "")

    # Generate 8 character code
    code_chars = [random.choice(chars) for _ in range(8)]

    # Format as XXXX-YYYY
    return f"{''.join(code_chars[:4])}-{''.join(code_chars[4:])}"


@router.post("/device/code", response_model=DeviceCodeResponse)
async def request_device_code(
    request: Request,
) -> DeviceCodeResponse:
    """
    Request a device authorization code for CLI login.

    This is the first step in the OAuth 2.0 Device Authorization Flow (RFC 8628).
    The CLI calls this endpoint to get a device_code and user_code.

    The user_code is displayed to the user who must enter it at the verification_url.
    The device_code is used by the CLI to poll for authorization.

    Rate limited: 10 requests per minute per IP address.

    Returns:
        DeviceCodeResponse with device_code, user_code, verification URL, and polling interval
    """
    import json
    import uuid

    # Rate limiting
    client_ip = get_client_ip(request)
    await auth_limiter.check("device_code", client_ip)

    r = await get_shared_redis()

    # Generate codes
    device_code = str(uuid.uuid4())
    user_code = _generate_user_code()

    # Store device code in Redis with pending status
    device_data = {
        "user_code": user_code,
        "status": "pending",
        "user_id": None,
    }

    await r.setex(
        device_code_key(device_code),
        TTL_DEVICE_CODE,
        json.dumps(device_data)
    )

    # Create reverse index from user_code to device_code for authorization lookup
    await r.setex(
        device_user_code_index_key(user_code),
        TTL_DEVICE_CODE,
        device_code
    )

    logger.info(
        f"Device authorization code requested: {user_code}",
        extra={"device_code": device_code, "user_code": user_code}
    )

    # Build verification URL
    # For now, use a simple path - frontend will construct full URL
    verification_url = "/device"

    return DeviceCodeResponse(
        device_code=device_code,
        user_code=user_code,
        verification_url=verification_url,
        expires_in=TTL_DEVICE_CODE,
        interval=5,
    )


@router.post("/device/token")
async def exchange_device_token(
    request: Request,
    token_request: DeviceTokenRequest,
    db: DbSession = None,
) -> DeviceTokenResponse | DeviceTokenErrorResponse:
    """
    Exchange device code for access token (polling endpoint).

    This is the second step in the Device Authorization Flow. The CLI polls this
    endpoint with the device_code until the user authorizes the request.

    Possible responses:
    - 200 + DeviceTokenResponse: Authorization granted, tokens issued
    - 200 + DeviceTokenErrorResponse: Still pending or denied/expired

    Rate limited: 20 requests per minute per IP address (allows polling).

    Args:
        request: FastAPI request
        token_request: Device token request with device_code
        db: Database session

    Returns:
        DeviceTokenResponse with tokens or DeviceTokenErrorResponse with error
    """
    import json

    # Rate limiting (more lenient for polling)
    client_ip = get_client_ip(request)
    await auth_limiter.check("device_token", client_ip)

    r = await get_shared_redis()

    # Get device code data from Redis
    device_data_json = await r.get(device_code_key(token_request.device_code))

    if not device_data_json:
        logger.warning(
            "Device token request with expired/invalid device_code",
            extra={"device_code": log_safe(token_request.device_code[:8]) + "..."}
        )
        return DeviceTokenErrorResponse(error="expired_token")

    device_data = json.loads(device_data_json)
    status = device_data.get("status")

    if status == "pending":
        return DeviceTokenErrorResponse(error="authorization_pending")

    if status == "denied":
        # Delete the device code
        await r.delete(device_code_key(token_request.device_code))
        await r.delete(device_user_code_index_key(device_data["user_code"]))

        logger.info(
            "Device authorization denied",
            extra={"device_code": log_safe(token_request.device_code[:8]) + "...", "user_code": log_safe(device_data["user_code"])}
        )
        return DeviceTokenErrorResponse(error="access_denied")

    if status == "authorized":
        user_id = device_data.get("user_id")

        if not user_id:
            logger.error(
                "Device code authorized but missing user_id",
                extra={"device_code": log_safe(token_request.device_code[:8]) + "..."}
            )
            return DeviceTokenErrorResponse(error="expired_token")

        # Get user from database
        user_repo = UserRepository(db)
        user = await user_repo.get_by_id(UUID(user_id))

        if not user or not user.is_active:
            logger.warning(
                "Device token request for inactive/deleted user",
                extra={"user_id": user_id}
            )
            return DeviceTokenErrorResponse(error="access_denied")

        # Generate tokens using existing helper
        login_response = await _generate_login_tokens(user, db, response=None)

        # Delete device code (one-time use)
        await r.delete(device_code_key(token_request.device_code))
        await r.delete(device_user_code_index_key(device_data["user_code"]))

        logger.info(
            f"Device authorization completed for user: {log_safe(user.email)}",
            extra={
                "user_id": str(user.id),
                "device_code": log_safe(token_request.device_code[:8]) + "...",
                "user_code": log_safe(device_data["user_code"]),
            }
        )

        return DeviceTokenResponse(
            access_token=login_response.access_token,
            refresh_token=login_response.refresh_token,
            token_type=login_response.token_type,
            expires_in=1800,  # 30 minutes
        )

    # Unknown status
    logger.error(
        f"Unknown device code status: {log_safe(status)}",
        extra={"device_code": log_safe(token_request.device_code[:8]) + "..."}
    )
    return DeviceTokenErrorResponse(error="expired_token")


class DeviceAuthorizeResponse(BaseModel):
    """Response for device authorization."""
    success: bool


# =============================================================================
# First-Time Setup with Passkey (Passwordless Registration)
# =============================================================================


@router.post("/setup/passkey/options", response_model=SetupPasskeyOptionsResponse)
async def setup_passkey_options(
    request: Request,
    setup_request: SetupPasskeyOptionsRequest,
    db: DbSession = None,
) -> SetupPasskeyOptionsResponse:
    """
    Start passwordless passkey registration for first-time platform setup.

    This endpoint is ONLY available when no users exist in the system.
    It allows the first user to register with a passkey instead of a password.

    Flow:
    1. Client calls this endpoint with email/name
    2. Server returns WebAuthn options + registration token
    3. Client performs WebAuthn ceremony (Face ID, Touch ID, etc.)
    4. Client calls /auth/setup/passkey/verify with credential

    Rate limited: 10 requests per minute per IP address.

    Args:
        request: FastAPI request object
        setup_request: Email and optional name for the new account
        db: Database session

    Returns:
        SetupPasskeyOptionsResponse with registration token and WebAuthn options

    Raises:
        HTTPException: If users already exist or email is invalid
    """
    from src.services.passkey_service import PasskeyService

    # Rate limiting
    client_ip = get_client_ip(request)
    await auth_limiter.check("register", client_ip)

    passkey_service = PasskeyService(db)

    try:
        registration_token, options = await passkey_service.generate_setup_registration_options(
            email=setup_request.email,
            name=setup_request.name,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    logger.info(
        f"Passkey setup initiated for first-time registration: {log_safe(setup_request.email)}",
    )

    return SetupPasskeyOptionsResponse(
        registration_token=registration_token,
        options=options,
        expires_in=300,
    )


@router.post("/setup/passkey/verify", response_model=SetupPasskeyVerifyResponse)
async def setup_passkey_verify(
    request: Request,
    response: Response,
    verify_request: SetupPasskeyVerifyRequest,
    db: DbSession = None,
) -> SetupPasskeyVerifyResponse:
    """
    Complete passwordless passkey registration for first-time platform setup.

    This endpoint verifies the WebAuthn credential and creates the user + passkey
    atomically. On success, returns JWT tokens so the user is immediately logged in.

    Rate limited: 10 requests per minute per IP address.

    Args:
        request: FastAPI request object
        response: FastAPI response object (for setting cookies)
        verify_request: Registration token and WebAuthn credential
        db: Database session

    Returns:
        SetupPasskeyVerifyResponse with user info and JWT tokens

    Raises:
        HTTPException: If token is invalid/expired or verification fails
    """
    import json

    from src.services.passkey_service import PasskeyService

    # Rate limiting
    client_ip = get_client_ip(request)
    await auth_limiter.check("register", client_ip)

    passkey_service = PasskeyService(db)

    try:
        user, passkey = await passkey_service.verify_setup_registration(
            registration_token=verify_request.registration_token,
            credential_json=json.dumps(verify_request.credential),
            device_name=verify_request.device_name,
        )
        await db.commit()
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    logger.info(
        f"Passkey setup completed for first-time registration: {user.email}",
        extra={"user_id": str(user.id), "passkey_id": str(passkey.id)},
    )

    # Generate tokens for immediate login
    db_roles = await get_user_roles(db, user.id)
    roles = ["authenticated", *db_roles]

    token_data = {
        "sub": str(user.id),
        "email": user.email,
        "name": user.name or user.email.split("@")[0],
        "is_superuser": user.is_superuser,
        "is_external": await resolve_external_claim(db, user),
        "org_id": str(user.organization_id) if user.organization_id else None,
        "roles": roles,
    }

    access_token = create_access_token(data=token_data)
    refresh_token_str, jti = create_refresh_token(data={"sub": str(user.id)})

    # Store JTI in Redis for revocation support
    await store_refresh_token_jti(str(user.id), jti)

    # Set cookies for browser clients
    set_auth_cookies(response, access_token, refresh_token_str)

    return SetupPasskeyVerifyResponse(
        user_id=str(user.id),
        email=user.email,
        access_token=access_token,
        refresh_token=refresh_token_str,
    )


@router.post("/device/authorize", response_model=DeviceAuthorizeResponse)
async def authorize_device(
    authorize_request: DeviceAuthorizeRequest,
    current_user: CurrentActiveUser,
) -> DeviceAuthorizeResponse:
    """
    Authorize a device code (user approves CLI access).

    This endpoint is called by the web UI when a logged-in user enters the
    user_code and approves CLI access. The CLI will then receive tokens on
    its next poll to /device/token.

    Requires authentication - user must be logged in to authorize a device.

    Args:
        authorize_request: Device authorization request with user_code
        current_user: Current authenticated user (from JWT)

    Returns:
        Success response

    Raises:
        HTTPException: If user_code is invalid or expired
    """
    import json

    r = await get_shared_redis()

    # Look up device_code from user_code (reverse index)
    device_code = await r.get(device_user_code_index_key(authorize_request.user_code))

    if not device_code:
        logger.warning(
            f"Device authorization attempt with invalid user_code: {log_safe(authorize_request.user_code)}",
            extra={"user_id": str(current_user.user_id)}
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invalid or expired user code",
        )

    device_code_str = device_code.decode() if isinstance(device_code, bytes) else device_code

    # Get device data
    device_data_json = await r.get(device_code_key(device_code_str))

    if not device_data_json:
        logger.warning(
            f"Device code missing for user_code: {log_safe(authorize_request.user_code)}",
            extra={"user_id": str(current_user.user_id)}
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invalid or expired user code",
        )

    # Update device code status to authorized
    device_data = json.loads(device_data_json)
    device_data["status"] = "authorized"
    device_data["user_id"] = str(current_user.user_id)

    # Get remaining TTL and preserve it
    ttl = await r.ttl(device_code_key(device_code_str))
    if ttl > 0:
        await r.setex(
            device_code_key(device_code_str),
            ttl,
            json.dumps(device_data)
        )
    else:
        # Fallback if TTL query fails
        await r.setex(
            device_code_key(device_code_str),
            TTL_DEVICE_CODE,
            json.dumps(device_data)
        )

    logger.info(
        f"Device authorized by user: {current_user.email}",
        extra={
            "user_id": str(current_user.user_id),
            "user_code": log_safe(authorize_request.user_code),
            "device_code": log_safe(device_code_str[:8]) + "...",
        }
    )

    return DeviceAuthorizeResponse(success=True)


# =============================================================================
# Invite Registration
# =============================================================================

from src.models.contracts.user_invites import RegisterFromInviteRequest  # noqa: E402
from src.models.contracts.users import UserPublic  # noqa: E402
from src.services.user_invite_service import (  # noqa: E402
    InviteConsumeError,
    UserInviteService,
)


@router.post("/register-from-invite", response_model=UserPublic)
async def register_from_invite(
    request: RegisterFromInviteRequest,
    db: DbSession,
) -> UserPublic:
    """Consume a single-use invite token and complete user registration.

    This endpoint is intentionally unauthenticated — the token IS the
    credential. On success the user is marked is_registered=True and (if a
    password was supplied) their hashed_password is set.
    """
    svc = UserInviteService(db)
    try:
        registered = await svc.consume(token=request.token, password=request.password)
    except InviteConsumeError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if request.name and not registered.name:
        registered.name = request.name
        await db.flush()

    public = UserPublic.model_validate(registered)
    public.invite_status = "active"
    return public


@router.post("/register-from-invite/passkey/options", response_model=SetupPasskeyOptionsResponse)
async def register_from_invite_passkey_options(
    request: InvitePasskeyOptionsRequest,
    db: DbSession,
) -> SetupPasskeyOptionsResponse:
    """Start passkey registration for a user holding a valid invite token."""
    from src.services.passkey_service import PasskeyService

    invite_svc = UserInviteService(db)
    try:
        _, invited_user = await invite_svc.get_valid_invite_user(token=request.token)
        options = await PasskeyService(db).generate_registration_options(invited_user.id)
    except (InviteConsumeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))

    return SetupPasskeyOptionsResponse(
        registration_token=request.token,
        options=options,
        expires_in=300,
    )


@router.post("/register-from-invite/passkey/verify", response_model=SetupPasskeyVerifyResponse)
async def register_from_invite_passkey_verify(
    request: InvitePasskeyVerifyRequest,
    response: Response,
    db: DbSession,
) -> SetupPasskeyVerifyResponse:
    """Complete invite registration by verifying a passkey and logging the user in."""
    import json

    from src.services.passkey_service import PasskeyService

    invite_svc = UserInviteService(db)
    try:
        _, invited_user = await invite_svc.get_valid_invite_user(token=request.token)
        passkey_service = PasskeyService(db)
        await passkey_service.verify_registration(
            user_id=invited_user.id,
            credential_json=json.dumps(request.credential),
            device_name=request.device_name,
        )
        registered = await invite_svc.consume(token=request.token)
        await db.commit()
    except (InviteConsumeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))

    login = await _generate_login_tokens(registered, db, response)
    return SetupPasskeyVerifyResponse(
        user_id=str(registered.id),
        email=registered.email,
        access_token=login.access_token or "",
        refresh_token=login.refresh_token or "",
    )
