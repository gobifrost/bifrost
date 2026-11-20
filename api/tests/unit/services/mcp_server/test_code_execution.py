"""Inline execution keeps the REST boundary and existing tool schemas."""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.services.mcp_server.server import get_system_tools
from src.services.mcp_server.tools.code_execution import bifrost_execute_code


@pytest.mark.asyncio
async def test_submits_inline_code_and_returns_the_existing_execution_receipt():
    context = object()
    execution_id, org_id = str(uuid4()), str(uuid4())
    receipt = {"execution_id": execution_id, "status": "Pending"}
    with patch(
        "src.services.mcp_server.tools.code_execution.call_rest",
        new=AsyncMock(return_value=(200, receipt)),
    ) as call_rest:
        result = await bifrost_execute_code(
            context, "result = count * 2", input_data={"count": 21},
            script_name="one_off", org_id=org_id,
        )

    call_rest.assert_awaited_once_with(
        context, "POST", "/api/workflows/execute", json_body={
            "code": "result = count * 2", "input_data": {"count": 21},
            "script_name": "one_off", "org_id": org_id,
        },
    )
    assert result.structured_content == {**receipt, "async": True}


@pytest.mark.asyncio
async def test_omitted_org_preserves_the_existing_execution_context():
    with patch(
        "src.services.mcp_server.tools.code_execution.call_rest",
        new=AsyncMock(return_value=(200, {"execution_id": str(uuid4()), "status": "Pending"})),
    ) as call_rest:
        await bifrost_execute_code(object(), "result = 42")

    body = call_rest.await_args.kwargs["json_body"]
    assert body == {"code": "result = 42", "input_data": {}, "script_name": "inline_script"}


@pytest.mark.asyncio
async def test_rest_denial_is_an_error_instead_of_a_queued_execution():
    with patch(
        "src.services.mcp_server.tools.code_execution.call_rest",
        new=AsyncMock(return_value=(403, {"detail": "Execute Code is required"})),
    ):
        result = await bifrost_execute_code(object(), "result = 42")

    assert result.is_error
    assert "Execute Code is required" in result.structured_content["error"]
    assert "execution_id" not in result.structured_content


def test_assigned_tool_schema_accepts_structured_input_values():
    from src.services.capabilities.catalog import require_valid_capability_arguments

    tool = next(tool for tool in get_system_tools() if tool["id"] == "bifrost_execute_code")
    assert tool["parameters"]["required"] == ["code"]
    require_valid_capability_arguments(tool["parameters"], {
        "code": "result = records", "input_data": {"records": [1, True, {"name": "a"}]},
    })


@pytest.mark.asyncio
async def test_native_agent_receives_execution_learning_and_module_tools_when_assigned():
    from types import SimpleNamespace

    from src.services.execution.agent_helpers import resolve_agent_tools

    assigned = {"bifrost_execute_code", "bifrost_get_docs", "bifrost_execution_get",
                "list_content", "bifrost_file_search", "read_content_lines"}
    agent = SimpleNamespace(
        system_tools=list(assigned), tools=[], knowledge_sources=[],
        delegated_agents=[], organization_id=None,
    )
    tools, _ = await resolve_agent_tools(agent, AsyncMock())
    assert {tool.name for tool in tools} == assigned
    execute = next(tool for tool in tools if tool.name == "bifrost_execute_code")
    assert "bifrost_get_docs" in execute.description
    assert "bifrost_execution_get" in execute.description
    assert "repository.read" in execute.description

    agent.system_tools = ["bifrost_execute_code"]
    tools, _ = await resolve_agent_tools(agent, AsyncMock())
    assert {tool.name for tool in tools} == {"bifrost_execute_code"}
