"""Wire-level proof for progressive agent discovery on the default MCP URL."""

import json
import os
import uuid

import pytest
import requests

TEST_API_URL = os.getenv("TEST_API_URL", "http://api:8000")
MCP_ACCEPT = "application/json, text/event-stream"
GATEWAY_TOOLS = {
    "bifrost_find_agents",
    "bifrost_get_agent",
    "bifrost_get_tool_schema",
    "bifrost_execute_tool",
}


def _mcp_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": MCP_ACCEPT,
    }


def _api_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _mcp_request(
    token: str,
    method: str,
    params: dict,
    *,
    path: str = "/mcp",
    request_id: int = 1,
) -> dict:
    response = requests.post(
        f"{TEST_API_URL}{path}",
        headers=_mcp_headers(token),
        json={
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert "error" not in payload, payload
    return payload


def _call_gateway(token: str, name: str, arguments: dict) -> dict:
    payload = _mcp_request(
        token,
        "tools/call",
        {"name": name, "arguments": arguments},
    )
    result = payload["result"]
    structured = result.get("structuredContent")
    if structured is not None:
        return structured
    assert len(result["content"]) == 1, result
    return json.loads(result["content"][0]["text"])


@pytest.mark.e2e
class TestMCPAgentGateway:
    @pytest.fixture(autouse=True, scope="class")
    def gateway_fixture(self, request, platform_admin):
        suffix = uuid.uuid4().hex[:8]
        function_name = f"gateway_echo_{suffix}"
        path = f"workflows/{function_name}.py"
        token = platform_admin.access_token
        assert token is not None
        headers = _api_headers(token)

        content = (
            "from bifrost import tool\n\n"
            "@tool(description='Echo a message for the MCP gateway proof.')\n"
            f"async def {function_name}(message: str) -> list[dict]:\n"
            "    return [{'echo': message}]\n"
        )
        write_response = requests.put(
            f"{TEST_API_URL}/api/files/editor/content",
            headers=headers,
            json={"path": path, "content": content, "encoding": "utf-8"},
        )
        assert write_response.status_code in (200, 201), write_response.text

        register_response = requests.post(
            f"{TEST_API_URL}/api/workflows/register",
            headers=headers,
            json={"path": path, "function_name": function_name},
        )
        assert register_response.status_code == 201, register_response.text
        workflow_id = register_response.json()["id"]

        agent_name = f"Gateway Proof {suffix}"
        prompt = f"Live gateway instructions {suffix}"
        agent_response = requests.post(
            f"{TEST_API_URL}/api/agents",
            headers=headers,
            json={
                "name": agent_name,
                "description": "Agent used to prove progressive MCP discovery.",
                "system_prompt": prompt,
                "channels": ["chat"],
                "tool_ids": [workflow_id],
                "system_tools": ["get_docs"],
            },
        )
        assert agent_response.status_code == 201, agent_response.text
        agent_id = agent_response.json()["id"]

        request.cls.token = token
        request.cls.headers = headers
        request.cls.agent_id = agent_id
        request.cls.agent_name = agent_name
        request.cls.prompt = prompt
        request.cls.function_name = function_name

        yield

        requests.delete(
            f"{TEST_API_URL}/api/agents/{agent_id}",
            headers=headers,
        )
        requests.delete(
            f"{TEST_API_URL}/api/workflows/{workflow_id}",
            headers=headers,
        )
        requests.delete(
            f"{TEST_API_URL}/api/files/editor",
            headers=headers,
            params={"path": path},
        )

    def test_default_and_agent_scoped_surfaces_are_distinct(self):
        initialize = _mcp_request(
            self.token,
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "gateway-e2e", "version": "1.0"},
            },
        )
        assert "bifrost_find_agents" in initialize["result"]["instructions"]

        default_tools = _mcp_request(
            self.token,
            "tools/list",
            {},
        )["result"]["tools"]
        assert {tool["name"] for tool in default_tools} == GATEWAY_TOOLS

        scoped_tools = _mcp_request(
            self.token,
            "tools/list",
            {},
            path=f"/mcp/{self.agent_id}",
        )["result"]["tools"]
        scoped_names = {tool["name"] for tool in scoped_tools}
        assert any(
            name == self.function_name or name.endswith(self.function_name)
            for name in scoped_names
        )
        assert not (scoped_names & GATEWAY_TOOLS)

    def test_live_discovery_schema_execution_and_revocation(self):
        found = _call_gateway(
            self.token,
            "bifrost_find_agents",
            {"query": self.agent_name},
        )
        assert any(
            agent["id"] == self.agent_id for agent in found["agents"]
        )

        loaded = _call_gateway(
            self.token,
            "bifrost_get_agent",
            {"agent_id": self.agent_id},
        )
        assert loaded["agent"]["instructions"] == self.prompt
        workflow_tool = next(
            tool for tool in loaded["tools"] if tool["source"] == "workflow"
        )
        tool_ref = workflow_tool["tool_ref"]
        system_tool = next(
            tool
            for tool in loaded["tools"]
            if tool["source"] == "system" and tool["name"] == "get_docs"
        )

        schema = _call_gateway(
            self.token,
            "bifrost_get_tool_schema",
            {"agent_id": self.agent_id, "tool_ref": tool_ref},
        )
        assert schema["input_schema"]["required"] == ["message"]

        execution_payload = _mcp_request(
            self.token,
            "tools/call",
            {
                "name": "bifrost_execute_tool",
                "arguments": {
                    "agent_id": self.agent_id,
                    "tool_ref": tool_ref,
                    "arguments": {"message": "hello"},
                },
            },
        )["result"]
        assert "structuredContent" not in execution_payload
        assert json.loads(execution_payload["content"][0]["text"]) == [
            {"echo": "hello"}
        ]

        invalid = _call_gateway(
            self.token,
            "bifrost_execute_tool",
            {
                "agent_id": self.agent_id,
                "tool_ref": tool_ref,
                "arguments": {"message": 42},
            },
        )
        assert invalid["code"] == "INVALID_ARGUMENTS"
        assert invalid["retryable"] is True
        assert invalid["agent_id"] == self.agent_id
        assert invalid["issues"][0]["path"] == "/message"

        executed = _call_gateway(
            self.token,
            "bifrost_execute_tool",
            {
                "agent_id": self.agent_id,
                "tool_ref": system_tool["tool_ref"],
                "arguments": {},
            },
        )
        assert "schema" in executed

        updated_prompt = f"{self.prompt} updated"
        update_response = requests.put(
            f"{TEST_API_URL}/api/agents/{self.agent_id}",
            headers=self.headers,
            json={"system_prompt": updated_prompt},
        )
        assert update_response.status_code == 200, update_response.text
        refreshed = _call_gateway(
            self.token,
            "bifrost_get_agent",
            {"agent_id": self.agent_id},
        )
        assert refreshed["agent"]["instructions"] == updated_prompt

        revoke_response = requests.put(
            f"{TEST_API_URL}/api/agents/{self.agent_id}",
            headers=self.headers,
            json={"tool_ids": []},
        )
        assert revoke_response.status_code == 200, revoke_response.text
        revoked = _call_gateway(
            self.token,
            "bifrost_execute_tool",
            {
                "agent_id": self.agent_id,
                "tool_ref": tool_ref,
                "arguments": {"message": "must not run"},
            },
        )
        assert revoked["code"] == "TOOL_NOT_FOUND_OR_FORBIDDEN"
