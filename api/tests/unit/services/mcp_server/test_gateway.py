"""Unit tests for the unscoped MCP agent gateway."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.models.orm.agents import Agent
from src.services.llm import ToolDefinition
from src.services.mcp_server.config_service import MCPConfig
from src.services.mcp_server.gateway import (
    AgentToolSnapshot,
    GatewayError,
    MCPAgentGatewayService,
    ResolvedGatewayTool,
)
from src.services.mcp_server.server import MCPContext


def _context() -> MCPContext:
    return MCPContext(
        user_id=uuid4(),
        org_id=uuid4(),
        user_email="robot@example.com",
        user_name="Robot",
    )


def _agent() -> MagicMock:
    agent = MagicMock(spec=Agent)
    agent.id = uuid4()
    agent.name = "Operations Agent"
    agent.description = "Handles operational tasks"
    agent.system_prompt = "Use the tools carefully."
    agent.system_tools = ["list_workflows"]
    agent.knowledge_sources = ["runbooks"]
    agent.delegated_agents = []
    agent.organization_id = uuid4()
    return agent


def _resolved_tool(
    *,
    name: str = "lookup_ticket",
    parameters: dict | None = None,
) -> ResolvedGatewayTool:
    return ResolvedGatewayTool(
        tool_ref=str(uuid4()),
        definition=ToolDefinition(
            name=name,
            description="Look up a ticket",
            parameters=parameters
            or {
                "type": "object",
                "properties": {"ticket_id": {"type": "integer"}},
                "required": ["ticket_id"],
                "additionalProperties": False,
            },
        ),
        source="workflow",
        source_identity=f"workflow:{uuid4()}",
        source_id=uuid4(),
    )


def test_workflow_reference_is_stable_across_display_name_change():
    service = MCPAgentGatewayService(_context())
    agent = _agent()
    workflow_id = uuid4()

    first = service._resolve_gateway_tools(
        agent,
        [
            ToolDefinition(
                name="old_name",
                description="Old",
                parameters={"type": "object"},
            )
        ],
        {"old_name": workflow_id},
        MCPConfig(),
    )
    second = service._resolve_gateway_tools(
        agent,
        [
            ToolDefinition(
                name="new_name",
                description="New",
                parameters={"type": "object"},
            )
        ],
        {"new_name": workflow_id},
        MCPConfig(),
    )

    assert first[0].tool_ref == second[0].tool_ref
    assert first[0].source_identity == f"workflow:{workflow_id}"


def test_live_config_filters_underlying_tools_by_name_or_source_id():
    service = MCPAgentGatewayService(_context())
    agent = _agent()
    workflow_id = uuid4()
    definitions = [
        ToolDefinition(
            name="list_workflows",
            description="List",
            parameters={"type": "object"},
        ),
        ToolDefinition(
            name="lookup_ticket",
            description="Lookup",
            parameters={"type": "object"},
        ),
    ]

    tools = service._resolve_gateway_tools(
        agent,
        definitions,
        {"lookup_ticket": workflow_id},
        MCPConfig(allowed_tool_ids=[str(workflow_id)]),
    )

    assert [tool.definition.name for tool in tools] == ["lookup_ticket"]


def test_validation_error_is_model_repairable():
    tool = _resolved_tool()

    with pytest.raises(GatewayError) as exc_info:
        MCPAgentGatewayService.validate_arguments(
            tool,
            {"ticket_id": "not-an-integer", "surprise": True},
        )

    error = exc_info.value
    assert error.code == "INVALID_ARGUMENTS"
    assert error.retryable is True
    assert error.details["input_schema"] == tool.definition.parameters
    assert {issue["path"] for issue in error.details["issues"]} == {
        "/",
        "/ticket_id",
    }


def test_unknown_reference_does_not_fall_back_to_tool_name():
    agent = _agent()
    snapshot = AgentToolSnapshot(agent=agent, tools=[_resolved_tool()])

    with pytest.raises(GatewayError) as exc_info:
        MCPAgentGatewayService.find_tool(snapshot, "lookup_ticket")

    assert exc_info.value.code == "TOOL_NOT_FOUND_OR_FORBIDDEN"
    assert exc_info.value.retryable is True


@pytest.mark.asyncio
async def test_execute_validates_before_dispatch():
    service = MCPAgentGatewayService(_context())
    agent = _agent()
    tool = _resolved_tool()
    snapshot = AgentToolSnapshot(agent=agent, tools=[tool])

    with patch.object(service, "_dispatch", new_callable=AsyncMock) as dispatch:
        with pytest.raises(GatewayError) as exc_info:
            await service.execute_tool(
                snapshot,
                tool,
                {"ticket_id": "invalid"},
            )

    assert exc_info.value.code == "INVALID_ARGUMENTS"
    assert exc_info.value.details["agent_id"] == str(agent.id)
    dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_returns_auditable_envelope():
    service = MCPAgentGatewayService(_context())
    agent = _agent()
    tool = _resolved_tool()
    snapshot = AgentToolSnapshot(agent=agent, tools=[tool])

    with patch.object(
        service,
        "_dispatch",
        new=AsyncMock(return_value={"ticket": 42}),
    ):
        result = await service.execute_tool(
            snapshot,
            tool,
            {"ticket_id": 42},
        )

    assert result["agent_id"] == str(agent.id)
    assert result["tool_ref"] == tool.tool_ref
    assert result["tool_name"] == "lookup_ticket"
    assert result["source"] == "workflow"
    assert result["result"] == {"ticket": 42}
    assert isinstance(result["duration_ms"], int)


@pytest.mark.asyncio
async def test_workflow_dispatch_returns_only_the_workflow_result():
    service = MCPAgentGatewayService(_context())
    tool = _resolved_tool()
    response = MagicMock()
    response.execution_id = str(uuid4())
    response.status.value = "Success"
    response.duration_ms = 125
    response.result = [{"ticket": 42}]
    response.error = None
    response.error_type = None

    with patch(
        "src.services.execution.service.execute_tool",
        new=AsyncMock(return_value=response),
    ):
        result = await service._dispatch_workflow(tool, {"ticket_id": 42})

    assert result == [{"ticket": 42}]


@pytest.mark.asyncio
async def test_workflow_dispatch_keeps_execution_details_on_failure():
    service = MCPAgentGatewayService(_context())
    tool = _resolved_tool()
    execution_id = str(uuid4())
    response = MagicMock()
    response.execution_id = execution_id
    response.status.value = "Failed"
    response.duration_ms = 125
    response.result = None
    response.error = "HaloPSA rejected the query"
    response.error_type = "UserError"

    with patch(
        "src.services.execution.service.execute_tool",
        new=AsyncMock(return_value=response),
    ):
        with pytest.raises(GatewayError) as exc_info:
            await service._dispatch_workflow(tool, {"ticket_id": 42})

    error = exc_info.value
    assert error.message == "HaloPSA rejected the query"
    assert error.details["underlying_result"] == {
        "execution_id": execution_id,
        "status": "Failed",
        "duration_ms": 125,
        "result": None,
        "error": "HaloPSA rejected the query",
        "error_type": "UserError",
    }


def test_external_dispatch_prefers_the_structured_tool_payload():
    result = MCPAgentGatewayService.unwrap_external_result(
        {
            "content": [{"type": "text", "text": "fallback"}],
            "structured_content": {"tickets": [42]},
            "is_error": False,
            "_resolution_path": "user_token",
        }
    )

    assert result == {"tickets": [42]}


def test_external_dispatch_uses_content_without_structured_payload():
    content = [{"type": "text", "text": "plain result"}]

    result = MCPAgentGatewayService.unwrap_external_result(
        {
            "content": content,
            "structured_content": None,
            "is_error": False,
            "_resolution_path": "service_token",
        }
    )

    assert result == content


def test_external_dispatch_preserves_structured_error_details():
    underlying = {
        "content": [{"type": "text", "text": "vendor rejected request"}],
        "structured_content": {"error": "Invalid project"},
        "is_error": True,
        "_resolution_path": "user_token",
    }

    with pytest.raises(GatewayError) as exc_info:
        MCPAgentGatewayService.unwrap_external_result(underlying)

    error = exc_info.value
    assert error.message == "Invalid project"
    assert error.retryable is True
    assert error.details["underlying_result"] == underlying
