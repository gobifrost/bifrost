"""Execution MCP Tools — thin wrappers around the REST API.

RBAC R1b batch 2. ``bifrost_execution_list`` / ``bifrost_execution_get`` call
``GET /api/executions`` and ``GET /api/executions/{id}`` through the
in-process HTTP bridge (:mod:`_http_bridge`), same as ``tools/roles.py`` /
``tools/agents.py``. No ORM, no repositories, no ``AsyncSession`` — MCP now
enforces exactly what REST enforces (see ``shared/sdk_execution_reads.py``
for the shared scope/authorization logic both surfaces call into).

Known behavior changes vs. the old ORM-backed tools (see the R1b batch 2
report for the full persona table):

* A platform admin now sees executions across all orgs by default (REST's
  ``scope`` query param, exposed here as an optional ``scope`` argument),
  not just their own org.
* REST defaults ``excludeLocal=true``; local-runner executions are excluded
  by default where the old MCP path included them. Pass ``exclude_local``
  explicitly to opt back in.
* Non-admin filtering (``executed_by == caller``) is unchanged — enforced by
  the shared read service, not by this tool.
"""

from __future__ import annotations

import logging
from typing import Any

from fastmcp.tools import ToolResult

from src.services.mcp_server.tool_result import error_result, success_result
from src.services.mcp_server.tools._http_bridge import call_rest

logger = logging.getLogger(__name__)

# Old, larger executions could carry thousands of structured log lines; keep
# the response body reasonable for a model by trimming to the most recent
# entries. Trimmed from the REST response, never a second query.
_MAX_LOGS = 20


async def bifrost_execution_list(
    context: Any,
    workflow_name: str | None = None,
    status: str | None = None,
    limit: int = 20,
    scope: str | None = None,
    exclude_local: bool | None = None,
) -> ToolResult:
    """List recent workflow executions — thin wrapper over ``GET /api/executions``.

    ``scope`` forwards to REST: omit for the caller's default (all orgs for
    a platform admin, own org otherwise), ``"global"`` for global-only, or an
    org UUID for that org + global. ``exclude_local`` defaults to REST's
    ``excludeLocal=true`` when omitted.
    """
    logger.info(
        "MCP bifrost_execution_list (HTTP bridge) workflow=%s status=%s",
        workflow_name,
        status,
    )

    params: dict[str, Any] = {"limit": limit}
    if workflow_name:
        params["workflowName"] = workflow_name
    if status:
        params["status"] = status
    if scope is not None:
        params["scope"] = scope
    if exclude_local is not None:
        params["excludeLocal"] = exclude_local

    status_code, body = await call_rest(context, "GET", "/api/executions", params=params)
    if status_code != 200 or not isinstance(body, dict):
        return error_result(
            f"bifrost_execution_list failed: HTTP {status_code}", {"body": body}
        )

    executions = body.get("executions", [])
    return success_result(
        f"Found {len(executions)} execution(s)",
        {
            "executions": executions,
            "count": len(executions),
            "continuation_token": body.get("continuation_token"),
        },
    )


async def bifrost_execution_get(context: Any, execution_id: str) -> ToolResult:
    """Get details and logs for a specific workflow execution — thin wrapper
    over ``GET /api/executions/{execution_id}``.
    """
    logger.info("MCP bifrost_execution_get (HTTP bridge) id=%s", execution_id)

    if not execution_id:
        return error_result("execution_id is required")

    status_code, body = await call_rest(
        context, "GET", f"/api/executions/{execution_id}"
    )
    if status_code == 404:
        return error_result(f"Execution not found: {execution_id}")
    if status_code == 403:
        return error_result("Access denied")
    if status_code != 200 or not isinstance(body, dict):
        return error_result(
            f"bifrost_execution_get failed: HTTP {status_code}", {"body": body}
        )

    logs = body.get("logs")
    if isinstance(logs, list) and len(logs) > _MAX_LOGS:
        body = {**body, "logs": logs[-_MAX_LOGS:]}

    workflow_name = body.get("workflow_name") or "Unknown"
    status_value = body.get("status") or "unknown"
    return success_result(f"Execution: {workflow_name} ({status_value})", body)


# Tool metadata for registration
TOOLS = [
    (
        "bifrost_execution_list",
        "List Executions",
        "List recent workflow executions.",
    ),
    (
        "bifrost_execution_get",
        "Get Execution",
        "Get details and logs for a specific workflow execution.",
    ),
]


def register_tools(mcp: Any, get_context_fn: Any) -> None:
    """Register all execution tools with FastMCP."""
    from src.services.mcp_server.generators.fastmcp_generator import register_tool_with_context

    tool_funcs = {
        "bifrost_execution_list": bifrost_execution_list,
        "bifrost_execution_get": bifrost_execution_get,
    }

    for tool_id, name, description in TOOLS:
        register_tool_with_context(mcp, tool_funcs[tool_id], tool_id, description, get_context_fn)


__all__ = [
    "TOOLS",
    "bifrost_execution_get",
    "bifrost_execution_list",
    "register_tools",
]
