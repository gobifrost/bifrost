"""Tests for the single shared Pydantic runtime constructor."""

from unittest.mock import AsyncMock

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.test import TestModel
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RequestUsage, RunUsage

from src.services.agent_runtime import AgentRunBudget, AgentRuntimeRunner
from src.services.llm.base import ToolDefinition


class CountingTestModel(TestModel):
    """Test model with the token-count hook used by hard budgets."""

    async def count_tokens(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> RequestUsage:
        return RequestUsage(input_tokens=10)


@pytest.mark.asyncio
async def test_runner_executes_with_shared_budget_capabilities() -> None:
    budget = AgentRunBudget(max_requests=3, max_total_tokens=10_000)
    runner = AgentRuntimeRunner(
        model=CountingTestModel(custom_output_text="shared runtime"),
        instructions="Work carefully.",
        budget=budget,
        model_settings=ModelSettings(),
    )

    result = await runner.run(
        "Start",
        usage=RunUsage(),
        usage_limits=budget.usage_limits(),
        conversation_id="runner-test",
    )

    assert result.output == "shared runtime"


def test_runner_requires_executor_for_configured_tools() -> None:
    with pytest.raises(ValueError, match="tool_executor"):
        AgentRuntimeRunner(
            model=TestModel(custom_output_text="unused"),
            instructions="Work carefully.",
            budget=AgentRunBudget(max_requests=3, max_total_tokens=10_000),
            model_settings=ModelSettings(),
            tool_definitions=[
                ToolDefinition(
                    name="read_file",
                    description="Read a workspace file.",
                    parameters={"type": "object", "properties": {}},
                )
            ],
        )


@pytest.mark.asyncio
async def test_runner_emits_model_observation() -> None:
    observer = AsyncMock()
    budget = AgentRunBudget(max_requests=3, max_total_tokens=10_000)
    runner = AgentRuntimeRunner(
        model=CountingTestModel(custom_output_text="observed"),
        instructions="Work carefully.",
        budget=budget,
        model_settings=ModelSettings(),
        model_event_handler=observer,
    )

    await runner.run(
        "Start",
        usage=RunUsage(),
        usage_limits=budget.usage_limits(),
        conversation_id="observed-test",
    )

    assert {call.args[0].type for call in observer.await_args_list} == {
        "request",
        "response",
    }


@pytest.mark.asyncio
async def test_runner_reports_real_context_compaction() -> None:
    compaction_observer = AsyncMock()
    budget = AgentRunBudget(
        max_requests=3,
        max_total_tokens=10_000,
        context_target_tokens=100,
    )
    runner = AgentRuntimeRunner(
        model=CountingTestModel(custom_output_text="compacted"),
        instructions="Work carefully.",
        budget=budget,
        model_settings=ModelSettings(),
        compaction_event_handler=compaction_observer,
    )

    await runner.run(
        "Continue",
        message_history=[
            ModelRequest(parts=[UserPromptPart(content="Start")]),
            ModelResponse(parts=[TextPart(content="x" * 20_000)]),
        ],
        usage=RunUsage(),
        usage_limits=budget.usage_limits(),
        conversation_id="compaction-test",
    )

    compaction_observer.assert_awaited()
    before_tokens, after_tokens = compaction_observer.await_args.args
    assert before_tokens > budget.context_target_tokens
    assert after_tokens < before_tokens
