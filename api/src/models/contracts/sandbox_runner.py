"""Contracts for configuring external sandbox runners."""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

SandboxRunnerProvider = Literal["cloudflare", "local"]


DEFAULT_CLOUDFLARE_SCRIPT_NAME = "bifrost-builder-runner"
DEFAULT_CLOUDFLARE_WORKFLOW_NAME = "bifrost-builder-workflow"


class SandboxRunnerCloudflareConfig(BaseModel):
    """Cloudflare-specific runner settings.

    ``api_token`` is write-only. The saved/read response uses ``api_token_set``.
    """

    account_id: str | None = Field(default=None, min_length=1)
    api_token: str | None = Field(default=None, min_length=1)
    script_name: str = Field(
        default=DEFAULT_CLOUDFLARE_SCRIPT_NAME,
        min_length=1,
        max_length=128,
    )
    workflow_name: str = Field(
        default=DEFAULT_CLOUDFLARE_WORKFLOW_NAME,
        min_length=1,
        max_length=64,
    )

    @field_validator("account_id", "api_token", "script_name", "workflow_name", mode="before")
    @classmethod
    def _blank_to_none_or_trim(cls, value: object) -> object:
        if isinstance(value, str):
            trimmed = value.strip()
            return trimmed or None
        return value


class SandboxRunnerCloudflarePublic(BaseModel):
    """Cloudflare runner settings safe to return to browsers."""

    account_id: str | None = None
    api_token_set: bool = False
    script_name: str = DEFAULT_CLOUDFLARE_SCRIPT_NAME
    workflow_name: str = DEFAULT_CLOUDFLARE_WORKFLOW_NAME


class SandboxRunnerLocalConfig(BaseModel):
    """Local runner settings.

    ``runner_secret`` is write-only. The service generates and stores one when a
    local endpoint is first saved without a secret.
    """

    endpoint_url: str | None = Field(default=None, min_length=1)
    runner_secret: str | None = Field(default=None, min_length=1)

    @field_validator("endpoint_url", "runner_secret", mode="before")
    @classmethod
    def _blank_to_none_or_trim(cls, value: object) -> object:
        if isinstance(value, str):
            trimmed = value.strip()
            return trimmed or None
        return value

    @field_validator("endpoint_url")
    @classmethod
    def _validate_endpoint_url(cls, value: str | None) -> str | None:
        return _validate_http_url(value, "endpoint_url")


class SandboxRunnerLocalPublic(BaseModel):
    """Local runner settings safe to return to browsers."""

    endpoint_url: str | None = None
    runner_secret_set: bool = False


class SandboxRunnerConfigSave(BaseModel):
    """Request shape for saving runner configuration."""

    provider: SandboxRunnerProvider
    enabled: bool = False
    callback_base_url: str | None = Field(default=None, min_length=1)
    cloudflare: SandboxRunnerCloudflareConfig | None = None
    local: SandboxRunnerLocalConfig | None = None

    @field_validator("callback_base_url", mode="before")
    @classmethod
    def _trim_callback(cls, value: object) -> object:
        if isinstance(value, str):
            trimmed = value.strip().rstrip("/")
            return trimmed or None
        return value

    @field_validator("callback_base_url")
    @classmethod
    def _validate_callback_url(cls, value: str | None) -> str | None:
        return _validate_http_url(value, "callback_base_url")


def _validate_http_url(value: str | None, field: str) -> str | None:
    if value is None:
        return None
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{field} must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError(f"{field} cannot contain credentials or a fragment")
    return value


class SandboxRunnerConfigPublic(BaseModel):
    """Runner configuration response with secrets masked."""

    provider: SandboxRunnerProvider
    enabled: bool = False
    callback_base_url: str | None = None
    provisioned: bool = False
    connected: bool = False
    cloudflare: SandboxRunnerCloudflarePublic | None = None
    local: SandboxRunnerLocalPublic | None = None


class SandboxRunnerBlocker(BaseModel):
    """One actionable readiness blocker for the admin setup UI."""

    code: str
    message: str
    action: str


class SandboxRunnerReadiness(BaseModel):
    """Readiness facts for enabling the native builder."""

    configured: bool
    ready: bool
    ai_configured: bool
    provider: SandboxRunnerProvider | None = None
    enabled: bool = False
    credentials_configured: bool = False
    callback_configured: bool = False
    provisioned: bool = False
    connected: bool = False
    blockers: list[SandboxRunnerBlocker] = Field(default_factory=list)


class SandboxRunnerStoredConfig(BaseModel):
    """Internal encrypted-at-rest SystemConfig payload."""

    model_config = ConfigDict(extra="ignore")

    provider: SandboxRunnerProvider
    enabled: bool = False
    callback_base_url: str | None = None
    provisioned: bool = False
    connected: bool = False
    cloudflare: dict[str, object] | None = None
    local: dict[str, object] | None = None


class SandboxJobProgressUpdate(BaseModel):
    """Bounded progress reported by one sandbox job attempt."""

    phase: str = Field(min_length=1, max_length=200)
    current: int = Field(default=0, ge=0)
    total: int | None = Field(default=None, ge=0)
    percent: float | None = Field(default=None, ge=0, le=100)


class SandboxJobCancelled(BaseModel):
    cancelled: bool
