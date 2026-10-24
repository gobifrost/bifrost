"""MCP and CodeMode projections share the same capability identity and ranking."""

from types import SimpleNamespace
from uuid import uuid4

from pydantic_ai.tools import ToolDefinition as PydanticToolDefinition

from src.services.agent_runtime.composition import _tool_reference
from src.services.agent_runtime.tool_policy import _rank_bifrost_tools
from src.services.capabilities.catalog import resolve_capability_tools
from src.services.capabilities.search import CapabilitySearchItem, rank_capabilities
from src.services.llm import ToolDefinition
from src.services.mcp_server.config_service import MCPConfig
from src.services.mcp_server.gateway import AgentToolSnapshot, MCPAgentGatewayService


def test_mcp_and_code_mode_rank_the_same_authorized_capabilities():
    owner = SimpleNamespace(id=uuid4(), system_tools=[], knowledge_sources=[], delegated_agents=[], mcp_connections=[])
    connector = f"bifrost_agent_{owner.id.hex}"
    definitions = [
        ToolDefinition("read_ticket", "Read ticket details", {"type": "object"}),
        ToolDefinition("list_tickets", "List all open tickets", {"type": "object"}),
    ]
    ids = {definition.name: uuid4() for definition in definitions}
    catalog = resolve_capability_tools(owner, definitions, ids)
    metadata = {
        definition.name: {
            "bifrost_original_name": definition.name,
            "bifrost_connector": connector,
            "bifrost_search_path": f"{connector}.{definition.name}",
            "bifrost_tool_description": definition.description,
        }
        for definition in definitions
    }
    gateway = MCPAgentGatewayService(SimpleNamespace())
    public_tools = gateway._resolve_gateway_tools(owner, definitions, ids, MCPConfig())
    for capability in catalog:
        assert _tool_reference(owner, capability.definition.name, ids) == capability.tool_ref
    assert [tool.tool_ref for tool in public_tools] == [tool.tool_ref for tool in catalog]

    code_mode_tools = [
        PydanticToolDefinition(
            name=definition.name,
            description=definition.description,
            metadata=metadata[definition.name],
        )
        for definition in definitions
    ]
    result = _rank_bifrost_tools(SimpleNamespace(), ["ticket"], code_mode_tools)
    public = gateway._search_agent_snapshot(
        AgentToolSnapshot(agent=SimpleNamespace(**vars(owner), name="Support", description="", system_prompt=""), tools=public_tools),
        query="ticket", tool_ref=None, limit=10,
    )
    ranked = rank_capabilities("ticket", [
        CapabilitySearchItem(path=f"{connector}.{definition.name}", connector=connector, method=definition.name, description=definition.description)
        for definition in definitions
    ])
    assert result == [item["method"] for item in ranked]
    assert [item["name"] for item in public["agents"][0]["matching_tools"]] == [item["method"] for item in ranked]
