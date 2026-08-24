"""Contract tests for REST-backed workflow execution-history MCP tools."""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.services.mcp_server.server import MCPContext
from src.services.mcp_server.tools import execution


@pytest.fixture
def context() -> MCPContext:
    return MCPContext(
        user_id=uuid4(),
        org_id=uuid4(),
        is_platform_admin=False,
        user_email="user@example.com",
        user_name="User",
    )


@pytest.mark.asyncio
async def test_list_workflow_executions_forwards_all_filters(context: MCPContext) -> None:
    response = {
        "executions": [
            {
                "execution_id": "execution-id",
                "workflow_name": "Daily Sync",
                "status": "Success",
            }
        ],
        "continuation_token": "next-page",
    }
    with patch.object(
        execution,
        "call_rest",
        AsyncMock(return_value=(200, response)),
    ) as call:
        result = await execution.bifrost_list_workflow_executions(
            context,
            scope="global",
            workflow_name="Daily Sync",
            workflow_id="workflow-id",
            status="Success,Failed",
            start_date="2026-08-01",
            end_date="2026-08-17",
            exclude_local=False,
            limit=50,
            continuation_token="page-1",
        )

    assert result.structured_content == {
        "executions": response["executions"],
        "count": 1,
        "continuation_token": "next-page",
    }
    call.assert_awaited_once_with(
        context,
        "GET",
        "/api/executions",
        params={
            "excludeLocal": False,
            "limit": 50,
            "scope": "global",
            "workflowName": "Daily Sync",
            "workflowId": "workflow-id",
            "status": "Success,Failed",
            "startDate": "2026-08-01",
            "endDate": "2026-08-17",
            "continuationToken": "page-1",
        },
    )


@pytest.mark.asyncio
async def test_get_workflow_execution_returns_rest_record(context: MCPContext) -> None:
    response = {
        "execution_id": "execution-id",
        "workflow_name": "Daily Sync",
        "status": "Success",
        "result": {"ok": True},
        "logs": [],
    }
    with patch.object(
        execution,
        "call_rest",
        AsyncMock(return_value=(200, response)),
    ) as call:
        result = await execution.bifrost_get_workflow_execution(
            context,
            "execution-id",
        )

    assert result.structured_content == response
    call.assert_awaited_once_with(
        context,
        "GET",
        "/api/executions/execution-id",
    )


@pytest.mark.asyncio
async def test_get_workflow_execution_preserves_rest_denial(context: MCPContext) -> None:
    with patch.object(
        execution,
        "call_rest",
        AsyncMock(return_value=(403, {"detail": "Access denied"})),
    ):
        result = await execution.bifrost_get_workflow_execution(
            context,
            "forbidden-id",
        )

    assert result.structured_content == {
        "error": "Access denied",
        "status_code": 403,
        "body": {"detail": "Access denied"},
    }


@pytest.mark.asyncio
async def test_get_workflow_execution_requires_id(context: MCPContext) -> None:
    result = await execution.bifrost_get_workflow_execution(context, "")

    assert result.structured_content == {"error": "execution_id is required"}
