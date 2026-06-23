"""Files MCP Tools — thin wrappers around file policy REST endpoints."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

from fastmcp.tools import ToolResult

from src.services.mcp_server.tool_result import error_result, success_result
from src.services.mcp_server.tools._http_bridge import call_rest

logger = logging.getLogger(__name__)


def _policy_path(path: str) -> str:
    return quote(path.strip("/"), safe="")


def _policy_params(location: str, scope: str | None, solution: str | None = None) -> dict[str, str]:
    params = {"location": location}
    if scope is not None:
        params["scope"] = scope
    if solution is not None:
        params["solution"] = solution
    return params


async def list_file_policies(
    context: Any,
    location: str = "workspace",
    scope: str | None = None,
    solution: str | None = None,
) -> ToolResult:
    """List file policies — thin wrapper over ``GET /api/files/policies``."""
    logger.info("MCP list_file_policies (HTTP bridge)")
    status_code, body = await call_rest(
        context,
        "GET",
        "/api/files/policies",
        params=_policy_params(location, scope, solution),
    )
    if status_code != 200:
        return error_result(
            f"list_file_policies failed: HTTP {status_code}",
            {"body": body},
        )
    items = body if isinstance(body, list) else body.get("policies", []) if isinstance(body, dict) else []
    return success_result(
        f"Found {len(items)} file policie(s)",
        {"file_policies": items, "count": len(items)},
    )


async def get_file_policy(
    context: Any,
    path: str,
    location: str = "workspace",
    scope: str | None = None,
    solution: str | None = None,
) -> ToolResult:
    """Get a file policy — thin wrapper over ``GET /api/files/policies/{path}``."""
    if not path:
        return error_result("path is required")
    status_code, body = await call_rest(
        context,
        "GET",
        f"/api/files/policies/{_policy_path(path)}",
        params=_policy_params(location, scope, solution),
    )
    if status_code != 200:
        return error_result(f"get_file_policy failed: HTTP {status_code}", {"body": body})
    return success_result(
        f"File policy: {location}/{path}".rstrip("/"),
        body if isinstance(body, dict) else {"body": body},
    )


async def set_file_policy(
    context: Any,
    path: str,
    policies: list[dict[str, Any]] | dict[str, Any],
    location: str = "workspace",
    scope: str | None = None,
    solution: str | None = None,
) -> ToolResult:
    """Set a file policy — thin wrapper over ``PUT /api/files/policies/{path}``."""
    if not path:
        return error_result("path is required")
    if not isinstance(policies, (list, dict)):
        return error_result("policies must be a list or object")
    status_code, body = await call_rest(
        context,
        "PUT",
        f"/api/files/policies/{_policy_path(path)}",
        params=_policy_params(location, scope, solution),
        json_body={"policies": policies},
    )
    if status_code not in (200, 201):
        return error_result(f"set_file_policy failed: HTTP {status_code}", {"body": body})
    return success_result(
        f"Set file policy: {location}/{path}".rstrip("/"),
        body if isinstance(body, dict) else {"body": body},
    )


async def delete_file_policy(
    context: Any,
    path: str,
    location: str = "workspace",
    scope: str | None = None,
    solution: str | None = None,
) -> ToolResult:
    """Delete a file policy — thin wrapper over ``DELETE /api/files/policies/{path}``."""
    if not path:
        return error_result("path is required")
    status_code, body = await call_rest(
        context,
        "DELETE",
        f"/api/files/policies/{_policy_path(path)}",
        params=_policy_params(location, scope, solution),
    )
    if status_code not in (200, 204):
        return error_result(
            f"delete_file_policy failed: HTTP {status_code}",
            {"body": body},
        )
    return success_result(
        f"Deleted file policy: {location}/{path}".rstrip("/"),
        {"deleted": path, "location": location, "scope": scope},
    )


TOOLS = [
    ("list_file_policies", "List File Policies", "List file access policies."),
    ("get_file_policy", "Get File Policy", "Get a file access policy."),
    ("set_file_policy", "Set File Policy", "Create or replace a file access policy."),
    ("delete_file_policy", "Delete File Policy", "Delete a file access policy."),
]


def register_tools(mcp: Any, get_context_fn: Any) -> None:
    """Register all file policy parity tools with FastMCP."""
    from src.services.mcp_server.generators.fastmcp_generator import (
        register_tool_with_context,
    )

    tool_funcs = {
        "list_file_policies": list_file_policies,
        "get_file_policy": get_file_policy,
        "set_file_policy": set_file_policy,
        "delete_file_policy": delete_file_policy,
    }

    for tool_id, _name, description in TOOLS:
        register_tool_with_context(
            mcp, tool_funcs[tool_id], tool_id, description, get_context_fn
        )


__all__ = [
    "TOOLS",
    "delete_file_policy",
    "get_file_policy",
    "list_file_policies",
    "register_tools",
    "set_file_policy",
]
