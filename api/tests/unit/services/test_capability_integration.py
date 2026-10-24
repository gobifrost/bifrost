"""MCP and CodeMode projections share the same capability identity and ranking."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.services.agent_runtime.composition import _tool_reference
from src.services.agent_runtime.sandbox_sdk import _BifrostSandboxSDKToolset
from src.services.agent_runtime.toolset import BifrostToolset
from src.services.capabilities.catalog import resolve_capability_tools
from src.services.capabilities.search import CapabilitySearchItem, rank_capabilities
from src.services.llm import ToolDefinition
from src.services.mcp_server.config_service import MCPConfig
from src.services.mcp_server.gateway import AgentToolSnapshot, MCPAgentGatewayService


@pytest.mark.asyncio
async def test_mcp_and_code_mode_rank_the_same_authorized_capabilities():
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
            "bifrost_python_name": f"python_{definition.name}",
        }
        for definition in definitions
    }
    sdk = _BifrostSandboxSDKToolset(
        wrapped=BifrostToolset(definitions, AsyncMock(), tool_metadata=metadata),
        max_business_calls=16,
    )
    gateway = MCPAgentGatewayService(SimpleNamespace())
    public_tools = gateway._resolve_gateway_tools(owner, definitions, ids, MCPConfig())
    for capability in catalog:
        assert _tool_reference(owner, capability.definition.name, ids) == capability.tool_ref
    assert [tool.tool_ref for tool in public_tools] == [tool.tool_ref for tool in catalog]

    result = await sdk._search(SimpleNamespace(), {"query": "ticket"})
    public = gateway._search_agent_snapshot(
        AgentToolSnapshot(agent=SimpleNamespace(**vars(owner), name="Support", description="", system_prompt=""), tools=public_tools),
        query="ticket", tool_ref=None, limit=10,
    )
    ranked = rank_capabilities("ticket", [
        CapabilitySearchItem(path=f"{connector}.{definition.name}", connector=connector, method=definition.name, description=definition.description)
        for definition in definitions
    ])
    assert [item["method"] for item in result["results"]] == [item["method"] for item in ranked]
    assert [item["score"] for item in result["results"]] == [item["score"] for item in ranked]
    assert [item["name"] for item in public["agents"][0]["matching_tools"]] == [item["method"] for item in ranked]
    assert all(item["path"].startswith("python_") for item in result["results"])


@pytest.mark.asyncio
async def test_code_mode_documents_structured_payload_without_retyping_its_envelope():
    executor = AsyncMock()
    payload = {"type": "object", "properties": {"record_id": {"type": "string"}}, "required": ["record_id"]}
    definition = ToolDefinition("read_record", "Read a remote record", {"type": "object"}, return_schema={"type": "string"}, structured_output_schema=payload)
    sdk = _BifrostSandboxSDKToolset(wrapped=BifrostToolset([definition], executor), max_business_calls=16)

    document = await sdk._describe(SimpleNamespace(), {"target": "read_record"})
    assert "record_id: str" in document["types"]
    assert "result['structured_content']" in document["types"]
    assert "def read_record() -> Any" in document["types"]
    assert "def read_record_structured_content" not in document["types"]
    executor.assert_not_called()
