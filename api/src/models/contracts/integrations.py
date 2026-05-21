"""
Integrations contract models for Bifrost.

Defines request/response models for integration management and mapping.
"""

from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    pass


# ==================== TYPE DEFINITIONS ====================

ConfigItemType = Literal["string", "int", "bool", "json", "secret"]


# ==================== CONFIG SCHEMA MODELS ====================


class ConfigSchemaItem(BaseModel):
    """
    Metadata for a single configuration item.
    Defines what configuration keys are available for an integration.

    Note: Default values are stored in the configs table, not in the schema.
    Use the integration config endpoint to set defaults.
    """

    model_config = ConfigDict(from_attributes=True)

    key: str = Field(
        ...,
        min_length=1,
        max_length=255,
        pattern=r"^[a-zA-Z0-9_]+$",
        description="Configuration key (alphanumeric, underscores)",
    )
    type: ConfigItemType = Field(
        ..., description="Configuration value type"
    )
    required: bool = Field(
        default=False, description="Whether this configuration is required"
    )
    description: str | None = Field(
        default=None,
        max_length=500,
        description="Human-readable description of this config item",
    )
    options: list[str] | None = Field(
        default=None,
        description="List of valid string options for dropdown UI",
    )


# ==================== INTEGRATION REQUEST MODELS ====================


class IntegrationCreate(BaseModel):
    """
    Request model for creating a new integration.
    POST /api/integrations
    """

    name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Unique integration name (e.g., 'Microsoft Partner', 'QuickBooks Online')",
    )
    config_schema: list[ConfigSchemaItem] | None = Field(
        default=None,
        description="Optional schema defining available configuration for this integration",
    )
    entity_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="Optional global entity ID for token URL templating (e.g., tenant ID, partner tenant ID)",
    )
    entity_id_name: str | None = Field(
        default=None,
        max_length=255,
        description="Optional display name for the global entity ID",
    )
    default_entity_id: str | None = Field(
        default=None,
        max_length=255,
        description="Default value for entity_id in URL templates (e.g., 'common' for Azure multi-tenant)",
    )


class IntegrationUpdate(BaseModel):
    """
    Request model for updating an integration.
    PUT /api/integrations/{integration_id}
    """

    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="Integration name",
    )
    list_entities_data_provider_id: UUID | None = Field(
        default=None,
        description="Data provider ID for listing entities",
    )
    config_schema: list[ConfigSchemaItem] | None = Field(
        default=None,
        description="Configuration schema",
    )
    entity_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="Global entity ID for token URL templating",
    )
    entity_id_name: str | None = Field(
        default=None,
        max_length=255,
        description="Display name for the global entity ID",
    )
    default_entity_id: str | None = Field(
        default=None,
        max_length=255,
        description="Default value for entity_id in URL templates (e.g., 'common' for Azure multi-tenant)",
    )


class IntegrationMappingCreate(BaseModel):
    """
    Request model for creating an integration mapping.
    POST /api/integrations/{integration_id}/mappings

    Note: This model requires organization_id because the API only creates
    org-scoped mappings. Global mappings (organization_id=NULL) are created
    programmatically via migrations or admin scripts, not through the API.
    """

    organization_id: UUID = Field(
        ...,
        description="Organization ID to map to this integration",
    )
    entity_id: str = Field(
        ...,
        max_length=255,
        description=(
            "External entity ID (e.g., tenant ID, company ID). May be empty when "
            "creating a mapping ahead of an OAuth Connect flow that will fill it "
            "from the callback via OAuthProvider.entity_id_source."
        ),
    )
    entity_name: str | None = Field(
        default=None,
        max_length=255,
        description="Display name for the external entity",
    )
    oauth_token_id: UUID | None = Field(
        default=None,
        description="Optional per-organization OAuth token override",
    )
    config: dict[str, Any] | None = Field(
        default=None,
        description="Per-organization integration configuration values",
    )


class IntegrationMappingUpdate(BaseModel):
    """
    Request model for updating an integration mapping.
    PUT /api/integrations/{integration_id}/mappings/{mapping_id}
    """

    entity_id: str | None = Field(
        default=None,
        max_length=255,
        description="External entity ID (empty string allowed; see IntegrationMappingCreate)",
    )
    entity_name: str | None = Field(
        default=None,
        max_length=255,
        description="Display name for the external entity",
    )
    oauth_token_id: UUID | None = Field(
        default=None,
        description="Per-organization OAuth token override",
    )
    config: dict[str, Any] | None = Field(
        default=None,
        description="Per-organization integration configuration",
    )


class IntegrationMappingBatchItem(BaseModel):
    """A single mapping in a batch upsert request."""

    organization_id: UUID = Field(
        ...,
        description="Organization ID to map",
    )
    entity_id: str = Field(
        ...,
        max_length=255,
        description="External entity ID (empty string allowed; see IntegrationMappingCreate)",
    )
    entity_name: str | None = Field(
        default=None,
        max_length=255,
        description="Display name for the external entity",
    )


class IntegrationMappingBatchRequest(BaseModel):
    """Batch upsert request for integration mappings."""

    mappings: list[IntegrationMappingBatchItem] = Field(
        ...,
        min_length=1,
        max_length=500,
        description="List of mappings to create or update",
    )


class IntegrationMappingBatchResponse(BaseModel):
    """Response from batch mapping upsert."""

    created: int = Field(..., description="Number of new mappings created")
    updated: int = Field(..., description="Number of existing mappings updated")
    errors: list[str] = Field(default_factory=list, description="Error messages for failed items")


class MappingAuthorizeRequest(BaseModel):
    """Request to begin OAuth authorize flow for a specific mapping."""

    redirect_uri: str = Field(..., description="Frontend callback URL")


class MappingAuthorizeResponse(BaseModel):
    """Response with the authorization URL to redirect the user to."""

    authorization_url: str = Field(..., description="URL to redirect user for authorization")


class EntityIdSourceUpdateRequest(BaseModel):
    """Set the entity_id_source on an integration's OAuth provider, optionally
    populating a triggering mapping's entity_id at the same time."""

    type: str = Field(..., description="url_param | token_response_field | id_token_claim")
    key: str = Field(..., description="Dotted path (e.g. 'team.id')")
    apply_to_mapping_id: UUID | None = Field(
        default=None,
        description="When set, also write apply_value to this mapping's entity_id",
    )
    apply_value: str | None = Field(
        default=None,
        description="Captured value from the picker for the triggering mapping",
    )


# ==================== INTEGRATION RESPONSE MODELS ====================


class IntegrationResponse(BaseModel):
    """
    Response model for a single integration.
    GET /api/integrations/{integration_id}
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(..., description="Integration ID")
    name: str = Field(..., description="Integration name")
    list_entities_data_provider_id: UUID | None = Field(
        default=None,
        description="Associated data provider ID for listing entities",
    )
    config_schema: list[ConfigSchemaItem] | None = Field(
        default=None,
        description="Configuration schema for this integration",
    )
    entity_id: str | None = Field(
        default=None,
        description="Global entity ID for token URL templating",
    )
    entity_id_name: str | None = Field(
        default=None,
        description="Display name for the global entity ID",
    )
    default_entity_id: str | None = Field(
        default=None,
        description="Default value for entity_id in URL templates",
    )
    has_oauth_config: bool = Field(
        default=False,
        description="Whether OAuth configuration is set up for this integration",
    )
    is_deleted: bool = Field(
        default=False,
        description="Soft delete flag",
    )
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")


class IntegrationMappingResponse(BaseModel):
    """
    Response model for a single integration mapping.
    GET /api/integrations/{integration_id}/mappings/{mapping_id}
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(..., description="Mapping ID")
    integration_id: UUID = Field(..., description="Associated integration ID")
    organization_id: UUID | None = Field(
        default=None,
        description="Associated organization ID (NULL for global mappings)",
    )
    entity_id: str = Field(..., description="External entity ID")
    entity_name: str | None = Field(
        default=None,
        description="Display name for the external entity",
    )
    oauth_token_id: UUID | None = Field(
        default=None,
        description="Per-organization OAuth token override ID",
    )
    config: dict[str, Any] | None = Field(
        default=None,
        description="Per-organization integration configuration",
    )
    connection_status: str | None = Field(
        default=None,
        description="Per-mapping OAuth token status (mirrors OAuthToken.status); None if no per-row token",
    )
    connection_message: str | None = Field(
        default=None,
        description="Last status message from the per-mapping token (e.g., refresh error)",
    )
    last_refresh_at: datetime | None = Field(
        default=None,
        description="When the per-mapping token was last refreshed",
    )
    connection_expires_at: datetime | None = Field(
        default=None,
        description="When the per-mapping OAuth token expires; None if no per-row token",
    )
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")


class IntegrationListResponse(BaseModel):
    """
    Response model for listing integrations.
    GET /api/integrations
    """

    items: list[IntegrationResponse] = Field(
        ..., description="List of integrations"
    )
    total: int = Field(..., description="Total number of integrations")


class IntegrationMappingListResponse(BaseModel):
    """
    Response model for listing integration mappings.
    GET /api/integrations/{integration_id}/mappings
    """

    items: list[IntegrationMappingResponse] = Field(
        ..., description="List of integration mappings"
    )
    total: int = Field(..., description="Total number of mappings")


# ==================== DETAILED RESPONSE MODELS ====================


class OAuthConfigSummary(BaseModel):
    """
    OAuth configuration summary for integration detail response.
    Includes provider config and connection status.
    """

    model_config = ConfigDict(from_attributes=True)

    # Provider configuration
    provider_name: str = Field(..., description="OAuth provider name")
    oauth_flow_type: str = Field(..., description="OAuth flow type (authorization_code, client_credentials)")
    client_id: str = Field(..., description="OAuth client ID")
    authorization_url: str | None = Field(default=None, description="Authorization URL")
    token_url: str = Field(..., description="Token URL")
    scopes: list[str] = Field(default_factory=list, description="OAuth scopes")

    # Connection status
    status: str = Field(default="not_connected", description="Connection status")
    status_message: str | None = Field(default=None, description="Status message")
    expires_at: datetime | None = Field(default=None, description="Token expiration time")
    last_refresh_at: datetime | None = Field(default=None, description="Last token refresh time")
    has_refresh_token: bool = Field(default=False, description="Whether a refresh token is available")
    entity_id_source: dict | None = Field(
        default=None,
        description="Configured entity_id source ({'type': ..., 'key': ...}) or null",
    )


class IntegrationDetailResponse(BaseModel):
    """
    Detailed response model for a single integration.
    Includes mappings and OAuth configuration in a single response.
    GET /api/integrations/{integration_id}
    """

    model_config = ConfigDict(from_attributes=True)

    # Core integration fields
    id: UUID = Field(..., description="Integration ID")
    name: str = Field(..., description="Integration name")
    list_entities_data_provider_id: UUID | None = Field(
        default=None,
        description="Associated data provider ID for listing entities",
    )
    config_schema: list[ConfigSchemaItem] | None = Field(
        default=None,
        description="Configuration schema for this integration",
    )
    config_defaults: dict[str, Any] | None = Field(
        default=None,
        description="Integration-level default configuration values",
    )
    entity_id: str | None = Field(
        default=None,
        description="Global entity ID for token URL templating",
    )
    entity_id_name: str | None = Field(
        default=None,
        description="Display name for the global entity ID",
    )
    default_entity_id: str | None = Field(
        default=None,
        description="Default value for {entity_id} in URL templates (e.g., 'common' for Azure multi-tenant)",
    )
    has_oauth_config: bool = Field(
        default=False,
        description="Whether OAuth configuration is set up for this integration",
    )
    is_deleted: bool = Field(
        default=False,
        description="Soft delete flag",
    )
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")

    # Nested data
    mappings: list["IntegrationMappingResponse"] = Field(
        default_factory=list,
        description="All organization mappings for this integration",
    )
    oauth_config: OAuthConfigSummary | None = Field(
        default=None,
        description="OAuth provider configuration and connection status",
    )


# ==================== SDK RESPONSE MODELS ====================


class IntegrationSDKResponse(BaseModel):
    """
    Integration data for API SDK endpoint responses.
    Used by /api/integrations/sdk/{name} endpoint.
    Does NOT include decrypted OAuth tokens (use IntegrationData from sdk.py for that).
    """

    model_config = ConfigDict(from_attributes=True)

    integration_id: UUID = Field(
        ..., description="Integration ID"
    )
    entity_id: str = Field(
        ..., description="Mapped external entity ID"
    )
    entity_name: str | None = Field(
        default=None,
        description="Display name for the mapped entity",
    )
    config: dict[str, Any] = Field(
        default_factory=dict,
        description="Merged configuration (schema defaults + org overrides)",
    )
    oauth_client_id: str | None = Field(
        default=None,
        description="OAuth client ID (from provider or override)",
    )
    oauth_token_url: str | None = Field(
        default=None,
        description="OAuth token URL (with {entity_id} placeholder if applicable)",
    )
    oauth_scopes: str | None = Field(
        default=None,
        description="OAuth scopes for this integration",
    )


# ==================== INTEGRATION TEST MODELS ====================


class IntegrationTestRequest(BaseModel):
    """
    Request model for testing an integration connection.
    POST /api/integrations/{integration_id}/test
    """

    organization_id: UUID | None = Field(
        default=None,
        description="Organization ID to test with. If None, uses global defaults only.",
    )
    endpoint: str = Field(
        default="/",
        description="API endpoint path to test (e.g., /api/users). Appended to base_url.",
    )


class IntegrationTestResponse(BaseModel):
    """
    Response model for integration connection test.
    POST /api/integrations/{integration_id}/test
    """

    success: bool = Field(..., description="Whether the test succeeded")
    message: str = Field(..., description="Human-readable result message")
    method_called: str | None = Field(
        default=None,
        description="The SDK method that was called for the test",
    )
    duration_ms: int | None = Field(
        default=None,
        description="Time taken for the test call in milliseconds",
    )
    error_details: str | None = Field(
        default=None,
        description="Detailed error message if test failed",
    )
