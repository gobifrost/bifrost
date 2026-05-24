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
