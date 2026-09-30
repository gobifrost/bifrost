"""Context governance follows the model's real window, not a fixed constant."""

import pytest
from pydantic_ai import Agent as PydanticAgent
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RequestUsage

from src.services.agent_runtime import AgentRunBudget, build_runtime_capabilities

# Provider-reported input of a production ticket run's first request: system
# prompt, tool catalog, and ticket. A fixed 24k context target flagged every
# later request of that run as over-limit.
FIRST_REQUEST_INPUT_TOKENS = 27_000
TOOL_RESULT = "ticket details " * 50


async def _second_request(context_window: int) -> list[ModelMessage]:
    seen: list[list[ModelMessage]] = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.append(messages)
        if len(seen) == 1:
            return ModelResponse(
                parts=[ToolCallPart("read_ticket", {})],
                usage=RequestUsage(
                    input_tokens=FIRST_REQUEST_INPUT_TOKENS, output_tokens=20
                ),
            )
        return ModelResponse(parts=[TextPart("done")])

    runtime = PydanticAgent(
        FunctionModel(respond),
        capabilities=build_runtime_capabilities(
            AgentRunBudget(max_requests=40), context_window=context_window
        ),
    )

    @runtime.tool_plain
    def read_ticket() -> str:
        return TOOL_RESULT

    await runtime.run("work the ticket")
    assert len(seen) == 2
    return seen[1]


def _warnings(messages: list[ModelMessage]) -> list[str]:
    return [
        part.content
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart)
        and isinstance(part.content, str)
        and "[WarnNearLimits]" in part.content
    ]


def _tool_results(messages: list[ModelMessage]) -> list[object]:
    return [
        part.content
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]


@pytest.mark.asyncio
async def test_large_first_request_is_not_warned_or_compacted_on_a_large_window() -> None:
    messages = await _second_request(context_window=200_000)

    assert _warnings(messages) == []
    assert _tool_results(messages) == [TOOL_RESULT]


@pytest.mark.asyncio
async def test_context_warning_follows_the_model_window() -> None:
    messages = await _second_request(context_window=30_000)

    [warning] = _warnings(messages)
    assert "Context window:" in warning
    assert "/30000 tokens" in warning
