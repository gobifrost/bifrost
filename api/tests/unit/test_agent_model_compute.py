"""Contracts for the credential-free remote PydanticAI model wire."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Mapping

import pytest
from pydantic_ai import Agent
from pydantic_ai._json_schema import InlineDefsJsonSchemaTransformer
from pydantic_ai.messages import (
    BinaryContent,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    ThinkingPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.profiles import DEFAULT_PROFILE
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.usage import RequestUsage

from src.services.agent_runtime.empty_output import (
    EMPTY_OUTPUT_FALLBACK_MAX_TOKENS,
    EmptyOutputCircuitBreaker,
)
from shared.agent_model_compute import (
    MAX_WIRE_BYTES,
    ModelWireError,
    RemoteModel,
    current_model_output_cap,
    deserialize_model_profile,
    deserialize_model_request,
    deserialize_model_response,
    serve_model_count_tokens,
    serve_model_request,
    serve_model_stream,
    serialize_model_profile,
    serialize_model_request,
    serialize_model_response,
)


def _request() -> list[ModelMessage]:
    return [
        ModelRequest(
            parts=[
                UserPromptPart(
                    [
                        "inspect this attachment",
                        BinaryContent(data=b"image bytes", media_type="image/png"),
                    ]
                )
            ]
        )
    ]


def _parameters() -> ModelRequestParameters:
    return ModelRequestParameters(
        function_tools=[
            ToolDefinition(
                name="visible_tool",
                description="Available now",
                parameters_json_schema={"type": "object"},
            ),
            ToolDefinition(
                name="deferred_tool",
                description="Available after capability loading",
                parameters_json_schema={
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                },
                defer_loading=True,
                capability_id="files",
            ),
        ],
        tool_visibility={
            "visible_tool": "visible",
            "deferred_tool": "deferred",
        },
        revealed_tool_names={"visible_tool"},
        deferred_capability_ids={"files"},
    )


def test_profile_bootstrap_round_trips_full_resolved_profile() -> None:
    """A physical worker receives profile semantics, not a pickled callback."""

    expected = {
        **DEFAULT_PROFILE,
        "json_schema_transformer": InlineDefsJsonSchemaTransformer,
    }
    payload = serialize_model_profile(expected)
    profile = deserialize_model_profile(payload)

    assert profile == expected
    assert b"json_schema_transformer" in payload
    assert b"provider_url" not in payload


def test_wire_scrubs_only_typed_routing_fields_not_tool_content() -> None:
    """Provider metadata is private; user tool data may legitimately use its names."""

    tool_content = {
        "provider_url": "user-owned URL",
        "provider_details": {"user_owned": True},
    }
    messages = [
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="lookup",
                    tool_call_id="call-1",
                    content=tool_content,
                )
            ]
        )
    ]
    decoded_request = deserialize_model_request(
        serialize_model_request(messages, _parameters())
    )
    decoded_part = decoded_request.messages[0].parts[0]
    assert isinstance(decoded_part, ToolReturnPart)
    assert decoded_part.content == tool_content

    response = ModelResponse(
        parts=[
            ThinkingPart(
                content="reasoning",
                signature="provider-signature",
                provider_details={"private": "remove"},
            )
        ],
        provider_url="https://private.provider.example/v1",
        provider_details={"private": "remove"},
    )
    decoded_response = deserialize_model_response(serialize_model_response(response))
    assert decoded_response.provider_url is None
    assert decoded_response.provider_details is None
    assert isinstance(decoded_response.parts[0], ThinkingPart)
    assert decoded_response.parts[0].signature == "provider-signature"
    assert decoded_response.parts[0].provider_details is None


@pytest.mark.asyncio
async def test_remote_request_round_trips_multimodal_messages_and_deferred_tools() -> (
    None
):
    """The API receives the exact worker request shape without worker settings."""

    wire_request = deserialize_model_request(
        serialize_model_request(_request(), _parameters())
    )
    assert wire_request.parameters.visibility_of("deferred_tool") == "deferred"

    seen_messages: list[ModelMessage] = []
    seen_declared_tools: list[ToolDefinition] = []
    seen_all_tools: list[ToolDefinition] = []
    seen_visibility: Mapping[str, str] | None = None

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal seen_visibility
        seen_messages.extend(messages)
        seen_declared_tools.extend(info.function_tools)
        seen_all_tools.extend(info.model_request_parameters.function_tools)
        seen_visibility = info.model_request_parameters.tool_visibility
        return ModelResponse(
            parts=[TextPart("accepted")],
            model_name="function-api-model",
            usage=RequestUsage(input_tokens=11, output_tokens=3, details={"cost": 17}),
            provider_url="https://private.provider.example/v1",
            provider_details={"secret": "must not cross to worker"},
        )

    api_model = FunctionModel(respond, model_name="function-api-model")
    remote = RemoteModel(
        model_name="function-api-model",
        request=lambda payload: serve_model_request(api_model, None, payload),
        request_stream=lambda payload: serve_model_stream(api_model, None, payload),
        count_tokens=lambda payload: serve_model_count_tokens(api_model, None, payload),
        profile_payload=serialize_model_profile(api_model.profile),
    )

    response = await remote.request(_request(), None, _parameters())

    assert isinstance(seen_messages[0], ModelRequest)
    assert isinstance(seen_messages[0].parts[0], UserPromptPart)
    assert isinstance(seen_messages[0].parts[0].content, list)
    attachment = seen_messages[0].parts[0].content[1]
    assert isinstance(attachment, BinaryContent)
    assert attachment.data == b"image bytes"
    assert attachment.media_type == "image/png"
    assert [tool.name for tool in seen_declared_tools] == ["visible_tool"]
    assert [tool.name for tool in seen_all_tools] == ["visible_tool", "deferred_tool"]
    assert seen_visibility == {
        "visible_tool": "visible",
        # FunctionModel has no native deferral renderer, so its own trusted
        # profile resolves the preserved definition to withheld.
        "deferred_tool": "withheld",
    }
    assert response.usage == RequestUsage(
        input_tokens=11, output_tokens=3, details={"cost": 17}
    )
    assert response.provider_url is None
    assert response.provider_details is None


@pytest.mark.asyncio
async def test_remote_stream_replays_actual_deltas_and_final_usage() -> None:
    """Streaming never reconstructs text or tool ids from a completed response."""

    async def stream_response(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str]:
        del messages, info
        yield "first "
        yield "second"

    api_model = FunctionModel(
        stream_function=stream_response,
        model_name="function-stream-model",
    )
    remote = RemoteModel(
        model_name="function-stream-model",
        request=lambda payload: serve_model_request(api_model, None, payload),
        request_stream=lambda payload: serve_model_stream(api_model, None, payload),
        count_tokens=lambda payload: serve_model_count_tokens(api_model, None, payload),
        profile_payload=serialize_model_profile(api_model.profile),
    )

    async with remote.request_stream(_request(), None, _parameters()) as stream:
        events = [event async for event in stream]
        response = stream.get()

    starts = [
        event.part.content
        for event in events
        if isinstance(event, PartStartEvent) and isinstance(event.part, TextPart)
    ]
    deltas = [
        event.delta.content_delta
        for event in events
        if isinstance(event, PartDeltaEvent) and hasattr(event.delta, "content_delta")
    ]
    assert starts == ["first "]
    assert deltas == ["second"]
    assert response.text == "first second"
    assert stream.usage == response.usage
    assert stream.model_name == response.model_name == "function-stream-model"
    assert stream.timestamp == response.timestamp


@pytest.mark.asyncio
async def test_remote_count_tokens_uses_api_model_callback() -> None:
    """Pre-request budgets use the trusted API model rather than an estimate."""

    class CountingTestModel(TestModel):
        async def count_tokens(
            self,
            messages: list[ModelMessage],
            model_settings: ModelSettings | None,
            model_request_parameters: ModelRequestParameters,
        ) -> RequestUsage:
            del messages, model_settings, model_request_parameters
            return RequestUsage(input_tokens=29, details={"counted": 1})

    api_model = CountingTestModel(custom_output_text="unused")
    remote = RemoteModel(
        model_name="test",
        request=lambda payload: serve_model_request(api_model, None, payload),
        request_stream=lambda payload: serve_model_stream(api_model, None, payload),
        count_tokens=lambda payload: serve_model_count_tokens(api_model, None, payload),
        profile_payload=serialize_model_profile(api_model.profile),
    )

    usage = await remote.count_tokens(_request(), None, _parameters())

    assert usage == RequestUsage(input_tokens=29, details={"counted": 1})


@pytest.mark.asyncio
async def test_remote_model_uses_injected_profile_and_does_not_prepare_twice() -> None:
    """Worker preparation happens once; the API model receives its resolved result."""

    seen: list[AgentInfo] = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del messages
        seen.append(info)
        return ModelResponse(parts=[TextPart("complete")])

    api_model = FunctionModel(respond, model_name="profiled-function")
    remote = RemoteModel(
        model_name="profiled-function",
        request=lambda payload: serve_model_request(api_model, None, payload),
        request_stream=lambda payload: serve_model_stream(api_model, None, payload),
        count_tokens=lambda payload: serve_model_count_tokens(api_model, None, payload),
        profile_payload=serialize_model_profile(api_model.profile),
    )

    result = await Agent(remote, tools=[lambda: "unused tool"]).run("hello")

    assert result.output == "complete"
    assert remote.profile.get("supports_tools") is True
    assert len(seen) == 1
    assert [tool.name for tool in seen[0].function_tools] == ["<lambda>"]


@pytest.mark.asyncio
async def test_empty_output_retry_cap_reaches_the_trusted_api_model() -> None:
    """The breaker cap crosses the wire, bounded by the API-owned configuration."""

    seen_settings: list[ModelSettings | None] = []
    responses = iter(
        [ModelResponse(parts=[]), ModelResponse(parts=[TextPart("complete")])]
    )

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del messages
        seen_settings.append(info.model_settings)
        return next(responses)

    api_model = FunctionModel(respond, model_name="empty-output-function")
    remote = RemoteModel(
        model_name="empty-output-function",
        request=lambda payload: serve_model_request(
            api_model, {"max_tokens": 4_096}, payload
        ),
        request_stream=lambda payload: serve_model_stream(
            api_model, {"max_tokens": 4_096}, payload
        ),
        count_tokens=lambda payload: serve_model_count_tokens(
            api_model, {"max_tokens": 4_096}, payload
        ),
        profile_payload=serialize_model_profile(api_model.profile),
    )

    result = await Agent(remote, capabilities=[EmptyOutputCircuitBreaker()]).run(
        "hello"
    )

    assert result.output == "complete"
    assert seen_settings == [
        {"max_tokens": 4_096},
        {"max_tokens": EMPTY_OUTPUT_FALLBACK_MAX_TOKENS},
    ]


@pytest.mark.asyncio
async def test_api_output_cap_scope_resets_and_isolated_between_requests() -> None:
    """Concurrent authorized requests cannot inherit another worker's retry cap."""

    observed_caps: list[int | None] = []

    async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del messages, info
        await asyncio.sleep(0)
        observed_caps.append(current_model_output_cap())
        return ModelResponse(parts=[TextPart("complete")])

    api_model = FunctionModel(respond, model_name="cap-scope-function")
    request_one = serialize_model_request(_request(), _parameters(), max_tokens=1_001)
    request_two = serialize_model_request(_request(), _parameters(), max_tokens=2_002)

    assert current_model_output_cap() is None
    await asyncio.gather(
        serve_model_request(api_model, {"max_tokens": 4_096}, request_one),
        serve_model_request(api_model, {"max_tokens": 4_096}, request_two),
    )

    assert sorted(observed_caps) == [1_001, 2_002]
    assert current_model_output_cap() is None


def test_request_codec_rejects_oversized_payload_and_settings_fields() -> None:
    """Wire input is bounded and cannot smuggle provider settings or endpoints."""

    with pytest.raises(ModelWireError, match="maximum"):
        deserialize_model_request(b"x" * (MAX_WIRE_BYTES + 1))

    payload = json.loads(serialize_model_request(_request(), _parameters()))
    payload["settings"] = {"api_key": "not allowed", "base_url": "https://provider"}
    with pytest.raises(ModelWireError, match="unexpected fields"):
        deserialize_model_request(json.dumps(payload).encode())


def test_native_run_usage_round_trips_decimal_cost_before_ledger_arithmetic():
    from decimal import Decimal
    from pydantic_ai.usage import RunUsage
    from shared.agent_model_compute import deserialize_run_usage, serialize_run_usage

    initial = RunUsage(input_tokens=11, output_tokens=3, requests=1, cost=Decimal("0.000123456789"))
    wire = serialize_run_usage(initial)
    assert json.loads(json.dumps(wire))["cost"] == "0.000123456789"
    restored = deserialize_run_usage(wire)
    restored.incr(RunUsage(input_tokens=2, cost=Decimal("0.000000000001")))
    assert restored.cost == Decimal("0.000123456790")
    assert restored.input_tokens == 13
