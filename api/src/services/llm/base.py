"""
LLM Provider Base Interface

Abstract base class and data types for LLM providers.
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class ToolDefinition:
    """Tool definition for LLM function calling."""

    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema format


@dataclass
class ToolCallRequest:
    """Tool call requested by the LLM."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class LLMInputFile:
    """Binary user input kept provider-neutral until the Pydantic adapter."""

    filename: str
    media_type: str
    data: bytes


@dataclass
class LLMMessage:
    """
    Message in the conversation.

    Supports multiple roles and optional tool-related fields.
    """

    role: Literal["user", "assistant", "system", "tool"]
    content: str | None = None
    input_files: list[LLMInputFile] = field(default_factory=list)

    # For assistant messages that request tool calls
    tool_calls: list[ToolCallRequest] | None = None

    # For tool result messages
    tool_call_id: str | None = None
    tool_name: str | None = None


@dataclass
class LLMResponse:
    """Response from LLM completion (non-streaming)."""

    content: str | None = None
    tool_calls: list[ToolCallRequest] | None = None
    finish_reason: str | None = None

    # Token usage
    input_tokens: int | None = None
    output_tokens: int | None = None

    # Model info
    model: str | None = None


@dataclass
class LLMStreamChunk:
    """Streaming response chunk from LLM."""

    type: Literal["delta", "tool_call", "done", "error"]

    # For delta chunks (text content)
    content: str | None = None

    # For tool_call chunks
    tool_call: ToolCallRequest | None = None

    # For done chunks
    finish_reason: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None

    # For error chunks
    error: str | None = None


@dataclass
class LLMConfig:
    """Configuration for LLM client."""

    provider: Literal["openai", "anthropic", "google"]
    model: str
    api_key: str
    endpoint: str | None = None
    max_tokens: int = 16384
    # Optional parameters
    extra_params: dict[str, Any] = field(default_factory=dict)


class BaseLLMClient(ABC):
    """
    Abstract base class for LLM providers.

    Implementations must provide both streaming and non-streaming completion methods.
    """

    def __init__(self, config: LLMConfig):
        self.config = config

    @abstractmethod
    async def complete(
        self,
        messages: list[LLMMessage],
        tools: list[ToolDefinition] | None = None,
        *,
        max_tokens: int | None = None,
        model: str | None = None,
    ) -> LLMResponse:
        """
        Non-streaming completion.

        Args:
            messages: Conversation history
            tools: Optional list of tools the model can call
            max_tokens: Override default max tokens
            model: Override default model (must be compatible with configured provider)

        Returns:
            LLMResponse with content and/or tool calls
        """
        ...

    @abstractmethod
    def stream(
        self,
        messages: list[LLMMessage],
        tools: list[ToolDefinition] | None = None,
        *,
        max_tokens: int | None = None,
        model: str | None = None,
    ) -> AsyncGenerator[LLMStreamChunk, None]:
        """
        Streaming completion.

        Args:
            messages: Conversation history
            tools: Optional list of tools the model can call
            max_tokens: Override default max tokens
            model: Override default model (must be compatible with configured provider)

        Yields:
            LLMStreamChunk objects as they arrive
        """
        ...

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return the provider name (e.g., 'openai', 'anthropic')."""
        ...

    @property
    def model_name(self) -> str:
        """Return the model name."""
        return self.config.model
