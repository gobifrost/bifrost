"""
OAuth SSO Configuration Router.

API endpoints for managing OAuth SSO provider configurations.
Platform admin only - used in the Settings page.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.auth import Context, CurrentSuperuser
from src.core.database import get_db
from src.core.log_safety import log_safe
from src.models.contracts.oauth_config import (
    GoogleOAuthConfigRequest,
    MicrosoftOAuthConfigRequest,
    OAuthConfigListResponse,
    OAuthLoginPreference,
    OAuthConfigTestRequest,
    OAuthConfigTestResponse,
    OAuthProviderConfigResponse,
    OAuthSSOProvider,
    OIDCConfigRequest,
)
from src.services.oauth_config_service import (
    OAuthConfigService,
    OAuthLoginPreferenceError,
    OAuthProviderPreferenceConflict,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/settings/oauth", tags=["OAuth SSO Config"])


def _get_callback_url(request: Request) -> str:
    """Get the OAuth callback URL based on the request origin."""
    # Use X-Forwarded headers if behind a proxy, otherwise use request base URL
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.headers.get("host", "localhost"))
    return f"{proto}://{host}/auth/oauth/callback"


@router.get(
    "",
    response_model=OAuthConfigListResponse,
    summary="List OAuth provider configurations",
    description="Get configuration status for all OAuth SSO providers (Platform admin only)",
)
async def list_oauth_configs(
    request: Request,
    ctx: Context,
    user: CurrentSuperuser,
    db: AsyncSession = Depends(get_db),
) -> OAuthConfigListResponse:
    """
    List all OAuth provider configurations with their status.

    Returns configuration details for Microsoft, Google, and OIDC providers.
    Client secrets are never exposed - only a flag indicating if they are set.
    """
    service = OAuthConfigService(db)
    providers = await service.get_all_provider_configs()
    login_preference = await service.get_login_preference()
    callback_url = _get_callback_url(request)

    return OAuthConfigListResponse(
        providers=providers,
        callback_url=callback_url,
        login_preference=login_preference,
    )


@router.put(
    "/login-preference",
    response_model=OAuthLoginPreference,
    summary="Configure preferred SSO redirect",
    description="Choose whether login should first redirect to a configured SSO provider",
)
async def set_oauth_login_preference(
    preference: OAuthLoginPreference,
    ctx: Context,
    user: CurrentSuperuser,
    db: AsyncSession = Depends(get_db),
) -> OAuthLoginPreference:
    """Update the platform-wide preferred SSO behavior."""
    service = OAuthConfigService(db)
    try:
        result = await service.set_login_preference(
            preference,
            updated_by=user.email,
        )
    except OAuthLoginPreferenceError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    await db.commit()
    return result


@router.get(
    "/{provider}",
    response_model=OAuthProviderConfigResponse,
    summary="Get OAuth provider configuration",
    description="Get configuration for a specific OAuth provider (Platform admin only)",
)
async def get_oauth_config(
    provider: OAuthSSOProvider,
    ctx: Context,
    user: CurrentSuperuser,
    db: AsyncSession = Depends(get_db),
) -> OAuthProviderConfigResponse:
    """Get configuration for a specific OAuth provider."""
    service = OAuthConfigService(db)
    config = await service.get_provider_config(provider)

    if config:
        return OAuthProviderConfigResponse(
            provider=provider,
            configured=config.is_complete,
            client_id=config.client_id,
            client_secret_set=bool(config.client_secret),
            tenant_id=config.tenant_id if provider == "microsoft" else None,
            discovery_url=config.discovery_url if provider == "oidc" else None,
            display_name=config.display_name if provider == "oidc" else None,
        )

    return OAuthProviderConfigResponse(
        provider=provider,
        configured=False,
    )


@router.put(
    "/microsoft",
    response_model=OAuthProviderConfigResponse,
    summary="Configure Microsoft OAuth",
    description="Set up Microsoft Entra ID (Azure AD) OAuth SSO (Platform admin only)",
)
async def set_microsoft_config(
    config: MicrosoftOAuthConfigRequest,
    ctx: Context,
    user: CurrentSuperuser,
    db: AsyncSession = Depends(get_db),
) -> OAuthProviderConfigResponse:
    """
    Configure Microsoft Entra ID (Azure AD) OAuth SSO.

    **Setup Instructions:**
    1. Go to Azure Portal > Microsoft Entra ID > App registrations
    2. Create a new registration or select an existing one
    3. Under "Authentication", add a Web platform redirect URI:
       `{your-domain}/auth/oauth/callback`
    4. Under "Certificates & secrets", create a new client secret
    5. Copy the Application (client) ID and secret value

    **Tenant ID Options:**
    - `common` - Any Microsoft account (personal + work/school) - multi-tenant
    - `organizations` - Work/school accounts only
    - `consumers` - Personal Microsoft accounts only
    - Specific tenant ID/domain - Single organization only

    **Required API Permissions:**
    - Microsoft Graph: User.Read (delegated)
    - Microsoft Graph: email (delegated)
    - Microsoft Graph: openid (delegated)
    - Microsoft Graph: profile (delegated)
    """
    service = OAuthConfigService(db)
    await service.set_microsoft_config(config, updated_by=user.email)
    await db.commit()

    return OAuthProviderConfigResponse(
        provider="microsoft",
        configured=True,
        client_id=config.client_id,
        client_secret_set=True,
        tenant_id=config.tenant_id,
    )


@router.put(
    "/google",
    response_model=OAuthProviderConfigResponse,
    summary="Configure Google OAuth",
    description="Set up Google OAuth SSO (Platform admin only)",
)
async def set_google_config(
    config: GoogleOAuthConfigRequest,
    ctx: Context,
    user: CurrentSuperuser,
    db: AsyncSession = Depends(get_db),
) -> OAuthProviderConfigResponse:
    """
    Configure Google OAuth SSO.

    **Setup Instructions:**
    1. Go to Google Cloud Console > APIs & Services > Credentials
    2. Create an OAuth 2.0 Client ID (Web application type)
    3. Add authorized redirect URI:
       `{your-domain}/auth/oauth/callback`
    4. Copy the Client ID and Client secret

    **OAuth Consent Screen:**
    - Configure the OAuth consent screen if not already done
    - Add scopes: email, profile, openid
    - Set user type (Internal for G Suite, External for any Google account)
    """
    service = OAuthConfigService(db)
    await service.set_google_config(config, updated_by=user.email)
    await db.commit()

    return OAuthProviderConfigResponse(
        provider="google",
        configured=True,
        client_id=config.client_id,
        client_secret_set=True,
    )


@router.put(
    "/oidc",
    response_model=OAuthProviderConfigResponse,
    summary="Configure OIDC provider",
    description="Set up a generic OIDC provider for SSO (Platform admin only)",
)
async def set_oidc_config(
    config: OIDCConfigRequest,
    ctx: Context,
    user: CurrentSuperuser,
    db: AsyncSession = Depends(get_db),
) -> OAuthProviderConfigResponse:
    """
    Configure a generic OIDC provider for SSO.

    **Setup Instructions:**
    1. In your OIDC provider (Okta, Auth0, Keycloak, etc.), create a new application
    2. Set the application type to "Web" or "Regular Web Application"
    3. Add the redirect URI:
       `{your-domain}/auth/oauth/callback`
    4. Copy the discovery URL, client ID, and client secret

    **Discovery URL Examples:**
    - Okta: `https://your-org.okta.com/.well-known/openid-configuration`
    - Auth0: `https://your-tenant.auth0.com/.well-known/openid-configuration`
    - Keycloak: `https://your-server/realms/your-realm/.well-known/openid-configuration`

    **Required Scopes:**
    Most OIDC providers should support: openid, email, profile
    """
    service = OAuthConfigService(db)
    await service.set_oidc_config(config, updated_by=user.email)
    await db.commit()

    return OAuthProviderConfigResponse(
        provider="oidc",
        configured=True,
        client_id=config.client_id,
        client_secret_set=True,
        discovery_url=config.discovery_url,
        display_name=config.display_name,
    )


@router.delete(
    "/{provider}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete OAuth provider configuration",
    description="Remove configuration for an OAuth provider (Platform admin only)",
)
async def delete_oauth_config(
    provider: OAuthSSOProvider,
    ctx: Context,
    user: CurrentSuperuser,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete all configuration for a specific OAuth provider."""
    service = OAuthConfigService(db)
    try:
        deleted = await service.delete_provider_config(
            provider,
            updated_by=user.email,
        )
    except OAuthProviderPreferenceConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    await db.commit()

    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"OAuth provider {provider} not found or already deleted",
        )

    logger.info(f"OAuth config deleted for {log_safe(provider)} by {user.email}")


@router.post(
    "/{provider}/test",
    response_model=OAuthConfigTestResponse,
    summary="Test OAuth provider configuration",
    description="Test connectivity to an OAuth provider (Platform admin only)",
)
async def test_oauth_config(
    provider: OAuthSSOProvider,
    ctx: Context,
    user: CurrentSuperuser,
    test_data: OAuthConfigTestRequest | None = None,
    db: AsyncSession = Depends(get_db),
) -> OAuthConfigTestResponse:
    """
    Test OAuth provider configuration by checking the discovery endpoint.

    Can test with:
    - Saved configuration (if no test_data provided)
    - New credentials (if test_data provided) - useful before saving

    The test validates that the discovery endpoint is reachable and returns
    valid OIDC configuration. It does NOT test the actual OAuth flow.
    """
    service = OAuthConfigService(db)

    test_dict = test_data.model_dump(exclude_none=True) if test_data else None
    result = await service.test_provider_config(provider, test_dict)

    return result
