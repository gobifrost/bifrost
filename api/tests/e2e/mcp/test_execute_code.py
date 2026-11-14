"""One real inline job: MCP admission, worker SDK authority, and owned results.

Permission/configuration edge cases stay in the service and middleware tests.
Fixtures seed only rows; no Workflow registration or setup jobs are needed.
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
    e2e_client, org1, org1_user, human_minter, async_session_factory,
):
    """One default MCP execution preserves original-user SDK permissions.

    The same human token sees grants and revocation immediately. The default
    endpoint exposes the tool without an Agent; Agent MCP additionally requires
    its explicit assignment. SDK authorization is exercised inside the sole job.
    """
    tool = "bifrost_execute_code"
    role_id, assigned_id, unassigned_id = uuid4(), uuid4(), uuid4()
    execution_id = None
    human_minter(org1_user)
    headers = org1_user.headers
    async with async_session_factory() as db:
        db.add(Role(id=role_id, name=f"Execute Code {role_id}", created_by="test"))
        await db.flush()
        db.add(UserRole(user_id=org1_user.user_id, role_id=role_id, assigned_by="test"))
        await db.flush()
        db.add(UserRoleBoundary(user_id=org1_user.user_id, role_id=role_id, kind="platform"))
        for agent_id, tools in [(assigned_id, [tool, "bifrost_execution_get"]), (unassigned_id, [])]:
            db.add(Agent(
                id=agent_id, name=f"Execute Code {agent_id}", system_prompt="Use assigned tools.",
                organization_id=UUID(org1["id"]), access_level=AgentAccessLevel.AUTHENTICATED,
                system_tools=tools, created_by="test",
            ))
        await db.commit()

    try:
        assert tool not in _tools(e2e_client, headers)
        async with async_session_factory() as db:
            db.add(RolePermission(role_id=role_id, permission="code.execute"))
            await db.commit()
        assert tool in _tools(e2e_client, headers)
        assert tool in _tools(e2e_client, headers, path=f"/mcp/{assigned_id}")
        assert tool not in _tools(e2e_client, headers, path=f"/mcp/{unassigned_id}")

        # A specific SDK denial proves Execute Code grants no SDK powers;
        # unrelated import/runtime failures must fail the execution instead.
        source = """
from bifrost import organizations
from bifrost.client import BifrostAuthorizationError
try:
    await organizations.list()
    sdk_read_denied = False
except BifrostAuthorizationError:
    sdk_read_denied = True
result = {"total": left + right, "sdk_read_denied": sdk_read_denied}
"""
        receipt = _call(e2e_client, headers, tool, {
            "code": source, "input_data": {"left": 19, "right": 23},
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
        assert result["result"] == {"total": 42, "sdk_read_denied": True}
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
        async with async_session_factory() as db:
            if execution_id is not None:
                await db.execute(delete(ExecutionLog).where(ExecutionLog.execution_id == execution_id))
                await db.execute(delete(Execution).where(Execution.id == execution_id))
            await db.execute(delete(Agent).where(Agent.id.in_([assigned_id, unassigned_id])))
            await db.execute(delete(Role).where(Role.id == role_id))
            await db.commit()
