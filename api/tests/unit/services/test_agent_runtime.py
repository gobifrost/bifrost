"""Contract tests for the shared Pydantic AI runtime boundary."""

from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from openai.types.chat import ChatCompletion
from pydantic_ai import Agent as PydanticAgent
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai_harness.cache_stability import CacheStabilityMonitor
from pydantic_ai_harness.compaction import LimitWarner, TieredCompaction
from pydantic_ai.messages import (
    BinaryContent,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestParameters, StreamedResponse
from pydantic_ai.models.test import TestModel
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import ToolDefinition as PydanticToolDefinition
from pydantic_ai.usage import RequestUsage, RunUsage

from src.services.agent_runtime import (
    AgentRunBudget,
    BifrostToolset,
    ObservedModel,
    agent_model_settings,
    build_runtime_capabilities,
    bound_tool_result_for_model,
    create_agent_model,
    provider_name_for_config,
    provider_reported_cost,
)
from src.services.llm.base import LLMConfig, LLMInputFile, ToolDefinition
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


class TrailingUsageStream(StreamedResponse):
    """Mimic providers that send usage after the graph's final text event."""

    async def _get_event_iterator(self):
        for event in self._parts_manager.handle_text_delta(
            vendor_part_id="text", content="done"
        ):
            yield event
        self._usage = RequestUsage(
            input_tokens=12_345,
            output_tokens=67,
            cache_read_tokens=10_000,
        )

    async def close_stream(self) -> None:
        return None

    @property
    def model_name(self) -> str:
        return "test-model"

    @property
    def provider_name(self) -> str:
        return "openrouter"

    @property
    def provider_url(self) -> str:
        return "https://openrouter.ai/api/v1"

    @property
    def timestamp(self) -> datetime:
        return datetime.now(timezone.utc)


class TrailingUsageTestModel(TestModel):
    @asynccontextmanager
    async def request_stream(
        self,
        messages,
        model_settings,
        model_request_parameters,
        run_context=None,
    ):
        yield TrailingUsageStream(model_request_parameters)


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


def test_create_agent_model_uses_native_openrouter_adapter() -> None:
    from pydantic_ai.models.openrouter import OpenRouterModel, OpenRouterModelSettings

    config = LLMConfig(
        provider="openai",
        model="~deepseek/deepseek-v4-flash-latest",
        api_key="test-key",
        endpoint="https://openrouter.ai/api/v1",
    )

    model = create_agent_model(config)

    assert isinstance(model, OpenRouterModel)
    assert model.model_name == "~deepseek/deepseek-v4-flash-latest"
    assert model.system == "openrouter"
    settings = cast(OpenRouterModelSettings, model.settings)
    assert settings.get("openrouter_usage") == {"include": True}


def test_openrouter_runtime_uses_billing_identity_and_sticky_run_route() -> None:
    config = LLMConfig(
        provider="openai",
        model="deepseek/deepseek-v4-flash",
        api_key="test-key",
        endpoint="https://openrouter.ai/api/v1",
    )

    assert provider_name_for_config(config) == "openrouter"
    assert agent_model_settings(
        config,
        max_tokens=4_096,
        session_id="run-123",
    ) == {
        "max_tokens": 4_096,
        "extra_body": {"session_id": "run-123"},
    }


def test_runtime_uses_provider_output_defaults_except_when_api_requires_limit() -> None:
    openai = LLMConfig(provider="openai", model="gpt-5", api_key="test-key")
    anthropic = LLMConfig(provider="anthropic", model="claude-sonnet", api_key="test-key")

    assert agent_model_settings(openai, max_tokens=None, session_id="run-123") == {}
    assert agent_model_settings(anthropic, max_tokens=None, session_id="run-123") == {
        "max_tokens": 16_384,
    }


def test_provider_reported_cost_is_preserved_exactly() -> None:
    response = ModelResponse(
        parts=[TextPart("done")],
        usage=RequestUsage(
            input_tokens=1_000,
            output_tokens=100,
            cache_read_tokens=800,
        ),
        provider_details={"cost": 0.00012345},
    )

    assert provider_reported_cost(response) == Decimal("0.00012345")


def test_native_openrouter_adapter_preserves_provider_reported_usage() -> None:
    config = LLMConfig(
        provider="openai",
        model="~deepseek/deepseek-v4-flash-latest",
        api_key="test-key",
        endpoint="https://openrouter.ai/api/v1",
    )
    model = create_agent_model(config)
    provider_response = ChatCompletion.model_validate(
        {
            "id": "generation-123",
            "object": "chat.completion",
            "created": 0,
            "model": "deepseek/deepseek-v4-flash-0731",
            "provider": "DeepSeek",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "done"},
                }
            ],
            "usage": {
                "prompt_tokens": 12_345,
                "completion_tokens": 678,
                "total_tokens": 13_023,
                "cost": 0.0012345,
                "prompt_tokens_details": {
                    "cached_tokens": 10_000,
                    "cache_write_tokens": 0,
                },
            },
        }
    )

    validated_response = model._validate_completion(provider_response)  # type: ignore[attr-defined]
    response = model._process_response(validated_response)  # type: ignore[attr-defined]

    assert response.provider_response_id == "generation-123"
    assert response.usage.input_tokens == 12_345
    assert response.usage.output_tokens == 678
    assert response.usage.cache_read_tokens == 10_000
    assert provider_reported_cost(response) == Decimal("0.0012345")


def test_openrouter_stream_class_uses_bifrost_usage_mapper() -> None:
    from pydantic_ai.models.openrouter import _OpenRouterChatCompletionChunk

    config = LLMConfig(
        provider="openai",
        model="deepseek/deepseek-v4-flash",
        api_key="test-key",
        endpoint="https://openrouter.ai/api/v1",
    )
    model = create_agent_model(config)

    stream_class = model._streamed_response_cls  # type: ignore[attr-defined]
    stream = object.__new__(stream_class)
    stream._model_name = "deepseek/deepseek-v4-flash"
    stream._provider_name = "openrouter"
    stream._provider_url = "https://openrouter.ai/api/v1"
    stream.provider_details = None
    chunk = _OpenRouterChatCompletionChunk.model_validate(
        {
            "id": "generation-123",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "deepseek/deepseek-v4-flash",
            "choices": [],
            "usage": {
                "prompt_tokens": 3_394,
                "completion_tokens": 16,
                "total_tokens": 3_410,
                "cost": 0.000106904,
                "prompt_tokens_details": {
                    "cached_tokens": 3_328,
                    "cache_write_tokens": 0,
                },
            },
        }
    )

    usage = stream._map_usage(chunk)

    assert usage.input_tokens == 3_394
    assert usage.cache_read_tokens == 3_328
    assert usage.output_tokens == 16
    assert stream.provider_details["cost"] == Decimal("0.000106904")


def test_budget_is_enforced_before_requests_and_warns_before_hard_stop() -> None:
    budget = AgentRunBudget(max_requests=9, max_total_tokens=100_000)

    limits = budget.usage_limits()
    capabilities = build_runtime_capabilities(budget)

    assert limits.request_limit == 9
    assert limits.total_tokens_limit == 100_000
    assert limits.count_tokens_before_request is True
    assert any(isinstance(item, TieredCompaction) for item in capabilities)
    assert any(isinstance(item, CacheStabilityMonitor) for item in capabilities)
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


def test_legacy_history_preserves_multimodal_user_files() -> None:
    messages = PydanticAIClient.convert_messages(
        [
            LLMMessage(
                role="user",
                content="Describe this image",
                input_files=[
                    LLMInputFile(
                        filename="diagram.png",
                        media_type="image/png",
                        data=b"image-bytes",
                    )
                ],
            )
        ]
    )

    assert isinstance(messages[0], ModelRequest)
    prompt = messages[0].parts[0]
    assert isinstance(prompt, UserPromptPart)
    assert isinstance(prompt.content, list)
    assert prompt.content[0] == "Describe this image"
    assert prompt.content[1] == "Attached file: diagram.png"
    assert isinstance(prompt.content[2], BinaryContent)
    assert prompt.content[2].data == b"image-bytes"
    assert prompt.content[2].media_type == "image/png"


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


@pytest.mark.asyncio
async def test_toolset_bounds_large_model_result_but_observer_receives_full_result() -> None:
    full_result = "A" * 40_000
    events = []

    async def execute(name: str, arguments: dict, tool_call_id: str) -> str:
        return full_result

    async def observe(event) -> None:
        events.append(event)

    toolset = BifrostToolset(
        [ToolDefinition(name="large_result", description="Return data", parameters={})],
        execute,
        event_handler=observe,
    )
    ctx = MagicMock(tool_call_id="call-1")
    tools = await toolset.get_tools(ctx)

    result = await toolset.call_tool(
        "large_result",
        {},
        ctx,
        tools["large_result"],
    )

    assert len(result) < len(full_result)
    assert "[tool result truncated: 16000 of 40000 characters omitted" in result
    assert events[-1].result == full_result


def test_bounded_tool_result_has_no_hidden_recovery_tool_protocol() -> None:
    bounded = bound_tool_result_for_model("A" * 40_000)

    assert "read_tool_result" not in bounded
    assert bounded.startswith("A" * 100)
    assert bounded.endswith("A" * 100)


def test_bounded_tool_result_preserves_small_objects_and_bounds_large_objects() -> None:
    small = {"ticket": 42}
    large = {"body": "A" * 40_000}

    assert bound_tool_result_for_model(small) is small
    bounded = bound_tool_result_for_model(large)
    assert isinstance(bounded, str)
    assert len(bounded) < 40_000
    assert "[tool result truncated:" in bounded


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
    assert breakdown["provider_cache_prefix_bytes"] == (
        breakdown["system_prompt_bytes"] + breakdown["tool_schema_bytes"]
    )
    assert breakdown["replayed_history_bytes"] > 0
    assert len(str(breakdown["tool_schema_sha256"])) == 64
    assert "private" not in str(breakdown)


def test_triage_style_loop_exposes_stable_cache_prefix_and_history_growth() -> None:
    """Nine normal agent turns resend a stable prefix plus growing tool history."""
    parameters = ModelRequestParameters(
        function_tools=[
            PydanticToolDefinition(
                name=f"ticket_tool_{index}",
                description="Operate on one ticket using the supplied structured fields.",
                parameters_json_schema={
                    "type": "object",
                    "properties": {
                        "ticket_id": {"type": "integer"},
                        "details": {"type": "string"},
                    },
                    "required": ["ticket_id"],
                },
            )
            for index in range(17)
        ]
    )
    messages: list[ModelMessage] = [
        ModelRequest(parts=[SystemPromptPart(content="S" * 22_944)]),
        ModelRequest(parts=[UserPromptPart(content="Triage ticket 123")]),
    ]
    breakdowns: list[dict[str, int | str]] = []
    for index in range(9):
        breakdowns.append(ObservedModel._context_breakdown(messages, parameters))
        messages.extend(
            [
                ModelResponse(
                    parts=[
                        ToolCallPart(
                            tool_name=f"ticket_tool_{index % 17}",
                            args={"ticket_id": 123},
                            tool_call_id=f"call-{index}",
                        )
                    ]
                ),
                ModelRequest(
                    parts=[
                        ToolReturnPart(
                            tool_name=f"ticket_tool_{index % 17}",
                            content={"result": "R" * 1_000},
                            tool_call_id=f"call-{index}",
                        )
                    ]
                ),
            ]
        )

    assert len({item["tool_schema_sha256"] for item in breakdowns}) == 1
    assert len({item["provider_cache_prefix_bytes"] for item in breakdowns}) == 1
    assert int(breakdowns[-1]["replayed_history_bytes"]) > int(
        breakdowns[0]["replayed_history_bytes"]
    )
    # This quantifies why cumulative input rises even though the cacheable
    # system/schema prefix is stable and can be billed as a cache read.
    assert sum(int(item["estimated_input_tokens"]) for item in breakdowns) > int(
        breakdowns[-1]["estimated_input_tokens"]
    ) * 5


@pytest.mark.asyncio
async def test_missing_provider_usage_is_not_replaced_by_safety_estimates() -> None:
    messages = [ModelRequest(parts=[UserPromptPart(content="hello")])]
    parameters = ModelRequestParameters()
    observer = AsyncMock()
    model = ObservedModel(
        MissingUsageTestModel(custom_output_text="hello back"),
        observer,
    )

    response = await model.request(messages, None, parameters)

    assert response.usage.input_tokens == 0
    assert response.usage.output_tokens == 0
    assert "bifrost_usage_estimated" not in response.usage.details

    async with model.request_stream(messages, None, parameters) as response_stream:
        async for _event in response_stream:
            pass

    assert response_stream.usage.input_tokens == 0
    assert response_stream.usage.output_tokens == 0
    assert "bifrost_usage_estimated" not in response_stream.usage.details


@pytest.mark.asyncio
async def test_observer_drains_trailing_usage_after_consumer_stops_at_final_event() -> None:
    events = []
    model = ObservedModel(TrailingUsageTestModel(), AsyncMock(side_effect=events.append))

    async with model.request_stream(
        [ModelRequest(parts=[UserPromptPart(content="hello")])],
        None,
        ModelRequestParameters(),
    ) as stream:
        async for _ in stream:
            break

    response_event = next(event for event in events if event.type == "response")
    assert response_event.response.usage.input_tokens == 12_345
    assert response_event.response.usage.cache_read_tokens == 10_000
    assert response_event.response.state == "complete"
