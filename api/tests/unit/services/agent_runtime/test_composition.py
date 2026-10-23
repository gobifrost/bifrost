"""Contracts for the shared agent-runtime composition adapter."""

import json

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from pydantic_ai import Agent as PydanticAgent
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolSearchReturnPart,
)
from pydantic_ai.models.function import FunctionModel

from src.services.agent_runtime.composition import (
    AgentComposition,
    COMPOSITION_INSTRUCTIONS,
    DELEGATED_COMPOSITION_INSTRUCTIONS,
    AgentCompositionError,
    ChildBinding,
    ToolBinding,
    _DeferredAgent,
    _capability_id,
    composition_instructions,
    _qualified_tool_name,
)
from src.services.llm import ToolDefinition


def _composition(*, deferred=(), delegates=None, bindings=None, automatic=True):
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
        automatic=automatic,
    )


def test_composition_instructions_prefer_discovered_same_conversation_tools():
    """Runtime guidance keeps tool selection bounded to discovered callables."""
    instructions = " ".join(COMPOSITION_INSTRUCTIONS.split())
    assert "exact callable name and input schema" in instructions
    assert "supplied by the runtime or returned by discovery" in instructions
    assert "Never guess a tool name, arguments, or schema" in instructions
    assert "same-conversation specialist tools by default" in instructions
    assert "context isolation or independent review" in instructions
    assert "permission workaround" in instructions
    assert "delegate_to_" not in instructions


def test_delegated_composition_instructions_name_only_direct_tools_and_native_children():
    """Delegated mode must not prompt the model to use unavailable discovery."""
    instructions = " ".join(composition_instructions(automatic=False).split())

    assert instructions == " ".join(DELEGATED_COMPOSITION_INSTRUCTIONS.split())
    assert "exact callable name and input schema" in instructions
    assert "directly by the runtime" in instructions
    assert "delegate_task" in instructions
    assert "search_tools" not in instructions
    assert "load an approved capability" not in instructions


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

    def model(messages, info):
        nonlocal requests
        requests += 1
        names = {tool.name for tool in info.function_tools}
        if requests == 1:
            assert "load_capability" in names
            assert not names & {qualified, archive_qualified, nested_qualified}
            # A model may mistake a tool name for its parent capability id.
            # Pydantic AI must return its ordinary no-such-capability correction
            # without reaching Bifrost authorization or the effect executor.
            return ModelResponse(
                parts=[ToolCallPart("load_capability", {"id": case_search.name}, "unknown-load")]
            )
        if requests == 2:
            assert "load_capability" in names
            assert not names & {qualified, archive_qualified, nested_qualified}
            assert any(
                isinstance(part, RetryPromptPart)
                and part.tool_call_id == "unknown-load"
                and "No capability found" in str(part.content)
                for message in messages
                if isinstance(message, ModelRequest)
                for part in message.parts
            )
            executed.assert_not_awaited()
            composition.authorize_capability_load.assert_not_awaited()
            return ModelResponse(parts=[ToolCallPart("load_capability", {"id": capability_id}, "load")])
        if requests == 3:
            assert "search_tools" in names
            assert not names & {qualified, archive_qualified, nested_qualified}
            return ModelResponse(parts=[ToolCallPart("search_tools", {"queries": ["find"]}, "search-case")])
        if requests == 4:
            assert qualified in names
            assert archive_qualified not in names
            assert nested_qualified not in names
            return ModelResponse(parts=[ToolCallPart(qualified, {"case": "123"}, "tool")])
        if requests == 5:
            return ModelResponse(parts=[ToolCallPart("load_capability", {"id": nested_capability_id}, "load-nested")])
        if requests == 6:
            assert "search_tools" in names
            assert nested_qualified not in names
            return ModelResponse(parts=[ToolCallPart("search_tools", {"queries": ["invoice audit"]}, "search-nested")])
        if requests == 7:
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
    assert all(
        call.args[0] != case_search.name
        for call in composition.authorize_capability_load.await_args_list
    )
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
async def test_guessed_action_name_is_corrected_before_discovery_and_effect():
    """The framework rejects a guessed callable before Bifrost can dispatch it."""
    root_id = uuid4()
    definition = ToolDefinition(
        name="halopsa_submit_triage",
        description="Submit a reviewed triage result.",
        parameters={
            "type": "object",
            "properties": {"ticket_id": {"type": "integer"}},
            "required": ["ticket_id"],
        },
    )
    binding = ToolBinding(
        name=definition.name,
        owner_id=root_id,
        original_name=definition.name,
        path=(root_id,),
        capability_id=None,
        parameters=definition.parameters,
    )
    composition = _composition(bindings={definition.name: binding})
    composition.root_id = root_id
    composition.entry_definitions = (definition,)
    executed = AsyncMock(return_value="triage submitted")
    requests = 0

    def model(messages, info):
        nonlocal requests
        requests += 1
        names = {tool.name for tool in info.function_tools}
        if requests == 1:
            assert definition.name not in names
            return ModelResponse(parts=[ToolCallPart(
                "halopsa_halopsa_submit_triage", {"ticket_id": 123}, "guessed-action",
            )])
        if requests == 2:
            assert any(
                isinstance(part, RetryPromptPart)
                and part.tool_call_id == "guessed-action"
                and "halopsa_halopsa_submit_triage" in str(part.content)
                for message in messages
                if isinstance(message, ModelRequest)
                for part in message.parts
            )
            executed.assert_not_awaited()
            return ModelResponse(parts=[ToolCallPart(
                "search_tools", {"queries": ["submit triage"]}, "discover-action",
            )])
        if requests == 3:
            assert definition.name in names
            return ModelResponse(parts=[ToolCallPart(
                definition.name, {"ticket_id": 123}, "exact-action",
            )])
        return ModelResponse(parts=[TextPart("done")])

    result = await PydanticAgent(
        FunctionModel(model),
        toolsets=[composition.entry_toolset(executed)],
        capabilities=composition.capabilities(executed),
    ).run("Submit triage for ticket 123")

    assert result.output == "done"
    executed.assert_awaited_once_with(definition.name, {"ticket_id": 123}, "exact-action")


@pytest.mark.asyncio
async def test_automatic_toolsets_keep_native_subagents_outside_discovery():
    """Automatic discovery defers domain tools while native children stay available."""
    root_id, child_id = uuid4(), uuid4()
    child = SimpleNamespace(id=child_id, name="Case Specialist", system_prompt="Find cases.")
    capability_id = _capability_id(child_id)
    delegate_name = child_id.hex
    root_case = ToolDefinition(
        name="halopsa_get_case",
        description="Get one HaloPSA case.",
        parameters={"type": "object", "properties": {}},
    )
    child_case = ToolDefinition(
        name="halopsa_search_case_notes",
        description="Search HaloPSA case notes.",
        parameters={
            "type": "object",
            "properties": {"case_id": {"type": "integer"}},
            "required": ["case_id"],
        },
    )
    qualified_child_case = _qualified_tool_name(child_id, child_case.name)
    bindings = {
        definition.name: ToolBinding(
            name=definition.name,
            owner_id=root_id,
            original_name=definition.name,
            path=(root_id,),
            capability_id=None,
            parameters=definition.parameters,
        )
        for definition in (root_case,)
    }
    bindings.update({
        qualified_child_case: ToolBinding(
            name=qualified_child_case,
            owner_id=child_id,
            original_name=child_case.name,
            path=(root_id, child_id),
            capability_id=capability_id,
            parameters=child_case.parameters,
        ),
    })
    delegate_binding = ChildBinding(
        owner_id=root_id,
        parent_path=(root_id,),
        target_path=(root_id, child_id),
    )
    composition = AgentComposition(
        root_id=root_id,
        caller=SimpleNamespace(),
        session_factory=AsyncMock(),
        caller_user_id=uuid4(),
        instructions="",
        entry_definitions=(root_case,),
        bindings=bindings,
        deferred=(
            _DeferredAgent(
                agent=child,
                path=(root_id, child_id),
                definitions=(child_case,),
                capability_id=capability_id,
            ),
        ),
        isolated_delegates={delegate_name: delegate_binding},
    )
    composition.authorize_capability_load = AsyncMock()
    executed = AsyncMock(side_effect=["root case", "case notes", "child receipt"])
    requests = 0

    def model(_, info):
        nonlocal requests
        requests += 1
        names = {tool.name for tool in info.function_tools}
        if requests == 1:
            assert root_case.name not in names
            return ModelResponse(parts=[ToolCallPart(
                "search_tools", {"queries": ["get case"]}, "search-root",
            )])
        if requests == 2:
            assert root_case.name in names
            return ModelResponse(parts=[ToolCallPart(root_case.name, {}, "root-case")])
        if requests == 3:
            return ModelResponse(parts=[ToolCallPart(
                "load_capability", {"id": capability_id}, "load-child",
            )])
        if requests == 4:
            assert qualified_child_case not in names
            return ModelResponse(parts=[ToolCallPart(
                "search_tools", {"queries": ["case notes"]}, "search-child",
            )])
        if requests == 5:
            assert qualified_child_case in names
            return ModelResponse(parts=[ToolCallPart(
                qualified_child_case, {"case_id": 123}, "child-case",
            )])
        if requests == 6:
            assert "delegate_task" in names
            return ModelResponse(parts=[ToolCallPart(
                "delegate_task", {"agent_name": delegate_name, "task": "independently review"},
                "delegate",
            )])
        return ModelResponse(parts=[TextPart("done")])

    result = await PydanticAgent(
        FunctionModel(model),
        toolsets=[composition.entry_toolset(executed)],
        capabilities=composition.capabilities(executed),
    ).run("Review case 123")

    assert result.output == "done"
    assert [call.args for call in executed.await_args_list] == [
        (root_case.name, {}, "root-case"),
        (qualified_child_case, {"case_id": 123}, "child-case"),
        ("delegate_task", {"agent_name": delegate_name, "task": "independently review"}, "delegate"),
    ]


@pytest.mark.asyncio
async def test_native_subagents_routes_one_isolated_child_callback_without_a_model_call():
    """The framework's delegate_task wrapper forwards its real tool-call id."""
    root_id, child_id = uuid4(), uuid4()
    name = child_id.hex
    binding = ChildBinding(
        owner_id=root_id,
        parent_path=(root_id,),
        target_path=(root_id, child_id),
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
async def test_native_delegate_resolves_a_uuid_bound_child_without_a_legacy_alias():
    """The selection key maps directly to its approved child UUID and paths."""
    root_id, child_id = uuid4(), uuid4()
    parent = SimpleNamespace(id=root_id)
    child = SimpleNamespace(id=child_id)
    binding = ChildBinding(
        owner_id=root_id,
        parent_path=(root_id,),
        target_path=(root_id, child_id),
    )
    composition = _composition(delegates={child_id.hex: binding})
    composition.root_id = root_id
    composition._authorize_path = AsyncMock(side_effect=[parent, child])

    resolved = await composition.resolve_call(
        "delegate_task", {"agent_name": child_id.hex, "task": "inspect the case"}
    )

    assert resolved.owner is parent
    assert resolved.arguments == {"task": "inspect the case"}
    assert resolved.delegated_target_id == child_id
    assert resolved.provenance["delegated_agent_id"] == str(child_id)
    assert [call.args for call in composition._authorize_path.await_args_list] == [
        ((root_id,),),
        ((root_id, child_id),),
    ]
    composition.session_factory.assert_not_called()


@pytest.mark.asyncio
async def test_delegated_mode_exposes_direct_root_tools_and_native_children_without_discovery():
    """Delegated mode keeps direct tools while using the same native child bridge."""
    root_id, child_id = uuid4(), uuid4()
    root_tool = ToolDefinition(
        name="read_case",
        description="Read a case.",
        parameters={"type": "object", "properties": {}},
    )
    composition = AgentComposition(
        root_id=root_id,
        caller=SimpleNamespace(),
        session_factory=AsyncMock(),
        caller_user_id=uuid4(),
        instructions="",
        entry_definitions=(root_tool,),
        bindings={
            root_tool.name: ToolBinding(
                name=root_tool.name,
                owner_id=root_id,
                original_name=root_tool.name,
                path=(root_id,),
                capability_id=None,
                parameters=root_tool.parameters,
            )
        },
        deferred=(),
        isolated_delegates={
            child_id.hex: ChildBinding(
                owner_id=root_id,
                parent_path=(root_id,),
                target_path=(root_id, child_id),
                target_name="Case Review Specialist",
                target_description="Reviews case evidence independently.",
            )
        },
        automatic=False,
    )
    executed = AsyncMock(return_value="child receipt")
    requests = 0

    def model(messages, info):
        nonlocal requests
        requests += 1
        names = {tool.name for tool in info.function_tools}
        assert root_tool.name in names
        assert "delegate_task" in names
        assert "search_tools" not in names
        assert "load_capability" not in names
        delegate_task = next(tool for tool in info.function_tools if tool.name == "delegate_task")
        catalog = "\n".join((
            info.instructions or "",
            json.dumps(delegate_task.parameters_json_schema),
        ))
        assert "Case Review Specialist" in catalog
        assert "Reviews case evidence independently." in catalog
        if requests == 1:
            return ModelResponse(parts=[ToolCallPart(
                "delegate_task",
                {"agent_name": child_id.hex, "task": "review independently"},
                "delegate",
            )])
        return ModelResponse(parts=[TextPart("done")])

    result = await PydanticAgent(
        FunctionModel(model),
        toolsets=[composition.entry_toolset(executed)],
        capabilities=composition.capabilities(executed),
    ).run("review")

    assert result.output == "done"
    executed.assert_awaited_once_with(
        "delegate_task",
        {"agent_name": child_id.hex, "task": "review independently"},
        "delegate",
    )


@pytest.mark.asyncio
async def test_native_subagent_returns_a_failure_receipt_so_parent_can_enrich_and_retry():
    """A child failure is recoverable evidence, matching the legacy toolset."""
    root_id, child_id = uuid4(), uuid4()
    name = child_id.hex
    binding = ChildBinding(
        owner_id=root_id,
        parent_path=(root_id,),
        target_path=(root_id, child_id),
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
    name = child_id.hex
    binding = ChildBinding(
        owner_id=root_id,
        parent_path=(root_id,),
        target_path=(root_id, child_id),
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


def test_owner_names_do_not_conflate_uuid_prefixes():
    first = UUID("12345678-1234-0000-0000-000000000001")
    second = UUID("12345678-1234-0000-0000-000000000002")
    assert _qualified_tool_name(first, "read") != _qualified_tool_name(second, "read")
