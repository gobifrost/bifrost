"""Exercise the real SDK transport against an existing MCP wire contract."""

import json
from types import SimpleNamespace

import httpx2
import pytest
from mcp.shared._httpx_utils import create_mcp_http_client

from src.services.mcp_client.client import open_session
from src.services.mcp_client.dispatch import _normalize_call_tool_result


@pytest.mark.asyncio
@pytest.mark.parametrize("streamed", [False, True], ids=["json", "large-sse"])
async def test_existing_server_auth_schema_and_result_survive_sdk_upgrade(monkeypatch, streamed):
    requests = []

    def respond(request):
        assert request.headers["Authorization"] == "Bearer service-token"
        assert str(request.url) == "https://vendor.example/mcp"
        if request.method != "POST":
            return httpx2.Response(405)
        body = json.loads(request.content)
        requests.append(body)
        if "id" not in body:
            return httpx2.Response(202)
        results = {
            "initialize": {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "existing-vendor", "version": "1"},
            },
            "tools/list": {"tools": [{
                "name": "lookup", "inputSchema": {"type": "object"},
            }]},
            "tools/call": {
                "content": [{"type": "image", "data": "aGVsbG8=", "mimeType": "image/png"}],
                "structuredContent": {"count": 1}, "isError": False,
            },
        }
        payload = {
            "jsonrpc": "2.0", "id": body["id"], "result": results[body["method"]],
        }
        if streamed and body["method"] == "tools/call":
            payload["result"]["content"][0]["data"] = "aGVsbG8=" * 140000
            return httpx2.Response(200, headers={"Content-Type": "text/event-stream"},
                                   content="event: message\ndata: " + json.dumps(payload) + "\n\n")
        return httpx2.Response(200, json=payload)

    def client(**kwargs):
        instance = create_mcp_http_client(**kwargs)
        # Preserve the SDK's production timeout and header setup with a fake network.
        instance._transport = httpx2.MockTransport(respond)
        return instance

    monkeypatch.setattr("mcp.shared._httpx_utils.create_mcp_http_client", client)
    connection = SimpleNamespace(
        id="test", server_url_override=None,
        server=SimpleNamespace(server_url="https://vendor.example/mcp"),
    )
    async with open_session(connection, "service-token") as session:
        tools = await session.list_tools()
        assert tools.tools[0].input_schema == {"type": "object"}
        result = _normalize_call_tool_result(await session.call_tool("lookup", {}))

    assert result["structured_content"] == {"count": 1}
    assert result["is_error"] is False
    assert result["content"][0]["mimeType"] == "image/png"
    assert requests[0]["method"] == "initialize"
