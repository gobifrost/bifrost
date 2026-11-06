"""
OAuth SSO Configuration Models.

Defines the configuration models for OAuth SSO providers (Microsoft, Google, OIDC).
These are stored in the system_configs table with category='oauth_sso'.
"""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

# Supported OAuth SSO providers
OAuthSSOProvider = Literal["microsoft", "google", "oidc"]

# Client authentication methods supported for the token endpoint
OAuthTokenEndpointAuthMethod = Literal[
    "client_secret_post", "client_secret_basic", "private_key_jwt"
]

# Authlib 1.9.5's PrivateKeyJWT signer is RSA-only, so EC algorithms (ES256/
# ES384/ES512) are intentionally rejected. Supporting them needs joserfc or a
# newer authlib signer and is a follow-up.
OAUTH_PRIVATE_KEY_ALGS: tuple[str, ...] = ("RS256", "RS384", "RS512")
_PRIVATE_KEY_ALG_PATTERN = "^(" + "|".join(OAUTH_PRIVATE_KEY_ALGS) + ")$"

# Config key constants for each provider
OAUTH_CONFIG_CATEGORY = "oauth_sso"
OAUTH_LOGIN_PREFERENCE = "login_preference"

# Microsoft config keys
OAUTH_MICROSOFT_CLIENT_ID = "microsoft_client_id"
OAUTH_MICROSOFT_CLIENT_SECRET = "microsoft_client_secret"
OAUTH_MICROSOFT_TENANT_ID = "microsoft_tenant_id"
OAUTH_MICROSOFT_TOKEN_ENDPOINT_AUTH_METHOD = "microsoft_token_endpoint_auth_method"
OAUTH_MICROSOFT_PRIVATE_KEY = "microsoft_private_key"
OAUTH_MICROSOFT_PRIVATE_KEY_KID = "microsoft_private_key_kid"
OAUTH_MICROSOFT_PRIVATE_KEY_ALG = "microsoft_private_key_alg"

# Google config keys
OAUTH_GOOGLE_CLIENT_ID = "google_client_id"
OAUTH_GOOGLE_CLIENT_SECRET = "google_client_secret"
OAUTH_GOOGLE_TOKEN_ENDPOINT_AUTH_METHOD = "google_token_endpoint_auth_method"
OAUTH_GOOGLE_PRIVATE_KEY = "google_private_key"
OAUTH_GOOGLE_PRIVATE_KEY_KID = "google_private_key_kid"
OAUTH_GOOGLE_PRIVATE_KEY_ALG = "google_private_key_alg"

# OIDC config keys
OAUTH_OIDC_DISCOVERY_URL = "oidc_discovery_url"
OAUTH_OIDC_CLIENT_ID = "oidc_client_id"
OAUTH_OIDC_CLIENT_SECRET = "oidc_client_secret"
OAUTH_OIDC_DISPLAY_NAME = "oidc_display_name"
OAUTH_OIDC_TOKEN_ENDPOINT_AUTH_METHOD = "oidc_token_endpoint_auth_method"
OAUTH_OIDC_PRIVATE_KEY = "oidc_private_key"
OAUTH_OIDC_PRIVATE_KEY_KID = "oidc_private_key_kid"
OAUTH_OIDC_PRIVATE_KEY_ALG = "oidc_private_key_alg"


class OAuthLoginPreference(BaseModel):
    """Preferred SSO behavior before the full login screen is shown."""

    auto_redirect_to_sso: bool = Field(
        default=False,
        description="Whether login should first redirect to the preferred SSO provider",
    )
    default_sso_provider: OAuthSSOProvider | None = Field(
        default=None,
        description="Configured SSO provider used for the preferred redirect",
    )
    password_login_enabled: bool = Field(
        default=True,
        description="Whether password login is available to accounts without an SSO exclusion",
    )
    passkey_login_enabled: bool = Field(
        default=True,
        description="Whether passkey login/enrollment is available to accounts without an SSO exclusion",
    )

    excluded_user_ids: list[UUID] | None = Field(
        default=None, description="Users exempt from login restrictions; null safely exempts platform admins"
    )
    excluded_organization_ids: list[UUID] = Field(
        default_factory=list, description="Organizations exempt from login restrictions"
    )

    @model_validator(mode="after")
    def require_provider_when_enabled(self) -> "OAuthLoginPreference":
        if self.auto_redirect_to_sso and self.default_sso_provider is None:
            raise ValueError(
                "default_sso_provider is required when auto_redirect_to_sso is enabled"
            )
        return self


class ClientAuthConfigRequest(BaseModel):
    """Token-endpoint client authentication shared by every provider."""

    token_endpoint_auth_method: OAuthTokenEndpointAuthMethod = Field(
        default="client_secret_post",
        description="How Bifrost authenticates to the token endpoint",
    )
    private_key: str | None = Field(
        default=None,
        description=(
            "PEM private key for private_key_jwt. Write-only: it is encrypted "
            "at rest and never returned."
        ),
    )
    private_key_kid: str | None = Field(
        default=None,
        max_length=255,
        description="Optional JWT header kid for private_key_jwt",
    )
    private_key_alg: str = Field(
        default="RS256",
        pattern=_PRIVATE_KEY_ALG_PATTERN,
        description=(
            "JWT signing algorithm for private_key_jwt (RSA only; EC is a "
            "documented follow-up)"
        ),
    )
    clear_private_key: bool = Field(
        default=False,
        description=(
            "Remove the stored private key (and kid) without deleting the "
            "provider. Cannot be combined with private_key."
        ),
    )


class MicrosoftOAuthConfigRequest(ClientAuthConfigRequest):
    """Request model for configuring Microsoft OAuth SSO."""

    client_id: str = Field(
        ...,
        min_length=1,
        description="Azure AD Application (client) ID"
    )
    client_secret: str | None = Field(
        default=None,
        min_length=1,
        description="Azure AD client secret value (required unless private_key_jwt)",
    )
    tenant_id: str = Field(
        default="common",
        description=(
            "Azure AD tenant ID. Use 'common' for multi-tenant apps "
            "(allows any Microsoft account), 'organizations' for work/school only, "
            "or a specific tenant ID/domain for single-tenant"
        )
    )


class GoogleOAuthConfigRequest(ClientAuthConfigRequest):
    """Request model for configuring Google OAuth SSO."""

    client_id: str = Field(
        ...,
        min_length=1,
        description="Google OAuth 2.0 Client ID"
    )
    client_secret: str | None = Field(
        default=None,
        min_length=1,
        description="Google OAuth 2.0 Client secret (required unless private_key_jwt)",
    )


class OIDCConfigRequest(ClientAuthConfigRequest):
    """Request model for configuring a generic OIDC provider."""

    discovery_url: str = Field(
        ...,
        min_length=1,
        pattern=r"^https://",
        description="OIDC discovery URL (must be HTTPS, e.g., https://provider.com/.well-known/openid-configuration)"
    )
    client_id: str = Field(
        ...,
        min_length=1,
        description="OIDC Client ID"
    )
    client_secret: str | None = Field(
        default=None,
        min_length=1,
        description="OIDC Client secret (required unless private_key_jwt)",
    )
    display_name: str = Field(
        default="SSO",
        max_length=50,
        description="Display name for the login button (e.g., 'Okta', 'Auth0', 'Company SSO')"
    )


class OAuthProviderConfigResponse(BaseModel):
    """Response model for a configured OAuth provider."""

    provider: OAuthSSOProvider = Field(
        ...,
        description="OAuth provider name"
    )
    configured: bool = Field(
        ...,
        description="Whether the provider is fully configured"
    )
    client_id: str | None = Field(
        default=None,
        description="Client ID (not sensitive)"
    )
    # Secrets are never returned
    client_secret_set: bool = Field(
        default=False,
        description="Whether client secret is configured"
    )
    token_endpoint_auth_method: OAuthTokenEndpointAuthMethod = Field(
        default="client_secret_post",
        description="How Bifrost authenticates to the token endpoint",
    )
    private_key_set: bool = Field(
        default=False,
        description="Whether a private_key_jwt key is configured (key material is never returned)",
    )
    private_key_kid: str | None = Field(
        default=None,
        description="JWT header kid for private_key_jwt",
    )
    private_key_alg: str | None = Field(
        default=None,
        description="JWT signing algorithm for private_key_jwt",
    )
    # Provider-specific fields
    tenant_id: str | None = Field(
        default=None,
        description="Microsoft tenant ID"
    )
    discovery_url: str | None = Field(
        default=None,
        description="OIDC discovery URL"
    )
    display_name: str | None = Field(
        default=None,
        description="OIDC display name for login button"
    )


class OAuthConfigListResponse(BaseModel):
    """Response model for listing all OAuth provider configurations."""

    providers: list[OAuthProviderConfigResponse] = Field(
        default_factory=list,
        description="List of OAuth provider configurations"
    )
    callback_url: str = Field(
        ...,
        description="OAuth callback URL to configure in each provider"
    )
    login_preference: OAuthLoginPreference = Field(
        ...,
        description="Preferred SSO behavior before the full login screen",
    )


class OAuthConfigTestRequest(BaseModel):
    """Request model for testing OAuth configuration."""

    # Optional new credentials to test (if not provided, tests saved config)
    client_id: str | None = Field(
        default=None,
        description="Client ID to test (uses saved if not provided)"
    )
    client_secret: str | None = Field(
        default=None,
        description="Client secret to test (uses saved if not provided)"
    )
    # Provider-specific
    tenant_id: str | None = Field(
        default=None,
        description="Microsoft tenant ID to test"
    )
    discovery_url: str | None = Field(
        default=None,
        description="OIDC discovery URL to test"
    )


class OAuthConfigTestResponse(BaseModel):
    """Response model for OAuth configuration test."""

    success: bool = Field(
        ...,
        description="Whether the configuration is valid"
    )
    message: str = Field(
        ...,
        description="Test result message"
    )
    details: dict | None = Field(
        default=None,
        description="Additional details (e.g., discovered endpoints for OIDC)"
    )
