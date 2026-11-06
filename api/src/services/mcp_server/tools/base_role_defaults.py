"""Thin REST wrappers for account provisioning defaults."""

from __future__ import annotations

from typing import Any

from src.services.mcp_server.tool_result import ToolResult, error_result, success_result
from src.services.mcp_server.tools._http_bridge import call_rest


async def bifrost_base_role_default_get(context: Any, organization_id: str | None = None) -> ToolResult:
    """Read the instance default base role or an organization's override."""
    params = {"organization_id": organization_id} if organization_id is not None else {}
    code, body = await call_rest(context, "GET", "/api/base-role-defaults", params=params)
    if code != 200:
        return error_result(f"Could not read base role default: HTTP {code}", {"body": body})
    return success_result("Default base role", body)


async def bifrost_base_role_default_update(
    context: Any, default_base_role_id: str | None, organization_id: str | None = None,
) -> ToolResult:
    """Set the base role for new accounts; null resets/inherits. Existing accounts are unchanged."""
    params = {"organization_id": organization_id} if organization_id is not None else {}
    code, body = await call_rest(
        context, "PUT", "/api/base-role-defaults", params=params,
        json_body={"default_base_role_id": default_base_role_id},
    )
    if code != 200:
        return error_result(f"Could not update base role default: HTTP {code}", {"body": body})
    return success_result("Default base role updated", body)


TOOLS = [
    ("bifrost_base_role_default_get", "Get Default Base Role", "Read the instance or organization default base role."),
    ("bifrost_base_role_default_update", "Set Default Base Role", "Set the base role for new users and custom identities."),
]


def register_tools(mcp: Any, get_context_fn: Any) -> None:
    from src.services.mcp_server.generators.fastmcp_generator import register_tool_with_context

    functions = {
        "bifrost_base_role_default_get": bifrost_base_role_default_get,
        "bifrost_base_role_default_update": bifrost_base_role_default_update,
    }
    for tool_id, _name, description in TOOLS:
        register_tool_with_context(mcp, functions[tool_id], tool_id, description, get_context_fn)
