"""Wire-level proof for progressive agent discovery on the default MCP URL."""

import json
import os
import time
import uuid

import pytest
import requests

TEST_API_URL = os.getenv("TEST_API_URL", "http://api:8000")
MCP_ACCEPT = "application/json, text/event-stream"
GATEWAY_TOOLS = {
    "bifrost_get_required_instructions",
    "bifrost_search_capabilities",
    "bifrost_execute_tool",
    "bifrost_get_execution",
    "bifrost_search_memory",
    "bifrost_save_memory",
    "bifrost_remove_memory",
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

        delegated_agent_name = f"Gateway Delegate {suffix}"
        delegated_agent_response = requests.post(
            f"{TEST_API_URL}/api/agents",
            headers=headers,
            json={
                "name": delegated_agent_name,
                "description": "Agent used to prove async MCP delegation.",
                "system_prompt": "Return a concise result without using tools.",
                "channels": ["chat"],
            },
        )
        assert delegated_agent_response.status_code == 201, delegated_agent_response.text
        delegated_agent_id = delegated_agent_response.json()["id"]

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
                "delegated_agent_ids": [delegated_agent_id],
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
        request.cls.delegated_agent_id = delegated_agent_id
        request.cls.delegated_agent_name = delegated_agent_name

        yield

        requests.delete(
            f"{TEST_API_URL}/api/agents/{agent_id}",
            headers=headers,
        )
        requests.delete(
            f"{TEST_API_URL}/api/agents/{delegated_agent_id}",
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
        assert "bifrost_search_capabilities" in initialize["result"]["instructions"]
        assert "bifrost_get_required_instructions" in initialize["result"]["instructions"]

        default_tools = _mcp_request(
            self.token,
            "tools/list",
            {},
        )["result"]["tools"]
        assert {tool["name"] for tool in default_tools} == GATEWAY_TOOLS
        execute_schema = next(
            tool["inputSchema"]
            for tool in default_tools
            if tool["name"] == "bifrost_execute_tool"
        )
        assert "async" in execute_schema["properties"]
        assert "async_" not in execute_schema["properties"]
        assert "async" not in execute_schema.get("required", [])

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

    def test_required_instructions_can_be_empty(self):
        result = _call_gateway(
            self.token,
            "bifrost_get_required_instructions",
            {},
        )
        assert result["instructions"] == []

    def test_memory_tools_save_search_and_remove_over_mcp(self):
        embedding_response = requests.post(
            f"{TEST_API_URL}/api/admin/llm/embedding-config",
            headers=self.headers,
            json={
                "model": "fixture-embedding",
                "api_key": "fixture-key",
                "endpoint": "http://scheduler-fixtures:8080/v1",
            },
        )
        assert embedding_response.status_code == 200, embedding_response.text
        assert embedding_response.json()["saved"] is True

        platform_response = requests.put(
            f"{TEST_API_URL}/api/admin/memory/settings",
            headers=self.headers,
            json={"enabled": True},
        )
        assert platform_response.status_code == 200, platform_response.text
        user_response = requests.get(
            f"{TEST_API_URL}/api/memory/settings",
            headers=self.headers,
        )
        assert user_response.status_code == 200, user_response.text
        assert user_response.json()["user_enabled"] is True
        assert user_response.json()["effective_enabled"] is True

        memory_id: str | None = None
        try:
            instructions = _call_gateway(
                self.token,
                "bifrost_get_required_instructions",
                {},
            )
            assert "Search memory" in instructions["instructions"][0]

            saved = _call_gateway(
                self.token,
                "bifrost_save_memory",
                {
                    "content": "# Acme onboarding\nUse the Northwind tenant checklist.",
                    "metadata": {"customer": "acme"},
                },
            )
            memory_id = saved["id"]
            assert saved["metadata"] == {"customer": "acme"}

            found = _call_gateway(
                self.token,
                "bifrost_search_memory",
                {"query": "How do we onboard Acme?", "limit": 5},
            )
            assert found["count"] == 1
            assert found["results"][0]["id"] == memory_id
            assert found["results"][0]["content"].startswith("# Acme onboarding")

            removed = _call_gateway(
                self.token,
                "bifrost_remove_memory",
                {"memory_id": memory_id},
            )
            assert removed == {"removed_id": memory_id}
            memory_id = None

            empty = _call_gateway(
                self.token,
                "bifrost_search_memory",
                {"query": "Acme onboarding"},
            )
            assert empty == {"results": [], "count": 0}
        finally:
            if memory_id is not None:
                requests.delete(
                    f"{TEST_API_URL}/api/memory/{memory_id}",
                    headers=self.headers,
                )
            requests.put(
                f"{TEST_API_URL}/api/memory/settings",
                headers=self.headers,
                json={"enabled": False},
            )
            requests.put(
                f"{TEST_API_URL}/api/admin/memory/settings",
                headers=self.headers,
                json={"enabled": False},
            )
            requests.delete(
                f"{TEST_API_URL}/api/admin/llm/embedding-config",
                headers=self.headers,
            )

    def test_live_discovery_schema_execution_and_revocation(self):
        found = _call_gateway(
            self.token,
            "bifrost_search_capabilities",
            {"query": self.agent_name},
        )
        found_agent = next(
            agent for agent in found["agents"] if agent["id"] == self.agent_id
        )
        assert found_agent["instructions_included"] is False
        assert found_agent["complete"] is False
        assert "not the agent's full tool catalog" in found_agent["search_again"]

        loaded = _call_gateway(
            self.token,
            "bifrost_search_capabilities",
            {"agent_id": self.agent_id},
        )
        selected_agent = loaded["agents"][0]
        assert selected_agent["instructions"] == self.prompt
        assert selected_agent["matching_tools"] == []
        assert selected_agent["total_tools"] == 3
        assert selected_agent["returned_tools"] == 0
        assert selected_agent["complete"] is False

        matched = _call_gateway(
            self.token,
            "bifrost_search_capabilities",
            {"agent_id": self.agent_id, "query": "echo message"},
        )
        workflow_tool = next(
            tool
            for tool in matched["agents"][0]["matching_tools"]
            if tool["source"] == "workflow"
        )
        tool_ref = workflow_tool["tool_ref"]
        assert workflow_tool["supports_async"] is True
        assert workflow_tool["default_async"] is False

        delegation_search = _call_gateway(
            self.token,
            "bifrost_search_capabilities",
            {"agent_id": self.agent_id, "query": self.delegated_agent_name},
        )
        delegation_tool = next(
            tool
            for tool in delegation_search["agents"][0]["matching_tools"]
            if tool["source"] == "delegation"
        )
        assert delegation_tool["supports_async"] is True
        assert delegation_tool["default_async"] is True

        system_search = _call_gateway(
            self.token,
            "bifrost_search_capabilities",
            {"agent_id": self.agent_id, "query": "platform documentation"},
        )
        system_tool = next(
            tool
            for tool in system_search["agents"][0]["matching_tools"]
            if tool["source"] == "system" and tool["name"] == "get_docs"
        )

        schema = _call_gateway(
            self.token,
            "bifrost_search_capabilities",
            {"agent_id": self.agent_id, "tool_ref": tool_ref},
        )
        hydrated_tool = schema["agents"][0]["matching_tools"][0]
        assert hydrated_tool["schema_included"] is True
        assert hydrated_tool["input_schema"]["required"] == ["message"]

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

        async_receipt = _call_gateway(
            self.token,
            "bifrost_execute_tool",
            {
                "agent_id": self.agent_id,
                "tool_ref": tool_ref,
                "arguments": {"message": "later"},
                "async": True,
            },
        )
        assert async_receipt["async"] is True
        assert async_receipt["execution_type"] == "workflow"
        assert async_receipt["status"] == "Pending"
        assert async_receipt["execution_id"]

        deadline = time.monotonic() + 15
        while True:
            async_result = _call_gateway(
                self.token,
                "bifrost_get_execution",
                {"execution_id": async_receipt["execution_id"]},
            )
            if async_result["status"] not in {"Pending", "Running"}:
                break
            assert time.monotonic() < deadline, async_result
            time.sleep(0.1)
        assert async_result["status"] == "Success"
        assert async_result["execution_type"] == "workflow"
        assert async_result["result"] == [{"echo": "later"}]
        assert async_result["result_page"]["has_more"] is False

        delegation_receipt = _call_gateway(
            self.token,
            "bifrost_execute_tool",
            {
                "agent_id": self.agent_id,
                "tool_ref": delegation_tool["tool_ref"],
                "arguments": {"task": "Return a concise gateway proof."},
            },
        )
        assert delegation_receipt["async"] is True
        assert delegation_receipt["execution_type"] == "agent_run"
        assert delegation_receipt["status"] == "Pending"
        assert delegation_receipt["execution_id"]

        deadline = time.monotonic() + 15
        while True:
            delegation_result = _call_gateway(
                self.token,
                "bifrost_get_execution",
                {"execution_id": delegation_receipt["execution_id"]},
            )
            if delegation_result["status"] not in {"Pending", "Running"}:
                break
            assert time.monotonic() < deadline, delegation_result
            time.sleep(0.1)
        assert delegation_result["execution_type"] == "agent_run"
        assert delegation_result["agent_id"] == self.delegated_agent_id
        assert delegation_result["status"] == "Failed"
        assert delegation_result["error"]

        unsupported_async = _call_gateway(
            self.token,
            "bifrost_execute_tool",
            {
                "agent_id": self.agent_id,
                "tool_ref": system_tool["tool_ref"],
                "arguments": {},
                "async": True,
            },
        )
        assert unsupported_async["code"] == "ASYNC_NOT_SUPPORTED"
        assert unsupported_async["retryable"] is True

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
            "bifrost_search_capabilities",
            {"agent_id": self.agent_id},
        )
        assert refreshed["agents"][0]["instructions"] == updated_prompt

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
