"""Return-contract coverage for Bifrost tools exposed to native CodeMode."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from pydantic_ai import Agent
from pydantic_ai.capabilities import ToolSearch
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from src.services.agent_runtime.code_mode import build_code_mode_capabilities
from src.services.agent_runtime.composition import (
    AgentComposition,
    ToolBinding,
    _DeferredAgent,
    _capability_id,
    _qualified_tool_name,
)
from src.services.agent_runtime.toolset import (
    BifrostToolset,
    get_code_mode_tool_result_scope,
    unbounded_model_tool_results,
)
from src.services.execution.agent_helpers import resolve_agent_tools
from src.services.llm import ToolDefinition
from src.services.llm.base import bifrost_adapter_return_schema


@pytest.mark.asyncio
@patch("src.services.mcp_server.server.get_system_tools")
async def test_resolver_marks_bifrost_system_adapter_with_string_contract(
    get_system_tools: MagicMock,
) -> None:
    get_system_tools.return_value = [
        {
            "id": "bifrost_lookup",
            "description": "Look up one Bifrost record.",
            "parameters": {"type": "object", "properties": {}},
        }
    ]
    agent = SimpleNamespace(
        id=uuid4(),
        organization_id=None,
        tools=[],
        system_tools=["bifrost_lookup"],
        knowledge_sources=[],
        delegated_agents=[],
    )

    definitions, workflow_ids = await resolve_agent_tools(agent, MagicMock())

    assert workflow_ids == {}
    assert definitions[0].return_schema == bifrost_adapter_return_schema()


def test_code_mode_scope_getter_is_read_only_and_scoped() -> None:
    assert get_code_mode_tool_result_scope() is None

    with unbounded_model_tool_results() as scope:
        assert get_code_mode_tool_result_scope() is scope
        assert scope.outer_tool_call_id is None

    assert get_code_mode_tool_result_scope() is None


@pytest.mark.asyncio
async def test_code_mode_decodes_json_or_text_before_the_snippet_receives_it() -> None:
    """Nested CodeMode calls receive parsed JSON while ordinary tool transport stays text."""
    seen: list[list[ModelMessage]] = []

    async def execute(name: str, arguments: dict[str, Any], tool_call_id: str) -> str:
        assert (name, arguments) == ("read_record", {"record_id": "r-7"})
        assert tool_call_id
        return json.dumps({"record_id": "r-7", "status": "active"})

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.append(messages)
        if len(seen) == 1:
            return ModelResponse(
                parts=[ToolCallPart("search_tools", {"queries": ["read record"]}, "search")]
            )
        if len(seen) == 2:
            assert info.instructions is not None
            assert "def read_record(*, record_id: str) -> Any:" in info.instructions
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "run_code",
                        {
                            "code": (
                                "record = read_record(record_id='r-7')\n"
                                "{'record_id': record['record_id'], 'status': record['status']}"
                            )
                        },
                        "code",
                    )
                ]
            )
        if len(seen) == 3:
            return ModelResponse(parts=[TextPart("complete")])
        raise AssertionError(f"unexpected request {len(seen)}")

    toolset = BifrostToolset(
        [
            ToolDefinition(
                name="read_record",
                description="Read one record.",
                parameters={
                    "type": "object",
                    "properties": {"record_id": {"type": "string"}},
                    "required": ["record_id"],
                },
                return_schema=bifrost_adapter_return_schema(),
            )
        ],
        execute,
    )
    exposed_tools = await toolset.get_tools(SimpleNamespace(capabilities={}))
    assert (
        exposed_tools["read_record"].tool_def.return_schema
        == bifrost_adapter_return_schema()
    )
    assert "-> str:" in exposed_tools["read_record"].tool_def.render_signature("...")
    assert "json.loads" in exposed_tools["read_record"].tool_def.return_schema["description"]

    result = await Agent(
        FunctionModel(model),
        toolsets=[toolset.defer_loading()],
        capabilities=(
            ToolSearch(strategy="keywords"),
            *build_code_mode_capabilities(tools=["read_record"]),
        ),
    ).run("Read the record.")

    assert result.output == "complete"
    assert _tool_returns(seen[2], "run_code") == [{"record_id": "r-7", "status": "active"}]


@pytest.mark.asyncio
async def test_composition_preserves_known_return_schema_for_specialist_tool() -> None:
    root_id, child_id = uuid4(), uuid4()
    capability_id = _capability_id(child_id)
    source = SimpleNamespace(
        id=child_id,
        name="Records Specialist",
        description="Reads approved records.",
        system_prompt="Use the record tool.",
    )
    definition = ToolDefinition(
        name="read_record",
        description="Read one record.",
        parameters={"type": "object", "properties": {}},
        return_schema=bifrost_adapter_return_schema(),
    )
    qualified_name = _qualified_tool_name(child_id, definition.name)
    composition = AgentComposition(
        root_id=root_id,
        caller=SimpleNamespace(),
        session_factory=lambda: None,
        caller_user_id=None,
        instructions="Use approved tools.",
        entry_definitions=(),
        bindings={
            qualified_name: ToolBinding(
                name=qualified_name,
                owner_id=child_id,
                original_name=definition.name,
                path=(root_id, child_id),
                capability_id=capability_id,
                parameters=definition.parameters,
            )
        },
        deferred=(
            _DeferredAgent(source, (root_id, child_id), (definition,), capability_id),
        ),
        isolated_delegates={},
    )
    composition.authorize_capability_load = AsyncMock()
    requests = 0

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal requests
        requests += 1
        if requests == 1:
            return ModelResponse(
                parts=[ToolCallPart("load_capability", {"id": capability_id}, "load")]
            )
        if requests == 2:
            return ModelResponse(
                parts=[ToolCallPart("search_tools", {"queries": ["record"]}, "search")]
            )
        if requests == 3:
            tool = next(item for item in info.function_tools if item.name == qualified_name)
            assert tool.return_schema is None
            assert tool.description is not None
            _, serialized_schema = tool.description.rsplit("Return schema:\n\n", 1)
            assert json.loads(serialized_schema) == bifrost_adapter_return_schema()
            return ModelResponse(parts=[TextPart("complete")])
        raise AssertionError(f"unexpected request {requests}")

    result = await Agent(
        FunctionModel(model), capabilities=composition.capabilities(AsyncMock())
    ).run("Read a record.")

    assert result.output == "complete"


@pytest.mark.asyncio
async def test_toolset_without_a_known_return_schema_keeps_direct_behavior() -> None:
    result_value = {"unchanged": True}

    async def execute(name: str, arguments: dict[str, Any], tool_call_id: str) -> dict[str, bool]:
        assert (name, arguments, tool_call_id) == ("unknown_result", {}, "direct")
        return result_value

    toolset = BifrostToolset(
        [
            ToolDefinition(
                name="unknown_result",
                description="Return an unknown external value.",
                parameters={"type": "object", "properties": {}},
            )
        ],
        execute,
    )
    ctx = SimpleNamespace(tool_call_id="direct", capabilities={})
    tools = await toolset.get_tools(ctx)

    assert tools["unknown_result"].tool_def.return_schema is None
    assert (
        await toolset.call_tool("unknown_result", {}, ctx, tools["unknown_result"])
        == result_value
    )


def _tool_returns(messages: list[ModelMessage], name: str) -> list[Any]:
    return [
        part.content
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.tool_name == name
    ]
