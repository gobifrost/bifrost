"""
OAuth Connections Router

Manages OAuth connections for workflow integrations.
This is separate from oauth_sso.py which handles user authentication.
"""

import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import (
    CreateOAuthConnectionRequest,
    EntityIdPickerCandidate,
    UpdateOAuthConnectionRequest,
    OAuthConnectionDetail,
    OAuthCredentialsResponse,
    OAuthCallbackRequest,
    OAuthCallbackResponse,
    OAuthFlowType,
    OAuthStatus,
)
from src.core.auth import Context, CurrentSuperuser
from src.core.log_safety import log_safe
from src.models import OAuthProvider, OAuthToken
from src.models.orm.integrations import IntegrationMapping
from src.services.oauth_entity_id import extract_entity_id
from src.services.oauth_provider import (
    build_token_refresh_context,
    get_url_resolution_defaults,
    refresh_oauth_token_http,
    resolve_url_template,
)
from src.services.oauth_state import consume_nonce, decode_state, OAuthStateError

# Import cache invalidation
try:
    from src.core.cache import invalidate_oauth, invalidate_oauth_token
    CACHE_INVALIDATION_AVAILABLE = True
except ImportError:
    CACHE_INVALIDATION_AVAILABLE = False
    invalidate_oauth = None  # type: ignore
    invalidate_oauth_token = None  # type: ignore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/oauth", tags=["OAuth Connections"])


# =============================================================================
# Response Models
# =============================================================================


class AuthorizeResponse(BaseModel):
    """Response for initiating OAuth authorization."""
    authorization_url: str = Field(..., description="URL to redirect user for authorization")
    state: str = Field(..., description="State parameter for CSRF protection")
    message: str = Field(default="Redirect user to authorization_url")


class RefreshTokenResponse(BaseModel):
    """Response for token refresh."""
    success: bool
    message: str
    expires_at: str | None = None


class RefreshJobRun(BaseModel):
    """Details of a single refresh job run."""
    status: str = Field(default="completed")
    start_time: datetime | None = None
    end_time: datetime | None = None
    connections_checked: int = 0
    refreshed_successfully: int = 0
    refresh_failed: int = 0
    needs_refresh: int = 0
    total_connections: int = 0
    error: str | None = None
    errors: list[str] = Field(default_factory=list)


class RefreshJobStatusResponse(BaseModel):
    """Response for refresh job status."""
    enabled: bool = Field(default=True)
    last_run: RefreshJobRun | None = None
    next_run: datetime | None = None


class RefreshAllResponse(BaseModel):
    """Response for triggering refresh of all tokens."""
    triggered: bool
    message: str
    connections_queued: int = 0
    refreshed_successfully: int = 0
    refresh_failed: int = 0


# =============================================================================
# Repository
# =============================================================================


class OAuthConnectionRepository:
    """PostgreSQL-based OAuth connection repository."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_connection(
        self,
        connection_name: str,
        org_id: UUID | None = None,
    ) -> OAuthProvider | None:
        """Get a specific OAuth connection.

        Supports lookup by:
        - integration_id (UUID string) - preferred
        - provider_name (string) - legacy fallback
        """
        # Try to parse as UUID first (integration_id lookup)
        try:
            integration_id = UUID(connection_name)
            query = select(OAuthProvider).where(
                OAuthProvider.integration_id == integration_id
            )
        except ValueError:
            # Not a valid UUID, fall back to provider_name lookup
            query = select(OAuthProvider).where(
                OAuthProvider.provider_name == connection_name
            )

        # Cascade: org-specific OR global (NULL), matching OrgScopedRepository pattern
        if org_id is not None:
            query = query.where(
                or_(
                    OAuthProvider.organization_id == org_id,
                    OAuthProvider.organization_id.is_(None),
                )
            )
        else:
            query = query.where(OAuthProvider.organization_id.is_(None))

        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def create_connection(
        self,
        request: CreateOAuthConnectionRequest,
        org_id: UUID | None,
        created_by: str,
    ) -> OAuthProvider:
        """Create a new OAuth connection."""
        # Encrypt client secret if provided
        encrypted_secret = b""
        if request.client_secret:
            from src.core.security import encrypt_secret
            encrypted_secret = encrypt_secret(request.client_secret).encode()

        provider = OAuthProvider(
            organization_id=org_id,
            provider_name=request.connection_name,
            display_name=request.name or request.connection_name,
            description=request.description,
            oauth_flow_type=request.oauth_flow_type,
            client_id=request.client_id,
            encrypted_client_secret=encrypted_secret,
            authorization_url=request.authorization_url,
            token_url=request.token_url,
            scopes=request.scopes.split(",") if request.scopes else [],
            status="not_connected",
            created_by=created_by,
        )

        self.db.add(provider)
        await self.db.flush()
        await self.db.refresh(provider)

        return provider

    async def update_connection(
        self,
        connection_name: str,
        org_id: UUID | None,
        request: UpdateOAuthConnectionRequest,
    ) -> OAuthProvider | None:
        """Update an existing OAuth connection."""
        provider = await self.get_connection(connection_name, org_id)
        if not provider:
            return None

        if request.name is not None:
            provider.display_name = request.name
        if request.oauth_flow_type is not None:
            provider.oauth_flow_type = request.oauth_flow_type
        if request.client_id is not None:
            provider.client_id = request.client_id
        if request.client_secret is not None:
            # Encrypt the new client secret
            from src.core.security import encrypt_secret
            provider.encrypted_client_secret = encrypt_secret(request.client_secret).encode()
        if request.authorization_url is not None:
            provider.authorization_url = request.authorization_url
        if request.token_url is not None:
            provider.token_url = request.token_url
        if request.scopes is not None:
            provider.scopes = request.scopes
        if request.audience is not None:
            provider.audience = request.audience

        provider.updated_at = datetime.now(timezone.utc)

        await self.db.flush()
        await self.db.refresh(provider)

        return provider

    async def delete_connection(
        self,
        connection_name: str,
        org_id: UUID | None,
    ) -> bool:
        """Delete an OAuth connection."""
        provider = await self.get_connection(connection_name, org_id)
        if not provider:
            return False

        # Delete associated tokens first
        token_query = select(OAuthToken).where(OAuthToken.provider_id == provider.id)
        token_result = await self.db.execute(token_query)
        for token in token_result.scalars().all():
            await self.db.delete(token)

        await self.db.delete(provider)
        await self.db.flush()

        return True

    async def update_status(
        self,
        connection_name: str,
        org_id: UUID | None,
        status: str,
        status_message: str | None = None,
    ) -> bool:
        """Update connection status."""
        provider = await self.get_connection(connection_name, org_id)
        if not provider:
            return False

        provider.status = status
        provider.status_message = status_message
        provider.updated_at = datetime.now(timezone.utc)

        await self.db.flush()
        return True

    async def get_token(
        self,
        connection_name: str,
        org_id: UUID | None,
    ) -> OAuthToken | None:
        """Get the current token for a connection scoped to org_id.

        Tokens are stored per (provider, organization_id) — passing org_id=None
        returns the integration-level fallback token; passing a UUID returns
        the org-scoped token for that org. The two are NOT cascaded here:
        callers that want fallback semantics (mapping-first, then global)
        must use src.services.oauth_provider.get_token_for_org.
        """
        provider = await self.get_connection(connection_name, org_id)
        if not provider:
            return None

        query = (
            select(OAuthToken)
            .where(OAuthToken.provider_id == provider.id)
        )
        if org_id is not None:
            query = query.where(OAuthToken.organization_id == org_id)
        else:
            query = query.where(OAuthToken.organization_id.is_(None))
        query = query.order_by(OAuthToken.created_at.desc())

        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def store_token(
        self,
        connection_name: str,
        org_id: UUID | None,
        access_token: str,
        refresh_token: str | None,
        expires_at: datetime,
        scopes: list[str] | None = None,
    ) -> OAuthToken | None:
        """Store a new token for a connection."""
        from src.core.security import encrypt_secret

        provider = await self.get_connection(connection_name, org_id)
        if not provider:
            return None

        # Encrypt tokens for storage
        encrypted_access = encrypt_secret(access_token).encode()
        encrypted_refresh = encrypt_secret(refresh_token).encode() if refresh_token else None

        # Check for existing token and update, or create new
        existing_token = await self.get_token(connection_name, org_id)

        now = datetime.now(timezone.utc)
        if existing_token:
            existing_token.encrypted_access_token = encrypted_access
            existing_token.encrypted_refresh_token = encrypted_refresh
            existing_token.expires_at = expires_at
            existing_token.scopes = scopes or []
            existing_token.status = "completed"
            existing_token.status_message = None
            existing_token.last_refresh_at = now
            token = existing_token
        else:
            token = OAuthToken(
                organization_id=org_id,
                provider_id=provider.id,
                encrypted_access_token=encrypted_access,
                encrypted_refresh_token=encrypted_refresh,
                expires_at=expires_at,
                scopes=scopes or [],
                status="completed",
                status_message=None,
                last_refresh_at=now,
            )
            self.db.add(token)

        # Update provider status (integration-level fallback health view)
        provider.status = "completed"
        provider.status_message = "Token acquired successfully"
        provider.last_token_refresh = now
        provider.updated_at = now

        await self.db.flush()
        await self.db.refresh(token)

        return token

    async def _to_detail(self, provider: OAuthProvider) -> OAuthConnectionDetail:
        """Convert to detail model."""
        # Get latest token expiry - query directly to avoid lazy load issues
        expires_at = None
        token = await self.get_token(provider.provider_name, provider.organization_id)
        if token:
            expires_at = token.expires_at

        # Cast string values to literal types for Pydantic
        oauth_flow_type: OAuthFlowType = provider.oauth_flow_type  # type: ignore[assignment]
        status: OAuthStatus = provider.status or "not_connected"  # type: ignore[assignment]

        # Convert scopes list to space-separated string
        scopes_str = " ".join(provider.scopes) if provider.scopes else ""

        return OAuthConnectionDetail(
            connection_name=provider.provider_name,
            name=provider.display_name,
            provider=provider.provider_name,
            description=provider.description,
            oauth_flow_type=oauth_flow_type,
            client_id=provider.client_id,
            authorization_url=provider.authorization_url,
            token_url=provider.token_url or "",
            scopes=scopes_str,
            audience=provider.audience,
            status=status,
            status_message=provider.status_message,
            integration_id=str(provider.integration_id) if provider.integration_id else None,
            expires_at=expires_at,
            last_refresh_at=provider.last_token_refresh,
            last_test_at=None,
            created_at=provider.created_at,
            created_by=provider.created_by or "",
            updated_at=provider.updated_at,
        )


# =============================================================================
# HTTP Endpoints
# =============================================================================


@router.get(
    "/connections/{connection_name}",
    response_model=OAuthConnectionDetail,
    summary="Get OAuth connection",
    description="Get a specific OAuth connection (Platform admin only)",
)
async def get_connection(
    connection_name: str,
    ctx: Context,
    user: CurrentSuperuser,
) -> OAuthConnectionDetail:
    """Get a specific OAuth connection."""
    repo = OAuthConnectionRepository(ctx.db)
    org_id = ctx.org_id
    provider = await repo.get_connection(connection_name, org_id)

    if not provider:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"OAuth connection '{connection_name}' not found",
        )

    return await repo._to_detail(provider)


@router.post(
    "/connections",
    response_model=OAuthConnectionDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Create OAuth connection",
    description="Create a new OAuth connection (Platform admin only)",
)
async def create_connection(
    request: CreateOAuthConnectionRequest,
    ctx: Context,
    user: CurrentSuperuser,
) -> OAuthConnectionDetail:
    """Create a new OAuth connection."""
    from uuid import UUID
    from src.models.orm.integrations import Integration

    repo = OAuthConnectionRepository(ctx.db)
    org_id = ctx.org_id

    # Look up integration by ID to verify it exists
    integration_id = UUID(request.integration_id)
    query = select(Integration).where(Integration.id == integration_id)
    result = await ctx.db.execute(query)
    integration = result.scalar_one_or_none()

    if not integration:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Integration '{request.integration_id}' not found",
        )

    # Use the integration name as the provider/connection name
    provider_name = integration.name

    # Check for existing connection (global check — top-level connections are org=NULL)
    existing = await repo.get_connection(provider_name)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"OAuth connection for integration '{integration.name}' already exists",
        )

    # Create OAuth provider with integration_id
    encrypted_secret = b""
    if request.client_secret:
        from src.core.security import encrypt_secret
        encrypted_secret = encrypt_secret(request.client_secret).encode()

    provider = OAuthProvider(
        # organization_id omitted → NULL → global connection
        provider_name=provider_name,
        display_name=integration.name,
        description=request.description,
        oauth_flow_type=request.oauth_flow_type,
        client_id=request.client_id,
        encrypted_client_secret=encrypted_secret,
        authorization_url=request.authorization_url,
        token_url=request.token_url,
        scopes=request.scopes.split(",") if request.scopes else [],
        audience=request.audience,
        status="not_connected",
        created_by=ctx.user.email,
        integration_id=integration_id,
    )

    ctx.db.add(provider)
    await ctx.db.flush()
    await ctx.db.refresh(provider)

    logger.info(f"Created OAuth connection for integration: {integration.name}")

    # Invalidate cache
    if CACHE_INVALIDATION_AVAILABLE and invalidate_oauth:
        org_id_str = str(org_id) if org_id else None
        await invalidate_oauth(org_id_str, provider_name)

    return await repo._to_detail(provider)


@router.put(
    "/connections/{connection_name}",
    response_model=OAuthConnectionDetail,
    summary="Update OAuth connection",
    description="Update an existing OAuth connection (Platform admin only)",
)
async def update_connection(
    connection_name: str,
    request: UpdateOAuthConnectionRequest,
    ctx: Context,
    user: CurrentSuperuser,
) -> OAuthConnectionDetail:
    """Update an OAuth connection."""
    repo = OAuthConnectionRepository(ctx.db)
    org_id = ctx.org_id

    provider = await repo.update_connection(connection_name, org_id, request)

    if not provider:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"OAuth connection '{connection_name}' not found",
        )

    logger.info(f"Updated OAuth connection: {log_safe(connection_name)}")

    # Invalidate cache
    if CACHE_INVALIDATION_AVAILABLE and invalidate_oauth:
        org_id_str = str(org_id) if org_id else None
        await invalidate_oauth(org_id_str, connection_name)

    return await repo._to_detail(provider)


@router.delete(
    "/connections/{connection_name}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete OAuth connection",
    description="Delete an OAuth connection (Platform admin only)",
)
async def delete_connection(
    connection_name: str,
    ctx: Context,
    user: CurrentSuperuser,
) -> None:
    """Delete an OAuth connection."""
    repo = OAuthConnectionRepository(ctx.db)
    org_id = ctx.org_id

    deleted = await repo.delete_connection(connection_name, org_id)

    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"OAuth connection '{connection_name}' not found",
        )

    logger.info(f"Deleted OAuth connection: {log_safe(connection_name)}")

    # Invalidate cache
    if CACHE_INVALIDATION_AVAILABLE and invalidate_oauth:
        org_id_str = str(org_id) if org_id else None
        await invalidate_oauth(org_id_str, connection_name)


@router.post(
    "/connections/{connection_name}/authorize",
    response_model=AuthorizeResponse,
    summary="Initiate OAuth authorization",
    description="Get authorization URL for user to complete OAuth flow",
)
async def authorize_connection(
    connection_name: str,
    ctx: Context,
    user: CurrentSuperuser,
    redirect_uri: str = Query(..., description="Frontend callback URL for OAuth redirect"),
) -> AuthorizeResponse:
    """Initiate OAuth authorization flow."""
    repo = OAuthConnectionRepository(ctx.db)
    org_id = ctx.org_id

    provider = await repo.get_connection(connection_name, org_id)

    if not provider:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"OAuth connection '{connection_name}' not found",
        )

    if not provider.authorization_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This connection uses client_credentials flow and doesn't require user authorization",
        )

    # Resolve URL template placeholders (e.g., {entity_id} -> actual tenant ID)
    defaults = await get_url_resolution_defaults(ctx.db, provider)
    resolved_authorization_url = resolve_url_template(
        url=provider.authorization_url,
        defaults=defaults,
    )

    # Generate state for CSRF protection
    state = secrets.token_urlsafe(32)

    # Build authorization URL
    # Convert scopes list to space-separated string (OAuth standard format)
    scopes_str = " ".join(provider.scopes) if provider.scopes else ""
    params = {
        "client_id": provider.client_id,
        "response_type": "code",
        "state": state,
        "scope": scopes_str,
        "redirect_uri": redirect_uri,
    }

    authorization_url = f"{resolved_authorization_url}?{urlencode(params)}"

    # Update status to waiting
    await repo.update_status(
        connection_name=connection_name,
        org_id=org_id,
        status="waiting_callback",
        status_message="Waiting for user to complete authorization",
    )

    return AuthorizeResponse(
        authorization_url=authorization_url,
        state=state,
        message="Redirect user to authorization_url to complete OAuth flow",
    )


@router.post(
    "/connections/{connection_name}/cancel",
    response_model=OAuthConnectionDetail,
    summary="Cancel OAuth authorization",
    description="Cancel pending OAuth authorization and reset to not_connected",
)
async def cancel_authorization(
    connection_name: str,
    ctx: Context,
    user: CurrentSuperuser,
) -> OAuthConnectionDetail:
    """Cancel OAuth authorization and reset status."""
    repo = OAuthConnectionRepository(ctx.db)
    org_id = ctx.org_id

    provider = await repo.get_connection(connection_name, org_id)

    if not provider:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"OAuth connection '{connection_name}' not found",
        )

    await repo.update_status(
        connection_name=connection_name,
        org_id=org_id,
        status="not_connected",
        status_message="Authorization cancelled",
    )

    # Refresh provider to get updated data
    await ctx.db.refresh(provider)
    return await repo._to_detail(provider)


@router.post(
    "/connections/{connection_name}/refresh",
    response_model=RefreshTokenResponse,
    summary="Refresh OAuth token",
    description="Refresh the OAuth access token. For client_credentials flow, fetches a new token. For authorization_code flow, uses the refresh token.",
)
async def refresh_token(
    connection_name: str,
    ctx: Context,
    user: CurrentSuperuser,
) -> RefreshTokenResponse:
    """Refresh OAuth token.

    For client_credentials flow: Fetches a new token using client credentials.
    For authorization_code flow: Uses the stored refresh token.

    The HTTP refresh itself is delegated to the shared primitive
    :func:`src.services.oauth_provider.refresh_oauth_token_http`; this handler
    only owns the provider lookup, context build, persistence, and cache
    invalidation.
    """
    repo = OAuthConnectionRepository(ctx.db)
    org_id = ctx.org_id

    provider = await repo.get_connection(connection_name, org_id)

    if not provider:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"OAuth connection '{connection_name}' not found",
        )

    if not provider.token_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No token URL configured for this connection",
        )

    # For authorization_code flows we need the stored token up front.
    stored_token: OAuthToken | None = None
    if provider.oauth_flow_type != "client_credentials":
        stored_token = await repo.get_token(connection_name, org_id)
        if not stored_token or not stored_token.encrypted_refresh_token:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No refresh token available for this connection",
            )

    if provider.oauth_flow_type == "client_credentials" and not provider.encrypted_client_secret:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Client secret is required for client_credentials flow",
        )

    # Build context and delegate. The {entity_id} fallback chain lives in
    # build_token_refresh_context so this handler, the SDK endpoint, and the
    # scheduler cannot drift.
    td = await build_token_refresh_context(
        db=ctx.db,
        provider=provider,
        token=stored_token,
        org_id=org_id,
    )
    outcome = await refresh_oauth_token_http(td)

    if not outcome["success"]:
        error_msg = outcome.get("error", "Refresh failed")
        provider.status = "failed"
        provider.status_message = error_msg
        await ctx.db.flush()
        return RefreshTokenResponse(
            success=False,
            message=error_msg,
            expires_at=None,
        )

    new_expires_at = outcome.get("expires_at")

    # Persistence differs per flow to preserve existing behavior:
    #   - client_credentials uses repo.store_token (may create the row)
    #   - authorization_code updates the existing row in place
    if provider.oauth_flow_type == "client_credentials":
        if not new_expires_at:
            # Default to 1 hour from now if no expiry provided
            new_expires_at = datetime.now(timezone.utc) + timedelta(hours=1)

        await repo.store_token(
            connection_name=connection_name,
            org_id=org_id,
            access_token=outcome["access_token"],
            refresh_token=None,  # client_credentials doesn't have refresh tokens
            expires_at=new_expires_at,
            scopes=provider.scopes,
        )

        logger.info(f"Client credentials token acquired for {log_safe(connection_name)}")

    else:
        # Update the token row we already loaded above.
        assert stored_token is not None  # narrowed by the guard above
        stored_token.encrypted_access_token = outcome["encrypted_access_token"]
        if outcome.get("encrypted_refresh_token"):
            stored_token.encrypted_refresh_token = outcome["encrypted_refresh_token"]
        stored_token.expires_at = new_expires_at

        provider.status = "completed"
        provider.status_message = None
        provider.last_token_refresh = datetime.now(timezone.utc)

        await ctx.db.flush()

        logger.info(f"Token refreshed successfully for {log_safe(connection_name)}")

    # Invalidate cache (token was updated)
    if CACHE_INVALIDATION_AVAILABLE and invalidate_oauth_token:
        org_id_str = str(org_id) if org_id else None
        await invalidate_oauth_token(org_id_str, connection_name)

    expires_at_str = new_expires_at.isoformat() if new_expires_at else None
    return RefreshTokenResponse(
        success=True,
        message="Token acquired successfully" if provider.oauth_flow_type == "client_credentials" else "Token refreshed successfully",
        expires_at=expires_at_str,
    )


async def _apply_callback_to_mapping(
    db: AsyncSession,
    mapping_id: UUID,
    token: OAuthToken,
    provider: OAuthProvider,
    callback_url_params: dict[str, str],
    token_response: dict[str, Any],
) -> str | None:
    """Link the freshly-stored token to the mapping and capture entity_id.

    Idempotent for the ``oauth_token_id`` link. Does NOT overwrite a non-empty
    ``mapping.entity_id`` — manual overrides win over auto-capture.

    Returns the captured entity_id value when extraction succeeded AND was
    actually written to the mapping. Returns None otherwise (no source set,
    extraction missed, mapping already had a value, or mapping not found).
    """
    mapping = await db.get(IntegrationMapping, mapping_id)
    if not mapping:
        return None  # silently skip — the connection still happened at the provider level

    mapping.oauth_token_id = token.id

    captured: str | None = None
    if not mapping.entity_id:
        extracted = extract_entity_id(
            provider.entity_id_source,
            callback_url_params=callback_url_params,
            token_response=token_response,
        )
        if extracted:
            mapping.entity_id = extracted
            captured = extracted

    await db.flush()
    return captured


@router.post(
    "/callback/{connection_name}",
    response_model=OAuthCallbackResponse,
    summary="OAuth callback",
    description="Handle OAuth callback and exchange code for tokens",
)
async def oauth_callback(
    connection_name: str,
    request: OAuthCallbackRequest,
    ctx: Context,
    user: CurrentSuperuser,
) -> OAuthCallbackResponse:
    """Handle OAuth callback and exchange authorization code for tokens."""
    from src.core.security import decrypt_secret
    from src.services.oauth_provider import OAuthProviderClient

    repo = OAuthConnectionRepository(ctx.db)
    # Use org_id from request body for org-specific token storage (None for global)
    org_id = UUID(request.organization_id) if request.organization_id else None

    # Per-mapping flows carry mapping_id in a signed state token. If present,
    # the resulting OAuthToken must be scoped to the MAPPING'S org — otherwise
    # store_token would upsert into the integration-level (organization_id IS NULL)
    # slot and stomp the global fallback, leaving every "per-mapping" connection
    # pointing at the same shared token row.
    mapping_id_from_state: UUID | None = None
    nonce_from_state: str | None = None
    if request.state:
        try:
            payload = decode_state(request.state)
            nonce_from_state = payload.get("nonce")
            mid = payload.get("mapping_id")
            if mid:
                mapping_id_from_state = UUID(mid)
                # Look up the mapping now so we can use its org for token storage.
                from src.models.orm import IntegrationMapping as _IM
                mapping_row = await ctx.db.get(_IM, mapping_id_from_state)
                if mapping_row and mapping_row.organization_id:
                    org_id = mapping_row.organization_id
        except (OAuthStateError, ValueError) as e:
            # Legacy integration-level flows use secrets.token_urlsafe() as state —
            # decoding is expected to fail. Fall through to global-token storage.
            logger.warning(f"OAuth state decode failed (treating as legacy flow): {e}")

    provider = await repo.get_connection(connection_name, org_id)

    if not provider:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"OAuth connection '{connection_name}' not found",
        )

    if not provider.token_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No token URL configured for this connection",
        )

    # Decrypt client secret
    client_secret = None
    if provider.encrypted_client_secret:
        try:
            client_secret = decrypt_secret(provider.encrypted_client_secret.decode())
        except Exception as e:
            logger.error(f"Failed to decrypt client secret: {e}")

    # Get redirect URI from request (must match what was used in authorization)
    if not request.redirect_uri:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="redirect_uri is required in callback request",
        )
    redirect_uri = request.redirect_uri

    # Resolve URL template placeholders (e.g., {entity_id} -> actual tenant ID)
    defaults = await get_url_resolution_defaults(ctx.db, provider)
    resolved_token_url = resolve_url_template(
        url=provider.token_url,
        defaults=defaults,
    )

    # Exchange authorization code for tokens
    oauth_client = OAuthProviderClient()
    success, result = await oauth_client.exchange_code_for_token(
        token_url=resolved_token_url,
        code=request.code,
        client_id=provider.client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        scopes=" ".join(provider.scopes) if provider.scopes else None,
        audience=provider.audience,
    )

    if not success:
        error_msg = result.get("error_description", result.get("error", "Token exchange failed"))
        await repo.update_status(
            connection_name=connection_name,
            org_id=org_id,
            status="failed",
            status_message=error_msg,
        )
        return OAuthCallbackResponse(
            success=False,
            message="Token exchange failed",
            status="failed",
            connection_name=connection_name,
            error_message=error_msg,
            warning_message=None,
        )

    # Extract tokens from result
    access_token = result.get("access_token")
    refresh_token = result.get("refresh_token")
    expires_at = result.get("expires_at")
    scope = result.get("scope", "")
    scopes = scope.split() if scope else list(provider.scopes or [])

    if not access_token:
        await repo.update_status(
            connection_name=connection_name,
            org_id=org_id,
            status="failed",
            status_message="No access token in response",
        )
        return OAuthCallbackResponse(
            success=False,
            message="Token exchange failed - no access token received",
            status="failed",
            connection_name=connection_name,
            error_message="No access token in response",
            warning_message=None,
        )

    if not expires_at:
        # Default to 1 hour from now if no expiry provided
        expires_at = datetime.now(timezone.utc) + timedelta(hours=1)

    # Store tokens
    await repo.store_token(
        connection_name=connection_name,
        org_id=org_id,
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=expires_at,
        scopes=scopes,
    )

    logger.info(f"OAuth callback completed for {log_safe(connection_name)}")

    # Per-mapping flow: link the freshly-stored (org-scoped) token to the mapping
    # and capture entity_id from the provider's configured source. State + org_id
    # were already decoded at the top of this handler — see mapping_id_from_state.
    captured_value: str | None = None
    if mapping_id_from_state is not None:
        # Single-use enforcement: reject replays of valid-signature state.
        if nonce_from_state and not await consume_nonce(nonce_from_state):
            logger.warning(
                "OAuth state nonce already consumed — possible replay, skipping mapping link"
            )
        else:
            stored = await repo.get_token(connection_name, org_id)
            if stored:
                captured_value = await _apply_callback_to_mapping(
                    db=ctx.db,
                    mapping_id=mapping_id_from_state,
                    token=stored,
                    provider=provider,
                    callback_url_params=request.callback_url_params or {},
                    token_response=result,
                )

    # Invalidate cache (token was stored)
    if CACHE_INVALIDATION_AVAILABLE and invalidate_oauth_token:
        await invalidate_oauth_token(None, connection_name)  # org_id is None in callback

    # Surface the picker when the admin needs to make (or correct) a choice:
    #   (a) entity_id_source is unset — first-time setup, any connect path
    #   (b) entity_id_source IS set, this was a per-mapping connect, but
    #       extraction returned nothing (the configured field wasn't in this
    #       response). Re-show candidates so admin can pick a different source.
    # When source is set AND extraction succeeded, we skip the picker.
    picker: list[EntityIdPickerCandidate] | None = None
    extraction_missed = (
        provider.entity_id_source is not None
        and mapping_id_from_state is not None
        and captured_value is None
    )
    if provider.entity_id_source is None or extraction_missed:
        from src.services.oauth_entity_id import enumerate_candidate_fields
        candidates = enumerate_candidate_fields(
            callback_url_params=request.callback_url_params or {},
            token_response=result,
        )
        if candidates:
            picker = [EntityIdPickerCandidate(**c) for c in candidates]
        if extraction_missed:
            src = provider.entity_id_source or {}
            logger.warning(
                f"entity_id auto-capture missed: source={src.get('type')}:{src.get('key')} "
                f"not found in callback artifacts for {log_safe(connection_name)}; "
                f"re-showing picker"
            )

    # Build response with optional warning
    warning_msg = None
    if not refresh_token:
        warning_msg = (
            "No refresh token received. The connection may require re-authorization "
            "when the access token expires."
        )

    captured_from: str | None = None
    if captured_value and provider.entity_id_source:
        src = provider.entity_id_source
        captured_from = f"{src.get('type')}:{src.get('key')}"

    return OAuthCallbackResponse(
        success=True,
        message="OAuth connection completed successfully",
        status="completed",
        connection_name=connection_name,
        warning_message=warning_msg,
        error_message=None,
        entity_id_picker=picker,
        triggering_mapping_id=mapping_id_from_state,
        captured_entity_id=captured_value,
        captured_entity_id_from=captured_from,
    )


@router.get(
    "/credentials/{connection_name}",
    response_model=OAuthCredentialsResponse,
    summary="Get OAuth credentials",
    description="Get OAuth credentials for use in workflows (Platform admin only)",
)
async def get_credentials(
    connection_name: str,
    ctx: Context,
    user: CurrentSuperuser,
) -> OAuthCredentialsResponse:
    """Get OAuth credentials for use in workflows."""
    from src.core.security import decrypt_secret
    from src.models import OAuthCredentialsModel

    repo = OAuthConnectionRepository(ctx.db)
    org_id = ctx.org_id

    provider = await repo.get_connection(connection_name, org_id)

    if not provider:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"OAuth connection '{connection_name}' not found",
        )

    token = await repo.get_token(connection_name, org_id)

    # If no token, return response with null credentials
    if not token:
        status_val: OAuthStatus = provider.status or "not_connected"  # type: ignore[assignment]
        return OAuthCredentialsResponse(
            connection_name=connection_name,
            credentials=None,
            status=status_val,
            integration_id=str(provider.integration_id) if provider.integration_id else None,
            expires_at=None,
        )

    # Decrypt tokens
    try:
        access_token = decrypt_secret(token.encrypted_access_token.decode())
        refresh_token = None
        if token.encrypted_refresh_token:
            refresh_token = decrypt_secret(token.encrypted_refresh_token.decode())
    except Exception as e:
        logger.error(f"Failed to decrypt token: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to decrypt token",
        )

    expires_at_str = token.expires_at.isoformat() if token.expires_at else None
    scopes = " ".join(token.scopes) if token.scopes else ""

    credentials = OAuthCredentialsModel(
        connection_name=connection_name,
        access_token=access_token,
        token_type="Bearer",
        expires_at=expires_at_str or "",
        refresh_token=refresh_token,
        scopes=scopes,
        integration_id=str(provider.integration_id) if provider.integration_id else None,
    )

    status_val: OAuthStatus = provider.status or "completed"  # type: ignore[assignment]
    return OAuthCredentialsResponse(
        connection_name=connection_name,
        credentials=credentials,
        status=status_val,
        integration_id=str(provider.integration_id) if provider.integration_id else None,
        expires_at=expires_at_str,
    )


@router.get(
    "/refresh_job_status",
    response_model=RefreshJobStatusResponse,
    summary="Get OAuth refresh job status",
    description="Get status of the background token refresh job",
)
async def get_refresh_job_status(
    ctx: Context,
    user: CurrentSuperuser,
) -> RefreshJobStatusResponse:
    """Get refresh job status from stored job history."""
    from src.models import SystemConfig

    # Query for the last job status
    query = select(SystemConfig).where(
        SystemConfig.category == "oauth",
        SystemConfig.key == "refresh_job_status",
        SystemConfig.organization_id.is_(None),  # Global system config
    )
    result = await ctx.db.execute(query)
    config = result.scalar_one_or_none()

    if not config or not config.value_json:
        return RefreshJobStatusResponse(
            enabled=True,
            last_run=None,
            next_run=None,
        )

    job_data = config.value_json

    # Build last_run from stored data
    last_run = None
    if job_data.get("start_time"):
        last_run = RefreshJobRun(
            status="completed" if not job_data.get("errors") else "completed_with_errors",
            start_time=datetime.fromisoformat(job_data["start_time"]) if job_data.get("start_time") else None,
            end_time=datetime.fromisoformat(job_data["end_time"]) if job_data.get("end_time") else None,
            connections_checked=job_data.get("total_connections", 0),
            refreshed_successfully=job_data.get("refreshed_successfully", 0),
            refresh_failed=job_data.get("refresh_failed", 0),
            needs_refresh=job_data.get("needs_refresh", 0),
            total_connections=job_data.get("total_connections", 0),
            errors=job_data.get("errors", []),
        )

    return RefreshJobStatusResponse(
        enabled=True,
        last_run=last_run,
        next_run=None,  # Could calculate from scheduler if needed
    )


@router.post(
    "/refresh_all",
    response_model=RefreshAllResponse,
    summary="Trigger refresh of all OAuth tokens",
    description="Manually trigger refresh of all OAuth tokens (Platform admin only)",
)
async def trigger_refresh_all(
    ctx: Context,
    user: CurrentSuperuser,
) -> RefreshAllResponse:
    """Trigger refresh of all OAuth tokens."""
    from src.jobs.schedulers.oauth_token_refresh import run_refresh_job
    from src.models import SystemConfig

    logger.info(f"User {ctx.user.email} manually triggering OAuth refresh job")

    # Run the refresh job (manual trigger refreshes all connections)
    results = await run_refresh_job(
        trigger_type="manual",
        trigger_user=ctx.user.email,
        refresh_threshold_minutes=None,  # Refresh all, not just expiring
    )

    # Store job status in SystemConfig for later retrieval
    query = select(SystemConfig).where(
        SystemConfig.category == "oauth",
        SystemConfig.key == "refresh_job_status",
        SystemConfig.organization_id.is_(None),
    )
    result = await ctx.db.execute(query)
    config = result.scalar_one_or_none()

    if config:
        config.value_json = results
        config.updated_at = datetime.now(timezone.utc)
    else:
        config = SystemConfig(
            category="oauth",
            key="refresh_job_status",
            value_json=results,
            organization_id=None,
        )
        ctx.db.add(config)

    await ctx.db.flush()

    return RefreshAllResponse(
        triggered=True,
        message=f"Refreshed {results['refreshed_successfully']} of {results['needs_refresh']} connections",
        connections_queued=results["needs_refresh"],
        refreshed_successfully=results["refreshed_successfully"],
        refresh_failed=results["refresh_failed"],
    )
