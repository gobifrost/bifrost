"""Unit tests for the unscoped MCP agent gateway."""

from types import SimpleNamespace
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
from src.services.mcp_server.tools.gateway import bifrost_find_agents


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
    agent.bundle_path = None
    agent.solution_id = None
    agent.created_by = "admin@example.com"
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


def _builder_tool(name: str = "list_files") -> ResolvedGatewayTool:
    return ResolvedGatewayTool(
        tool_ref=str(uuid4()),
        definition=ToolDefinition(
            name=name,
            description="Builder workspace tool",
            parameters={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        ),
        source="system",
        source_identity=f"system:{name}",
    )


@pytest.mark.asyncio
async def test_gateway_tool_omits_absent_builder_session_query_parameter():
    call_rest = AsyncMock(
        return_value=(
            200,
            {
                "query": None,
                "agents": [],
                "count": 0,
                "total_matches": 0,
                "has_more": False,
            },
        )
    )
    with patch(
        "src.services.mcp_server.tools.gateway.call_rest",
        call_rest,
    ):
        await bifrost_find_agents(_context())
        await bifrost_find_agents(
            _context(),
            query="builder",
            builder_session_id="session-id",
        )

    assert call_rest.await_args_list[0].kwargs["params"] == {"limit": 10}
    assert call_rest.await_args_list[1].kwargs["params"] == {
        "limit": 10,
        "query": "builder",
        "builder_session_id": "session-id",
    }


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


def test_skill_asset_is_classified_as_an_agent_bound_capability():
    service = MCPAgentGatewayService(_context())
    agent = _agent()
    agent.bundle_path = "skills/operations"

    tools = service._resolve_gateway_tools(
        agent,
        [
            ToolDefinition(
                name="read_skill_asset",
                description="Read Skill asset",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            )
        ],
        {},
        MCPConfig(),
    )

    assert len(tools) == 1
    assert tools[0].source == "skill"
    assert tools[0].source_identity == "skill:read_skill_asset"


@pytest.mark.asyncio
async def test_get_agent_returns_canonical_skill_instructions_and_file_catalog():
    service = MCPAgentGatewayService(_context())
    agent = _agent()
    agent.bundle_path = "skills/operations"
    snapshot = AgentToolSnapshot(agent=agent, tools=[])

    with (
        patch.object(
            service,
            "get_agent_snapshot",
            new=AsyncMock(return_value=snapshot),
        ),
        patch(
            "src.services.agent_skills.get_agent_skill_markdown",
            new=AsyncMock(return_value="---\nname: operations\n---\n\nDo the work."),
        ),
        patch(
            "src.services.agent_skills.list_agent_skill_files",
            new=AsyncMock(return_value=["references/runbook.md"]),
        ),
    ):
        result = await service.get_agent(str(agent.id))

    assert result["agent"]["instruction_source"] == "skill"
    assert result["agent"]["instructions"].endswith("Do the work.")
    assert result["agent"]["skill"] == {
        "bundle_path": "skills/operations",
        "files": ["SKILL.md", "references/runbook.md"],
        "automatic_capabilities": ["read_skill_asset"],
    }


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
async def test_builder_workspace_tool_requires_session_id():
    service = MCPAgentGatewayService(_context())
    agent = _agent()
    tool = _builder_tool("list_files")
    snapshot = AgentToolSnapshot(agent=agent, tools=[tool])

    with pytest.raises(GatewayError) as exc_info:
        await service.execute_tool(snapshot, tool, {})

    assert exc_info.value.code == "INVALID_ARGUMENTS"
    assert exc_info.value.details["tool_name"] == "list_files"


@pytest.mark.asyncio
async def test_builder_session_id_rejected_for_ordinary_tool():
    service = MCPAgentGatewayService(_context())
    agent = _agent()
    tool = _resolved_tool()
    snapshot = AgentToolSnapshot(agent=agent, tools=[tool])

    with pytest.raises(GatewayError) as exc_info:
        await service.execute_tool(
            snapshot,
            tool,
            {"ticket_id": 42},
            builder_session_id=str(uuid4()),
        )

    assert exc_info.value.code == "INVALID_ARGUMENTS"
    assert "only be used with Builder workspace tools" in exc_info.value.message


@pytest.mark.asyncio
async def test_builder_session_id_routes_existing_builder_tool(monkeypatch):
    service = MCPAgentGatewayService(_context())
    agent = _agent()
    session_id = uuid4()
    tool = _builder_tool("list_files")

    class _Context:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    harness = AsyncMock()
    harness.execute = AsyncMock(return_value={"paths": []})
    harness_class = MagicMock(return_value=harness)
    monkeypatch.setattr(
        "src.core.database.get_db_context",
        lambda: _Context(),
    )
    monkeypatch.setattr(
        "src.services.builder.mcp_harness.BuilderMCPHarness",
        harness_class,
    )

    result = await service._dispatch(
        agent,
        tool,
        {},
        builder_session_id=str(session_id),
    )

    assert result == {"paths": []}
    harness.execute.assert_awaited_once_with(
        agent=agent,
        tool_name="list_files",
        builder_session_id=session_id,
        arguments={},
    )


@pytest.mark.asyncio
async def test_skill_dispatch_carries_the_exact_agent_storage_scope():
    service = MCPAgentGatewayService(_context())
    agent = _agent()
    agent.bundle_path = "skills/operations"
    skill_tool = ResolvedGatewayTool(
        tool_ref=str(uuid4()),
        definition=ToolDefinition(
            name="read_skill_asset",
            description="Read Skill asset",
            parameters={"type": "object"},
        ),
        source="skill",
        source_identity="skill:read_skill_asset",
    )
    captured = {}

    async def read_asset(context, **arguments):
        captured["context"] = context
        captured["arguments"] = arguments
        return SimpleNamespace(
            content=[],
            structured_content={"path": arguments["path"], "content": "guide"},
        )

    with patch(
        "src.services.mcp_server.server.get_system_tool_function",
        return_value=read_asset,
    ):
        await service._dispatch_system_tool(
            agent,
            skill_tool,
            {"path": "references/guide.md"},
        )

    context = captured["context"]
    assert context.agent_bundle_path == "skills/operations"
    assert context.agent_skill_id == agent.id
    assert context.agent_solution_id is None
    assert context.agent_skill_in_repo is False
