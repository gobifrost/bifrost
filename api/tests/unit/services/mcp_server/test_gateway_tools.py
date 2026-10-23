"""Unit coverage for the stable MCP gateway's REST wrappers."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.services.mcp_server.server import MCPContext
from src.services.mcp_server.tools import gateway as gateway_tools


@pytest.mark.asyncio
async def test_execute_agent_posts_exact_uuid_path_and_returns_run_receipt():
    context = MCPContext(user_id=uuid4(), org_id=uuid4())
    agent_id = uuid4()
    run_id = uuid4()
    input_data = {"ticket_id": 42}
    output_schema = {"type": "object"}

    with patch.object(
        gateway_tools,
        "call_rest",
        AsyncMock(return_value=(200, {"run_id": str(run_id)})),
    ) as call_rest:
        result = await gateway_tools.bifrost_execute_agent(
            context,
            str(agent_id),
            input_data,
            output_schema,
        )

    call_rest.assert_awaited_once_with(
        context,
        "POST",
        f"/api/mcp/gateway/agents/{agent_id}/execute",
        json_body={"input": input_data, "output_schema": output_schema},
    )
    assert result.structured_content == {"run_id": str(run_id)}


@pytest.mark.asyncio
async def test_execute_agent_preserves_rest_error_payload():
    context = MCPContext(user_id=uuid4(), org_id=uuid4())
    response = {
        "detail": {
            "message": "Agent not found or you do not have access.",
            "code": "AGENT_NOT_FOUND_OR_FORBIDDEN",
        }
    }

    with patch.object(
        gateway_tools,
        "call_rest",
        AsyncMock(return_value=(404, response)),
    ):
        result = await gateway_tools.bifrost_execute_agent(
            context,
            str(uuid4()),
            {},
        )

    assert result.structured_content == {
        "error": "Agent not found or you do not have access.",
        "message": "Agent not found or you do not have access.",
        "code": "AGENT_NOT_FOUND_OR_FORBIDDEN",
    }


@pytest.mark.asyncio
async def test_rest_bridge_forwards_scoped_agent_id_header():
    from src.services.mcp_server.tools import _http_bridge

    scoped_agent_id = uuid4()
    context = MCPContext(
        user_id=uuid4(),
        org_id=uuid4(),
        scoped_agent_id=scoped_agent_id,
    )
    client = MagicMock()
    client_context = MagicMock()
    client_context.__aenter__ = AsyncMock(return_value=client)
    client_context.__aexit__ = AsyncMock(return_value=False)

    with (
        patch.object(_http_bridge, "_token_from_context", return_value="token"),
        patch.object(_http_bridge, "_build_in_process_transport", return_value=MagicMock()),
        patch.object(_http_bridge.httpx, "AsyncClient", return_value=client_context) as async_client,
    ):
        async with _http_bridge.rest_client(context) as returned_client:
            assert returned_client is client

    headers = async_client.call_args.kwargs["headers"]
    assert headers["X-Bifrost-MCP-Agent-ID"] == str(scoped_agent_id)
