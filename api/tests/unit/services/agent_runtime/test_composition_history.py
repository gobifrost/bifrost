"""History and owner-isolation contracts for native agent capabilities.

Chat persistence stores Bifrost's public ``LLMMessage`` shape, so a resumed
turn must reconstruct Pydantic AI's deferred-capability state from the durable
tool call and return receipt alone.  The framework-only availability delta is
intentionally not persisted.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from pydantic_ai import Agent as PydanticAgent
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    LoadCapabilityCallPart,
    LoadCapabilityReturnPart,
    TextPart,
    ToolAvailabilityDeltaPart,
    ToolCallPart,
    ToolReturnPart,
    ToolSearchCallPart,
    ToolSearchReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import FunctionModel

from src.services.agent_runtime.composition import (
    AgentComposition,
    ToolBinding,
    _DeferredAgent,
    _capability_id,
    _qualified_tool_name,
)
from src.services.llm import LLMMessage, ToolCallRequest, ToolDefinition
from src.services.llm.pydantic_client import PydanticAIClient


def _composition(*, root_id, deferred=(), bindings=None, session_factory=None):
    return AgentComposition(
        root_id=root_id,
        caller=SimpleNamespace(),
        session_factory=session_factory or (lambda: None),
        caller_user_id=uuid4(),
        instructions="Root instructions remain in effect.",
        entry_definitions=(),
        bindings=bindings or {},
        deferred=deferred,
        isolated_delegates={},
    )


def _legacy_history_without_availability_deltas(messages):
    """Persist only the history shapes represented by the chat database."""
    history: list[LLMMessage] = []
    for message in messages:
        if isinstance(message, ModelResponse):
            tool_calls = [
                ToolCallRequest(
                    id=part.tool_call_id,
                    name=part.tool_name,
                    arguments=part.args_as_dict(),
                )
                for part in message.parts
                if isinstance(part, ToolCallPart)
            ]
            content = "".join(
                part.content for part in message.parts if isinstance(part, TextPart)
            ) or None
            if content or tool_calls:
                history.append(
                    LLMMessage(
                        role="assistant",
                        content=content,
                        tool_calls=tool_calls or None,
                    )
                )
            continue

        assert isinstance(message, ModelRequest)
        for part in message.parts:
            if isinstance(part, ToolAvailabilityDeltaPart):
                continue
            elif isinstance(part, ToolReturnPart):
                history.append(
                    LLMMessage(
                        role="tool",
                        # The chat DTO records a JSON value, not an in-memory
                        # Pydantic message part.
                        content=json.dumps(part.content),
                        tool_call_id=part.tool_call_id,
                        tool_name=part.tool_name,
                    )
                )
            elif isinstance(part, UserPromptPart):
                history.append(LLMMessage(role="user", content=str(part.content)))

    return history


def test_only_completed_framework_receipts_restore_typed_framework_parts():
    """Ordinary namesake tools must retain generic Pydantic message parts."""
    converted = PydanticAIClient.convert_messages(
        [
            LLMMessage(
                role="assistant",
                tool_calls=[
                    ToolCallRequest(
                        id="namesake",
                        name="load_capability",
                        arguments={"id": "not-a-framework-capability"},
                    )
                ],
            ),
            LLMMessage(
                role="tool",
                content=json.dumps({"error": "not available"}),
                tool_call_id="namesake",
                tool_name="load_capability",
            ),
            LLMMessage(
                role="assistant",
                tool_calls=[
                    ToolCallRequest(
                        id="search-namesake",
                        name="search_tools",
                        arguments={"queries": ["specialist"]},
                    )
                ],
            ),
            LLMMessage(
                role="tool",
                content=json.dumps({"error": "not available"}),
                tool_call_id="search-namesake",
                tool_name="search_tools",
            ),
        ]
    )

    call = converted[0].parts[0]
    receipt = converted[1].parts[0]
    search_call = converted[2].parts[0]
    search_receipt = converted[3].parts[0]
    assert type(call) is ToolCallPart
    assert type(receipt) is ToolReturnPart
    assert type(search_call) is ToolCallPart
    assert type(search_receipt) is ToolReturnPart


@pytest.mark.asyncio
async def test_deferred_capability_survives_legacy_chat_history_without_a_second_load():
    """A resumed chat restores native loaded tools from durable receipts.

    This is the compatibility boundary for chat resumption: Bifrost persists
    assistant ToolCall and tool ToolReturn records, but not Pydantic AI's
    transient ToolAvailabilityDeltaPart.
    """
    root_id, child_id = uuid4(), uuid4()
    source = ToolDefinition(
        name="search_knowledge",
        description="Search this specialist's knowledge.",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    )
    child = SimpleNamespace(
        id=child_id,
        name="Knowledge Specialist",
        system_prompt="Use evidence from the assigned knowledge source.",
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

    def runtime(*, root_id):
        composition = _composition(
            root_id=root_id,
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
        # The real framework still owns load state. This callback is the
        # composition's authorization seam, not a replacement for it.
        composition.authorize_capability_load = AsyncMock()
        return composition

    first = runtime(root_id=root_id)
    first_executed = AsyncMock(return_value={"evidence": "case 123"})
    first_requests = 0

    def first_model(_, info):
        nonlocal first_requests
        first_requests += 1
        visible = {
            tool.name
            for tool in info.function_tools
            if info.model_request_parameters.visibility_of(tool.name)
            in {"visible", "via_history"}
        }
        if first_requests == 1:
            assert qualified not in visible
            return ModelResponse(
                parts=[ToolCallPart("load_capability", {"id": capability_id}, "load")]
            )
        if first_requests == 2:
            assert qualified not in visible
            assert "search_tools" in visible
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "search_tools",
                        {"queries": ["specialist knowledge"]},
                        "search",
                    )
                ]
            )
        if first_requests == 3:
            assert qualified in visible
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        qualified,
                        {"query": "case 123"},
                        "specialist-tool",
                    )
                ]
            )
        return ModelResponse(parts=[TextPart("first turn complete")])

    first_result = await PydanticAgent(
        FunctionModel(first_model),
        instructions="Root instructions remain in effect.",
        capabilities=first.capabilities(first_executed),
    ).run("find the case")
    legacy_history = _legacy_history_without_availability_deltas(first_result.all_messages())
    restored_history = PydanticAIClient.convert_messages(legacy_history)

    load_call = next(
        call
        for message in legacy_history
        for call in message.tool_calls or []
        if call.name == "load_capability"
    )
    load_return = next(message for message in legacy_history if message.tool_call_id == "load")
    assert load_call.id == "load"
    assert load_call.arguments == {"id": capability_id}
    assert load_return.tool_name == "load_capability"
    assert load_return.content == json.dumps(
        next(
            part.content
            for message in first_result.all_messages()
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart) and part.tool_call_id == "load"
        )
    )
    restored_load_call = next(
        part
        for message in restored_history
        if isinstance(message, ModelResponse)
        for part in message.parts
        if isinstance(part, LoadCapabilityCallPart)
    )
    restored_load_return = next(
        part
        for message in restored_history
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, LoadCapabilityReturnPart)
    )
    assert restored_load_call.tool_kind == "capability-load"
    assert restored_load_return.tool_kind == "capability-load"
    restored_search_call = next(
        part
        for message in restored_history
        if isinstance(message, ModelResponse)
        for part in message.parts
        if isinstance(part, ToolSearchCallPart)
    )
    restored_search_return = next(
        part
        for message in restored_history
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolSearchReturnPart)
    )
    assert restored_search_call.queries == ["specialist knowledge"]
    assert restored_search_return.discovered_tools == [{"name": qualified}]

    second = runtime(root_id=root_id)
    second_executed = AsyncMock(return_value={"evidence": "case 456"})
    second_requests = 0

    def resumed_model(_, info):
        nonlocal second_requests
        second_requests += 1
        visible = {
            tool.name
            for tool in info.function_tools
            if info.model_request_parameters.visibility_of(tool.name)
            in {"visible", "via_history"}
        }
        # The next turn restores both the loaded specialist and its discovered
        # tool; the model repeats neither framework-managed action.
        assert qualified in visible
        if second_requests == 1:
            return ModelResponse(parts=[ToolCallPart(qualified, {"query": "case 456"}, "resume")])
        return ModelResponse(parts=[TextPart("resumed turn complete")])

    result = await PydanticAgent(
        FunctionModel(resumed_model),
        instructions="Root instructions remain in effect.",
        capabilities=second.capabilities(second_executed),
    ).run("continue", message_history=restored_history)

    assert result.output == "resumed turn complete"
    second_executed.assert_awaited_once_with(qualified, {"query": "case 456"}, "resume")
    assert second_requests == 2


@pytest.mark.asyncio
async def test_same_named_knowledge_tools_resolve_to_their_fresh_owner_without_conflation():
    """Owner-qualified names preserve independent knowledge-source bindings."""
    root_id, first_id, second_id = uuid4(), uuid4(), uuid4()
    schema = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    }
    source = ToolDefinition(
        name="search_knowledge",
        description="Search knowledge.",
        parameters=schema,
    )
    first_owner = SimpleNamespace(id=first_id, knowledge_sources=["customers"])
    second_owner = SimpleNamespace(id=second_id, knowledge_sources=["vendors"])
    first_name = _qualified_tool_name(first_id, source.name)
    second_name = _qualified_tool_name(second_id, source.name)
    bindings = {
        first_name: ToolBinding(
            name=first_name,
            owner_id=first_id,
            original_name=source.name,
            path=(root_id, first_id),
            capability_id=_capability_id(first_id),
            parameters=schema,
        ),
        second_name: ToolBinding(
            name=second_name,
            owner_id=second_id,
            original_name=source.name,
            path=(root_id, second_id),
            capability_id=_capability_id(second_id),
            parameters=schema,
        ),
    }

    @asynccontextmanager
    async def sessions():
        yield SimpleNamespace()

    composition = _composition(
        root_id=root_id,
        bindings=bindings,
        session_factory=sessions,
    )
    composition._authorize_path = AsyncMock(side_effect=[first_owner, second_owner])

    async def resolve_tools(owner, _session, *, caller_user_id):
        assert caller_user_id == composition.caller_user_id
        assert owner in (first_owner, second_owner)
        return [source], {}

    with patch(
        "src.services.agent_runtime.composition.resolve_agent_tools",
        new=AsyncMock(side_effect=resolve_tools),
    ) as resolve_agent_tools:
        first = await composition.resolve_call(first_name, {"query": "renewal"})
        second = await composition.resolve_call(second_name, {"query": "invoice"})

    assert first_name != second_name
    assert first.owner is first_owner
    assert first.arguments == {"query": "renewal"}
    assert first.provenance["source_agent_id"] == str(first_id)
    assert first_owner.knowledge_sources == ["customers"]
    assert second.owner is second_owner
    assert second.arguments == {"query": "invoice"}
    assert second.provenance["source_agent_id"] == str(second_id)
    assert second_owner.knowledge_sources == ["vendors"]
    assert [call.args[0] for call in resolve_agent_tools.await_args_list] == [
        first_owner,
        second_owner,
    ]
