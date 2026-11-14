"""One real inline job: MCP admission, worker SDK authority, and owned results.

Permission/configuration edge cases stay in the service and middleware tests.
Fixtures seed rows and one helper file; no Workflow registration or setup jobs
are needed. The sole job proves module imports and SDK authorization together.
"""

import asyncio
import time
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete

from src.models.enums import AgentAccessLevel
from src.models.orm.agents import Agent
from src.models.orm.executions import Execution, ExecutionLog
from src.models.orm.users import Role, RolePermission, UserRole, UserRoleBoundary


def _mcp(client, headers, method, params, *, path="/mcp"):
    response = client.post(path, headers={
        **headers, "Accept": "application/json, text/event-stream",
    }, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert "error" not in payload, payload
    return payload["result"]


def _tools(client, headers, *, path="/mcp"):
    return {tool["name"] for tool in _mcp(client, headers, "tools/list", {}, path=path)["tools"]}


def _call(client, headers, name, arguments, *, path="/mcp"):
    result = _mcp(client, headers, "tools/call", {"name": name, "arguments": arguments}, path=path)
    assert not result.get("isError"), result
    return result["structuredContent"]


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_execute_code_mcp_permission_assignment_and_worker_boundary(
    e2e_client, org1, org1_user, platform_admin, human_minter, async_session_factory,
):
    """One default MCP execution preserves original-user SDK permissions.

    The same human token sees grants and revocation immediately. The default
    endpoint exposes the tool without an Agent; Agent MCP additionally requires
    its explicit assignment. SDK authorization is exercised inside the sole job.
    """
    tool = "bifrost_execute_code"
    role_id, assigned_id, unassigned_id = uuid4(), uuid4(), uuid4()
    module_package = f"execute_code_{uuid4().hex}"
    module_path = f"{module_package}/helper.py"
    helper_source = "def add(left, right):\n    return left + right\n"
    companions = ["bifrost_get_docs", "bifrost_execution_get", "list_content",
                  "bifrost_file_search", "read_content_lines"]
    execution_id = None
    human_minter(org1_user)
    headers = org1_user.headers
    async with async_session_factory() as db:
        db.add(Role(id=role_id, name=f"Execute Code {role_id}", created_by="test"))
        await db.flush()
        db.add(UserRole(user_id=org1_user.user_id, role_id=role_id, assigned_by="test"))
        await db.flush()
        db.add(UserRoleBoundary(user_id=org1_user.user_id, role_id=role_id, kind="platform"))
        for agent_id, tools in [(assigned_id, [tool, *companions]), (unassigned_id, [])]:
            db.add(Agent(
                id=agent_id, name=f"Execute Code {agent_id}", system_prompt="Use assigned tools.",
                organization_id=UUID(org1["id"]), access_level=AgentAccessLevel.AUTHENTICATED,
                system_tools=tools, created_by="test",
            ))
        await db.commit()

    try:
        seed = e2e_client.put("/api/files/editor/content", headers=platform_admin.headers, json={
            "path": module_path, "content": helper_source, "encoding": "utf-8",
        })
        assert seed.status_code in (200, 201), seed.text
        assert tool not in _tools(e2e_client, headers)
        async with async_session_factory() as db:
            db.add(RolePermission(role_id=role_id, permission="code.execute"))
            await db.commit()
        assert tool in _tools(e2e_client, headers)
        assert {tool, *companions} <= _tools(e2e_client, headers, path=f"/mcp/{assigned_id}")
        assert tool not in _tools(e2e_client, headers, path=f"/mcp/{unassigned_id}")
        docs = _call(e2e_client, headers, "bifrost_get_docs", {})["schema"]
        assert "files.read(" in docs and "files.list(" in docs
        assert "modules.customer_helpers" in docs
        assert "bifrost_execution_get" in docs
        files_reference = _call(e2e_client, headers, "bifrost_get_docs", {"sdk_module": "files"})
        assert "files.read(" in files_reference["schema"]
        agent_reference = _call(e2e_client, headers, "bifrost_get_docs", {
            "sdk_module": "artifacts",
        }, path=f"/mcp/{assigned_id}")
        assert "artifacts.create_document(" in agent_reference["schema"]

        # Tool assignment and Execute Code alone do not grant source access.
        read_denied = _mcp(e2e_client, headers, "tools/call", {
            "name": "read_content_lines", "arguments": {"path": module_path},
        }, path=f"/mcp/{assigned_id}")
        assert read_denied["isError"] is True
        async with async_session_factory() as db:
            db.add(RolePermission(role_id=role_id, permission="repository.read"))
            await db.commit()
        matches = _call(e2e_client, headers, "bifrost_file_search", {
            "query": "def add", "source": "workspace", "include_pattern": f"{module_package}/*.py",
        }, path=f"/mcp/{assigned_id}")
        assert {hit["file_path"] for hit in matches["matches"]} == {module_path}
        source_read = _call(e2e_client, headers, "read_content_lines", {
            "path": module_path,
        }, path=f"/mcp/{assigned_id}")
        assert "def add(left, right)" in source_read["content"]

        # A specific SDK denial proves Execute Code grants no SDK powers;
        # unrelated import/runtime failures must fail the execution instead.
        source = f"""
from {module_package}.helper import add
from bifrost import files, organizations
from bifrost.client import BifrostAuthorizationError
module_source = await files.read(module_path, location="workspace")
module_files = await files.list("{module_package}", location="workspace")
try:
    await organizations.list()
    sdk_read_denied = False
except BifrostAuthorizationError:
    sdk_read_denied = True
result = {{"total": add(left, right), "sdk_read_denied": sdk_read_denied,
          "source_read": module_source == expected_source, "module_files": module_files}}
"""
        receipt = _call(e2e_client, headers, tool, {
            "code": source, "input_data": {
                "left": 19, "right": 23, "module_path": module_path, "expected_source": helper_source,
            },
            "script_name": "execute-code-spike-proof",
        })
        execution_id = UUID(receipt["execution_id"])
        assert receipt["async"] is True

        deadline = time.monotonic() + 30
        while True:
            result = _call(e2e_client, headers, "bifrost_get_execution", {"execution_id": str(execution_id)})
            if result["status"] not in {"Pending", "Running"}:
                break
            assert time.monotonic() < deadline, result
            await asyncio.sleep(0.1)
        assert result["status"] == "Success", result
        assert result["workflow_id"] is None
        assert result["result"]["total"] == 42
        assert result["result"]["sdk_read_denied"] is True
        assert result["result"]["source_read"] is True
        assert module_path in result["result"]["module_files"]
        agent_result = _call(
            e2e_client, headers, "bifrost_execution_get", {"execution_id": str(execution_id)},
            path=f"/mcp/{assigned_id}",
        )
        assert agent_result["result"] == result["result"]

        async with async_session_factory() as db:
            await db.execute(delete(RolePermission).where(RolePermission.role_id == role_id))
            await db.commit()
        assert tool not in _tools(e2e_client, headers)
        assert tool not in _tools(e2e_client, headers, path=f"/mcp/{assigned_id}")
    finally:
        cleanup = e2e_client.delete("/api/files/editor", headers=platform_admin.headers, params={
            "path": module_path,
        })
        assert cleanup.status_code in (200, 204, 404), cleanup.text
        async with async_session_factory() as db:
            if execution_id is not None:
                await db.execute(delete(ExecutionLog).where(ExecutionLog.execution_id == execution_id))
                await db.execute(delete(Execution).where(Execution.id == execution_id))
            await db.execute(delete(Agent).where(Agent.id.in_([assigned_id, unassigned_id])))
            await db.execute(delete(Role).where(Role.id == role_id))
            await db.commit()
