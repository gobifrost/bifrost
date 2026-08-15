"""Compatibility adapter from Bifrost's public LLM contract to Pydantic AI.

The SDK, chat service, summarizer, and tuning flows still consume the stable
``BaseLLMClient`` interface. This adapter moves their provider transport onto
Pydantic AI without changing those callers while the higher-level chat loop is
cut over separately.
"""

import logging
from collections.abc import AsyncGenerator

from pydantic_ai.direct import model_request_stream
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    PartDeltaEvent,
    PartStartEvent,
    SystemPromptPart,
    TextPart,
    TextPartDelta,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import ToolDefinition as PydanticToolDefinition

from src.services.agent_runtime.model_factory import create_agent_model
from src.services.llm.base import (
    BaseLLMClient,
    LLMMessage,
    LLMResponse,
    LLMStreamChunk,
    ToolCallRequest,
    ToolDefinition,
)

logger = logging.getLogger(__name__)


class PydanticAIClient(BaseLLMClient):
    """Provider-neutral implementation of Bifrost's existing LLM contract."""

    @property
    def provider_name(self) -> str:
        return self.config.provider

    async def complete(
        self,
        messages: list[LLMMessage],
        tools: list[ToolDefinition] | None = None,
        *,
        max_tokens: int | None = None,
        model: str | None = None,
    ) -> LLMResponse:
        # Consume the provider's streaming transport and return one assembled
        # response. Anthropic rejects high-output non-streaming requests that
        # could exceed ten minutes, so this preserves the legacy contract
        # without leaking provider-specific behavior into callers.
        async with model_request_stream(
            create_agent_model(self.config, model=model),
            self.convert_messages(messages),
            model_settings=self._model_settings(max_tokens),
            model_request_parameters=self._request_parameters(tools),
        ) as stream:
            async for _ in stream:
                pass
            response = stream.get()
        return self._convert_response(response)

    async def stream(
        self,
        messages: list[LLMMessage],
        tools: list[ToolDefinition] | None = None,
        *,
        max_tokens: int | None = None,
        model: str | None = None,
    ) -> AsyncGenerator[LLMStreamChunk, None]:
        try:
            async with model_request_stream(
                create_agent_model(self.config, model=model),
                self.convert_messages(messages),
                model_settings=self._model_settings(max_tokens),
                model_request_parameters=self._request_parameters(tools),
            ) as stream:
                async for event in stream:
                    if isinstance(event, PartStartEvent) and isinstance(event.part, TextPart):
                        if event.part.content:
                            yield LLMStreamChunk(type="delta", content=event.part.content)
                    elif isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
                        if event.delta.content_delta:
                            yield LLMStreamChunk(type="delta", content=event.delta.content_delta)

                response = stream.get()
                for tool_call in response.tool_calls:
                    yield LLMStreamChunk(
                        type="tool_call",
                        tool_call=ToolCallRequest(
                            id=tool_call.tool_call_id,
                            name=tool_call.tool_name,
                            arguments=tool_call.args_as_dict(),
                        ),
                    )
                yield LLMStreamChunk(
                    type="done",
                    finish_reason=response.finish_reason,
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                )
        except Exception as exc:
            logger.error("Pydantic AI streaming error: %s", exc)
            yield LLMStreamChunk(type="error", error=str(exc))

    def _model_settings(self, max_tokens: int | None) -> ModelSettings:
        return ModelSettings(max_tokens=max_tokens or self.config.max_tokens)

    @staticmethod
    def _request_parameters(
        tools: list[ToolDefinition] | None,
    ) -> ModelRequestParameters:
        return ModelRequestParameters(
            function_tools=[
                PydanticToolDefinition(
                    name=tool.name,
                    description=tool.description,
                    parameters_json_schema=tool.parameters,
                    sequential=True,
                )
                for tool in tools or []
            ]
        )

    @staticmethod
    def convert_messages(messages: list[LLMMessage]) -> list[ModelMessage]:
        """Convert Bifrost's stable message DTOs to provider-neutral history."""
        converted: list[ModelMessage] = []
        for message in messages:
            if message.role == "assistant":
                parts = []
                if message.content:
                    parts.append(TextPart(message.content))
                parts.extend(
                    ToolCallPart(
                        tool_name=call.name,
                        args=call.arguments,
                        tool_call_id=call.id,
                    )
                    for call in message.tool_calls or []
                )
                if parts:
                    converted.append(ModelResponse(parts=parts))
                continue

            if message.role == "system":
                request_parts = [SystemPromptPart(message.content or "")]
            elif message.role == "tool":
                tool_return = ToolReturnPart(
                    tool_name=message.tool_name or "tool",
                    content=message.content or "",
                    tool_call_id=message.tool_call_id or "unknown-tool-call",
                )
                if (
                    converted
                    and isinstance(converted[-1], ModelRequest)
                    and converted[-1].parts
                    and all(isinstance(part, ToolReturnPart) for part in converted[-1].parts)
                ):
                    converted[-1].parts.append(tool_return)
                    continue
                request_parts = [tool_return]
            else:
                request_parts = [UserPromptPart(message.content or "")]
            converted.append(ModelRequest(parts=request_parts))
        return converted

    @staticmethod
    def _convert_response(response: ModelResponse) -> LLMResponse:
        return LLMResponse(
            content=response.text or None,
            tool_calls=[
                ToolCallRequest(
                    id=call.tool_call_id,
                    name=call.tool_name,
                    arguments=call.args_as_dict(),
                )
                for call in response.tool_calls
            ]
            or None,
            finish_reason=response.finish_reason,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            model=response.model_name,
        )
