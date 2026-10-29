"""Privacy-safe request-composition coverage for the observed model."""

from dataclasses import replace
from unittest.mock import AsyncMock

import pydantic_core
import pytest
from pydantic_ai.messages import (
    InstructionPart,
    ModelMessage,
    ModelRequest,
    SystemPromptPart,
    UserPromptPart,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai import Agent
from pydantic_ai.capabilities import Capability
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.test import TestModel
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.usage import RequestUsage

from src.services.agent_runtime.observed_model import ObservedModel


def _tool(name: str, *, deferred: bool = False) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=f"Use {name}.",
        parameters_json_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
        },
        defer_loading=deferred,
    )


def test_context_breakdown_counts_only_provider_selected_instruction_bytes() -> None:
    instruction = "Use caf\u00e9 workflows."
    messages = [ModelRequest(parts=[UserPromptPart(content="Current request")])]
    without_instructions = ObservedModel._context_breakdown(
        messages, ModelRequestParameters()
    )
    breakdown = ObservedModel._context_breakdown(
        messages,
        ModelRequestParameters(
            instruction_parts=[InstructionPart(content=instruction)]
        ),
    )

    assert breakdown["instruction_bytes"] == len(instruction.encode())
    assert breakdown["provider_cache_prefix_bytes"] == len(instruction.encode())
    # The estimate remains a whole-request guard. Do not invent a separate
    # provider-token attribution for native Pydantic instruction parts.
    assert breakdown["estimated_input_tokens"] == without_instructions[
        "estimated_input_tokens"
    ]
    assert instruction not in str(breakdown)


def test_context_breakdown_uses_native_instruction_fallback_without_contents() -> None:
    instructions = "Capability guidance: use the local directory."
    messages = [
        ModelRequest(
            parts=[UserPromptPart(content="Find a record")],
            instructions=instructions,
        )
    ]

    breakdown = ObservedModel._context_breakdown(messages, ModelRequestParameters())

    assert breakdown["instruction_bytes"] == len(instructions.encode())
    assert breakdown["provider_cache_prefix_bytes"] == len(instructions.encode())
    assert instructions not in str(breakdown)


def test_context_breakdown_excludes_tool_return_metadata_from_provider_estimates() -> None:
    raw_receipt = {"nested_receipt": "audit-only-sentinel-" + "x" * 100_000}
    result = ToolReturnPart(
        tool_name="read_record",
        tool_call_id="call-1",
        content='{"record_id":"r-7"}',
        metadata=raw_receipt,
    )
    messages = [ModelRequest(parts=[result])]
    parameters = ModelRequestParameters()

    without_metadata = ObservedModel._context_breakdown(
        [ModelRequest(parts=[replace(result, metadata=None)])], parameters
    )
    with_metadata = ObservedModel._context_breakdown(messages, parameters)

    for field in (
        "tool_result_bytes",
        "messages_serialized_bytes",
        "replayed_history_bytes",
        "estimated_input_tokens",
    ):
        assert with_metadata[field] == without_metadata[field]
    assert result.metadata == raw_receipt
    assert messages[0].parts[0].metadata == raw_receipt
    assert raw_receipt["nested_receipt"] not in str(with_metadata)


def test_context_breakdown_separates_active_and_deferred_tool_schemas() -> None:
    active = _tool("search_active")
    deferred = _tool("search_deferred", deferred=True)
    system = SystemPromptPart(content="Stable instructions")
    parameters = ModelRequestParameters(function_tools=[active, deferred])

    breakdown = ObservedModel._context_breakdown(
        [ModelRequest(parts=[system, UserPromptPart(content="Find it")])],
        parameters,
    )

    active_bytes = len(pydantic_core.to_json([active], fallback=str))
    deferred_bytes = len(pydantic_core.to_json([deferred], fallback=str))
    assert breakdown["active_tools_count"] == 1
    assert breakdown["deferred_tools_count"] == 1
    assert breakdown["active_tool_schema_bytes"] == active_bytes
    assert breakdown["deferred_tool_schema_bytes"] == deferred_bytes
    assert breakdown["tool_schema_bytes"] == len(
        pydantic_core.to_json([active, deferred], fallback=str)
    )
    assert breakdown["provider_cache_prefix_bytes"] == (
        len(pydantic_core.to_json(system, fallback=str)) + active_bytes
    )
    assert int(breakdown["provider_cache_prefix_bytes"]) < (
        len(pydantic_core.to_json(system, fallback=str)) + active_bytes + deferred_bytes
    )
    assert "search_active" not in str(breakdown)
    assert "search_deferred" not in str(breakdown)


@pytest.mark.parametrize("resolved", [False, True])
def test_loaded_tools_are_active_even_though_their_deferred_flag_stays_set(resolved):
    tool = _tool("loaded_read", deferred=True)
    parameters = ModelRequestParameters(
        function_tools=[tool], revealed_tool_names={tool.name},
        tool_visibility={tool.name: "visible"} if resolved else None,
    )
    breakdown = ObservedModel._context_breakdown([], parameters)
    assert tool.defer_loading is True
    assert breakdown["active_tools_count"] == 1
    assert breakdown["deferred_tools_count"] == 0


def test_hidden_tool_inventory_does_not_exhaust_the_context_budget():
    active = _tool("read")
    hidden = ToolDefinition(
        name="unloaded", description="Specialist contract. " * 10_000,
        defer_loading=True,
    )
    visible = ObservedModel._context_breakdown([], ModelRequestParameters(function_tools=[active]))
    with_inventory = ObservedModel._context_breakdown(
        [], ModelRequestParameters(function_tools=[active, hidden]),
    )
    assert with_inventory["tool_schema_bytes"] > visible["tool_schema_bytes"]
    assert with_inventory["estimated_input_tokens"] == visible["estimated_input_tokens"]


@pytest.mark.asyncio
async def test_native_capability_load_updates_the_observed_active_inventory():
    def approved_read() -> str:
        """Read approved evidence."""
        return "evidence"

    requests = 0

    def model(messages, info):
        nonlocal requests
        requests += 1
        if requests == 1:
            return ModelResponse(parts=[ToolCallPart("load_capability", {"id": "specialist"}, "load")])
        return ModelResponse(parts=[TextPart("loaded")])

    observer = AsyncMock()
    await Agent(
        ObservedModel(FunctionModel(model), observer),
        capabilities=[Capability(id="specialist", tools=[approved_read], defer_loading=True)],
    ).run("Load the specialist.")
    events = [call.args[0] for call in observer.await_args_list if call.args[0].type == "request"]
    assert [(event.context_breakdown["active_tools_count"], event.context_breakdown["deferred_tools_count"]) for event in events] == [(1, 1), (2, 0)]


class _FixedUsageModel(TestModel):
    """Local test model with provider-reported usage to preserve verbatim."""

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ):
        response = await super().request(
            messages, model_settings, model_request_parameters
        )
        return replace(
            response,
            usage=RequestUsage(input_tokens=123, output_tokens=45),
        )


@pytest.mark.asyncio
async def test_context_breakdown_does_not_replace_provider_usage() -> None:
    observed = ObservedModel(_FixedUsageModel(), AsyncMock())

    response = await observed.request(
        [ModelRequest(parts=[UserPromptPart(content="Use \u03c0")])],
        None,
        ModelRequestParameters(
            instruction_parts=[InstructionPart(content="Follow the runbook.")]
        ),
    )

    assert response.usage.input_tokens == 123
    assert response.usage.output_tokens == 45
