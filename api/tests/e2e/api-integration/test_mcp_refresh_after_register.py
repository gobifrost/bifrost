"""Gateway discovery refresh coverage for workflow registration and rename."""

import json
import os
import uuid

import pytest
import requests

TEST_API_URL = os.getenv("TEST_API_URL", "http://api:8000")
MCP_ACCEPT_HEADER = "application/json, text/event-stream"


def _mcp_headers(headers: dict[str, str]) -> dict[str, str]:
    return {
        **headers,
        "Content-Type": "application/json",
        "Accept": MCP_ACCEPT_HEADER,
    }


def _gateway_call(
    headers: dict[str, str],
    tool_name: str,
    arguments: dict,
    *,
    path: str = "/mcp",
) -> dict:
    response = requests.post(
        f"{TEST_API_URL}{path}",
        headers=_mcp_headers(headers),
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert "error" not in payload, payload
    result = payload["result"]
    structured = result.get("structuredContent")
    if structured is not None:
        return structured
    return json.loads(result["content"][0]["text"])


def _discover_workflow_tool(headers: dict[str, str], agent_id: str, query: str) -> dict:
    discovered = _gateway_call(
        headers,
        "bifrost_search_capabilities",
        {"agent_id": agent_id, "query": query},
        path=f"/mcp/{agent_id}",
    )
    agent = next(item for item in discovered["agents"] if item["id"] == agent_id)
    tools = [item for item in agent["matching_tools"] if item["source"] == "workflow"]
    assert len(tools) == 1, discovered
    return tools[0]


@pytest.mark.e2e
class TestMCPRefreshAfterRegister:
    """Workflow metadata is immediately visible through stable gateway discovery."""

    def test_registered_and_renamed_tool_refreshes_gateway_metadata(
        self, platform_admin
    ):
        suffix = uuid.uuid4().hex[:8]
        path = f"workflows/test_mcp_refresh_{suffix}.py"
        function_name = f"test_mcp_refresh_{suffix}"
        renamed_tool_name = f"renamed_tool_{suffix}"

        admin_h = platform_admin.headers
        agent_id: str | None = None
        workflow_id: str | None = None
        try:
            file_content = f'''
from bifrost import tool

@tool
def {function_name}(message: str) -> dict:
    """Echoes the message for MCP gateway discovery refresh coverage."""
    return {{"message": message}}
'''
            write_resp = requests.put(
                f"{TEST_API_URL}/api/files/editor/content",
                headers=admin_h,
                json={"path": path, "content": file_content, "encoding": "utf-8"},
            )
            assert write_resp.status_code in (200, 201), write_resp.text

            reg_resp = requests.post(
                f"{TEST_API_URL}/api/workflows/register",
                headers=admin_h,
                json={"path": path, "function_name": function_name},
            )
            assert reg_resp.status_code == 201, reg_resp.text
            registered = reg_resp.json()
            workflow_id = registered["id"]
            assert registered["type"] == "tool", registered

            agent_resp = requests.post(
                f"{TEST_API_URL}/api/agents",
                headers=admin_h,
                json={
                    "name": f"MCP Refresh Test Agent {suffix}",
                    "system_prompt": "Test agent for MCP gateway refresh regression",
                    "channels": ["chat"],
                    "tool_ids": [workflow_id],
                },
            )
            assert agent_resp.status_code == 201, agent_resp.text
            agent_id = agent_resp.json()["id"]

            original = _discover_workflow_tool(admin_h, agent_id, function_name)

            patch_resp = requests.patch(
                f"{TEST_API_URL}/api/workflows/{workflow_id}",
                headers=admin_h,
                json={"name": renamed_tool_name},
            )
            assert patch_resp.status_code == 200, patch_resp.text
            assert patch_resp.json()["name"] == renamed_tool_name

            renamed = _discover_workflow_tool(admin_h, agent_id, renamed_tool_name)
            assert renamed_tool_name in renamed["name"]
            assert function_name not in renamed["name"]
            assert renamed["name"] != original["name"]
            assert renamed["tool_ref"] == original["tool_ref"]
        finally:
            if agent_id:
                requests.delete(
                    f"{TEST_API_URL}/api/agents/{agent_id}",
                    headers=admin_h,
                )
            if workflow_id:
                requests.delete(
                    f"{TEST_API_URL}/api/workflows/{workflow_id}",
                    headers=admin_h,
                )
            requests.delete(
                f"{TEST_API_URL}/api/files/editor",
                headers=admin_h,
                params={"path": path},
            )
