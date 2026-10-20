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
)
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
