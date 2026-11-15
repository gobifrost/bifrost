"""Pure agent-runtime planning and construction contracts."""

from unittest.mock import AsyncMock

import pytest
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RunUsage

from src.services.agent_runtime.compute import (
    AgentRuntimePlan,
    build_agent_runtime,
)
from src.services.agent_runtime.empty_output import EmptyOutputCircuitBreaker
from src.services.llm.base import ToolDefinition


def _plan(**changes):
    values = {
        "surface": "autonomous",
        "prompt": "Keep ownership.",
        "definitions": (
            ToolDefinition(
                name="lookup_case",
                description="Look up one case.",
                parameters={"type": "object", "properties": {}},
            ),
        ),
        "composition_snapshot": None,
        "max_requests": 4,
        "max_total_tokens": 800,
        "warning_threshold": 0.7,
        "initial_requests": 1,
        "initial_total_tokens": 20,
        "context_window": 4_096,
        "automatic_code_mode": True,
        "runtime_id": "run-123",
        "toolset_id": "bifrost-run-123",
        "empty_output_guard": True,
    }
    values.update(changes)
    return AgentRuntimePlan(**values)


def test_runtime_plan_json_round_trip_excludes_model_settings_and_live_usage():
    plan = _plan()

    encoded = plan.to_json()
    restored = AgentRuntimePlan.from_json(encoded)

    assert restored == plan
    assert "model_settings" not in encoded
    assert "profile" not in encoded
    assert "usage" not in encoded
    assert encoded["max_total_tokens"] == 800


def test_runtime_plan_rejects_extra_bootstrap_fields():
    encoded = _plan().to_json()
    encoded["profile"] = {"api_key": "not accepted"}

    with pytest.raises(ValueError, match="Unexpected runtime-plan fields"):
        AgentRuntimePlan.from_json(encoded)


@pytest.mark.asyncio
async def test_factory_preserves_an_injected_empty_output_guard_and_toolset_id():
    guard = EmptyOutputCircuitBreaker()
    usage = RunUsage()
    model = FunctionModel(lambda *_: ModelResponse(parts=[TextPart("done")]))
    built = build_agent_runtime(
        _plan(),
        model=model,
        executor=AsyncMock(),
        event_handler=None,
        model_settings=None,
        empty_output_guard=guard,
        usage=usage,
    )

    assert built.empty_output_guard is guard
    assert built.usage is usage
    assert built.composition_view is None
    result = await built.runtime.run("go")
    assert result.output == "done"
    assert built.toolset.wrapped.id == "bifrost-run-123"


@pytest.mark.asyncio
async def test_factory_runs_a_chat_plan():
    seen: list[list[str]] = []

    def model(messages, _):
        seen.append([message.kind for message in messages])
        return ModelResponse(parts=[TextPart("done")])

    built = build_agent_runtime(
        _plan(surface="chat", automatic_code_mode=False, empty_output_guard=False),
        model=FunctionModel(model),
        executor=AsyncMock(),
        event_handler=None,
        model_settings=None,
    )

    result = await built.runtime.run("go")

    assert result.output == "done"
    assert seen == [["request"]]
