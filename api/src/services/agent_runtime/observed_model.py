"""Per-request observability wrapper for the shared agent runtime."""

import hashlib
import math
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Literal

import pydantic_core
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import Model, ModelRequestParameters, StreamedResponse
from pydantic_ai.tools import RunContext
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RequestUsage


@dataclass(frozen=True)
class ModelCallEvent:
    """One model request lifecycle event."""

    type: Literal["request", "response", "error"]
    messages_count: int
    tools_count: int
    duration_ms: int | None = None
    response: ModelResponse | None = None
    error: str | None = None
    context_breakdown: dict[str, int | str] | None = None


ModelCallObserver = Callable[[ModelCallEvent], Awaitable[None]]


class ObservedModel(WrapperModel):
    """Report every provider request while leaving Pydantic AI in control of the loop."""

    def __init__(self, wrapped: Model, observer: ModelCallObserver):
        super().__init__(wrapped)
        self._observer = observer

    @staticmethod
    def _estimated_input_tokens(
        *,
        messages_serialized_bytes: int,
        tool_schema_bytes: int,
        messages_count: int,
        tools_count: int,
    ) -> int:
        """Conservatively estimate tokens when a provider cannot count locally.

        Provider tokenizers differ, especially for OpenAI-compatible endpoints.
        Two serialized bytes per token plus structural overhead intentionally
        overestimates ordinary prose/JSON so the pre-request budget guard fails
        closed instead of allowing an uncounted request through.
        """

        content_tokens = math.ceil(
            (messages_serialized_bytes + tool_schema_bytes) / 2
        )
        return max(1, content_tokens + (messages_count * 8) + (tools_count * 16))

    @staticmethod
    def _context_breakdown(
        messages: list[ModelMessage],
        model_request_parameters: ModelRequestParameters,
    ) -> dict[str, int | str]:
        """Measure request composition without recording prompt contents."""

        categories = {
            "system_prompt_bytes": 0,
            "user_history_bytes": 0,
            "current_user_prompt_bytes": 0,
            "assistant_history_bytes": 0,
            "tool_result_bytes": 0,
            "other_history_bytes": 0,
        }
        last_user_part: tuple[int, int] | None = None
        for message_index, message in enumerate(messages):
            if isinstance(message, ModelRequest):
                for part_index, part in enumerate(message.parts):
                    if isinstance(part, UserPromptPart):
                        last_user_part = (message_index, part_index)

        for message_index, message in enumerate(messages):
            if isinstance(message, ModelResponse):
                categories["assistant_history_bytes"] += len(
                    pydantic_core.to_json(message, fallback=str)
                )
                continue
            if not isinstance(message, ModelRequest):
                categories["other_history_bytes"] += len(
                    pydantic_core.to_json(message, fallback=str)
                )
                continue
            for part_index, part in enumerate(message.parts):
                size = len(pydantic_core.to_json(part, fallback=str))
                if isinstance(part, SystemPromptPart):
                    categories["system_prompt_bytes"] += size
                elif isinstance(part, ToolReturnPart):
                    categories["tool_result_bytes"] += size
                elif isinstance(part, UserPromptPart):
                    key = (
                        "current_user_prompt_bytes"
                        if (message_index, part_index) == last_user_part
                        else "user_history_bytes"
                    )
                    categories[key] += size
                else:
                    categories["other_history_bytes"] += size

        tool_schema = pydantic_core.to_json(
            model_request_parameters.function_tools,
            fallback=str,
        )
        serialized_messages = ModelMessagesTypeAdapter.dump_json(messages)
        result: dict[str, int | str] = {
            **categories,
            "tool_schema_bytes": len(tool_schema),
            "messages_serialized_bytes": len(serialized_messages),
            "tool_schema_sha256": hashlib.sha256(tool_schema).hexdigest(),
        }
        result["estimated_input_tokens"] = ObservedModel._estimated_input_tokens(
            messages_serialized_bytes=len(serialized_messages),
            tool_schema_bytes=len(tool_schema),
            messages_count=len(messages),
            tools_count=len(model_request_parameters.function_tools),
        )
        return result

    async def count_tokens(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> RequestUsage:
        """Use native counting when available, otherwise fail closed locally."""

        try:
            return await self.wrapped.count_tokens(
                messages,
                model_settings,
                model_request_parameters,
            )
        except NotImplementedError:
            breakdown = self._context_breakdown(messages, model_request_parameters)
            return RequestUsage(
                input_tokens=int(breakdown["estimated_input_tokens"]),
            )

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        tools_count = len(model_request_parameters.function_tools)
        context_breakdown = self._context_breakdown(messages, model_request_parameters)
        await self._observer(
            ModelCallEvent(
                type="request",
                messages_count=len(messages),
                tools_count=tools_count,
                context_breakdown=context_breakdown,
            )
        )
        started = time.monotonic()
        try:
            response = await self.wrapped.request(
                messages,
                model_settings,
                model_request_parameters,
            )
        except Exception as exc:
            await self._observer(
                ModelCallEvent(
                    type="error",
                    messages_count=len(messages),
                    tools_count=tools_count,
                    duration_ms=int((time.monotonic() - started) * 1_000),
                    error=str(exc),
                    context_breakdown=context_breakdown,
                )
            )
            raise

        await self._observer(
            ModelCallEvent(
                type="response",
                messages_count=len(messages),
                tools_count=tools_count,
                duration_ms=int((time.monotonic() - started) * 1_000),
                response=response,
                context_breakdown=context_breakdown,
            )
        )
        return response

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: RunContext[object] | None = None,
    ) -> AsyncGenerator[StreamedResponse]:
        """Observe streamed requests after the provider stream is exhausted."""

        tools_count = len(model_request_parameters.function_tools)
        context_breakdown = self._context_breakdown(messages, model_request_parameters)
        await self._observer(
            ModelCallEvent(
                type="request",
                messages_count=len(messages),
                tools_count=tools_count,
                context_breakdown=context_breakdown,
            )
        )
        started = time.monotonic()
        try:
            async with self.wrapped.request_stream(
                messages,
                model_settings,
                model_request_parameters,
                run_context,
            ) as response_stream:
                yield response_stream
            response = response_stream.get()
        except Exception as exc:
            await self._observer(
                ModelCallEvent(
                    type="error",
                    messages_count=len(messages),
                    tools_count=tools_count,
                    duration_ms=int((time.monotonic() - started) * 1_000),
                    error=str(exc),
                    context_breakdown=context_breakdown,
                )
            )
            raise

        await self._observer(
            ModelCallEvent(
                type="response",
                messages_count=len(messages),
                tools_count=tools_count,
                duration_ms=int((time.monotonic() - started) * 1_000),
                response=response,
                context_breakdown=context_breakdown,
            )
        )
