"""
External MCP client contract models.

Pydantic Create / Update / Public models for the four external-MCP ORM
entities: ``MCPServer`` (template, no secrets), ``MCPConnection`` (per-org
instance, encrypted secret), ``MCPConnectionTool`` (per-connection catalog
populated from vendor's tools/list), ``UserMCPCredential`` (per-user delegated
tokens with consent metadata).

``*Public`` models intentionally omit ``encrypted_client_secret`` so they can
be returned by the API without leaking secrets.
"""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ==================== MCP SERVER ====================


class MCPServerOAuthProviderCreate(BaseModel):
    """Inline OAuth provider creation block carried on ``MCPServerCreate``.

    When present, ``POST /api/mcp-servers`` creates an ``OAuthProvider`` row
    and links it via ``mcp_servers.oauth_provider_id``. A placeholder
    ``encrypted_client_secret`` is stored on the provider — the per-org
    client_id+secret pair lives on each ``mcp_connections`` row, since each
    org registers its own OAuth app with the vendor. The provider row is
    just the schema (token_url, scopes, audience, flow_type).
    """

    oauth_flow_type: Literal["authorization_code", "client_credentials"] = Field(
        default="authorization_code",
        description=(
            "OAuth grant type. Detect from discovery's ``grant_types_supported``: "
            "if ``client_credentials`` is supported, default to that; else "
            "``authorization_code``. Admin can override."
        ),
    )
    token_url: str = Field(..., min_length=1, max_length=500)
    authorization_url: str | None = Field(
        default=None,
        max_length=500,
        description=(
            "Required for ``authorization_code`` flow; ignored for "
            "``client_credentials``."
        ),
    )
    scopes: list[str] = Field(default_factory=list)
    audience: str | None = Field(default=None, max_length=500)


class MCPServerCreate(BaseModel):
    """Request model for creating an MCP server template."""

    name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Unique server name (e.g. 'Microsoft 365 Copilot', 'halopsa-mcp')",
    )
    server_url: str = Field(
        ...,
        min_length=1,
        max_length=2048,
        description="MCP server URL (Streamable HTTP endpoint)",
    )
    oauth_provider_id: UUID | None = Field(
        default=None,
        description="OAuth provider configuration FK; absent for servers without auth",
    )
    oauth_provider: MCPServerOAuthProviderCreate | None = Field(
        default=None,
        description=(
            "Inline OAuth provider creation. When set, the router creates an "
            "OAuthProvider row and links it via ``oauth_provider_id``. Mutually "
            "exclusive with ``oauth_provider_id``."
        ),
    )
    redirect_url: str | None = Field(
        default=None,
        max_length=2048,
        description="Deterministic redirect URL for the OAuth callback",
    )
    discovery_metadata: dict[str, Any] | None = Field(
        default=None,
        description="Snapshot of /.well-known/oauth-authorization-server at create time",
    )
    organization_id: UUID | None = Field(
        default=None,
        description="Org UUID (NULL = platform-level template visible to all orgs)",
    )
    is_active: bool = Field(default=True, description="Active flag")


class MCPServerUpdate(BaseModel):
    """Request model for updating an MCP server template."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    server_url: str | None = Field(default=None, min_length=1, max_length=2048)
    oauth_provider_id: UUID | None = Field(default=None)
    redirect_url: str | None = Field(default=None, max_length=2048)
    discovery_metadata: dict[str, Any] | None = Field(default=None)
    organization_id: UUID | None = Field(default=None)
    is_active: bool | None = Field(default=None)


class MCPServerSummary(BaseModel):
    """Lightweight server summary used in list responses."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    server_url: str
    organization_id: UUID | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class MCPServerPublic(BaseModel):
    """Detailed server response including nested per-org connections."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    server_url: str
    oauth_provider_id: UUID | None
    oauth_flow_type: str | None = Field(
        default=None,
        description=(
            "OAuth flow type of the linked provider, if any. One of "
            "``authorization_code`` | ``client_credentials``. The frontend "
            "branches on this when rendering the connect button (popup vs "
            "synchronous activate)."
        ),
    )
    redirect_url: str | None
    discovery_metadata: dict[str, Any] | None
    organization_id: UUID | None
    is_active: bool
    created_at: datetime
    updated_at: datetime
    connections: list["MCPConnectionPublic"] = Field(default_factory=list)


# ==================== MCP CONNECTION ====================


class MCPConnectionCreate(BaseModel):
    """Request model for creating a per-org connection under a server template."""

    server_id: UUID = Field(..., description="Parent server template UUID")
    organization_id: UUID = Field(
        ..., description="Organization that owns this connection (per-org only)"
    )
    client_id: str = Field(
        ...,
        min_length=1,
        max_length=512,
        description="OAuth client_id this org registered with the vendor",
    )
    encrypted_client_secret: str = Field(
        ...,
        min_length=1,
        description="Encrypted OAuth client_secret (envelope-encrypted at rest)",
    )
    server_url_override: str | None = Field(
        default=None, max_length=2048,
        description="Optional server URL override for regional/sovereign deployments",
    )
    available_in_chat: bool = Field(
        default=False,
        description=(
            "If true, chat users without a personal credential fall back to "
            "the shared service token"
        ),
    )
    available_to_autonomous: bool = Field(
        default=False,
        description=(
            "If true, autonomous (non-chat) agent runs may invoke this "
            "connection's tools using the shared service token"
        ),
    )
    service_oauth_token_id: UUID | None = Field(
        default=None, description="FK to oauth_tokens for the shared service token"
    )


class MCPConnectionUpdate(BaseModel):
    """Request model for updating a per-org connection."""

    client_id: str | None = Field(default=None, min_length=1, max_length=512)
    encrypted_client_secret: str | None = Field(default=None, min_length=1)
    server_url_override: str | None = Field(default=None, max_length=2048)
    available_in_chat: bool | None = Field(default=None)
    available_to_autonomous: bool | None = Field(default=None)
    service_oauth_token_id: UUID | None = Field(default=None)


class MCPConnectionSummary(BaseModel):
    """Lightweight connection summary; omits encrypted secret."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    server_id: UUID
    organization_id: UUID
    client_id: str
    server_url_override: str | None
    available_in_chat: bool
    available_to_autonomous: bool
    service_oauth_token_id: UUID | None
    created_at: datetime
    updated_at: datetime


class MCPConnectionPublic(BaseModel):
    """Detailed connection response; omits encrypted_client_secret."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    server_id: UUID
    organization_id: UUID
    client_id: str
    server_url_override: str | None
    available_in_chat: bool
    available_to_autonomous: bool
    service_oauth_token_id: UUID | None
    created_at: datetime
    updated_at: datetime
    tools: list["MCPConnectionToolPublic"] = Field(default_factory=list)


# ==================== MCP CONNECTION TOOL ====================


class MCPConnectionToolCreate(BaseModel):
    """Request model for creating/upserting a tool catalog row."""

    connection_id: UUID = Field(...)
    tool_name: str = Field(..., min_length=1, max_length=255)
    tool_schema: dict[str, Any] = Field(
        ...,
        description="JSON schema for the tool, as returned by the vendor's tools/list",
    )
    enabled: bool = Field(default=True)
    disabled_reason: str | None = Field(default=None)
    last_seen_at: datetime | None = Field(
        default=None,
        description="When the vendor last published this tool (defaults to now)",
    )


class MCPConnectionToolUpdate(BaseModel):
    """Request model for partial tool catalog updates."""

    tool_schema: dict[str, Any] | None = Field(default=None)
    enabled: bool | None = Field(default=None)
    disabled_reason: str | None = Field(default=None)
    last_seen_at: datetime | None = Field(default=None)


class MCPConnectionToolPublic(BaseModel):
    """Tool catalog response."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    connection_id: UUID
    tool_name: str
    tool_schema: dict[str, Any]
    enabled: bool
    disabled_reason: str | None
    last_seen_at: datetime
    created_at: datetime
    updated_at: datetime


# ==================== USER MCP CREDENTIAL ====================


class UserMCPCredentialCreate(BaseModel):
    """Request model for creating a per-user delegated credential row."""

    user_id: UUID = Field(...)
    connection_id: UUID = Field(...)
    oauth_token_id: UUID = Field(
        ..., description="FK to the oauth_tokens row created by the OAuth callback"
    )
    consent_granted_at: datetime | None = Field(
        default=None,
        description="When the user granted consent (defaults to now if absent)",
    )
    consent_expires_at: datetime | None = Field(
        default=None,
        description="Vendor-stated consent expiration (e.g. M365 90-day offline_access)",
    )
    granted_scopes: list[str] = Field(
        default_factory=list,
        description="Scopes the user agreed to at consent time",
    )


class UserMCPCredentialPublic(BaseModel):
    """Per-user credential response. Does not embed the OAuth tokens."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    connection_id: UUID
    oauth_token_id: UUID
    consent_granted_at: datetime
    consent_expires_at: datetime | None
    granted_scopes: list[str]
    created_at: datetime
    updated_at: datetime


# ==================== Forward-reference resolution ====================

MCPServerPublic.model_rebuild()
MCPConnectionPublic.model_rebuild()
