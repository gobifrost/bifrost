"""Provider-boundary coverage for the reserved final response."""

from unittest.mock import MagicMock

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.usage import RunUsage

from src.services.agent_runtime.budgets import (
    AgentRunBudget,
    BudgetWindDown,
)


@pytest.mark.asyncio
async def test_wind_down_filters_every_function_tool() -> None:
    capability = BudgetWindDown(
        AgentRunBudget(max_requests=10, max_total_tokens=20_000)
    )
    ctx = MagicMock(usage=RunUsage(input_tokens=8_000))

    assert await capability.prepare_tools(ctx, [MagicMock(), MagicMock()]) == []


@pytest.mark.asyncio
async def test_wind_down_keeps_text_and_discards_stale_tool_intent() -> None:
    capability = BudgetWindDown(
        AgentRunBudget(max_requests=10, max_total_tokens=20_000)
    )
    ctx = MagicMock(usage=RunUsage(input_tokens=8_000))
    response = ModelResponse(
        parts=[
            TextPart(content="Here is the responsible handoff."),
            ToolCallPart(
                tool_name="update_ticket",
                args={"ticket_id": 42},
                tool_call_id="stale-call",
            ),
        ],
        finish_reason="tool_call",
    )

    result = await capability.after_model_request(
        ctx,
        request_context=MagicMock(
            messages=[
                ModelRequest(
                    parts=[
                        ToolReturnPart(
                            tool_name="get_ticket",
                            content={"ticket_id": 42},
                            tool_call_id="completed-call",
                        )
                    ]
                )
            ]
        ),
        response=response,
    )

    assert result.finish_reason == "stop"
    assert len(result.parts) == 1
    assert isinstance(result.parts[0], TextPart)
    assert result.parts[0].content.startswith("Here is the responsible handoff.")
    assert "Completed before the limit: Get Ticket." in result.parts[0].content
    assert "Not completed: Update Ticket." in result.parts[0].content
    assert "No remaining tool actions were run." in result.parts[0].content


@pytest.mark.asyncio
async def test_wind_down_uses_fallback_when_provider_returns_only_a_tool_call() -> None:
    capability = BudgetWindDown(
        AgentRunBudget(max_requests=10, max_total_tokens=20_000)
    )
    ctx = MagicMock(usage=RunUsage(input_tokens=8_000))
    response = ModelResponse(
        parts=[
            ToolCallPart(
                tool_name="update_ticket",
                args={"ticket_id": 42},
                tool_call_id="stale-call",
            )
        ],
        finish_reason="tool_call",
    )

    result = await capability.after_model_request(
        ctx,
        request_context=MagicMock(messages=[]),
        response=response,
    )

    assert len(result.parts) == 1
    assert isinstance(result.parts[0], TextPart)
    assert "configured run budget" in result.parts[0].content
    assert "Not completed: Update Ticket." in result.parts[0].content
    assert result.finish_reason == "stop"
