"""
OAuth connection contract models for Bifrost.
"""

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

if TYPE_CHECKING:
    pass


# ==================== OAUTH TYPE DEFINITIONS ====================

OAuthFlowType = Literal["authorization_code", "client_credentials", "refresh_token"]
OAuthStatus = Literal["not_connected", "waiting_callback", "testing", "connected", "completed", "failed"]


# ==================== OAUTH CONNECTION MODELS ====================


class CreateOAuthConnectionRequest(BaseModel):
    """
    Request model for creating a new OAuth connection
    POST /api/oauth/connections
    """
    integration_id: str = Field(
        ...,
        description="ID of the integration this OAuth connection belongs to"
    )
    description: str | None = Field(
        None,
        max_length=500,
        description="Optional description of this OAuth connection"
    )
    oauth_flow_type: OAuthFlowType = Field(
        ...,
        description="OAuth 2.0 flow type"
    )
    client_id: str = Field(
        ...,
        min_length=1,
        description="OAuth client ID (not sensitive)"
    )
    client_secret: str | None = Field(
        None,
        description="OAuth client secret (optional for PKCE flow, required for client_credentials, will be stored securely in Key Vault)"
    )
    authorization_url: str | None = Field(
        None,
        pattern=r"^https://",
        description="OAuth authorization endpoint URL (required for authorization_code, not used for client_credentials)"
    )
    token_url: str = Field(
        ...,
        pattern=r"^https://",
        description="OAuth token endpoint URL (must be HTTPS, may include {placeholders} for templating)"
    )
    token_url_defaults: dict[str, str] | None = Field(
        None,
        description="Default values for token_url placeholders (e.g., {'entity_id': 'common'})"
    )
    scopes: str = Field(
        default="",
        description="Comma-separated list of OAuth scopes to request"
    )
    audience: str | None = Field(
        None,
        max_length=500,
        description="OAuth audience parameter - identifies the target API/resource for the token request (e.g., required by Pax8, Auth0)"
    )
    redirect_uri: str | None = Field(
        None,
        description="OAuth redirect URI (defaults to /oauth/callback/{connection_name})"
    )

    @model_validator(mode='before')
    @classmethod
    def convert_empty_strings(cls, data):
        """Convert empty strings to None for optional fields before validation"""
        if isinstance(data, dict):
            if data.get('client_secret') == '':
                data['client_secret'] = None
            if data.get('authorization_url') == '':
                data['authorization_url'] = None
        return data

    @model_validator(mode='after')
    def validate_flow_requirements(self) -> 'CreateOAuthConnectionRequest':
        """Validate field requirements based on OAuth flow type"""
        if self.oauth_flow_type == 'client_credentials':
            # Client credentials: requires client_secret, doesn't need authorization_url
            if not self.client_secret:
                raise ValueError("client_secret is required for client_credentials flow")
            # Authorization URL is not used in client_credentials flow
            # We'll just ignore it if provided, or use a placeholder if needed

        elif self.oauth_flow_type == 'authorization_code':
            # Authorization code: requires authorization_url, client_secret is optional (PKCE)
            if not self.authorization_url:
                raise ValueError("authorization_url is required for authorization_code flow")

        return self


class UpdateOAuthConnectionRequest(BaseModel):
    """
    Request model for updating an OAuth connection
    PUT /api/oauth/connections/{connection_name}
    """
    name: str | None = Field(default=None, max_length=255, description="Display name")
    oauth_flow_type: OAuthFlowType | None = Field(default=None, description="OAuth 2.0 flow type")
    client_id: str | None = Field(default=None, min_length=1)
    client_secret: str | None = Field(default=None, min_length=1)
    authorization_url: str | None = Field(default=None, pattern=r"^https://")
    token_url: str | None = Field(default=None, pattern=r"^https://")
    token_url_defaults: dict[str, str] | None = Field(
        default=None,
        description="Default values for token_url placeholders"
    )
    scopes: list[str] | None = Field(default=None, description="List of OAuth scopes")
    audience: str | None = Field(
        default=None,
        max_length=500,
        description="OAuth audience parameter"
    )

    @field_validator('scopes', mode='before')
    @classmethod
    def parse_scopes(cls, v):
        """Accept scopes as string (space or comma separated) or list."""
        if v is None:
            return None
        if isinstance(v, list):
            return v
        if isinstance(v, str):
            # Handle both comma-separated and space-separated
            # First replace commas with spaces, then split
            return [s.strip() for s in v.replace(',', ' ').split() if s.strip()]
        return v


class OAuthConnectionSummary(BaseModel):
    """
    Summary model for OAuth connections (used in list responses)
    GET /api/oauth/connections

    Does not include sensitive fields or detailed configuration
    """
    connection_name: str
    name: str | None = Field(default=None, description="Display name for the connection")
    provider: str | None = Field(default=None, description="Provider identifier (same as connection_name)")
    description: str | None = None
    oauth_flow_type: OAuthFlowType
    status: OAuthStatus
    status_message: str | None = None
    integration_id: str | None = Field(
        default=None,
        description="ID of the integration this OAuth connection belongs to"
    )
    expires_at: datetime | None = Field(
        default=None,
        description="When the current access token expires"
    )
    last_refresh_at: datetime | None = Field(
        default=None,
        description="Last successful token refresh"
    )
    created_at: datetime
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class OAuthConnectionDetail(BaseModel):
    """
    Detailed model for OAuth connections (used in get/update responses)
    GET /api/oauth/connections/{connection_name}

    Includes configuration details but masks sensitive fields
    """
    connection_name: str
    name: str | None = Field(default=None, description="Display name for the connection")
    provider: str | None = Field(default=None, description="Provider identifier")
    description: str | None = None
    oauth_flow_type: OAuthFlowType
    client_id: str = Field(
        ...,
        description="OAuth client ID (safe to expose)"
    )
    authorization_url: str | None = Field(
        default=None,
        description="OAuth authorization endpoint (required for authorization_code, not used for client_credentials)"
    )
    token_url: str
    scopes: str
    audience: str | None = Field(
        default=None,
        description="OAuth audience parameter for token requests"
    )

    # Status information
    status: OAuthStatus
    status_message: str | None = None
    integration_id: str | None = Field(
        default=None,
        description="ID of the integration this OAuth connection belongs to"
    )
    expires_at: datetime | None = None
    last_refresh_at: datetime | None = None
    last_test_at: datetime | None = None

    # Metadata
    created_at: datetime
    created_by: str
    updated_at: datetime

    # NOTE: client_secret, access_token, refresh_token are NOT included
    # These are stored securely and never exposed in API responses

    model_config = ConfigDict(from_attributes=True)


class OAuthConnection(BaseModel):
    """
    Internal model representing full OAuth connection data
    Used for storage operations and business logic

    Includes references to secrets (not the actual secret values)
    """
    # Partition/Row Keys for Table Storage
    org_id: str = Field(..., description="Organization ID or 'GLOBAL'")
    connection_name: str = Field(
        ...,
        pattern=r"^[a-zA-Z0-9_-]+$",
        min_length=1,
        max_length=100
    )
    integration_id: str | None = Field(
        default=None,
        description="ID of the integration this OAuth connection belongs to"
    )

    # OAuth Configuration
    description: str | None = Field(default=None, max_length=500)
    oauth_flow_type: OAuthFlowType
    client_id: str
    client_secret_config_key: str = Field(
        ...,
        description="Config key containing the encrypted client secret (oauth_{name}_client_secret)"
    )
    oauth_response_config_key: str = Field(
        ...,
        description="Config key containing the encrypted OAuth response (oauth_{name}_oauth_response)"
    )
    authorization_url: str | None = Field(
        default=None,
        pattern=r"^https://",
        description="OAuth authorization endpoint (required for authorization_code, not used for client_credentials)"
    )
    token_url: str = Field(..., pattern=r"^https://")
    token_url_defaults: dict[str, str] = Field(
        default_factory=dict,
        description="Default values for token_url placeholders (e.g., {'entity_id': 'common'})"
    )
    scopes: str = ""
    redirect_uri: str = Field(
        ...,
        description="Callback URL: /api/oauth/callback/{connection_name}"
    )

    # Token metadata (not the actual tokens - those are in Config/KeyVault)
    token_type: str = "Bearer"
    expires_at: datetime | None = Field(
        default=None,
        description="When the current access token expires (copied from secret for quick checks)"
    )

    # Status tracking
    status: OAuthStatus
    status_message: str | None = None
    last_refresh_at: datetime | None = None
    last_test_at: datetime | None = None

    # Metadata
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    created_by: str
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Helper methods

    def is_expired(self) -> bool:
        """
        Check if the current access token is expired

        Returns:
            True if token is expired or expires_at is not set
        """
        if not self.expires_at:
            return True
        return datetime.now(timezone.utc) >= self.expires_at

    def expires_soon(self, hours: int = 4) -> bool:
        """
        Check if the access token expires within the specified number of hours

        Args:
            hours: Number of hours to check (default: 4)

        Returns:
            True if token expires within the specified hours or is already expired
        """
        if not self.expires_at:
            return True
        threshold = datetime.now(timezone.utc) + timedelta(hours=hours)
        return self.expires_at <= threshold

    def to_summary(self) -> OAuthConnectionSummary:
        """Convert to summary response model"""
        return OAuthConnectionSummary(
            connection_name=self.connection_name,
            name=None,
            provider=self.connection_name,
            description=self.description,
            oauth_flow_type=self.oauth_flow_type,
            status=self.status,
            status_message=self.status_message,
            integration_id=self.integration_id,
            expires_at=self.expires_at,
            last_refresh_at=self.last_refresh_at,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )

    def to_detail(self) -> OAuthConnectionDetail:
        """Convert to detail response model (masks secrets)"""
        return OAuthConnectionDetail(
            connection_name=self.connection_name,
            name=None,
            provider=self.connection_name,
            description=self.description,
            oauth_flow_type=self.oauth_flow_type,
            client_id=self.client_id,
            authorization_url=self.authorization_url,
            token_url=self.token_url,
            scopes=self.scopes,
            status=self.status,
            status_message=self.status_message,
            integration_id=self.integration_id,
            expires_at=self.expires_at,
            last_refresh_at=self.last_refresh_at,
            last_test_at=self.last_test_at,
            created_at=self.created_at,
            created_by=self.created_by,
            updated_at=self.updated_at,
        )

    model_config = ConfigDict(from_attributes=True)


class OAuthCredentialsModel(BaseModel):
    """
    OAuth credentials Pydantic model for API responses
    GET /api/oauth/credentials/{connection_name}

    Contains actual access token and refresh token for use in API calls
    This model is only exposed to authenticated workflow contexts

    Note: This is the Pydantic model for API responses. The regular OAuthCredentials
    class is used for workflow consumption with is_expired() and get_auth_header() methods.
    """
    connection_name: str = Field(
        ...,
        pattern=r"^[a-zA-Z0-9_-]+$",
        description="Connection identifier"
    )
    access_token: str = Field(
        ...,
        min_length=1,
        description="Current OAuth access token"
    )
    token_type: str = Field(
        default="Bearer",
        description="Token type (usually Bearer)"
    )
    expires_at: str = Field(
        ...,
        description="ISO 8601 timestamp when token expires"
    )
    refresh_token: str | None = Field(
        default=None,
        description="Refresh token if available"
    )
    scopes: str = Field(
        default="",
        description="Space-separated list of granted scopes"
    )
    integration_id: str | None = Field(
        default=None,
        description="ID of the integration this OAuth connection belongs to"
    )

    model_config = ConfigDict(from_attributes=True)


class OAuthCredentialsResponse(BaseModel):
    """
    Response wrapper for OAuth credentials endpoint
    Includes connection status and metadata
    """
    connection_name: str
    credentials: OAuthCredentialsModel | None = Field(
        default=None,
        description="Credentials if connection is active, None if not connected"
    )
    status: OAuthStatus = Field(
        ...,
        description="Current connection status"
    )
    integration_id: str | None = Field(
        default=None,
        description="ID of the integration this OAuth connection belongs to"
    )
    expires_at: str | None = Field(
        default=None,
        description="ISO 8601 timestamp when token expires"
    )

    model_config = ConfigDict(from_attributes=True)


class OAuthCallbackRequest(BaseModel):
    """Request model for OAuth callback endpoint"""
    code: str = Field(..., description="Authorization code from OAuth provider")
    state: str | None = Field(default=None, description="State parameter for CSRF protection")
    redirect_uri: str | None = Field(default=None, description="Redirect URI used in authorization request")
    organization_id: str | None = Field(
        default=None,
        description="Organization ID for org-specific token storage (optional, for org overrides)"
    )
    callback_url_params: dict[str, str] | None = Field(
        default=None,
        description="Raw query params from the OAuth callback URL (used to capture entity_id)",
    )


class EntityIdPickerCandidate(BaseModel):
    """A candidate entity_id field surfaced from an OAuth callback."""
    type: str = Field(..., description="One of: url_param, token_response_field, id_token_claim")
    key: str = Field(..., description="Dotted path (e.g. 'team.id' or 'tid')")
    value: str = Field(..., description="Stringified value found at that path")


class OAuthCallbackResponse(BaseModel):
    """Response model for OAuth callback endpoint"""
    success: bool = Field(..., description="Whether the OAuth connection was successful")
    message: str = Field(..., description="Status message")
    status: str = Field(..., description="Connection status")
    connection_name: str = Field(..., description="Name of the OAuth connection")
    warning_message: str | None = Field(default=None, description="Warning message displayed to user (e.g., missing refresh token)")
    error_message: str | None = Field(default=None, description="Error message displayed to user")
    entity_id_picker: list[EntityIdPickerCandidate] | None = Field(
        default=None,
        description=(
            "Candidate entity_id sources for the admin to pick from. Populated "
            "when entity_id_source is unset on the provider, OR when it is set "
            "but extraction returned no value (the configured field wasn't in "
            "this response). Null means 'don't show the picker'."
        ),
    )
    triggering_mapping_id: UUID | None = Field(
        default=None,
        description=(
            "When the callback was triggered by a per-mapping connect, the ID of "
            "that mapping. Used by the picker UI to backfill the mapping's "
            "entity_id with the chosen value."
        ),
    )
    captured_entity_id: str | None = Field(
        default=None,
        description=(
            "Value captured into the mapping's entity_id via the provider's "
            "entity_id_source. Null when nothing was captured (no source, "
            "extraction missed, or no triggering mapping)."
        ),
    )
    captured_entity_id_from: str | None = Field(
        default=None,
        description=(
            "Display string identifying where captured_entity_id came from, "
            "e.g. 'id_token_claim:tid'. Null when captured_entity_id is null."
        ),
    )


class OAuthProviderBase(BaseModel):
    """Shared OAuth provider fields."""
    provider_name: str = Field(max_length=100)
    client_id: str = Field(max_length=255)
    scopes: list = Field(default_factory=list)
    provider_metadata: dict = Field(default_factory=dict)


class OAuthProviderCreate(OAuthProviderBase):
    """Input for creating an OAuth provider."""
    client_secret: str  # Plain text, will be encrypted
    organization_id: Any | None = None


class OAuthProviderUpdate(BaseModel):
    """Input for updating an OAuth provider."""
    client_id: str | None = None
    client_secret: str | None = None
    scopes: list | None = None
    provider_metadata: dict | None = None


class OAuthProviderPublic(OAuthProviderBase):
    """OAuth provider output (secret NOT included)."""
    model_config = ConfigDict(from_attributes=True)

    id: Any
    organization_id: Any | None
    created_at: datetime
    updated_at: datetime


# Non-Pydantic class for workflow use
class OAuthCredentials:
    """
    OAuth credentials object for workflows

    Provides access to OAuth access_token and refresh_token
    for making authenticated API calls to third-party services
    """

    def __init__(
        self,
        connection_name: str,
        access_token: str,
        token_type: str,
        expires_at: datetime,
        refresh_token: str | None = None,
        scopes: str = ""
    ):
        self.connection_name = connection_name
        self.access_token = access_token
        self.token_type = token_type
        self.expires_at = expires_at
        self.refresh_token = refresh_token
        self.scopes = scopes

    def is_expired(self) -> bool:
        """Check if access token is expired"""
        return datetime.now(timezone.utc) >= self.expires_at

    def get_auth_header(self) -> str:
        """Get formatted Authorization header value"""
        return f"{self.token_type} {self.access_token}"

    def __repr__(self) -> str:
        return f"<OAuthCredentials connection={self.connection_name} expires_at={self.expires_at}>"
