"""Thin REST wrappers for the shared workflow conversion wizard operations."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastmcp.tools import ToolResult

from src.services.mcp_server.tool_result import error_result, success_result
from src.services.mcp_server.tools._http_bridge import call_rest


async def bifrost_workflow_conversion_create(
    context: Any,
    workflow_ids: list[str] | None = None,
    scope: str = "all",
    platform_cutover: bool = False,
) -> ToolResult:
    """Start durable read-only workflow permission analysis; observe with platform job get."""
    body: dict[str, Any] = {"scope": scope, "platform_cutover": platform_cutover}
    if workflow_ids is not None:
        body["workflow_ids"] = workflow_ids
    code, result = await call_rest(context, "POST", "/api/workflow-conversions", json_body=body)
    if code != 202:
        return error_result(f"Workflow conversion analysis failed: HTTP {code}", {"body": result})
    return success_result("Workflow conversion analysis queued; no permissions were changed.", result)


async def bifrost_workflow_conversion_get(
    context: Any, job_id: str, offset: int = 0, limit: int = 50,
) -> ToolResult:
    """Read a page of the saved report; this does not restart analysis."""
    try:
        job_uuid = UUID(job_id)
    except ValueError:
        return error_result("job_id must be a UUID")
    code, result = await call_rest(
        context, "GET", f"/api/workflow-conversions/{job_uuid}/report",
        params={"offset": offset, "limit": limit},
    )
    if code != 200:
        return error_result(f"Workflow conversion report failed: HTTP {code}", {"body": result})
    return success_result("Saved workflow conversion report", result)


async def bifrost_workflow_conversion_update(
    context: Any,
    job_id: str,
    selections: list[dict[str, Any]],
    acknowledged_solution_requests: bool = False,
) -> ToolResult:
    """Apply explicit reviewed selections, source fingerprints, identity strategies and acknowledgements."""
    try:
        job_uuid = UUID(job_id)
    except ValueError:
        return error_result("job_id must be a UUID")
    code, result = await call_rest(
        context, "POST", f"/api/workflow-conversions/{job_uuid}/apply",
        json_body={"selections": selections, "acknowledged_solution_requests": acknowledged_solution_requests},
    )
    if code != 202:
        return error_result(f"Reviewed workflow conversion failed: HTTP {code}", {"body": result})
    return success_result("Reviewed conversion queued; use platform job get to observe the outcome.", result)


async def bifrost_workflow_permission_default_update(
    context: Any, job_id: str, acknowledged_remaining_exceptions: bool = False,
) -> ToolResult:
    """Request the final platform cutover after reviewing a platform-wide conversion report."""
    try:
        job_uuid = UUID(job_id)
    except ValueError:
        return error_result("job_id must be a UUID")
    code, result = await call_rest(
        context, "POST", f"/api/workflow-conversions/{job_uuid}/cutover",
        json_body={"acknowledged_remaining_exceptions": acknowledged_remaining_exceptions},
    )
    if code != 202:
        return error_result(f"Platform permission cutover failed: HTTP {code}", {"body": result})
    return success_result("Platform cutover queued; readiness will be revalidated before any default change.", result)


TOOLS = [
    ("bifrost_workflow_conversion_create", "Analyze Workflow Permissions", "Start read-only workflow permission analysis."),
    ("bifrost_workflow_conversion_get", "Get Workflow Conversion", "Read a page of a saved workflow conversion report."),
    ("bifrost_workflow_conversion_update", "Apply Workflow Conversion", "Apply explicitly reviewed workflow permission and identity proposals."),
    ("bifrost_workflow_permission_default_update", "Set Workflow Permission Default", "Request the final reviewed platform Declared Permissions cutover."),
]


def register_tools(mcp: Any, get_context_fn: Any) -> None:
    from src.services.mcp_server.generators.fastmcp_generator import register_tool_with_context

    functions = {
        "bifrost_workflow_conversion_create": bifrost_workflow_conversion_create,
        "bifrost_workflow_conversion_get": bifrost_workflow_conversion_get,
        "bifrost_workflow_conversion_update": bifrost_workflow_conversion_update,
        "bifrost_workflow_permission_default_update": bifrost_workflow_permission_default_update,
    }
    for tool_id, _name, description in TOOLS:
        register_tool_with_context(mcp, functions[tool_id], tool_id, description, get_context_fn)
