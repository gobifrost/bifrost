"""Policy Rules MCP Tools — thin wrappers around the REST API.

Implements ``list_policy_rules``, ``create_policy_rule``, ``delete_policy_rule``
as thin HTTP bridge tools (no ORM, no repositories, no AsyncSession).

Mirrors :mod:`configs`: validate minimal inputs, then call the REST endpoint
via the in-process HTTP bridge.
"""

from __future__ import annotations

import logging
from typing import Any

from fastmcp.tools import ToolResult

from src.services.mcp_server.tool_result import error_result, success_result
from src.services.mcp_server.tools._http_bridge import call_rest

logger = logging.getLogger(__name__)


async def list_policy_rules(context: Any, domain: str | None = None) -> ToolResult:
    """List policy rules visible to the caller — ``GET /api/policy-rules``.

    ``domain`` optionally filters by ``'file'`` or ``'table'``.
    """
    logger.info("MCP list_policy_rules (HTTP bridge)")
    params: dict[str, str] = {}
    if domain:
        params["domain"] = domain

    url = "/api/policy-rules"
    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{url}?{qs}"

    status_code, body = await call_rest(context, "GET", url)
    if status_code != 200:
        return error_result(f"list_policy_rules failed: HTTP {status_code}", {"body": body})
    items = body if isinstance(body, list) else []
    return success_result(
        f"Found {len(items)} policy rule(s)",
        {"policy_rules": items, "count": len(items)},
    )


async def create_policy_rule(
    context: Any,
    name: str,
    domain: str,
    body: dict,
    description: str | None = None,
    organization_id: str | None = None,
) -> ToolResult:
    """Create a named policy rule — ``POST /api/policy-rules``.

    ``domain`` must be ``'file'`` or ``'table'``. ``body`` is the rule body
    dict (``{actions, when}``). ``organization_id`` is optional (omit for
    global scope).
    """
    if not name:
        return error_result("name is required")
    if not domain:
        return error_result("domain is required")
    if not body:
        return error_result("body is required")

    payload: dict[str, Any] = {
        "name": name,
        "domain": domain,
        "body": body,
    }
    if description is not None:
        payload["description"] = description
    if organization_id is not None:
        payload["organization_id"] = organization_id

    status_code, resp = await call_rest(context, "POST", "/api/policy-rules", json_body=payload)
    if status_code not in (200, 201):
        return error_result(f"create_policy_rule failed: HTTP {status_code}", {"body": resp})
    return success_result(
        f"Created policy rule: {name}",
        resp if isinstance(resp, dict) else {"body": resp},
    )


async def delete_policy_rule(
    context: Any,
    domain: str,
    name: str,
    organization_id: str | None = None,
) -> ToolResult:
    """Delete a policy rule — ``DELETE /api/policy-rules/{domain}/{name}``.

    Fails with HTTP 409 if the rule is in use or is a built-in.
    ``organization_id`` scopes the lookup (omit for global).
    """
    if not domain:
        return error_result("domain is required")
    if not name:
        return error_result("name is required")

    url = f"/api/policy-rules/{domain}/{name}"
    if organization_id is not None:
        url = f"{url}?organization_id={organization_id}"

    status_code, resp = await call_rest(context, "DELETE", url)
    if status_code not in (200, 204):
        return error_result(f"delete_policy_rule failed: HTTP {status_code}", {"body": resp})
    return success_result(
        f"Deleted policy rule: {domain}/{name}",
        {"deleted": f"{domain}/{name}"},
    )


TOOLS = [
    ("list_policy_rules", "List Policy Rules", "List named policy rules visible to the caller."),
    ("create_policy_rule", "Create Policy Rule", "Create a named, reusable policy rule."),
    ("delete_policy_rule", "Delete Policy Rule", "Delete a named policy rule by domain and name."),
]


def register_tools(mcp: Any, get_context_fn: Any) -> None:
    """Register all policy_rules parity tools with FastMCP."""
    from src.services.mcp_server.generators.fastmcp_generator import (
        register_tool_with_context,
    )

    tool_funcs = {
        "list_policy_rules": list_policy_rules,
        "create_policy_rule": create_policy_rule,
        "delete_policy_rule": delete_policy_rule,
    }

    for tool_id, _name, description in TOOLS:
        register_tool_with_context(
            mcp, tool_funcs[tool_id], tool_id, description, get_context_fn
        )


__all__ = [
    "TOOLS",
    "create_policy_rule",
    "delete_policy_rule",
    "list_policy_rules",
    "register_tools",
]
