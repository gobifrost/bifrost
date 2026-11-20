"""Framework capability loading keeps Bifrost's chat event/history contract."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart

from src.models.contracts.agents import ToolResult
from src.models.enums import MessageRole
from src.services.agent_runtime.composition_view import (
    AgentCompositionSnapshot, DeferredAgentSnapshot, ToolBinding,
    _capability_id, _qualified_tool_name,
)
from src.services.llm.base import LLMConfig, LLMMessage, ToolDefinition
from src.services.llm.pydantic_client import PydanticAIClient
from tests.unit.services.test_agent_executor_runtime import (
    CountingTestModel,
    _saved_message,
    caller as caller,
    configured_chat_model as configured_chat_model,
    conversation as conversation,
    executor as executor,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["specialist_read", "list_artifacts"])
async def test_native_skill_load_and_tool_result_are_streamed_and_persisted(executor, conversation, caller, tool_name):
    agent = MagicMock()
    agent.id = uuid4()
    agent.name = "Task Owner"
    agent.organization_id = None
    agent.delegated_agents = [SimpleNamespace(id=uuid4())]
    agent.max_iterations = None
    agent.max_token_budget = None
    agent.llm_max_tokens = None
    owner = SimpleNamespace(id=uuid4())
    is_specialist = tool_name == "specialist_read"
    if is_specialist:
        tool_name = _qualified_tool_name(owner.id, "read")
    capability_id = _capability_id(owner.id)
    definitions = [ToolDefinition(
        name="read", description="Read the approved resource.",
        parameters={"type": "object", "properties": {}},
    )]
    binding = SimpleNamespace(owner=owner, original_name="read", arguments={}, workflow_ids={})

    entry_definitions = (
        [
            ToolDefinition(
                name="list_artifacts",
                description="List files",
                parameters={"type": "object", "properties": {}},
            )
        ]
        if tool_name == "list_artifacts"
        else []
    )

    bindings = {
        _qualified_tool_name(owner.id, "read"): ToolBinding(
            name=_qualified_tool_name(owner.id, "read"), owner_id=owner.id,
            original_name="read", path=(agent.id, owner.id),
            capability_id=capability_id, parameters=definitions[0].parameters,
        ),
        **{definition.name: ToolBinding(
            name=definition.name, owner_id=agent.id, original_name=definition.name,
            path=(agent.id,), capability_id=None, parameters=definition.parameters,
        ) for definition in entry_definitions},
    }
    composition = SimpleNamespace(
        instructions="\n\nRetain task ownership.",
        entry_definitions=entry_definitions,
        snapshot=AgentCompositionSnapshot.from_parts(
            root_id=agent.id, instructions="Retain task ownership.",
            entry_definitions=entry_definitions, bindings=bindings,
            deferred=[DeferredAgentSnapshot(
                agent_id=owner.id, name="Read Specialist", description="Read specialist",
                system_prompt="Read before deciding.", path=(agent.id, owner.id),
                definitions=tuple(definitions), capability_id=capability_id,
            )],
            isolated_delegates={},
        ),
        authorize_capability_load=AsyncMock(),
        resolve_call=AsyncMock(return_value=binding),
    )

    class LoadingModel(CountingTestModel):
        def _request(self, messages, model_settings, parameters):
            returned = {
                part.tool_name for message in messages if isinstance(message, ModelRequest)
                for part in message.parts if isinstance(part, ToolReturnPart)
            }
            visible = {
                tool.name
                for tool in parameters.function_tools
                if parameters.visibility_of(tool.name) in {"visible", "via_history"}
            }
            if "load_capability" not in returned:
                return ModelResponse(parts=[ToolCallPart("load_capability", {"id": capability_id}, "load")])
            if "search_tools" not in returned:
                assert tool_name not in visible
                assert "run_code" in visible
                assert "search_tools" in visible
                query = (
                    "approved resource"
                    if is_specialist
                    else "list files"
                )
                return ModelResponse(parts=[ToolCallPart(
                    "search_tools", {"queries": [query]}, "search",
                )])
            if "run_code" not in returned:
                return ModelResponse(parts=[ToolCallPart(
                    "run_code", {"code": f"{tool_name}()"}, "code",
                )])
            return ModelResponse(parts=[TextPart("Verified.")])

    executor._save_message = AsyncMock(side_effect=_saved_message)
    executor._update_tool_call_message = AsyncMock()
    executor._record_ai_usage = AsyncMock()
    executor._get_agent_tools = AsyncMock(return_value=[])
    executor._build_message_history = AsyncMock(return_value=[
        LLMMessage(role="system", content="Keep task ownership."),
        LLMMessage(role="user", content="Verify the result."),
    ])
    executor._execute_tool = AsyncMock(return_value=ToolResult(
        tool_call_id="read", tool_name="read", result={"verified": True},
    ))
    client = PydanticAIClient(LLMConfig(provider="openai", model="test-model", api_key="test-key"))
    with (
        patch("src.services.agent_executor.authorize_agent_execution", new=AsyncMock(return_value=(caller, agent))),
        patch("src.services.agent_executor.build_agent_composition", new=AsyncMock(return_value=composition)),
        patch("src.services.agent_executor.get_llm_client", new=AsyncMock(return_value=client)),
        patch("src.services.agent_runtime.model_factory.create_agent_model", return_value=LoadingModel()),
    ):
        chunks = [chunk async for chunk in executor.chat(
            agent, conversation, "Verify the result.", stream=True,
            enable_routing=False, caller=caller, run_user_id=None,
            agent_execution_mode="automatic",
        )]

    calls = [chunk.tool_call.name for chunk in chunks if chunk.type == "tool_call"]
    results = [chunk.tool_result.tool_name for chunk in chunks if chunk.type == "tool_result"]
    assert [chunk.error for chunk in chunks if chunk.type == "error"] == []
    assert calls == ["load_capability", "search_tools", "run_code", tool_name]
    assert results == ["load_capability", "search_tools", tool_name, "run_code"]
    assert chunks[-1].type == "done"
    persisted_results = [
        call.kwargs for call in executor._save_message.await_args_list
        if call.kwargs.get("role") == MessageRole.TOOL
    ]
    assert [row["tool_name"] for row in persisted_results] == results
    nested_call = next(
        chunk.tool_call for chunk in chunks
        if chunk.type == "tool_call" and chunk.tool_call.name == tool_name
    )
    assert [row["tool_call_id"] for row in persisted_results] == ["load", "search", nested_call.id, "code"]
    completed = [
        call for call in executor._update_tool_call_message.await_args_list
        if call.kwargs["tool_state"] == "completed"
    ]
    assert len(completed) == 4
    executor._execute_tool.assert_awaited_once()
    assert executor._execute_tool.await_args.args[0].name == ("read" if is_specialist else tool_name)
    assert executor._execute_tool.await_args.args[1] is (owner if is_specialist else agent)
    if tool_name == "list_artifacts":
        composition.resolve_call.assert_not_awaited()
