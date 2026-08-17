"""Platform job MCP tools — thin wrappers around the REST API.

Implements the canonical ``bifrost_get_platform_job`` operation. Any
operation that enqueues durable work (Application publish, OAuth
connection provisioning, and so on) returns a job ID that this tool
reads; the job status contract is shared rather than per-feature.

These tools are **thin wrappers**: they call the corresponding REST
endpoint via the in-process HTTP bridge (:mod:`_http_bridge`). No ORM, no
repositories, no ``AsyncSession``. Authorization (requester identity or
platform administrator) happens behind the REST handler — same path as a
CLI invocation.
"""

from __future__ import annotations

import logging
from typing import Any

from fastmcp.tools import ToolResult

from src.services.mcp_server.tool_result import error_result, success_result
from src.services.mcp_server.tools._http_bridge import call_rest

logger = logging.getLogger(__name__)


def _rest_error(action: str, status_code: int, body: Any) -> ToolResult:
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, dict):
        message = detail.get("message") or detail.get("detail")
    else:
        message = detail
    return error_result(
        str(message) if message else f"{action} failed: HTTP {status_code}",
        {"status_code": status_code, "body": body},
    )


async def bifrost_get_platform_job(
    context: Any,
    job_id: str,
) -> ToolResult:
    """Read durable progress, result, or error for one platform job."""
    if not job_id:
        return error_result("job_id is required")
    logger.info("MCP bifrost_get_platform_job (HTTP bridge) job=%s", job_id)
    status_code, body = await call_rest(
        context,
        "GET",
        f"/api/platform-jobs/{job_id}",
    )
    if status_code != 200 or not isinstance(body, dict):
        return _rest_error("Get platform job", status_code, body)
    status_value = body.get("status", "unknown")
    progress = body.get("progress") or {}
    phase = progress.get("phase")
    description = f"Platform job {status_value}"
    if phase:
        description += f": {phase}"
    if status_value in ("failed", "cancelled"):
        error = body.get("error") or {}
        return error_result(error.get("message") or description, body)
    return success_result(description, body)


TOOLS = [
    (
        "bifrost_get_platform_job",
        "Get Platform Job",
        "Get progress, result, or error for a durable platform job.",
    ),
]


def register_tools(mcp: Any, get_context_fn: Any) -> None:
    """Register all platform job tools with FastMCP."""
    from src.services.mcp_server.generators.fastmcp_generator import (
        register_tool_with_context,
    )

    tool_funcs = {"bifrost_get_platform_job": bifrost_get_platform_job}

    for tool_id, name, description in TOOLS:
        register_tool_with_context(
            mcp, tool_funcs[tool_id], tool_id, description, get_context_fn
        )
