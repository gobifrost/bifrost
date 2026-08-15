"""Contract tests for the shared Pydantic AI runtime boundary."""

from contextlib import asynccontextmanager
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic_ai import Agent as PydanticAgent
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai_harness.compaction import LimitWarner, TieredCompaction
from pydantic_ai_harness.overflowing_tool_output import OverflowingToolOutput
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.test import TestModel
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import ToolDefinition as PydanticToolDefinition
from pydantic_ai.usage import RequestUsage, RunUsage

from src.services.agent_runtime import (
    AgentRunBudget,
    BifrostToolset,
    ObservedModel,
    build_runtime_capabilities,
    create_agent_model,
)
from src.services.llm.base import LLMConfig, ToolDefinition
from src.services.llm.base import LLMMessage, ToolCallRequest
from src.services.llm.factory import create_llm_client
from src.services.llm.pydantic_client import PydanticAIClient


class GuardedTestModel(TestModel):
    """Count preflight tokens and prove rejected requests never reach a provider."""

    provider_requests = 0

    async def count_tokens(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> RequestUsage:
        return RequestUsage(input_tokens=10)

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        self.provider_requests += 1
        return await super().request(messages, model_settings, model_request_parameters)


class UncountableGuardedModel(GuardedTestModel):
    """Represent OpenAI-compatible models without a local tokenizer."""

    async def count_tokens(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> RequestUsage:
        raise NotImplementedError


class MissingUsageTestModel(TestModel):
    """Represent an upstream route that omits usage in every transport."""

    async def request(self, *args, **kwargs):
        response = await super().request(*args, **kwargs)
        return replace(response, usage=RequestUsage())

    @asynccontextmanager
    async def request_stream(self, *args, **kwargs):
        async with super().request_stream(*args, **kwargs) as response_stream:
            yield response_stream
            response_stream._usage = RequestUsage()


@pytest.mark.parametrize(
    ("provider", "expected_system"),
    [
        ("openai", "openai"),
        ("anthropic", "anthropic"),
        ("google", "google"),
    ],
)
def test_create_agent_model_supports_every_configured_provider(
    provider: str,
    expected_system: str,
) -> None:
    config = LLMConfig(  # type: ignore[arg-type]
        provider=provider,
        model="test-model",
        api_key="test-key",
        endpoint="https://llm.example.test/v1",
    )

    model = create_agent_model(config)

    assert model.model_name == "test-model"
    assert model.system == expected_system


def test_budget_is_enforced_before_requests_and_warns_before_hard_stop() -> None:
    budget = AgentRunBudget(max_requests=9, max_total_tokens=100_000)

    limits = budget.usage_limits()
    capabilities = build_runtime_capabilities(budget)

    assert limits.request_limit == 9
    assert limits.total_tokens_limit == 100_000
    assert limits.count_tokens_before_request is True
    assert any(isinstance(item, OverflowingToolOutput) for item in capabilities)
    assert any(isinstance(item, TieredCompaction) for item in capabilities)
    warner = next(item for item in capabilities if isinstance(item, LimitWarner))
    assert warner.max_iterations == 9
    assert warner.max_total_tokens == 100_000
    assert warner.warning_threshold == 0.7


def test_unconfigured_budget_disables_run_limits_but_keeps_context_governance() -> None:
    budget = AgentRunBudget()

    limits = budget.usage_limits()
    capabilities = build_runtime_capabilities(budget)

    assert limits.request_limit is None
    assert limits.total_tokens_limit is None
    assert limits.count_tokens_before_request is False
    assert not budget.should_wind_down(
        RunUsage(requests=1_000, input_tokens=1_000_000)
    )
    assert any(isinstance(item, OverflowingToolOutput) for item in capabilities)
    assert any(isinstance(item, TieredCompaction) for item in capabilities)
    warner = next(item for item in capabilities if isinstance(item, LimitWarner))
    assert warner.max_iterations is None
    assert warner.max_total_tokens is None
    assert warner.max_context_tokens == budget.context_target_tokens


@pytest.mark.asyncio
async def test_total_token_ceiling_rejects_the_next_request_before_provider_call() -> None:
    model = GuardedTestModel(custom_output_text="must not be called")
    runtime = PydanticAgent(model)
    budget = AgentRunBudget(max_requests=5, max_total_tokens=100)
    usage = RunUsage(input_tokens=95)

    with pytest.raises(UsageLimitExceeded):
        await runtime.run(
            "one more request",
            usage=usage,
            usage_limits=budget.usage_limits(),
        )

    assert model.provider_requests == 0


@pytest.mark.asyncio
async def test_uncountable_provider_uses_local_estimate_for_preflight_guard() -> None:
    provider_model = UncountableGuardedModel(custom_output_text="must not be called")
    model = ObservedModel(provider_model, MagicMock())
    runtime = PydanticAgent(model)
    budget = AgentRunBudget(max_requests=5, max_total_tokens=100)
    usage = RunUsage(input_tokens=95)

    with pytest.raises(UsageLimitExceeded):
        await runtime.run(
            "one more request",
            usage=usage,
            usage_limits=budget.usage_limits(),
        )

    assert provider_model.provider_requests == 0


def test_delegated_subtree_shares_parent_ledger_and_keeps_local_cap() -> None:
    parent = AgentRunBudget(max_requests=20, max_total_tokens=100_000)

    child = parent.child_subtree(
        current_requests=7,
        current_total_tokens=60_000,
        child_max_requests=5,
        child_max_total_tokens=25_000,
    )
    constrained_child = parent.child_subtree(
        current_requests=18,
        current_total_tokens=95_000,
        child_max_requests=5,
        child_max_total_tokens=25_000,
    )

    assert child.max_requests == 12
    assert child.max_total_tokens == 85_000
    assert constrained_child.max_requests == 20
    assert constrained_child.max_total_tokens == 100_000


def test_delegated_subtree_preserves_optional_parent_and_child_limits() -> None:
    unbounded_parent = AgentRunBudget()
    unbounded_child = unbounded_parent.child_subtree(
        current_requests=7,
        current_total_tokens=60_000,
        child_max_requests=None,
        child_max_total_tokens=None,
    )
    locally_bounded_child = unbounded_parent.child_subtree(
        current_requests=7,
        current_total_tokens=60_000,
        child_max_requests=5,
        child_max_total_tokens=25_000,
    )
    bounded_parent_child = AgentRunBudget(
        max_requests=20,
        max_total_tokens=100_000,
    ).child_subtree(
        current_requests=7,
        current_total_tokens=60_000,
        child_max_requests=None,
        child_max_total_tokens=None,
    )

    assert unbounded_child.max_requests is None
    assert unbounded_child.max_total_tokens is None
    assert locally_bounded_child.max_requests == 12
    assert locally_bounded_child.max_total_tokens == 85_000
    assert bounded_parent_child.max_requests == 20
    assert bounded_parent_child.max_total_tokens == 100_000


def test_legacy_llm_contract_uses_pydantic_for_all_providers_and_preserves_tools() -> None:
    client = create_llm_client("google", "test-key")
    assert isinstance(client, PydanticAIClient)

    messages = PydanticAIClient.convert_messages(
        [
            LLMMessage(role="user", content="Inspect ticket 42"),
            LLMMessage(
                role="assistant",
                content="Checking",
                tool_calls=[
                    ToolCallRequest(
                        id="call-42",
                        name="get_ticket",
                        arguments={"ticket_id": 42},
                    )
                ],
            ),
            LLMMessage(
                role="tool",
                content='{"status":"open"}',
                tool_call_id="call-42",
                tool_name="get_ticket",
            ),
        ]
    )

    assert isinstance(messages[0], ModelRequest)
    assert isinstance(messages[1], ModelResponse)
    assert isinstance(messages[1].parts[1], ToolCallPart)
    assert isinstance(messages[2], ModelRequest)
    assert isinstance(messages[2].parts[0], ToolReturnPart)
    assert messages[2].parts[0].tool_call_id == "call-42"


def test_legacy_history_groups_consecutive_tool_results_for_provider_compatibility() -> None:
    messages = PydanticAIClient.convert_messages(
        [
            LLMMessage(
                role="assistant",
                tool_calls=[
                    ToolCallRequest(id="call-1", name="first", arguments={}),
                    ToolCallRequest(id="call-2", name="second", arguments={}),
                ],
            ),
            LLMMessage(
                role="tool",
                content="first result",
                tool_call_id="call-1",
                tool_name="first",
            ),
            LLMMessage(
                role="tool",
                content="second result",
                tool_call_id="call-2",
                tool_name="second",
            ),
        ]
    )

    assert len(messages) == 2
    assert isinstance(messages[1], ModelRequest)
    assert [part.tool_call_id for part in messages[1].parts] == ["call-1", "call-2"]


@pytest.mark.asyncio
async def test_legacy_complete_uses_stream_transport_for_large_output_limits() -> None:
    response = ModelResponse(
        parts=[TextPart("hello")],
        usage=RequestUsage(input_tokens=12, output_tokens=3),
        model_name="test-model",
        provider_name="anthropic",
        finish_reason="stop",
    )

    class FakeStream:
        def __aiter__(self):
            async def events():
                if False:
                    yield None

            return events()

        def get(self):
            return response

    class FakeStreamContext:
        async def __aenter__(self):
            return FakeStream()

        async def __aexit__(self, *args):
            return False

    client = PydanticAIClient(
        LLMConfig(provider="anthropic", model="test-model", api_key="test-key")
    )
    with patch(
        "src.services.llm.pydantic_client.create_agent_model",
        return_value=MagicMock(),
    ), patch(
        "src.services.llm.pydantic_client.model_request_stream",
        return_value=FakeStreamContext(),
    ) as request_stream:
        result = await client.complete(
            [LLMMessage(role="user", content="hello")],
            max_tokens=64_000,
        )

    assert result.content == "hello"
    assert result.input_tokens == 12
    assert result.output_tokens == 3
    assert request_stream.call_args.kwargs["model_settings"]["max_tokens"] == 64_000


@pytest.mark.asyncio
async def test_toolset_preserves_stored_json_schema_and_emits_lifecycle_events() -> None:
    calls: list[tuple[str, dict]] = []
    events = []

    async def execute(name: str, arguments: dict, tool_call_id: str) -> dict:
        assert tool_call_id == "call-1"
        calls.append((name, arguments))
        return {"ticket": 42}

    async def observe(event) -> None:
        events.append(event)

    schema = {
        "type": "object",
        "properties": {"ticket_id": {"type": "integer"}},
        "required": ["ticket_id"],
        "additionalProperties": False,
    }
    toolset = BifrostToolset(
        [ToolDefinition(name="get_ticket", description="Get one ticket", parameters=schema)],
        execute,
        event_handler=observe,
    )
    ctx = MagicMock(tool_call_id="call-1")

    tools = await toolset.get_tools(ctx)
    result = await toolset.call_tool(
        "get_ticket",
        {"ticket_id": 42},
        ctx,
        tools["get_ticket"],
    )

    assert tools["get_ticket"].tool_def.parameters_json_schema == schema
    assert tools["get_ticket"].tool_def.sequential is True
    assert calls == [("get_ticket", {"ticket_id": 42})]
    assert result == {"ticket": 42}
    assert [event.type for event in events] == ["tool_call", "tool_result"]


def test_request_observability_breaks_down_context_without_recording_contents() -> None:
    messages = [
        ModelRequest(parts=[SystemPromptPart(content="private system instructions")]),
        *PydanticAIClient.convert_messages(
            [
                LLMMessage(role="user", content="old private user request"),
                LLMMessage(role="assistant", content="old private response"),
                LLMMessage(
                    role="tool",
                    content="private tool result",
                    tool_call_id="call-1",
                    tool_name="get_ticket",
                ),
                LLMMessage(role="user", content="current private user request"),
            ]
        ),
    ]
    parameters = ModelRequestParameters(
        function_tools=[
            PydanticToolDefinition(
                name="get_ticket",
                description="Get one ticket",
                parameters_json_schema={
                    "type": "object",
                    "properties": {"ticket_id": {"type": "integer"}},
                },
            )
        ]
    )

    breakdown = ObservedModel._context_breakdown(messages, parameters)

    assert breakdown["system_prompt_bytes"] > 0
    assert breakdown["user_history_bytes"] > 0
    assert breakdown["current_user_prompt_bytes"] > 0
    assert breakdown["assistant_history_bytes"] > 0
    assert breakdown["tool_result_bytes"] > 0
    assert breakdown["tool_schema_bytes"] > 0
    assert breakdown["messages_serialized_bytes"] > 0
    assert breakdown["estimated_input_tokens"] > 0
    assert len(str(breakdown["tool_schema_sha256"])) == 64
    assert "private" not in str(breakdown)


@pytest.mark.asyncio
async def test_missing_provider_usage_is_backfilled_for_requests_and_streams() -> None:
    messages = [ModelRequest(parts=[UserPromptPart(content="hello")])]
    parameters = ModelRequestParameters()
    observer = AsyncMock()
    model = ObservedModel(
        MissingUsageTestModel(custom_output_text="hello back"),
        observer,
    )

    response = await model.request(messages, None, parameters)

    assert response.usage.input_tokens > 0
    assert response.usage.output_tokens > 0
    assert response.usage.details["bifrost_usage_estimated"] == 1

    async with model.request_stream(messages, None, parameters) as response_stream:
        async for _event in response_stream:
            pass

    assert response_stream.usage.input_tokens > 0
    assert response_stream.usage.output_tokens > 0
    assert response_stream.usage.details["bifrost_usage_estimated"] == 1
