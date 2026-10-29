"""Small shared request and response models used outside a single API domain."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class BrowserImpersonationTokenResponse(BaseModel):
    """A short-lived bearer credential for one browser impersonation tab."""
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: Literal[1800] = 1800


class SolutionDevContextRequest(BaseModel):
    """The selected portable app identity for a local Solution preview."""

    app_id: UUID


class SolutionDevContextResponse(BaseModel):
    """Short-lived, app-bound context accepted only as a supplemental header."""

    token: str
    expires_at: float
    solution_id: UUID
    organization_id: UUID | None
    app_id: UUID


class RuntimeLogRequest(BaseModel):
    """A line for the authenticated runtime's own log stream."""

    model_config = ConfigDict(extra="forbid")
    level: str = Field(min_length=1, max_length=32)
    message: str = Field(max_length=65536)
    metadata: dict[str, Any] | None = None
    timestamp: datetime | None = None


class RuntimeLogResponse(BaseModel):
    entry_id: str | None


class ServiceControlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ready: bool = False
    token_expires_at: AwareDatetime


class ServiceControlResponse(BaseModel):
    stop_requested: bool
    access_token: str | None = None
    token_expires_at: datetime | None = None


class ProviderCacheReadRequest(BaseModel):
    """Parameters for the authenticated runtime's own data-provider cache read."""

    model_config = ConfigDict(extra="forbid")
    parameters: dict[str, Any] = Field(default_factory=dict)


class ProviderCacheReadResponse(BaseModel):
    hit: bool
    result: Any | None = None
    expires_at: datetime | None = None


class ProviderCacheWriteRequest(BaseModel):
    """Result for the authenticated runtime's own data-provider cache entry."""

    model_config = ConfigDict(extra="forbid")
    parameters: dict[str, Any] = Field(default_factory=dict)
    result: Any


class ProviderCacheWriteResponse(BaseModel):
    expires_at: datetime | None = None


class DeviceAuthorizeRequest(BaseModel):
    """The code for a CLI device login being approved or denied."""

    user_code: str
