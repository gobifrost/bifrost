"""
SDK contract models for Bifrost.

Includes:
- Request/response models for SDK file/config operations
- Typed response models for SDK modules (integrations, executions, forms, workflows)

All response models use `str` for UUIDs and datetime fields (JSON-friendly, matches SDK patterns).
"""

from typing import TYPE_CHECKING, Any, Iterator

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    pass


# ==================== SDK FILE OPERATIONS ====================


FILE_LOCATION_DESCRIPTION = (
    "Storage location. Special values: workspace (default), temp, uploads. "
    "Custom names like reports are accepted; internal prefixes _repo, _tmp, "
    "and _apps are blocked."
)


class SDKFileReadRequest(BaseModel):
    """Request to read a file via SDK."""
    path: str = Field(..., description="Relative path to file")
    location: str = Field(default="workspace", description=FILE_LOCATION_DESCRIPTION)

    model_config = ConfigDict(from_attributes=True)


class SDKFileWriteRequest(BaseModel):
    """Request to write a file via SDK."""
    path: str = Field(..., description="Relative path to file")
    content: str = Field(..., description="File content (text)")
    location: str = Field(default="workspace", description=FILE_LOCATION_DESCRIPTION)

    model_config = ConfigDict(from_attributes=True)


class SDKFileListRequest(BaseModel):
    """Request to list files in a directory via SDK."""
    directory: str = Field(default="", description="Directory path (relative)")
    location: str = Field(default="workspace", description=FILE_LOCATION_DESCRIPTION)

    model_config = ConfigDict(from_attributes=True)


class SDKFileDeleteRequest(BaseModel):
    """Request to delete a file or directory via SDK."""
    path: str = Field(..., description="Path to file or directory")
    location: str = Field(default="workspace", description=FILE_LOCATION_DESCRIPTION)

    model_config = ConfigDict(from_attributes=True)


# ==================== SDK CONFIG OPERATIONS ====================


class SDKConfigGetRequest(BaseModel):
    """Request to get a config value via SDK."""
    key: str = Field(..., description="Configuration key")
    org_id: str | None = Field(
        default=None, description="Organization ID (optional, uses context default)")

    model_config = ConfigDict(from_attributes=True)


class SDKConfigSetRequest(BaseModel):
    """Request to set a config value via SDK."""
    key: str = Field(..., description="Configuration key")
    value: Any = Field(..., description="Configuration value")
    org_id: str | None = Field(
        default=None, description="Organization ID (optional, uses context default)")
    is_secret: bool = Field(
        default=False, description="Whether to encrypt the value")

    model_config = ConfigDict(from_attributes=True)


class SDKConfigListRequest(BaseModel):
    """Request to list config values via SDK."""
    org_id: str | None = Field(
        default=None, description="Organization ID (optional, uses context default)")

    model_config = ConfigDict(from_attributes=True)


class SDKConfigDeleteRequest(BaseModel):
    """Request to delete a config value via SDK."""
    key: str = Field(..., description="Configuration key")
    org_id: str | None = Field(
        default=None, description="Organization ID (optional, uses context default)")

    model_config = ConfigDict(from_attributes=True)


class SDKConfigValue(BaseModel):
    """Config value response from SDK."""
    key: str = Field(..., description="Configuration key")
    value: Any = Field(..., description="Configuration value")
    config_type: str = Field(..., description="Type of the config (string, int, bool, json, secret)")

    model_config = ConfigDict(from_attributes=True)


# ==================== SDK OAUTH OPERATIONS ====================


class SDKOAuthGetRequest(BaseModel):
    """Request to get OAuth connection data via SDK."""
    provider: str = Field(..., description="OAuth provider/connection name")
    org_id: str | None = Field(
        default=None, description="Organization ID (optional, uses context default)")

    model_config = ConfigDict(from_attributes=True)


class SDKOAuthGetResponse(BaseModel):
    """OAuth connection data response from SDK."""
    connection_name: str = Field(..., description="Connection/provider name")
    client_id: str = Field(..., description="OAuth client ID")
    client_secret: str | None = Field(None, description="OAuth client secret (decrypted)")
    authorization_url: str | None = Field(None, description="OAuth authorization URL")
    token_url: str | None = Field(None, description="OAuth token URL")
    scopes: list[str] = Field(default_factory=list, description="OAuth scopes")
    access_token: str | None = Field(None, description="Current access token (decrypted)")
    refresh_token: str | None = Field(None, description="Refresh token (decrypted)")
    expires_at: str | None = Field(None, description="Token expiration (ISO format)")

    model_config = ConfigDict(from_attributes=True)


# =============================================================================
# SDK RESPONSE MODELS (for typed returns from SDK modules)
# =============================================================================


# ==================== OAuth & Integrations ====================


class OAuthCredentials(BaseModel):
    """OAuth credentials and configuration for an integration."""

    connection_name: str
    client_id: str
    client_secret: str | None = None
    authorization_url: str | None = None
    token_url: str | None = None
    scopes: list[str] = Field(default_factory=list)
    access_token: str | None = None
    refresh_token: str | None = None
    expires_at: str | None = None

    model_config = ConfigDict(from_attributes=True)


class IntegrationData(BaseModel):
    """
    Full integration data returned by integrations.get().

    Includes entity mapping, configuration, and OAuth credentials.
    """

    integration_id: str
    entity_id: str | None = None
    entity_name: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    oauth: OAuthCredentials | None = None
    config_secret_keys: list[str] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


# ==================== Configuration ====================


class ConfigData:
    """
    Dynamic configuration access with dot notation.

    Allows accessing config keys as attributes:
        config_data = await config.list()
        api_url = config_data.api_url
        timeout = config_data.timeout

    Also supports dict-like access:
        api_url = config_data["api_url"]
        "api_url" in config_data
    """

    def __init__(self, data: dict[str, Any]) -> None:
        # Use object.__setattr__ to avoid triggering __setattr__
        object.__setattr__(self, "_data", data)

    def __getattr__(self, name: str) -> Any:
        """Get config value by attribute name."""
        if name.startswith("_"):
            raise AttributeError(name)
        return self._data.get(name)

    def __getitem__(self, key: str) -> Any:
        """Get config value by key (dict-like access)."""
        return self._data.get(key)

    def __contains__(self, key: str) -> bool:
        """Check if key exists in config."""
        return key in self._data

    def __iter__(self) -> Iterator[str]:
        """Iterate over config keys."""
        return iter(self._data)

    def __len__(self) -> int:
        """Return number of config keys."""
        return len(self._data)

    def __repr__(self) -> str:
        """Return string representation."""
        return f"ConfigData({self._data!r})"

    def keys(self) -> Any:
        """Return config keys."""
        return self._data.keys()

    def values(self) -> Any:
        """Return config values."""
        return self._data.values()

    def items(self) -> Any:
        """Return config key-value pairs."""
        return self._data.items()

    def get(self, key: str, default: Any = None) -> Any:
        """Get config value with optional default."""
        return self._data.get(key, default)

    def to_dict(self) -> dict[str, Any]:
        """Return underlying dict (for serialization)."""
        return self._data.copy()

