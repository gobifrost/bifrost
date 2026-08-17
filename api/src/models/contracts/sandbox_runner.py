"""Contracts for configuring external sandbox runners."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from shared.sandbox_runner_protocol import (
    SandboxBuilderAttachment as SandboxBuilderAttachment,
    SandboxBuilderMessage as SandboxBuilderMessage,
    SandboxBuilderModelConfig as SandboxBuilderModelConfig,
    SandboxBuilderToolDefinition as SandboxBuilderToolDefinition,
    SandboxBuilderToolFinish as SandboxBuilderToolFinish,
    SandboxBuilderToolResponse as SandboxBuilderToolResponse,
    SandboxBuilderToolStart as SandboxBuilderToolStart,
    SandboxBuilderTurnContext as SandboxBuilderTurnContext,
)

SandboxRunnerProvider = Literal["cloudflare", "local"]


DEFAULT_CLOUDFLARE_SCRIPT_NAME = "bifrost-build"
DEFAULT_CLOUDFLARE_WORKFLOW_NAME = "bifrost-build-workflow"


class SandboxRunnerCloudflareConfig(BaseModel):
    """Cloudflare-specific runner settings.

    ``api_token`` is write-only. The saved/read response uses ``api_token_set``.
    """

    account_id: str | None = Field(default=None, min_length=1)
    api_token: str | None = Field(default=None, min_length=1)

    @field_validator("account_id", "api_token", mode="before")
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
    """Local execution uses the existing Bifrost worker and needs no settings."""


class SandboxRunnerLocalPublic(BaseModel):
    """Local runner settings safe to return to browsers."""

    uses_existing_worker: bool = True


class SandboxRunnerConfigSave(BaseModel):
    """Request shape for saving runner configuration."""

    provider: SandboxRunnerProvider
    enabled: bool = False
    callback_base_url: str | None = Field(default=None, min_length=1)
    cloudflare: SandboxRunnerCloudflareConfig | None = None
    local: SandboxRunnerLocalConfig | None = None

    @model_validator(mode="after")
    def validate_provider_specific_callback(self) -> "SandboxRunnerConfigSave":
        if self.provider == "cloudflare" and self.callback_base_url is not None:
            raise ValueError(
                "callback_base_url is determined automatically for Cloudflare"
            )
        return self

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


class SandboxRunnerSetupState(BaseModel):
    """Complete admin setup state without provider secrets."""

    config: SandboxRunnerConfigPublic | None = None
    readiness: SandboxRunnerReadiness
    recommended_callback_base_url: str
    runner_image: str
    active_provisioning_job_id: UUID | None = None
    cloudflare_permissions: list[str] = Field(
        default_factory=lambda: [
            "Workers Scripts Write",
            "Workers Containers Write",
        ]
    )


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


class SandboxBuilderEventBatch(BaseModel):
    """A bounded batch of shared Chat stream events from a remote runtime."""

    events: list[dict[str, Any]] = Field(min_length=1, max_length=100)


class SandboxBuilderTextSegment(BaseModel):
    """Assistant text completed immediately before a tool call."""

    content: str = Field(min_length=1, max_length=100_000)


class SandboxHarnessToolDiagnostic(BaseModel):
    """Privacy-safe aggregate for one harness tool name."""

    name: str = Field(min_length=1, max_length=100)
    count: int = Field(ge=1, le=10_000)
    error_count: int = Field(default=0, ge=0, le=10_000)


class SandboxHarnessDiagnostics(BaseModel):
    """Bounded aggregate diagnostics with no prompts, paths, inputs, or outputs."""

    message_count: int = Field(ge=0, le=10_000)
    assistant_message_count: int = Field(ge=0, le=10_000)
    tool_call_count: int = Field(ge=0, le=10_000)
    tool_error_count: int = Field(ge=0, le=10_000)
    other_tool_call_count: int = Field(default=0, ge=0, le=10_000)
    compaction_count: int = Field(default=0, ge=0, le=10_000)
    retry_count: int = Field(default=0, ge=0, le=10_000)
    truncated: bool = False
    tools: list[SandboxHarnessToolDiagnostic] = Field(default_factory=list, max_length=32)


class SandboxBuilderTurnCompletion(BaseModel):
    """Terminal result reported by an isolated Builder harness."""

    status: Literal["succeeded", "failed", "cancelled"]
    output_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    final_text: str | None = Field(default=None, max_length=100_000)
    error: str | None = Field(default=None, max_length=4_000)
    retryable: bool = False
    tool_call_count: int = Field(default=0, ge=0, le=10_000)
    model_request_count: int = Field(default=0, ge=0, le=10_000)
    model: str | None = Field(default=None, max_length=100)
    provider: str | None = Field(default=None, max_length=100)
    token_count_input: int | None = Field(default=None, ge=0)
    token_count_output: int | None = Field(default=None, ge=0)
    cache_read_tokens: int = Field(default=0, ge=0)
    cache_write_tokens: int = Field(default=0, ge=0)
    provider_cost: Decimal | None = Field(default=None, ge=0)
    duration_ms: int | None = Field(default=None, ge=0)
    assistant_message_id: UUID | None = None
    harness_diagnostics: SandboxHarnessDiagnostics | None = None
    checkpoint_output_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )

    @model_validator(mode="after")
    def validate_terminal_payload(self) -> "SandboxBuilderTurnCompletion":
        if self.status == "succeeded":
            if self.output_sha256 is None:
                raise ValueError("A successful Builder turn requires output_sha256")
            if self.final_text is None:
                raise ValueError("A successful Builder turn requires final_text")
        elif self.status == "failed" and not self.error:
            raise ValueError("A failed Builder turn requires an error")
        if self.retryable and self.status != "failed":
            raise ValueError("Only failed Builder turns can be retryable")
        if self.checkpoint_output_sha256 is not None and self.status == "succeeded":
            raise ValueError("Successful Builder turns do not use failure checkpoints")
        return self
