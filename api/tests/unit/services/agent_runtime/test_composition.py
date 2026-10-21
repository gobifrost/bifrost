"""Contracts for the shared agent-runtime composition adapter."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from pydantic_ai import Agent as PydanticAgent
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolSearchReturnPart,
)
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
        bindings=bindings or {},
        deferred=deferred,
        isolated_delegates=delegates or {},
    )


@pytest.mark.asyncio
async def test_loaded_skill_searches_and_reveals_only_relevant_individual_tools():
    """Loading instructions and discovering individual tools are separate stages."""
    root_id, child_id, nested_id = uuid4(), uuid4(), uuid4()
    case_search = ToolDefinition(
        name="search_case",
        description="Find a case.",
        parameters={
            "type": "object",
            "properties": {"case": {"type": "string"}},
            "required": ["case"],
        },
    )
    case_archive = ToolDefinition(
        name="archive_record",
        description="Archive a completed record.",
        parameters={"type": "object", "properties": {"record_id": {"type": "string"}}},
    )
    nested_audit = ToolDefinition(
        name="audit_invoice",
        description="Audit an invoice for billing discrepancies.",
        parameters={"type": "object", "properties": {"invoice": {"type": "string"}}},
    )
    child = SimpleNamespace(
        id=child_id,
        name="Case Specialist",
        system_prompt="Find evidence before answering.",
    )
    nested = SimpleNamespace(
        id=nested_id,
        name="Billing Specialist",
        system_prompt="Audit billing evidence before answering.",
    )
    capability_id = _capability_id(child_id)
    nested_capability_id = _capability_id(nested_id)
    qualified = _qualified_tool_name(child_id, case_search.name)
    archive_qualified = _qualified_tool_name(child_id, case_archive.name)
    nested_qualified = _qualified_tool_name(nested_id, nested_audit.name)
    bindings = {
        name: ToolBinding(
            name=name,
            owner_id=owner.id,
            original_name=source.name,
            path=path,
            capability_id=capability,
            parameters=source.parameters,
        )
        for name, owner, source, path, capability in (
            (qualified, child, case_search, (root_id, child_id), capability_id),
            (archive_qualified, child, case_archive, (root_id, child_id), capability_id),
            (nested_qualified, nested, nested_audit, (root_id, child_id, nested_id), nested_capability_id),
        )
    }
    composition = _composition(
        deferred=(
            _DeferredAgent(
                agent=child,
                path=(root_id, child_id),
                definitions=(case_search, case_archive),
                capability_id=capability_id,
            ),
            _DeferredAgent(
                agent=nested,
                path=(root_id, child_id, nested_id),
                definitions=(nested_audit,),
                capability_id=nested_capability_id,
            ),
        ),
        bindings=bindings,
    )
    composition.root_id = root_id
    composition.authorize_capability_load = AsyncMock()
    executed = AsyncMock(side_effect=["case evidence", "invoice evidence"])
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
            assert not names & {qualified, archive_qualified, nested_qualified}
            return ModelResponse(parts=[ToolCallPart("load_capability", {"id": capability_id}, "load")])
        if requests == 2:
            assert "search_tools" in names
            assert not names & {qualified, archive_qualified, nested_qualified}
            return ModelResponse(parts=[ToolCallPart("search_tools", {"queries": ["find"]}, "search-case")])
        if requests == 3:
            assert qualified in names
            assert archive_qualified not in names
            assert nested_qualified not in names
            return ModelResponse(parts=[ToolCallPart(qualified, {"case": "123"}, "tool")])
        if requests == 4:
            return ModelResponse(parts=[ToolCallPart("load_capability", {"id": nested_capability_id}, "load-nested")])
        if requests == 5:
            assert "search_tools" in names
            assert nested_qualified not in names
            return ModelResponse(parts=[ToolCallPart("search_tools", {"queries": ["invoice audit"]}, "search-nested")])
        if requests == 6:
            assert qualified in names
            assert archive_qualified not in names
            assert nested_qualified in names
            return ModelResponse(parts=[ToolCallPart(nested_qualified, {"invoice": "456"}, "nested-tool")])
        return ModelResponse(parts=[TextPart("done")])

    result = await PydanticAgent(
        FunctionModel(model), capabilities=capabilities
    ).run("help")

    assert result.output == "done"
    assert [call.args for call in executed.await_args_list] == [
        (qualified, {"case": "123"}, "tool"),
        (nested_qualified, {"invoice": "456"}, "nested-tool"),
    ]
    composition.authorize_capability_load.assert_awaited()
    discovered = [
        match["name"]
        for message in result.all_messages()
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolSearchReturnPart)
        for match in part.discovered_tools
    ]
    assert discovered == [qualified, nested_qualified]


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
            assert next(tool for tool in info.function_tools if tool.name == "delegate_task").sequential
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
@pytest.mark.parametrize("correct_selection", [True, False])
async def test_unknown_subagent_gets_one_selection_correction_without_an_effect(correct_selection):
    root_id, child_id = uuid4(), uuid4()
    child = SimpleNamespace(id=child_id, name="Investigator")
    name = _delegate_name(child)
    binding = ToolBinding(
        name="delegate_task", owner_id=root_id, original_name="delegate_to_investigator",
        path=(root_id,), capability_id=None,
        parameters={"type": "object", "properties": {"task": {"type": "string"}}},
        delegated_target_path=(root_id, child_id),
    )
    composition = _composition(delegates={name: binding})
    executed = AsyncMock(return_value="verified child receipt")
    requests = 0

    def model(_, info):
        nonlocal requests
        requests += 1
        if requests <= 2:
            return ModelResponse(parts=[ToolCallPart(
                "delegate_task",
                {"agent_name": name if requests == 2 and correct_selection else "not_in_the_roster", "task": "inspect"},
                f"delegate-{requests}",
            )])
        return ModelResponse(parts=[TextPart("recovered")])

    runtime = PydanticAgent(
        FunctionModel(model), capabilities=composition.capabilities(executed), retries=1,
    )
    if not correct_selection:
        with pytest.raises(UnexpectedModelBehavior, match="max retries"):
            await runtime.run("inspect")
        assert requests == 2
        executed.assert_not_awaited()
        return
    result = await runtime.run("inspect")
    assert result.output == "recovered"
    executed.assert_awaited_once_with(
        "delegate_task", {"agent_name": name, "task": "inspect"}, "delegate-2",
    )


@pytest.mark.asyncio
async def test_revoked_loaded_capability_stops_before_the_next_model_request():
    root_id, child_id = uuid4(), uuid4()
    capability_id = _capability_id(child_id)
    child = SimpleNamespace(id=child_id, name="Specialist", system_prompt="Private instructions")
    composition = _composition(deferred=[_DeferredAgent(
        agent=child, path=(root_id, child_id), definitions=(), capability_id=capability_id,
    )])
    composition.authorize_capability_load = AsyncMock(side_effect=[
        None, AgentCompositionError("Specialist revoked"),
    ])
    executed = AsyncMock()
    requests = 0

    def model(_, info):
        nonlocal requests
        requests += 1
        return ModelResponse(parts=[ToolCallPart("load_capability", {"id": capability_id}, "load")])

    with pytest.raises(AgentCompositionError, match="Specialist revoked"):
        await PydanticAgent(
            FunctionModel(model), capabilities=composition.capabilities(executed),
        ).run("inspect")
    assert requests == 1
    executed.assert_not_awaited()


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


def test_long_subagent_names_retain_the_owner_identity():
    first = SimpleNamespace(id=uuid4(), name="Shared specialist name " * 10)
    second = SimpleNamespace(id=uuid4(), name=first.name)
    assert _delegate_name(first) != _delegate_name(second)
    assert _delegate_name(first).endswith(first.id.hex)
    assert len(_delegate_name(first)) <= 64


def test_owner_names_do_not_conflate_uuid_prefixes():
    first = UUID("12345678-1234-0000-0000-000000000001")
    second = UUID("12345678-1234-0000-0000-000000000002")
    assert _qualified_tool_name(first, "read") != _qualified_tool_name(second, "read")
    assert _delegate_name(SimpleNamespace(id=first, name="Same")) != _delegate_name(SimpleNamespace(id=second, name="Same"))
