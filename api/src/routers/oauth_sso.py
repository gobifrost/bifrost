"""
OAuth SSO Router

Provides endpoints for OAuth/SSO authentication:
- Get available providers
- Initialize OAuth flow
- Handle OAuth callback
- Link/unlink OAuth accounts
"""

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from pydantic import BaseModel

from src.core.cache import get_shared_redis
from src.core.cache.keys import (
    oauth_state_key,
    refresh_token_jti_key,
    TTL_OAUTH_STATE,
    TTL_REFRESH_TOKEN,
)
from src.core.log_safety import log_safe
from src.config import get_settings
from src.core.auth import CurrentActiveUser, get_current_user_from_db
from src.core.database import DbSession
from src.core.security import create_access_token, create_refresh_token, generate_csrf_token
from src.services.oauth_sso import OAuthError, OAuthService
from src.services.user_provisioning import ensure_user_provisioned, get_user_roles

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth/oauth", tags=["oauth"])


# =============================================================================
# Cookie Helpers
# =============================================================================


def _set_oauth_auth_cookies(response: Response, access_token: str, refresh_token: str) -> None:
    """
    Set HttpOnly authentication cookies for OAuth login.

    This mirrors the cookie-setting logic from auth.py but is kept here to avoid
    circular imports. Browser clients get XSS protection from HttpOnly cookies.

    Args:
        response: FastAPI response object
        access_token: JWT access token
        refresh_token: JWT refresh token
    """
    settings = get_settings()
    # Only use secure cookies in production - dev and testing use HTTP
    secure = settings.is_production

    # Access token cookie (short-lived)
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=30 * 60,  # 30 minutes
        path="/",
    )

    # Refresh token cookie (long-lived)
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=7 * 24 * 60 * 60,  # 7 days
        path="/",
    )

    # CSRF token cookie (JS readable for X-CSRF-Token header)
    csrf_token = generate_csrf_token()
    response.set_cookie(
        key="csrf_token",
        value=csrf_token,
        httponly=False,  # JS needs to read this
        secure=secure,
        samesite="strict",
        max_age=30 * 60,  # Match access token
        path="/",
    )


# =============================================================================
# Request/Response Models
# =============================================================================

class OAuthProviderInfo(BaseModel):
    """OAuth provider information."""
    name: str
    display_name: str
    icon: str | None = None


class OAuthProvidersResponse(BaseModel):
    """Available OAuth providers."""
    providers: list[OAuthProviderInfo]


class OAuthInitResponse(BaseModel):
    """OAuth initialization response."""
    authorization_url: str
    state: str


class OAuthCallbackRequest(BaseModel):
    """OAuth callback request (for when frontend handles callback)."""
    provider: str
    code: str
    state: str
    # Note: code_verifier is no longer sent by frontend - it's stored server-side in Redis


class OAuthTokenResponse(BaseModel):
    """OAuth login token response."""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class LinkedAccountResponse(BaseModel):
    """Linked OAuth account info."""
    provider: str
    provider_email: str
    linked_at: str
    last_used_at: str | None = None


class LinkedAccountsResponse(BaseModel):
    """List of linked OAuth accounts."""
    accounts: list[LinkedAccountResponse]


# Provider display names and icons
PROVIDER_INFO = {
    "microsoft": {"display_name": "Microsoft", "icon": "microsoft"},
    "google": {"display_name": "Google", "icon": "google"},
    "oidc": {"display_name": "SSO", "icon": "key"},
}


# =============================================================================
# Endpoints
# =============================================================================

@router.get("/providers", response_model=OAuthProvidersResponse)
async def get_oauth_providers(db: DbSession) -> OAuthProvidersResponse:
    """
    Get available OAuth providers.

    Returns list of configured OAuth providers that can be used for login.

    Returns:
        List of available OAuth providers with display info
    """
    oauth_service = OAuthService(db)
    available = await oauth_service.get_available_providers()

    providers = []
    for name in available:
        info = PROVIDER_INFO.get(name, {"display_name": name.title(), "icon": None})
        providers.append(OAuthProviderInfo(
            name=name,
            display_name=info.get("display_name") or name.title(),
            icon=info.get("icon"),
        ))

    return OAuthProvidersResponse(providers=providers)


@router.get("/init/{provider}", response_model=OAuthInitResponse)
async def init_oauth(
    provider: str,
    db: DbSession,
    redirect_uri: str = Query(..., description="Frontend callback URL"),
) -> OAuthInitResponse:
    """
    Initialize OAuth login flow.

    Generates authorization URL with PKCE challenge for secure OAuth flow.
    The PKCE code_verifier is stored server-side in Redis, bound to the state.

    Args:
        provider: OAuth provider name (microsoft, google, oidc)
        redirect_uri: Frontend callback URL

    Returns:
        Authorization URL and state for CSRF protection

    Security:
        - State is used for CSRF protection
        - PKCE verifier is stored server-side (never sent to client)
        - State can only be used once and expires after 10 minutes
    """
    oauth_service = OAuthService(db)

    try:
        # Generate PKCE values and state
        code_verifier = OAuthService.generate_code_verifier()
        state = OAuthService.generate_state()

        authorization_url = await oauth_service.get_authorization_url(
            provider=provider,
            redirect_uri=redirect_uri,
            state=state,
            code_verifier=code_verifier,
        )

        # Store state -> {code_verifier, redirect_uri} binding in Redis (server-side PKCE)
        # This prevents the client from ever seeing the verifier and ensures we use
        # the same redirect_uri in the callback that was used in the init
        r = await get_shared_redis()
        state_data = json.dumps({
            "code_verifier": code_verifier,
            "redirect_uri": redirect_uri,
        })
        await r.setex(
            oauth_state_key(state),
            TTL_OAUTH_STATE,
            state_data,
        )

        logger.info(
            f"OAuth flow initiated for provider: {log_safe(provider)}",
            extra={"provider": log_safe(provider), "state": log_safe(state[:8]) + "..."}
        )

        return OAuthInitResponse(
            authorization_url=authorization_url,
            state=state,
        )

    except OAuthError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.post("/callback", response_model=OAuthTokenResponse)
async def oauth_callback(
    callback_data: OAuthCallbackRequest,
    request: Request,
    response: Response,
    db: DbSession,
) -> OAuthTokenResponse:
    """
    Complete OAuth login flow.

    Called by frontend after receiving callback from OAuth provider.
    Validates state, retrieves PKCE verifier from Redis, exchanges code for tokens.

    OAuth users bypass MFA - the OAuth provider is trusted for authentication.
    (This can be changed via oauth_require_mfa config option.)

    Sets HttpOnly cookies for browser clients in addition to returning tokens in
    the response body. This provides defense-in-depth: browser clients get XSS
    protection from HttpOnly cookies, while API clients can use the response body.

    Args:
        callback_data: OAuth callback data with code and state
        response: FastAPI response for setting cookies

    Returns:
        JWT access and refresh tokens (also sets cookies)

    Raises:
        HTTPException: If OAuth flow fails or user cannot be provisioned

    Security:
        - State is validated against Redis (CSRF protection)
        - PKCE verifier is retrieved from Redis (never sent by client)
        - State is single-use (deleted after retrieval)
    """
    oauth_service = OAuthService(db)

    # Validate state and retrieve PKCE verifier + redirect_uri from Redis
    r = await get_shared_redis()
    state_key = oauth_state_key(callback_data.state)
    state_data_raw = await r.get(state_key)

    if not state_data_raw:
        logger.warning(
            "OAuth callback with invalid or expired state",
            extra={"state": log_safe(callback_data.state[:8]) + "..." if callback_data.state else "none"}
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired OAuth state",
        )

    # Delete state immediately (single-use)
    await r.delete(state_key)

    # Parse state data (contains code_verifier and redirect_uri)
    if isinstance(state_data_raw, bytes):
        state_data_raw = state_data_raw.decode("utf-8")

    try:
        state_data = json.loads(state_data_raw)
        code_verifier = state_data["code_verifier"]
        redirect_uri = state_data["redirect_uri"]
    except (json.JSONDecodeError, KeyError) as e:
        logger.error(f"Invalid OAuth state data format: {log_safe(e)}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid OAuth state data",
        )

    try:

        # Exchange code for tokens using server-side verifier
        tokens = await oauth_service.exchange_code_for_tokens(
            provider=callback_data.provider,
            code=callback_data.code,
            redirect_uri=redirect_uri,
            code_verifier=code_verifier,
        )

        # Get user info from provider
        user_info = await oauth_service.get_user_info(
            provider=callback_data.provider,
            tokens=tokens,
        )

        if not user_info.email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="OAuth provider did not return email address",
            )

    except OAuthError as e:
        logger.error(f"OAuth callback error: {log_safe(e)}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    # Check if user already has an OAuth account linked
    existing_user = await oauth_service.find_user_by_oauth(
        provider=user_info.provider,
        provider_user_id=user_info.provider_user_id,
    )

    if existing_user:
        # Existing OAuth user - update last login
        user = existing_user
        user.last_login = datetime.now(timezone.utc)
        user.is_registered = True
        user.is_verified = True

        # Update OAuth account
        await oauth_service.link_oauth_account(user, user_info, tokens)
        await db.commit()

    else:
        # New user or linking new OAuth account - provision user
        try:
            result = await ensure_user_provisioned(
                db=db,
                email=user_info.email,
                name=user_info.name,
            )
            user = result.user

            # Link OAuth account to user
            await oauth_service.link_oauth_account(user, user_info, tokens)

            user.last_login = datetime.now(timezone.utc)
            user.is_registered = True
            user.is_verified = True
            await db.commit()

        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=str(e),
            )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is inactive",
        )

    # Get user roles
    db_roles = await get_user_roles(db, user.id)
    roles = ["authenticated", *db_roles]

    # Build JWT claims
    token_data = {
        "sub": str(user.id),
        "email": user.email,
        "name": user.name or user.email.split("@")[0],
        "is_superuser": user.is_superuser,
        "org_id": str(user.organization_id) if user.organization_id else None,
        "roles": roles,
        "oauth_provider": callback_data.provider,  # Mark as OAuth login
    }

    # Generate tokens
    access_token = create_access_token(data=token_data)
    refresh_token, jti = create_refresh_token(data={"sub": str(user.id)})

    # Store JTI in Redis for revocation support
    r = await get_shared_redis()
    await r.setex(refresh_token_jti_key(str(user.id), jti), TTL_REFRESH_TOKEN, "1")

    logger.info(
        f"OAuth login successful: {log_safe(user.email)}",
        extra={
            "user_id": str(user.id),
            "provider": log_safe(callback_data.provider),
            "oauth_user_id": log_safe(user_info.provider_user_id),
        }
    )

    # Set HttpOnly cookies for browser clients (XSS protection)
    # Also sets CSRF token cookie for subsequent requests
    _set_oauth_auth_cookies(response, access_token, refresh_token)

    return OAuthTokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
    )


# =============================================================================
# Account Linking (for authenticated users)
# =============================================================================

@router.get("/accounts", response_model=LinkedAccountsResponse)
async def get_linked_accounts(
    current_user: CurrentActiveUser,
    db: DbSession,
) -> LinkedAccountsResponse:
    """
    Get OAuth accounts linked to current user.

    Returns:
        List of linked OAuth accounts
    """
    oauth_service = OAuthService(db)
    accounts = await oauth_service.get_user_oauth_accounts(current_user.user_id)

    return LinkedAccountsResponse(
        accounts=[
            LinkedAccountResponse(
                provider=acc.provider_id,
                provider_email=acc.email,
                linked_at=acc.created_at.isoformat(),
                last_used_at=acc.last_login.isoformat() if acc.last_login else None,
            )
            for acc in accounts
        ]
    )


@router.delete("/accounts/{provider}")
async def unlink_oauth_account(
    provider: str,
    current_user: CurrentActiveUser,
    db: DbSession,
) -> dict:
    """
    Unlink an OAuth account from current user.

    User must have password set or another OAuth account to unlink.

    Args:
        provider: OAuth provider to unlink

    Returns:
        Success message

    Raises:
        HTTPException: If account not found or user would be locked out
    """
    user = await get_current_user_from_db(current_user, db)
    oauth_service = OAuthService(db)

    # Check if user would be locked out
    accounts = await oauth_service.get_user_oauth_accounts(user.id)
    has_password = user.hashed_password is not None
    other_oauth = [a for a in accounts if a.provider_id != provider]

    if not has_password and not other_oauth:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot unlink: You need a password or another OAuth account to log in",
        )

    # Unlink account
    unlinked = await oauth_service.unlink_oauth_account(user.id, provider)
    if not unlinked:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No {provider} account linked",
        )

    await db.commit()

    logger.info(
        f"OAuth account unlinked: {log_safe(provider)}",
        extra={"user_id": str(user.id), "provider": log_safe(provider)}
    )

    return {"message": f"{provider.title()} account unlinked"}
