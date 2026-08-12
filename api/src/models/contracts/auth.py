"""
Authentication and MFA contract models for Bifrost.
"""

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, EmailStr

if TYPE_CHECKING:
    pass


# ==================== AUTH & MFA MODELS ====================


class OAuthProviderInfo(BaseModel):
    """OAuth provider information for login page"""
    name: str
    display_name: str
    icon: str | None = None


class AuthStatusResponse(BaseModel):
    """
    Pre-login status response.

    Provides all information the client needs to render the login page:
    - Whether initial setup is required (no users exist)
    - Whether password login is available
    - Whether MFA is required for password login
    - Available OAuth/SSO providers
    """
    needs_setup: bool
    password_login_enabled: bool
    mfa_required_for_password: bool
    oauth_providers: list[OAuthProviderInfo]
    auto_redirect_to_sso: bool = False
    default_sso_provider: Literal["microsoft", "google", "oidc"] | None = None


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


class MFAEnrollVerifyRequest(BaseModel):
    """Request to verify MFA during initial enrollment."""
    mfa_token: str
    code: str


class MFAEnrollVerifyResponse(BaseModel):
    """Response after completing MFA enrollment."""
    success: bool
    recovery_codes: list[str]
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class OAuthLoginRequest(BaseModel):
    """OAuth/SSO login request model."""
    email: EmailStr
    name: str
    provider: str


# ==================== DEVICE AUTHORIZATION FLOW MODELS ====================


class DeviceCodeResponse(BaseModel):
    """Response for device authorization code request."""
    device_code: str
    user_code: str
    verification_url: str
    expires_in: int = 300  # 5 minutes
    interval: int = 5  # Polling interval in seconds


class DeviceTokenRequest(BaseModel):
    """Request to exchange device code for access token."""
    device_code: str


class DeviceTokenResponse(BaseModel):
    """Response for device token request (successful)."""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = 1800  # 30 minutes


class DeviceTokenErrorResponse(BaseModel):
    """Error response for device token request."""
    error: str  # "authorization_pending", "expired_token", "access_denied"


class DeviceAuthorizeRequest(BaseModel):
    """Request to authorize a device using user code."""
    user_code: str
