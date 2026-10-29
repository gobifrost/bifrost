from contextlib import asynccontextmanager
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import httpx
from mcp.types import ImageContent, ResourceLink

from src.services.mcp_server import server


@pytest.mark.asyncio
async def test_workflow_artifact_results_become_mcp_media_and_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org_id = uuid4()
    context = server.MCPContext(user_id=uuid4(), org_id=org_id)
    monkeypatch.setattr(server, "_get_context_from_token", lambda: context)
    monkeypatch.setattr(
        server,
        "_execute_workflow_tool_impl",
        AsyncMock(
            return_value={
                "artifacts": [
                    {
                        "type": "bifrost_artifact",
                        "id": "00000000-0000-0000-0000-000000000001",
                        "filename": "chart.png",
                        "content_type": "image/png",
                        "size_bytes": 8,
                    },
                    {
                        "type": "bifrost_artifact",
                        "id": "00000000-0000-0000-0000-000000000002",
                        "filename": "brief.pdf",
                        "content_type": "application/pdf",
                        "size_bytes": 12,
                    },
                ]
            }
        ),
    )

    client = AsyncMock()
    client.get.side_effect = [
        httpx.Response(200, content=b"png-data"),
        httpx.Response(200, json={"url": "https://files.example.test/brief.pdf"}),
    ]

    @asynccontextmanager
    async def fake_rest_client(actual_context):
        assert actual_context is context
        yield client

    monkeypatch.setattr(
        "src.services.mcp_server.tools._http_bridge.rest_client", fake_rest_client
    )
    tool = server.WorkflowTool(
        name="create_brief",
        description="Create files",
        workflow_id=str(uuid4()),
        workflow_name="Create Brief",
        parameters={"type": "object", "properties": {}},
    )
    result = await tool.run({})

    assert result.structured_content is not None
    assert result.structured_content["artifacts"][0]["filename"] == "chart.png"
    assert any(isinstance(block, ImageContent) for block in result.content)
    resource = next(
        block for block in result.content if isinstance(block, ResourceLink)
    )
    assert resource.name == "brief.pdf"
    assert str(resource.uri) == "https://files.example.test/brief.pdf"


@pytest.mark.asyncio
async def test_native_workflow_tool_uses_current_parent_agent_boundary(monkeypatch):
    from types import SimpleNamespace
    context = server.MCPContext(user_id=uuid4(), org_id=uuid4())
    workflow_id, agent_id = str(uuid4()), uuid4()
    caller = object()
    execute = AsyncMock(return_value=SimpleNamespace(status=SimpleNamespace(value="Success"), result={"count": 1}))
    monkeypatch.setattr(server, "_get_agent_id_from_scope", lambda: agent_id)
    monkeypatch.setattr("src.services.mcp_server.authorization.load_mcp_caller", AsyncMock(return_value=caller))
    monkeypatch.setattr("src.services.execution.agent_workflow_tools.execute_agent_workflow_tool", execute)

    result = await server._execute_workflow_tool_impl(context, workflow_id, "Contoso Report", count=1)

    assert result == {"count": 1}
    execute.assert_awaited_once_with(
        agent_id=agent_id, workflow_id=workflow_id,
        parameters={"count": 1}, caller=caller, sync=True,
    )


@pytest.mark.asyncio
async def test_native_workflow_tool_preserves_permission_refusal(monkeypatch):
    from fastapi import HTTPException
    monkeypatch.setattr(server, "_get_agent_id_from_scope", uuid4)
    monkeypatch.setattr("src.services.mcp_server.authorization.load_mcp_caller", AsyncMock(return_value=object()))
    monkeypatch.setattr(
        "src.services.execution.agent_workflow_tools.execute_agent_workflow_tool",
        AsyncMock(side_effect=HTTPException(403, "Permission denied")),
    )
    result = await server._execute_workflow_tool_impl(
        server.MCPContext(user_id=uuid4()), str(uuid4()), "Contoso Report"
    )
    assert result == {"error": "Permission denied"}


@pytest.mark.asyncio
async def test_native_workflow_tool_requires_an_explicit_parent_agent(monkeypatch):
    monkeypatch.setattr(server, "_get_agent_id_from_scope", lambda: None)
    execute = AsyncMock()
    monkeypatch.setattr("src.services.execution.agent_workflow_tools.execute_agent_workflow_tool", execute)
    result = await server._execute_workflow_tool_impl(
        server.MCPContext(user_id=uuid4()), str(uuid4()), "Contoso Report"
    )
    assert "Agent-scoped" in result["error"]
    execute.assert_not_awaited()
