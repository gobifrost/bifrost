"""Whole-agent invocation cannot bypass external MCP capability restrictions."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.models.orm.agents import Agent
from src.services.llm import ToolDefinition
from src.services.mcp_server.config_service import MCPConfig, MCPConfigService
from src.services.mcp_server.gateway import GatewayError, MCPAgentGatewayService
from src.services.mcp_server.server import MCPContext


def _service():
    return MCPAgentGatewayService(MCPContext(user_id=uuid4(), org_id=uuid4()))


@asynccontextmanager
async def _database():
    yield MagicMock()


@pytest.mark.asyncio
@pytest.mark.parametrize("restricted", [False, True])
async def test_invocation_requires_agent_id_in_explicit_allowlist(restricted):
    service = _service()
    agent_id = uuid4()
    config = MCPConfig(allowed_tool_ids=[] if restricted else [str(agent_id)])
    service.get_agent_snapshot = AsyncMock()
    with (
        patch("src.core.database.get_db_context", _database),
        patch.object(MCPConfigService, "get_config", AsyncMock(return_value=config)),
    ):
        if restricted:
            with pytest.raises(GatewayError, match="not allowed"):
                await service.authorize_agent_invocation(agent_id)
        else:
            await service.authorize_agent_invocation(agent_id)
    service.get_agent_snapshot.assert_awaited_once_with(str(agent_id))


@pytest.mark.asyncio
@pytest.mark.parametrize("block_child_agent", [False, True])
async def test_invocation_rejects_blocked_capabilities_in_approved_children(block_child_agent):
    service = _service()
    root, child = MagicMock(spec=Agent), MagicMock(spec=Agent)
    root.id, child.id = uuid4(), uuid4()
    root.system_tools = child.system_tools = []
    workflow_id = uuid4()
    definition = ToolDefinition(name="protected_tool", description="Protected", parameters={})
    service.get_agent_snapshot = AsyncMock()
    service._approved_agents = AsyncMock(return_value={root.id: root, child.id: child})
    blocked = [str(child.id) if block_child_agent else str(workflow_id)]
    config = MCPConfig(allowed_tool_ids=[str(root.id)], blocked_tool_ids=blocked)
    with (
        patch("src.core.database.get_db_context", _database),
        patch.object(MCPConfigService, "get_config", AsyncMock(return_value=config)),
        patch(
            "src.services.mcp_server.gateway.resolve_agent_tools",
            AsyncMock(side_effect=[([], {}), ([definition], {definition.name: workflow_id})]),
        ),
    ):
        with pytest.raises(GatewayError, match="blocked MCP capability"):
            await service.authorize_agent_invocation(root.id)


@pytest.mark.asyncio
async def test_invocation_blocks_bound_oauth_connection_absent_from_caller_catalog():
    service = _service()
    connection_id = uuid4()
    agent = SimpleNamespace(
        id=uuid4(), tools=[], system_tools=[], knowledge_sources=[],
        mcp_connections=[SimpleNamespace(id=connection_id)],
    )
    service.get_agent_snapshot = AsyncMock()
    service._approved_agents = AsyncMock(return_value={agent.id: agent})
    config = MCPConfig(blocked_tool_ids=[str(connection_id)])
    with (
        patch("src.core.database.get_db_context", _database),
        patch.object(MCPConfigService, "get_config", AsyncMock(return_value=config)),
        patch(
            "src.services.mcp_server.gateway.resolve_agent_tools",
            AsyncMock(return_value=([], {})),
        ) as resolve,
    ):
        with pytest.raises(GatewayError, match="blocked MCP capability"):
            await service.authorize_agent_invocation(agent.id)
    resolve.assert_not_awaited()

