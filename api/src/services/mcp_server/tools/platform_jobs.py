"""Platform Job MCP Tools — thin wrappers around the REST API.

RBAC R1b batch 2. ``bifrost_platform_job_get`` reads durable job progress
through the canonical ``GET /api/platform-jobs/{job_id}`` endpoint — the
shared status contract for all long-running platform work (see
``docs/architecture/platform-jobs.md``). No ORM, no repositories.

Previously registered as ``get_app_publish_status`` in ``tools/apps.py``;
moved here and renamed to match the catalog's ``bifrost_platform_job_get``
name (``operation_catalog.py``: ``platform.jobs.get``) now that it reads any
platform job, not just an Application publish.
"""

from __future__ import annotations

import logging
from typing import Any

from fastmcp.tools import ToolResult

from src.services.mcp_server.tool_result import error_result, success_result
from src.services.mcp_server.tools._http_bridge import call_rest

logger = logging.getLogger(__name__)


async def bifrost_platform_job_get(context: Any, job_id: str) -> ToolResult:
    """Read durable platform job progress through the canonical REST endpoint."""
    logger.info("MCP bifrost_platform_job_get (HTTP bridge) job=%s", job_id)

    if not job_id:
        return error_result("job_id is required")

    status_code, body = await call_rest(
        context,
        "GET",
        f"/api/platform-jobs/{job_id}",
    )
    if status_code != 200 or not isinstance(body, dict):
        return error_result(
            f"bifrost_platform_job_get failed: HTTP {status_code}",
            {"body": body},
        )
    status_value = body.get("status", "unknown")
    progress = body.get("progress") or {}
    phase = progress.get("phase")
    description = f"Platform job {status_value}"
    if phase:
        description += f": {phase}"
    if status_value == "requires_action":
        result = body.get("result")
        action = result.get("requires_action") if isinstance(result, dict) else None
        safe_result = {"requires_action": action} if isinstance(action, str) else {}
        return error_result(
            description,
            {
                "status": status_value,
                "result": safe_result,
            },
        )
    if status_value in ("failed", "cancelled"):
        error = body.get("error") or {}
        return error_result(
            error.get("message") or description,
            body,
        )
    return success_result(description, body)


TOOLS = [
    (
        "bifrost_platform_job_get",
        "Get Platform Job",
        "Get progress, result, or error for a durable platform job.",
    ),
]


def register_tools(mcp: Any, get_context_fn: Any) -> None:
    """Register all platform job tools with FastMCP."""
    from src.services.mcp_server.generators.fastmcp_generator import (
        register_tool_with_context,
    )

    tool_funcs = {
        "bifrost_platform_job_get": bifrost_platform_job_get,
    }

    for tool_id, name, description in TOOLS:
        register_tool_with_context(
            mcp, tool_funcs[tool_id], tool_id, description, get_context_fn
        )


__all__ = [
    "TOOLS",
    "bifrost_platform_job_get",
    "register_tools",
]
