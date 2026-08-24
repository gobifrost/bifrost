"""Workflow execution-history MCP tools backed by the canonical REST API."""

from __future__ import annotations

from typing import Any

from fastmcp.tools import ToolResult

from src.services.mcp_server.tool_result import error_result, success_result
from src.services.mcp_server.tools._http_bridge import call_rest


def _rest_error(action: str, status_code: int, body: Any) -> ToolResult:
    detail = body.get("detail") if isinstance(body, dict) else None
    return error_result(
        str(detail) if detail else f"{action} failed: HTTP {status_code}",
        {"status_code": status_code, "body": body},
    )


def _set_param(params: dict[str, Any], key: str, value: Any) -> None:
    if value is not None:
        params[key] = value


async def bifrost_list_workflow_executions(
    context: Any,
    scope: str | None = None,
    workflow_name: str | None = None,
    workflow_id: str | None = None,
    status: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    exclude_local: bool = True,
    limit: int = 25,
    continuation_token: str | None = None,
) -> ToolResult:
    """List workflow execution summaries visible to the caller."""
    params: dict[str, Any] = {
        "excludeLocal": exclude_local,
        "limit": limit,
    }
    _set_param(params, "scope", scope)
    _set_param(params, "workflowName", workflow_name)
    _set_param(params, "workflowId", workflow_id)
    _set_param(params, "status", status)
    _set_param(params, "startDate", start_date)
    _set_param(params, "endDate", end_date)
    _set_param(params, "continuationToken", continuation_token)
    status_code, body = await call_rest(
        context,
        "GET",
        "/api/executions",
        params=params,
    )
    if status_code != 200 or not isinstance(body, dict):
        return _rest_error("List workflow executions", status_code, body)
    executions = body.get("executions", [])
    if not isinstance(executions, list):
        executions = []
    return success_result(
        f"Found {len(executions)} workflow execution(s)",
        {
            "executions": executions,
            "count": len(executions),
            "continuation_token": body.get("continuation_token"),
        },
    )


async def bifrost_get_workflow_execution(
    context: Any,
    execution_id: str,
) -> ToolResult:
    """Get one authorized workflow execution, including result and logs."""
    if not execution_id:
        return error_result("execution_id is required")
    status_code, body = await call_rest(
        context,
        "GET",
        f"/api/executions/{execution_id}",
    )
    if status_code != 200 or not isinstance(body, dict):
        return _rest_error("Get workflow execution", status_code, body)
    workflow_name = body.get("workflow_name") or "Unknown"
    execution_status = body.get("status") or "Unknown"
    return success_result(
        f"Execution: {workflow_name} ({execution_status})",
        body,
    )


TOOLS = [
    (
        "bifrost_list_workflow_executions",
        "List Workflow Executions",
        "List recent workflow execution summaries visible to the caller.",
    ),
    (
        "bifrost_get_workflow_execution",
        "Get Workflow Execution",
        "Get one workflow execution with result and bounded logs.",
    ),
]


def register_tools(mcp: Any, get_context_fn: Any) -> None:
    """Register canonical execution-history tools with FastMCP."""
    from src.services.mcp_server.generators.fastmcp_generator import (
        register_tool_with_context,
    )

    tool_funcs = {
        "bifrost_list_workflow_executions": bifrost_list_workflow_executions,
        "bifrost_get_workflow_execution": bifrost_get_workflow_execution,
    }
    for tool_id, _name, description in TOOLS:
        register_tool_with_context(
            mcp,
            tool_funcs[tool_id],
            tool_id,
            description,
            get_context_fn,
        )
