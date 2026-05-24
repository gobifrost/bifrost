"""Shared Pydantic models used by API routers and clients."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, RootModel


class VersionResponse(BaseModel):
    version: str


class CodexGatewayResponsesRequest(RootModel[dict[str, Any]]):
    """OpenAI-compatible Responses API request payload."""


class CodexGatewayKeyCreateRequest(BaseModel):
    """Request to create a downstream Codex Gateway key."""

    name: str = Field(min_length=1, max_length=255)
    project_id: UUID | None = None
    allowed_models: list[str] = Field(default_factory=list)
    denied_models: list[str] = Field(default_factory=list)
    daily_limit: int | None = Field(default=None, ge=1)
    monthly_limit: int | None = Field(default=None, ge=1)


class CodexGatewayKeyRecord(BaseModel):
    """Gateway key metadata safe to return to clients."""

    id: UUID
    user_id: UUID
    project_id: UUID | None = None
    name: str
    allowed_models: list[str] = Field(default_factory=list)
    denied_models: list[str] = Field(default_factory=list)
    daily_limit: int | None = None
    monthly_limit: int | None = None
    status: str
    created_at: datetime | None = None
    revoked_at: datetime | None = None
    last_used_at: datetime | None = None


class CodexGatewayKeyCreateResponse(BaseModel):
    """Created gateway key plus one-time plaintext key material."""

    record: CodexGatewayKeyRecord
    key: str


class CodexGatewayKeyListResponse(BaseModel):
    """List of gateway keys without plaintext or hashes."""

    items: list[CodexGatewayKeyRecord]


class CodexGatewayOAuthAccountRecord(BaseModel):
    """Connected upstream ChatGPT/Codex account metadata safe for clients."""

    id: UUID
    user_id: UUID
    provider: str = "chatgpt_codex"
    upstream_subject: str
    upstream_email: str | None = None
    upstream_workspace_id: str | None = None
    access_token_expires_at: datetime | None = None
    scopes: list[str] = Field(default_factory=list)
    last_refresh_at: datetime | None = None
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None


class CodexGatewayOAuthStatusResponse(BaseModel):
    """Current user's upstream Codex OAuth connection status."""

    connected: bool
    provider: str = "chatgpt_codex"
    account: CodexGatewayOAuthAccountRecord | None = None
    supported_connect_methods: list[str] = Field(
        default_factory=lambda: ["device_code", "auth_cache_import"]
    )


class CodexGatewayOAuthConnectResponse(BaseModel):
    """Instructions for starting the supported Codex OAuth onboarding path."""

    provider: str = "chatgpt_codex"
    preferred_method: str = "device_code"
    device_code_enabled: bool = True
    client_command: str = "codex login --device-auth"
    fallback_import_endpoint: str = "/api/codex-gateway/oauth/import-auth-cache"


class CodexGatewayOAuthImportRequest(BaseModel):
    """Request to import a user's own Codex auth cache into Bifrost's vault."""

    auth_cache: dict[str, Any]


class CodexGatewayOAuthImportResponse(BaseModel):
    """Result of importing a user's own Codex auth cache."""

    connected: bool
    provider: str = "chatgpt_codex"
    account: CodexGatewayOAuthAccountRecord


class CodexGatewayOAuthDisconnectResponse(BaseModel):
    """Result of disconnecting a user's upstream Codex OAuth account."""

    connected: bool = False
    revoked: bool
