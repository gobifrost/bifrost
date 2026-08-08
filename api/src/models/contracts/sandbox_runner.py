"""Contracts for configuring external sandbox runners."""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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


class SandboxBuilderMessage(BaseModel):
    """One persisted conversation message restored into a Builder sandbox."""

    role: Literal["user", "assistant", "system", "tool", "tool_call"]
    content: str | None = None
    tool_calls: list[dict[str, object]] | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None


class SandboxBuilderTurnContext(BaseModel):
    """Provider-neutral Agent context for one isolated Builder turn."""

    solution_id: str
    session_id: str
    turn_id: str
    base_revision_id: str
    system_prompt: str
    model: str
    max_iterations: int = Field(ge=1, le=200)
    max_token_budget: int = Field(ge=1)
    system_tools: list[str]
    messages: list[SandboxBuilderMessage]


class SandboxBuilderTurnCompletion(BaseModel):
    """Terminal result reported by an isolated Builder harness."""

    status: Literal["succeeded", "failed", "cancelled"]
    output_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    final_text: str | None = Field(default=None, max_length=100_000)
    error: str | None = Field(default=None, max_length=4_000)
    tool_call_count: int = Field(default=0, ge=0, le=10_000)
    model: str | None = Field(default=None, max_length=100)
    token_count_input: int | None = Field(default=None, ge=0)
    token_count_output: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_terminal_payload(self) -> "SandboxBuilderTurnCompletion":
        if self.status == "succeeded":
            if self.output_sha256 is None:
                raise ValueError("A successful Builder turn requires output_sha256")
            if self.final_text is None:
                raise ValueError("A successful Builder turn requires final_text")
        elif self.status == "failed" and not self.error:
            raise ValueError("A failed Builder turn requires an error")
        return self


class SandboxLLMToolCall(BaseModel):
    id: str = Field(min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=255)
    arguments: dict[str, object] = Field(default_factory=dict)


class SandboxLLMMessage(BaseModel):
    role: Literal["user", "assistant", "system", "tool"]
    content: str | None = Field(default=None, max_length=200_000)
    tool_calls: list[SandboxLLMToolCall] | None = Field(
        default=None,
        max_length=64,
    )
    tool_call_id: str | None = Field(default=None, max_length=255)
    tool_name: str | None = Field(default=None, max_length=255)


class SandboxLLMToolDefinition(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = Field(max_length=4_000)
    parameters: dict[str, object]


class SandboxLLMCompletionRequest(BaseModel):
    messages: list[SandboxLLMMessage] = Field(min_length=1, max_length=500)
    tools: list[SandboxLLMToolDefinition] | None = Field(
        default=None,
        max_length=64,
    )
    max_tokens: int = Field(default=16_384, ge=1, le=65_536)


class SandboxLLMCompletionResponse(BaseModel):
    content: str | None = None
    tool_calls: list[SandboxLLMToolCall] | None = None
    finish_reason: str | None = None
    input_tokens: int
    output_tokens: int
    model: str
