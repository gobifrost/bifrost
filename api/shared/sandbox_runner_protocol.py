"""Database-free wire contracts shared by Bifrost and isolated build runners."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


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
    max_iterations: int = Field(ge=1, le=200)
    max_token_budget: int = Field(ge=1)
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


__all__ = [
    "SandboxBuilderAttachment",
    "SandboxBuilderMessage",
    "SandboxBuilderModelConfig",
    "SandboxBuilderToolDefinition",
    "SandboxBuilderToolFinish",
    "SandboxBuilderToolResponse",
    "SandboxBuilderToolStart",
    "SandboxBuilderTurnContext",
]
