"""
Anthropic LLM Client

Implementation of the LLM interface for Anthropic's Claude API.
"""

import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

from anthropic import AsyncAnthropic
from anthropic.types import (
    ContentBlockParam,
    MessageParam,
    TextBlockParam,
    ToolParam,
    ToolResultBlockParam,
    ToolUseBlockParam,
)

from src.services.llm.base import (
    BaseLLMClient,
    LLMConfig,
    LLMMessage,
    LLMResponse,
    LLMStreamChunk,
    ToolCallRequest,
    ToolDefinition,
)

logger = logging.getLogger(__name__)


class AnthropicClient(BaseLLMClient):
    """Anthropic Claude LLM client implementation."""

    def __init__(self, config: LLMConfig):
        super().__init__(config)
        self.client = AsyncAnthropic(api_key=config.api_key, base_url=config.endpoint or None)

    @property
    def provider_name(self) -> str:
        return "anthropic"

    async def complete(
        self,
        messages: list[LLMMessage],
        tools: list[ToolDefinition] | None = None,
        *,
        max_tokens: int | None = None,
        model: str | None = None,
    ) -> LLMResponse:
        """Non-streaming completion via Anthropic API.

        Uses ``messages.stream()`` + ``get_final_message()`` under the hood
        rather than ``messages.create()``: the caller still receives one fully
        assembled ``LLMResponse``, but streaming avoids the Anthropic SDK's
        client-side guard that refuses a *non-streaming* request whenever
        ``max_tokens`` is large enough to risk a >10-minute completion
        ("Streaming is required for operations that may take longer than 10
        minutes"). That guard fires at ``max_tokens`` above ~21k, which breaks
        run summarization whenever the admin-configured ``LLMConfig.max_tokens``
        is set that high, since the summarizer inherits it.
        """
        # Extract system message and convert rest
        system_prompt, anthropic_messages = self._convert_messages(messages)
        anthropic_tools = self._convert_tools(tools) if tools else None

        kwargs: dict[str, Any] = {
            "model": model or self.config.model,
            "messages": anthropic_messages,
            "max_tokens": max_tokens or self.config.max_tokens,  # Anthropic requires this param
        }

        if system_prompt:
            kwargs["system"] = system_prompt
        if anthropic_tools:
            kwargs["tools"] = anthropic_tools

        async with self.client.messages.stream(**kwargs) as stream:
            response = await stream.get_final_message()

        # Extract content and tool calls
        content_parts: list[str] = []
        tool_calls: list[ToolCallRequest] = []

        for block in response.content:
            if block.type == "text":
                content_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCallRequest(
                        id=block.id,
                        name=block.name,
                        arguments=block.input if isinstance(block.input, dict) else {},
                    )
                )

        return LLMResponse(
            content="\n".join(content_parts) if content_parts else None,
            tool_calls=tool_calls if tool_calls else None,
            finish_reason=response.stop_reason,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            model=response.model,
        )

    async def stream(
        self,
        messages: list[LLMMessage],
        tools: list[ToolDefinition] | None = None,
        *,
        max_tokens: int | None = None,
        model: str | None = None,
    ) -> AsyncGenerator[LLMStreamChunk, None]:
        """Streaming completion via Anthropic API."""
        system_prompt, anthropic_messages = self._convert_messages(messages)
        anthropic_tools = self._convert_tools(tools) if tools else None

        kwargs: dict[str, Any] = {
            "model": model or self.config.model,
            "messages": anthropic_messages,
            "max_tokens": max_tokens or self.config.max_tokens,  # Anthropic requires this param
        }

        if system_prompt:
            kwargs["system"] = system_prompt
        if anthropic_tools:
            kwargs["tools"] = anthropic_tools

        # Track current tool use block being built
        current_tool: dict[str, Any] | None = None
        input_tokens = 0
        output_tokens = 0

        try:
            async with self.client.messages.stream(**kwargs) as stream:
                async for event in stream:
                    # Handle message start (contains input token count)
                    if event.type == "message_start":
                        if event.message.usage:
                            input_tokens = event.message.usage.input_tokens

                    # Handle content block start
                    elif event.type == "content_block_start":
                        if event.content_block.type == "tool_use":
                            current_tool = {
                                "id": event.content_block.id,
                                "name": event.content_block.name,
                                "input_json": "",
                            }

                    # Handle content block delta
                    elif event.type == "content_block_delta":
                        if event.delta.type == "text_delta":
                            yield LLMStreamChunk(
                                type="delta",
                                content=event.delta.text,
                            )
                        elif event.delta.type == "input_json_delta":
                            if current_tool:
                                current_tool["input_json"] += event.delta.partial_json

                    # Handle content block stop
                    elif event.type == "content_block_stop":
                        if current_tool:
                            try:
                                args = json.loads(current_tool["input_json"]) if current_tool["input_json"] else {}
                            except json.JSONDecodeError:
                                args = {}
                                logger.warning(f"Failed to parse tool input: {current_tool['input_json']}")

                            yield LLMStreamChunk(
                                type="tool_call",
                                tool_call=ToolCallRequest(
                                    id=current_tool["id"],
                                    name=current_tool["name"],
                                    arguments=args,
                                ),
                            )
                            current_tool = None

                    # Handle message delta (contains output token count)
                    elif event.type == "message_delta":
                        if event.usage:
                            output_tokens = event.usage.output_tokens

                        # Message is done
                        yield LLMStreamChunk(
                            type="done",
                            finish_reason=event.delta.stop_reason,
                            input_tokens=input_tokens,
                            output_tokens=output_tokens,
                        )

        except Exception as e:
            logger.error(f"Anthropic streaming error: {e}")
            yield LLMStreamChunk(
                type="error",
                error=str(e),
            )

    def _convert_messages(
        self, messages: list[LLMMessage]
    ) -> tuple[str | None, list[MessageParam]]:
        """
        Convert LLMMessage list to Anthropic format.

        Anthropic has system prompt separate from messages, so we extract it.

        Returns:
            Tuple of (system_prompt, messages)
        """
        system_prompt: str | None = None
        result: list[MessageParam] = []

        for msg in messages:
            if msg.role == "system":
                # Anthropic system prompt is separate
                system_prompt = msg.content

            elif msg.role == "user":
                result.append({
                    "role": "user",
                    "content": msg.content or "",
                })

            elif msg.role == "assistant":
                # Build content blocks
                content: list[ContentBlockParam] = []

                if msg.content:
                    content.append(TextBlockParam(type="text", text=msg.content))

                if msg.tool_calls:
                    for tc in msg.tool_calls:
                        content.append(
                            ToolUseBlockParam(
                                type="tool_use",
                                id=tc.id,
                                name=tc.name,
                                input=tc.arguments,
                            )
                        )

                result.append({
                    "role": "assistant",
                    "content": content if content else "",
                })

            elif msg.role == "tool":
                # Anthropic expects tool results as user messages with tool_result content.
                # Multiple consecutive tool results must be in a SINGLE user message,
                # not separate ones — otherwise the API rejects with "unexpected tool_use_id".
                tool_result_block = ToolResultBlockParam(
                    type="tool_result",
                    tool_use_id=msg.tool_call_id or "",
                    content=msg.content or "",
                )
                # Merge into previous user message if it already has tool_result blocks
                if (
                    result
                    and result[-1]["role"] == "user"
                    and isinstance(result[-1]["content"], list)
                ):
                    result[-1]["content"].append(tool_result_block)  # type: ignore[union-attr]
                else:
                    result.append({
                        "role": "user",
                        "content": [tool_result_block],
                    })

        return system_prompt, result

    def _convert_tools(self, tools: list[ToolDefinition]) -> list[ToolParam]:
        """Convert ToolDefinition list to Anthropic format."""
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.parameters,
            }
            for tool in tools
        ]
