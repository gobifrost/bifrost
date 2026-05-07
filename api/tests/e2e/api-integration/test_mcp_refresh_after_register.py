"""
Regression test: a freshly registered tool must appear in MCP tools/list
without restarting the API container.

Bug: register_workflow called refresh_workflow_tools() after db.flush() but
before the request transaction committed. refresh_workflow_tools() opens its
own session via get_db_context(), which (at READ COMMITTED isolation) cannot
see the uncommitted INSERT. The new workflow was therefore missing from the
in-memory FastMCP registry until the next API restart re-ran startup
registration against a fresh, committed snapshot.

This test exercises the full path:
    POST /api/workflows/register  (registers a @tool function as type=tool)
    POST /api/agents              (attaches the new tool to an agent)
    POST /mcp  tools/list         (must include the new tool name)

Without the fix, tools/list omits the new tool because FastMCP's in-memory
registry has no entry for it — middleware filters on `tool.name in all_tools`,
and `all_tools` comes from FastMCP, not the DB.
"""

import os
import uuid

import pytest
import requests

from tests.fixtures.auth import create_test_jwt

TEST_API_URL = os.getenv("TEST_API_URL", "http://api:8000")
MCP_ACCEPT_HEADER = "application/json, text/event-stream"


def _mcp_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": MCP_ACCEPT_HEADER,
    }


def _admin_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


@pytest.mark.e2e
class TestMCPRefreshAfterRegister:
    """A workflow registered via POST /api/workflows/register must appear in
    MCP tools/list immediately, without an API restart."""

    def test_registered_tool_appears_in_mcp_tools_list_immediately(self):
        suffix = uuid.uuid4().hex[:8]
        path = f"workflows/test_mcp_refresh_{suffix}.py"
        function_name = f"test_mcp_refresh_{suffix}"

        admin_token = create_test_jwt(is_superuser=True)
        admin_h = _admin_headers(admin_token)
        mcp_h = _mcp_headers(admin_token)

        agent_id: str | None = None
        workflow_id: str | None = None
        try:
            file_content = f'''
from bifrost import tool

@tool(name="MCP Refresh Test {suffix}")
def {function_name}(message: str) -> dict:
    """Echoes the message — exists only to verify MCP refresh-after-register."""
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
            reg = reg_resp.json()
            workflow_id = reg["id"]
            assert reg["type"] == "tool", f"expected type=tool, got {reg}"

            agent_resp = requests.post(
                f"{TEST_API_URL}/api/agents",
                headers=admin_h,
                json={
                    "name": f"MCP Refresh Test Agent {suffix}",
                    "system_prompt": "Test agent for MCP refresh regression",
                    "channels": ["chat"],
                    "tool_ids": [workflow_id],
                },
            )
            assert agent_resp.status_code == 201, agent_resp.text
            agent_id = agent_resp.json()["id"]

            init_resp = requests.post(
                f"{TEST_API_URL}/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "1.0"},
                    },
                },
                headers=mcp_h,
            )
            assert init_resp.status_code == 200, init_resp.text

            list_resp = requests.post(
                f"{TEST_API_URL}/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/list",
                    "params": {},
                },
                headers=mcp_h,
            )
            assert list_resp.status_code == 200, list_resp.text
            payload = list_resp.json()
            assert "result" in payload, payload
            tools = payload["result"]["tools"]
            tool_names = [t["name"] for t in tools]

            # The MCP tool name comes from _normalize_tool_name(workflow.name),
            # and workflow.name == function_name on register.
            assert function_name in tool_names, (
                f"Newly registered tool {function_name!r} missing from MCP "
                f"tools/list — refresh_workflow_tools() likely ran before the "
                f"register transaction committed. Tools returned: {tool_names}"
            )
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
