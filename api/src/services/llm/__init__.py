"""
LLM Provider Abstraction Layer

Provides a unified interface for interacting with different LLM providers
(OpenAI, Anthropic, Google, and compatible endpoints) for AI agent completions.

Usage:
    from src.services.llm import get_llm_client, LLMMessage

    client = await get_llm_client(session)
    response = await client.complete(
        messages=[LLMMessage(role="user", content="Hello!")],
        tools=tool_definitions,
    )
"""

from src.services.llm.base import (
    BaseLLMClient,
    LLMMessage,
    LLMResponse,
    LLMStreamChunk,
    ToolCallRequest,
    ToolDefinition,
)
from src.services.llm.factory import get_llm_client

__all__ = [
    "BaseLLMClient",
    "LLMMessage",
    "LLMResponse",
    "LLMStreamChunk",
    "ToolCallRequest",
    "ToolDefinition",
    "get_llm_client",
]
