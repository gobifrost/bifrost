"""
Tests for MCP JSON-RPC protocol endpoint at /mcp.

These tests verify the FastMCP server is properly mounted and accepting
authenticated requests via the JSON-RPC 2.0 protocol.
"""

import os

import pytest
import requests

from tests.fixtures.auth import create_test_jwt

TEST_API_URL = os.getenv("TEST_API_URL", "http://api:8000")

# MCP Streamable HTTP transport requires this Accept header
MCP_ACCEPT_HEADER = "application/json, text/event-stream"


def mcp_headers(token: str) -> dict[str, str]:
    """Create headers for MCP requests with proper Accept header."""
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": MCP_ACCEPT_HEADER,
    }


@pytest.mark.e2e
class TestMCPProtocol:
    """Test MCP JSON-RPC 2.0 protocol endpoint."""

    @pytest.fixture(autouse=True, scope="class")
    def _ensure_agent_with_tools(self):
        """Create an agent with system tools so MCP tools/list returns results."""
        token = create_test_jwt(is_superuser=True)
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        resp = requests.post(
            f"{TEST_API_URL}/api/agents",
            headers=headers,
            json={
                "name": "MCP Protocol Test Agent",
                "system_prompt": "Test agent for MCP protocol",
                "channels": ["chat"],
                "system_tools": [
                    "execute_workflow",
                    "list_workflows",
                    "list_integrations",
                    "list_forms",
                    "get_docs",
                    "search_knowledge",
                ],
            },
        )
        assert resp.status_code == 201, f"Failed to create test agent: {resp.text}"
        agent_id = resp.json()["id"]

        yield

        requests.delete(
            f"{TEST_API_URL}/api/agents/{agent_id}",
            headers=headers,
        )

    def test_mcp_requires_auth(self):
        """POST /mcp without auth should return 401."""
        response = requests.post(
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
            headers={
                "Content-Type": "application/json",
                "Accept": MCP_ACCEPT_HEADER,
            },
        )
        assert response.status_code == 401

    def test_mcp_initialize_success(self):
        """POST /mcp with valid admin token should return initialize response."""
        token = create_test_jwt(is_superuser=True)
        headers = mcp_headers(token)

        response = requests.post(
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
            headers=headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data.get("jsonrpc") == "2.0"
        assert data.get("id") == 1
        assert "result" in data
        # Verify server info is present
        result = data["result"]
        assert "serverInfo" in result
        assert "protocolVersion" in result
        assert "bifrost_find_agents" in result["instructions"]

    def test_mcp_list_tools(self):
        """Should be able to list available tools.

        Note: The MCP server uses stateless_http=True mode, which means each request
        is independent and no session ID is needed. We can call tools/list directly
        after initialize without needing to track session state.
        """
        token = create_test_jwt(is_superuser=True)
        headers = mcp_headers(token)

        # First initialize (required by MCP protocol before other methods)
        init_response = requests.post(
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
            headers=headers,
        )
        assert init_response.status_code == 200

        # In stateless mode, no session ID is needed - each request is independent
        # Just call tools/list directly
        response = requests.post(
            f"{TEST_API_URL}/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {},
            },
            headers=headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert "result" in data
        result = data["result"]
        assert "tools" in result
        tools = result["tools"]
        tool_names = {t["name"] for t in tools}
        assert tool_names == {
            "bifrost_find_agents",
            "bifrost_get_agent",
            "bifrost_get_tool_schema",
            "bifrost_execute_tool",
        }

    def test_mcp_non_admin_allowed(self):
        """Non-admin users can initialize MCP — per-user tool access is
        role-scoped downstream, so they connect successfully and simply see
        the tools their roles grant them (possibly none)."""
        token = create_test_jwt(is_superuser=False)
        headers = mcp_headers(token)

        response = requests.post(
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
            headers=headers,
        )

        assert response.status_code == 200

    def test_mcp_invalid_token_rejected(self):
        """Invalid token should be rejected."""
        headers = {
            "Authorization": "Bearer invalid_token_here",
            "Content-Type": "application/json",
            "Accept": MCP_ACCEPT_HEADER,
        }

        response = requests.post(
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
            headers=headers,
        )

        assert response.status_code == 401


@pytest.mark.e2e
class TestMCPDiscovery:
    """Test MCP OAuth discovery endpoints."""

    def test_oauth_authorization_server_metadata(self):
        """OAuth authorization server metadata should be available."""
        response = requests.get(
            f"{TEST_API_URL}/.well-known/oauth-authorization-server"
        )
        assert response.status_code == 200
        data = response.json()
        assert "issuer" in data
        assert "authorization_endpoint" in data
        assert "token_endpoint" in data

    def test_oauth_protected_resource_metadata(self):
        """OAuth protected resource metadata should be available."""
        response = requests.get(
            f"{TEST_API_URL}/.well-known/oauth-protected-resource/mcp"
        )
        assert response.status_code == 200
        data = response.json()
        assert "resource" in data
        assert "authorization_servers" in data
