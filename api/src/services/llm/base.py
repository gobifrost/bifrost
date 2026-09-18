"""
LLM Provider Base Interface

Abstract base class and data types for LLM providers.
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID


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
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    provider_cost: Decimal | None = None

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
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    provider_cost: Decimal | None = None

    # For error chunks
    error: str | None = None


ANTHROPIC_REQUIRED_MAX_TOKENS = 16_384

# No generic harness defaults: when neither the agent nor its model profile
# sets an explicit limit, the provider default applies (None) — except for
# the two narrow cases below. Per agreement: explicit Agent.llm_max_tokens
# wins, then Profile.default_max_tokens, then Anthropic's required 16384
# (the API mandates a value), then the DeepSeek-family guard. Everything
# else resolves in exactly one place (via request_max_tokens).

DEEPSEEK_FAMILY_MAX_TOKENS = 8_000
"""Heuristic safety-net cap for the DeepSeek model family.

DeepSeek (via OpenRouter or direct) rejects unbounded/high output requests
more aggressively than OpenAI/Anthropic, and its API only accepts the legacy
``max_tokens`` wire field. This guard fires only when neither the agent nor
the profile sets a value — explicit values always win.
"""


@dataclass
class LLMConfig:
    """Configuration for LLM client."""

    provider: Literal["openai", "anthropic", "google"]
    model: str
    api_key: str
    endpoint: str | None = None
    openai_transport: Literal["responses", "chat_completions"] | None = None
    provider_connection_id: UUID | None = None
    anthropic_prompt_cache_supported: bool | None = None
    # Profile-level default output cap (AIModelProfile.default_max_tokens).
    # Populated by AIModelService.resolve_config; loses to an explicit
    # per-request override (Agent.llm_max_tokens).
    default_max_tokens: int | None = None
    # Optional parameters
    extra_params: dict[str, Any] = field(default_factory=dict)


def is_deepseek_family(config: LLMConfig) -> bool:
    """Return True when the resolved model or endpoint belongs to DeepSeek.

    Normalized match: model ``startswith`` ``deepseek/`` (OpenRouter namespaced
    form, including the ``~`` latest-alias marker) or ``deepseek-`` (direct
    form such as ``deepseek-chat``/``deepseek-reasoner``), or the endpoint URL
    contains ``deepseek`` (direct/self-hosted DeepSeek-compatible servers).
    """
    model = (config.model or "").strip().lower().removeprefix("~")
    endpoint = (config.endpoint or "").lower()
    return (
        model.startswith("deepseek/")
        or model.startswith("deepseek-")
        or "deepseek" in endpoint
    )


def request_max_tokens(
    config: LLMConfig,
    override: int | None,
    *,
    agent_kind: str | None = None,
) -> int | None:
    """Resolve the per-request output limit.

    Precedence: explicit agent override wins > profile default >
    Anthropic required / DeepSeek guard > provider default (None).

    - ``override``: Agent.llm_max_tokens for the running agent.
    - ``config.default_max_tokens``: the resolved model profile's
      default_max_tokens (None when the profile sets none).
    - ``agent_kind``: accepted for backward compatibility but ignored —
      there are no generic per-kind fallbacks. Callers may stop passing it.
    - DeepSeek family guard (heuristic safety net): caps at 8000, and fires
      only when both the agent override and the profile default are null.
    - Anthropic path (16384) unless agent/profile sets otherwise.
    """
    del agent_kind
    if override is not None:
        return override
    if config.default_max_tokens is not None:
        return config.default_max_tokens
    if config.provider == "anthropic":
        return ANTHROPIC_REQUIRED_MAX_TOKENS
    if is_deepseek_family(config):
        return DEEPSEEK_FAMILY_MAX_TOKENS
    return None


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
        require_tool_call: bool = False,
    ) -> LLMResponse:
        """
        Non-streaming completion.

        Args:
            messages: Conversation history
            tools: Optional list of tools the model can call
            max_tokens: Override default max tokens
            model: Override default model (must be compatible with configured provider)
            require_tool_call: Reject text-only output so the provider must call a tool

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
