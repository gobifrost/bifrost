"""Database-free wire contracts shared by Bifrost and isolated build runners."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


SandboxUsageScope = Literal["platform", "organization", "user", "solution"]
SandboxUsagePeriod = Literal["daily", "monthly"]


class SandboxRuntimeUsageCeilings(BaseModel):
    """Provider-neutral usage ceilings understood by isolated runners."""

    model_config = ConfigDict(extra="forbid")

    model_requests: int | None = Field(default=None, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cache_read_tokens: int | None = Field(default=None, ge=0)
    cache_write_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    runner_duration_ms: int | None = Field(default=None, ge=0)
    sandbox_compute_ms: int | None = Field(default=None, ge=0)


class SandboxRuntimeUsageSnapshot(BaseModel):
    """Aggregate usage already consumed for one scope/period."""

    model_config = ConfigDict(extra="forbid")

    model_requests: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cache_read_tokens: int = Field(default=0, ge=0)
    cache_write_tokens: int = Field(default=0, ge=0)
    runner_duration_ms: int = Field(default=0, ge=0)
    sandbox_compute_ms: int = Field(default=0, ge=0)


class SandboxRuntimeUsagePolicySnapshot(BaseModel):
    """One usage policy projected into the DB-free runner envelope."""

    model_config = ConfigDict(extra="forbid")

    scope: SandboxUsageScope
    per_run: SandboxRuntimeUsageCeilings = Field(
        default_factory=SandboxRuntimeUsageCeilings
    )
    aggregate: SandboxRuntimeUsageCeilings = Field(
        default_factory=SandboxRuntimeUsageCeilings
    )
    aggregate_period: SandboxUsagePeriod = "monthly"


class SandboxRuntimeUsageAggregateSnapshot(BaseModel):
    """Usage already recorded for one scope and aggregate period."""

    model_config = ConfigDict(extra="forbid")

    scope: SandboxUsageScope
    period: SandboxUsagePeriod
    usage: SandboxRuntimeUsageSnapshot = Field(
        default_factory=SandboxRuntimeUsageSnapshot
    )


class SandboxRuntimeUsageGovernanceSnapshot(BaseModel):
    """Strict runtime-governance envelope for isolated Builder turns."""

    model_config = ConfigDict(extra="forbid")

    policies: list[SandboxRuntimeUsagePolicySnapshot] = Field(
        default_factory=list,
        max_length=16,
    )
    aggregate_usage: list[SandboxRuntimeUsageAggregateSnapshot] = Field(
        default_factory=list,
        max_length=64,
    )


class SandboxBuilderAttachment(BaseModel):
    """A conversation attachment available through the job capability."""

    id: UUID
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(ge=0)
    extracted_text: str | None = None
    binary_model_input: bool = False


class SandboxBuilderMessage(BaseModel):
    """One persisted conversation message restored into a Builder sandbox."""

    id: UUID
    sequence: int = Field(ge=1)
    role: Literal["user", "assistant", "system", "tool", "tool_call"]
    content: str | None = None
    tool_calls: list[dict[str, object]] | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_input: dict[str, Any] | None = None
    attachments: list[SandboxBuilderAttachment] = Field(default_factory=list)


class SandboxBuilderModelConfig(BaseModel):
    """Decrypted model settings delivered only to one fenced runner attempt."""

    provider: Literal["openai", "anthropic", "google"]
    model: str = Field(min_length=1, max_length=500)
    api_key: str = Field(min_length=1)
    endpoint: str | None = None
    max_tokens: int = Field(ge=1, le=200_000)
    extra_params: dict[str, Any] = Field(default_factory=dict)


class SandboxBuilderToolDefinition(BaseModel):
    """One resolved tool and the process trusted to execute it."""

    name: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=20_000)
    parameters: dict[str, Any] = Field(default_factory=dict)
    execution: Literal["sandbox", "bifrost"]


class SandboxBuilderTurnContext(BaseModel):
    """Provider-neutral Agent context for one isolated Builder turn."""

    solution_id: str
    session_id: str
    turn_id: str
    conversation_id: str
    user_message_id: UUID
    assistant_message_id: UUID
    base_revision_id: str
    system_prompt: str
    bundle_path: str | None = None
    llm_config: SandboxBuilderModelConfig
    max_iterations: int = Field(ge=0, le=200)
    max_token_budget: int = Field(ge=0)
    runtime_governance: SandboxRuntimeUsageGovernanceSnapshot | None = None
    tools: list[SandboxBuilderToolDefinition]
    messages: list[SandboxBuilderMessage]


class SandboxBuilderToolStart(BaseModel):
    """Start one model-requested tool under the durable conversation."""

    tool_call_id: str = Field(min_length=1, max_length=500)
    name: str = Field(min_length=1, max_length=255)
    arguments: dict[str, Any] = Field(default_factory=dict)


class SandboxBuilderToolFinish(BaseModel):
    """Complete one sandbox-executed tool call."""

    message_id: UUID
    execution_id: str = Field(min_length=1, max_length=255)
    result: Any = None
    error: str | None = Field(default=None, max_length=20_000)
    duration_ms: int | None = Field(default=None, ge=0)


class SandboxBuilderToolResponse(BaseModel):
    """Server-side persistence and execution result for a runtime tool."""

    execution: Literal["sandbox", "bifrost"]
    message_id: UUID
    execution_id: str
    model_content: str | None = None
    result: Any = None
    error: str | None = None
    duration_ms: int | None = None


class SandboxBuilderWorkspaceBuildRequest(BaseModel):
    """Identify one staged workspace snapshot for a production build check."""

    model_config = ConfigDict(extra="forbid")

    message_id: UUID
    output_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class SandboxBuilderWorkspaceBuildResult(BaseModel):
    """Model-visible result of compiling an isolated Builder workspace."""

    model_config = ConfigDict(extra="forbid")

    content: str
    structured_content: dict[str, Any] | None = None


__all__ = [
    "SandboxBuilderAttachment",
    "SandboxBuilderMessage",
    "SandboxBuilderModelConfig",
    "SandboxRuntimeUsageAggregateSnapshot",
    "SandboxRuntimeUsageCeilings",
    "SandboxRuntimeUsageGovernanceSnapshot",
    "SandboxRuntimeUsagePolicySnapshot",
    "SandboxRuntimeUsageSnapshot",
    "SandboxBuilderToolDefinition",
    "SandboxBuilderToolFinish",
    "SandboxBuilderToolResponse",
    "SandboxBuilderToolStart",
    "SandboxBuilderTurnContext",
    "SandboxBuilderWorkspaceBuildRequest",
    "SandboxBuilderWorkspaceBuildResult",
]
