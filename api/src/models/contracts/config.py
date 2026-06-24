"""
Configuration and secret contract models for Bifrost.
"""

from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from src.models.enums import ConfigType
from src.models.contracts.base import IntegrationType

if TYPE_CHECKING:
    pass


# ==================== CONFIG MODELS ====================


class ConfigResponse(BaseModel):
    """Configuration entity response (global or org-specific)"""
    id: UUID | None = Field(default=None, description="Config UUID")
    key: str
    value: Any = Field(..., description="Config value. For SECRET type, this will be '[SECRET]' in list responses.")
    type: ConfigType = ConfigType.STRING
    scope: Literal["GLOBAL", "org"] = Field(
        default="org", description="GLOBAL for MSP-wide or 'org' for org-specific")
    org_id: str | None = Field(
        default=None, description="Organization ID (only for org-specific config)")
    integration_id: str | None = Field(
        default=None, description="Integration ID (if config is managed by an integration)")
    integration_name: str | None = Field(
        default=None, description="Integration name (if config is managed by an integration)")
    description: str | None = None
    updated_at: datetime | None = None
    updated_by: str | None = None


class SetConfigRequest(BaseModel):
    """Request model for setting config"""
    key: str = Field(..., pattern=r"^[a-zA-Z0-9_]+$")
    value: str = Field(..., description="Config value. For SECRET type, this will be encrypted before storage.")
    type: ConfigType
    description: str | None = Field(default=None, description="Optional description of this config entry")
    organization_id: UUID | None = Field(default=None, description="Organization ID. Null for global config.")


class UpdateConfigRequest(BaseModel):
    """Request model for updating an existing config by ID.

    All fields are optional. For SECRET type configs, omitting value (or sending
    empty string) preserves the existing encrypted value.
    """
    key: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_]+$")
    value: str | None = Field(default=None, description="Config value. For SECRET type, leave empty to keep existing value.")
    type: ConfigType | None = Field(default=None)
    description: str | None = Field(default=None, description="Optional description of this config entry")
    organization_id: UUID | None = Field(default=None, description="Organization ID. Null for global config.")


# CRUD Pattern Models for Config
class ConfigBase(BaseModel):
    """Shared config fields."""
    key: str = Field(max_length=255)
    value: dict
    config_type: ConfigType = Field(default=ConfigType.STRING)
    description: str | None = Field(default=None)


class ConfigCreate(ConfigBase):
    """Input for creating a config."""
    organization_id: UUID | None = None


class ConfigUpdate(BaseModel):
    """Input for updating a config."""
    value: dict | None = None
    config_type: ConfigType | None = None
    description: str | None = None


class ConfigPublic(ConfigBase):
    """Config output for API responses."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID | None
    created_at: datetime
    updated_at: datetime
    updated_by: str

    @field_serializer("created_at", "updated_at")
    def serialize_dt(self, dt: datetime | None) -> str | None:
        return dt.isoformat() if dt else None


# ==================== INTEGRATION CONFIG MODELS ====================


class IntegrationConfig(BaseModel):
    """Integration configuration entity"""
    type: IntegrationType
    enabled: bool = Field(default=True)
    settings: dict[str, Any] = Field(...,
                                     description="Integration-specific settings")
    updated_at: datetime
    updated_by: str


class SetIntegrationConfigRequest(BaseModel):
    """Request model for setting integration config"""
    type: IntegrationType
    enabled: bool = Field(default=True)
    settings: dict[str, Any]

    @field_validator('settings')
    @classmethod
    def validate_settings(cls, v, info):
        """Validate integration-specific settings"""
        integration_type = info.data.get('type')

        if integration_type == IntegrationType.MSGRAPH:
            required_keys = {'tenant_id', 'client_id', 'client_secret_config_key'}
            if not required_keys.issubset(v.keys()):
                raise ValueError(
                    f"Microsoft Graph integration requires: {required_keys}")

        elif integration_type == IntegrationType.HALOPSA:
            required_keys = {'api_url', 'client_id', 'api_key_config_key'}
            if not required_keys.issubset(v.keys()):
                raise ValueError(
                    f"HaloPSA integration requires: {required_keys}")

        return v


# ==================== SECRET MODELS ====================


class SecretListResponse(BaseModel):
    """Response model for listing secrets"""
    secrets: list[str] = Field(...,
                               description="List of secret names available in Key Vault")
    org_id: str | None = Field(
        default=None, description="Organization ID filter (if applied)")
    count: int = Field(..., description="Total number of secrets returned")


class SecretCreateRequest(BaseModel):
    """Request model for creating a secret"""
    org_id: str = Field(...,
                       description="Organization ID or 'GLOBAL' for platform-wide")
    secret_key: str = Field(..., pattern=r"^[a-zA-Z0-9_-]+$",
                           description="Secret key (alphanumeric, hyphens, underscores)")
    value: str = Field(..., min_length=1, description="Secret value")

    @field_validator('secret_key')
    @classmethod
    def validate_secret_key(cls, v):
        """Validate secret key follows naming conventions"""
        if len(v) > 100:
            raise ValueError("Secret key must be 100 characters or less")
        return v


class SecretUpdateRequest(BaseModel):
    """Request model for updating a secret"""
    value: str = Field(..., min_length=1, description="New secret value")


class SecretResponse(BaseModel):
    """Response model for secret operations"""
    name: str = Field(...,
                      description="Full secret name in Key Vault (e.g., org-123--api-key)")
    org_id: str = Field(..., description="Organization ID or 'GLOBAL'")
    secret_key: str = Field(..., description="Secret key portion")
    value: str | None = Field(
        default=None, description="Secret value (only shown immediately after create/update)")
    message: str = Field(..., description="Operation result message")


# CRUD Pattern Models for Secret
class SecretBase(BaseModel):
    """Shared secret fields (note: encrypted_value is NOT exposed)."""
    name: str = Field(max_length=255)


class SecretCreate(SecretBase):
    """Input for creating a secret."""
    value: str  # Plain text value, will be encrypted
    organization_id: UUID | None = None


class SecretUpdate(BaseModel):
    """Input for updating a secret."""
    value: str | None = None  # Plain text value


class SecretPublic(SecretBase):
    """Secret output for API responses (value NOT included)."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID | None
    created_at: datetime
    updated_at: datetime

    @field_serializer("created_at", "updated_at")
    def serialize_dt(self, dt: datetime | None) -> str | None:
        return dt.isoformat() if dt else None
