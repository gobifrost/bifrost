"""Contracts for the shared agent-runtime composition adapter."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic_ai import Agent as PydanticAgent
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from src.services.agent_runtime.composition import (
    AgentComposition,
    AgentCompositionError,
    ToolBinding,
    _DeferredAgent,
    _capability_id,
    _delegate_name,
    _qualified_tool_name,
)
from src.services.llm import ToolDefinition


def _composition(*, deferred=(), delegates=None, bindings=None):
    root_id = uuid4()
    return AgentComposition(
        root_id=root_id,
        caller=SimpleNamespace(),
        session_factory=AsyncMock(),
        caller_user_id=uuid4(),
        instructions="composition instructions",
        entry_definitions=(),
        entry_workflow_ids={},
        bindings=bindings or {},
        deferred=deferred,
        direct_delegates=delegates or {},
    )


@pytest.mark.asyncio
async def test_deferred_capability_loads_then_executes_its_qualified_tool_in_one_run():
    """A model sees no specialist tool until its native capability is loaded."""
    root_id, child_id = uuid4(), uuid4()
    source = ToolDefinition(
        name="search_case",
        description="Find a case.",
        parameters={
            "type": "object",
            "properties": {"case": {"type": "string"}},
            "required": ["case"],
        },
    )
    child = SimpleNamespace(
        id=child_id,
        name="Case Specialist",
        system_prompt="Find evidence before answering.",
    )
    capability_id = _capability_id(child_id)
    qualified = _qualified_tool_name(child_id, source.name)
    binding = ToolBinding(
        name=qualified,
        owner_id=child_id,
        original_name=source.name,
        path=(root_id, child_id),
        capability_id=capability_id,
        parameters=source.parameters,
    )
    composition = _composition(
        deferred=(
            _DeferredAgent(
                agent=child,
                path=(root_id, child_id),
                definitions=(source,),
                capability_id=capability_id,
            ),
        ),
        bindings={qualified: binding},
    )
    composition.root_id = root_id
    composition.authorize_capability_load = AsyncMock()
    executed = AsyncMock(return_value="case evidence")
    capabilities = composition.capabilities(executed)
    specialist = next(
        capability for capability in capabilities if getattr(capability, "id", None) == capability_id
    )
    assert "Find evidence before answering." in str(specialist.get_instructions())
    requests = 0

    def model(_, info):
        nonlocal requests
        requests += 1
        names = {tool.name for tool in info.function_tools}
        if requests == 1:
            assert "load_capability" in names
            assert qualified not in names
            return ModelResponse(parts=[ToolCallPart("load_capability", {"id": capability_id}, "load")])
        if requests == 2:
            assert qualified in names
            return ModelResponse(parts=[ToolCallPart(qualified, {"case": "123"}, "tool")])
        return ModelResponse(parts=[TextPart("done")])

    result = await PydanticAgent(
        FunctionModel(model), capabilities=capabilities
    ).run("help")

    assert result.output == "done"
    executed.assert_awaited_once_with(qualified, {"case": "123"}, "tool")
    composition.authorize_capability_load.assert_awaited()


@pytest.mark.asyncio
async def test_native_subagents_routes_one_isolated_child_callback_without_a_model_call():
    """The framework's delegate_task wrapper forwards its real tool-call id."""
    root_id, child_id = uuid4(), uuid4()
    child = SimpleNamespace(id=child_id, name="Investigate", system_prompt="Investigate.")
    name = _delegate_name(child)
    binding = ToolBinding(
        name="delegate_task",
        owner_id=root_id,
        original_name="delegate_to_investigate",
        path=(root_id,),
        capability_id=None,
        parameters={"type": "object", "properties": {"task": {"type": "string"}}},
        delegated_target_path=(root_id, child_id),
    )
    composition = _composition(delegates={name: binding})
    composition.root_id = root_id
    executed = AsyncMock(return_value="child receipt")
    requests = 0

    def model(_, info):
        nonlocal requests
        requests += 1
        if requests == 1:
            assert {tool.name for tool in info.function_tools} >= {"delegate_task"}
            return ModelResponse(
                parts=[ToolCallPart("delegate_task", {"agent_name": name, "task": "audit"}, "delegate")]
            )
        return ModelResponse(parts=[TextPart("parent complete")])

    result = await PydanticAgent(
        FunctionModel(model), capabilities=composition.capabilities(executed)
    ).run("run")

    assert result.output == "parent complete"
    executed.assert_awaited_once_with(
        "delegate_task", {"agent_name": name, "task": "audit"}, "delegate"
    )


@pytest.mark.asyncio
async def test_native_subagent_returns_a_failure_receipt_so_parent_can_enrich_and_retry():
    """A child failure is recoverable evidence, matching the legacy toolset."""
    root_id, child_id = uuid4(), uuid4()
    child = SimpleNamespace(id=child_id, name="Investigate", system_prompt="Investigate.")
    name = _delegate_name(child)
    binding = ToolBinding(
        name="delegate_task",
        owner_id=root_id,
        original_name="delegate_to_investigate",
        path=(root_id,),
        capability_id=None,
        parameters={"type": "object", "properties": {"task": {"type": "string"}}},
        delegated_target_path=(root_id, child_id),
    )
    composition = _composition(delegates={name: binding})
    composition.root_id = root_id
    executed = AsyncMock(side_effect=[RuntimeError("missing case number"), "child receipt"])
    events = []

    async def record(event):
        events.append(event)
    requests = 0

    def model(_, info):
        nonlocal requests
        requests += 1
        if requests == 1:
            return ModelResponse(
                parts=[ToolCallPart("delegate_task", {"agent_name": name, "task": "find it"}, "first")]
            )
        if requests == 2:
            return ModelResponse(
                parts=[ToolCallPart("delegate_task", {"agent_name": name, "task": "find case 123"}, "second")]
            )
        return ModelResponse(parts=[TextPart("recovered")])

    result = await PydanticAgent(
        FunctionModel(model), capabilities=composition.capabilities(executed, event_handler=record)
    ).run("run")

    assert result.output == "recovered"
    assert executed.await_count == 2
    assert any(event.type == "tool_error" and event.tool_name == "delegate_task" for event in events)


@pytest.mark.asyncio
async def test_resolve_call_denies_revoked_binding_before_executor_effect():
    """A stale path does not open a session or produce an executable binding."""
    root_id, child_id = uuid4(), uuid4()
    binding = ToolBinding(
        name="bfa_test",
        owner_id=child_id,
        original_name="source",
        path=(root_id, child_id),
        capability_id="cap",
        parameters={"type": "object", "properties": {}},
    )
    composition = _composition(bindings={binding.name: binding})
    composition.root_id = root_id
    composition._authorize_path = AsyncMock(side_effect=AgentCompositionError("delegation revoked"))

    with pytest.raises(AgentCompositionError, match="delegation revoked"):
        await composition.resolve_call(binding.name, {})

    composition._authorize_path.assert_awaited_once_with((root_id, child_id))
    composition.session_factory.assert_not_called()


def test_qualified_names_keep_same_source_prefixes_distinct_and_ascii_bounded():
    owner = uuid4()
    first = _qualified_tool_name(owner, "A very long tool name that shares a prefix" * 3)
    second = _qualified_tool_name(owner, "A very long tool name that shares a prefix?" * 3)

    assert first != second
    assert len(first) <= 64
    assert first.isascii()
