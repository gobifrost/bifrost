"""Policy Rules MCP Tools — thin wrappers around the REST API.

Implements ``bifrost_policy_rule_list``, ``bifrost_policy_rule_get``,
``bifrost_policy_rule_create``, ``bifrost_policy_rule_update``,
``bifrost_policy_rule_delete``, and ``bifrost_policy_rule_usage_list`` as
thin HTTP bridge tools (no ORM, no repositories, no AsyncSession).

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


async def bifrost_policy_rule_list(context: Any, domain: str | None = None) -> ToolResult:
    """List policy rules visible to the caller — ``GET /api/policy-rules``.

    ``domain`` optionally filters by ``'file'`` or ``'table'``.
    """
    logger.info("MCP bifrost_policy_rule_list (HTTP bridge)")
    params: dict[str, str] = {}
    if domain:
        params["domain"] = domain

    url = "/api/policy-rules"
    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{url}?{qs}"

    status_code, body = await call_rest(context, "GET", url)
    if status_code != 200:
        return error_result(f"bifrost_policy_rule_list failed: HTTP {status_code}", {"body": body})
    items = body if isinstance(body, list) else []
    return success_result(
        f"Found {len(items)} policy rule(s)",
        {"policy_rules": items, "count": len(items)},
    )


async def bifrost_policy_rule_create(
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
        return error_result(f"bifrost_policy_rule_create failed: HTTP {status_code}", {"body": resp})
    return success_result(
        f"Created policy rule: {name}",
        resp if isinstance(resp, dict) else {"body": resp},
    )


async def bifrost_policy_rule_get(
    context: Any,
    domain: str,
    name: str,
    organization_id: str | None = None,
) -> ToolResult:
    """Get a single policy rule by domain and name — thin wrapper over
    ``GET /api/policy-rules/{domain}/{name}``.

    Solution-managed rules are readable (writes stay blocked).
    ``organization_id`` scopes the lookup (omit for global).
    """
    if not domain:
        return error_result("domain is required")
    if not name:
        return error_result("name is required")

    url = f"/api/policy-rules/{domain}/{name}"
    if organization_id is not None:
        url = f"{url}?organization_id={organization_id}"

    status_code, body = await call_rest(context, "GET", url)
    if status_code != 200:
        return error_result(f"bifrost_policy_rule_get failed: HTTP {status_code}", {"body": body})
    return success_result(
        f"Policy rule: {domain}/{name}",
        body if isinstance(body, dict) else {"body": body},
    )


async def bifrost_policy_rule_update(
    context: Any,
    domain: str,
    name: str,
    new_name: str | None = None,
    description: str | None = None,
    body: dict | None = None,
    organization_id: str | None = None,
) -> ToolResult:
    """Update a policy rule — ``PUT /api/policy-rules/{domain}/{name}``.

    Fails with HTTP 409 if the rule is solution-managed or a built-in.
    ``organization_id`` scopes the lookup (omit for global). ``new_name``
    renames the rule (wire field ``name`` on :class:`PolicyRuleUpdate`).
    """
    if not domain:
        return error_result("domain is required")
    if not name:
        return error_result("name is required")

    payload: dict[str, Any] = {}
    if new_name is not None:
        payload["name"] = new_name
    if description is not None:
        payload["description"] = description
    if body is not None:
        payload["body"] = body

    url = f"/api/policy-rules/{domain}/{name}"
    if organization_id is not None:
        url = f"{url}?organization_id={organization_id}"

    status_code, resp = await call_rest(context, "PUT", url, json_body=payload)
    if status_code != 200:
        return error_result(f"bifrost_policy_rule_update failed: HTTP {status_code}", {"body": resp})
    return success_result(
        f"Updated policy rule: {domain}/{name}",
        resp if isinstance(resp, dict) else {"body": resp},
    )


async def bifrost_policy_rule_usage_list(
    context: Any,
    domain: str,
    name: str,
    organization_id: str | None = None,
) -> ToolResult:
    """List the file policies and tables referencing a policy rule —
    ``GET /api/policy-rules/{domain}/{name}/usages``.
    """
    if not domain:
        return error_result("domain is required")
    if not name:
        return error_result("name is required")

    url = f"/api/policy-rules/{domain}/{name}/usages"
    if organization_id is not None:
        url = f"{url}?organization_id={organization_id}"

    status_code, body = await call_rest(context, "GET", url)
    if status_code != 200:
        return error_result(
            f"bifrost_policy_rule_usage_list failed: HTTP {status_code}", {"body": body}
        )
    return success_result(
        f"Usages for policy rule: {domain}/{name}",
        body if isinstance(body, dict) else {"body": body},
    )


async def bifrost_policy_rule_delete(
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
        return error_result(f"bifrost_policy_rule_delete failed: HTTP {status_code}", {"body": resp})
    return success_result(
        f"Deleted policy rule: {domain}/{name}",
        {"deleted": f"{domain}/{name}"},
    )


TOOLS = [
    ("bifrost_policy_rule_list", "List Policy Rules", "List named policy rules visible to the caller."),
    ("bifrost_policy_rule_get", "Get Policy Rule", "Get a single named policy rule by domain and name."),
    ("bifrost_policy_rule_create", "Create Policy Rule", "Create a named, reusable policy rule."),
    ("bifrost_policy_rule_update", "Update Policy Rule", "Update a named policy rule by domain and name."),
    ("bifrost_policy_rule_delete", "Delete Policy Rule", "Delete a named policy rule by domain and name."),
    (
        "bifrost_policy_rule_usage_list",
        "List Policy Rule Usages",
        "List the file policies and tables referencing a policy rule.",
    ),
]


def register_tools(mcp: Any, get_context_fn: Any) -> None:
    """Register all policy_rules parity tools with FastMCP."""
    from src.services.mcp_server.generators.fastmcp_generator import (
        register_tool_with_context,
    )

    tool_funcs = {
        "bifrost_policy_rule_list": bifrost_policy_rule_list,
        "bifrost_policy_rule_get": bifrost_policy_rule_get,
        "bifrost_policy_rule_create": bifrost_policy_rule_create,
        "bifrost_policy_rule_update": bifrost_policy_rule_update,
        "bifrost_policy_rule_delete": bifrost_policy_rule_delete,
        "bifrost_policy_rule_usage_list": bifrost_policy_rule_usage_list,
    }

    for tool_id, _name, description in TOOLS:
        register_tool_with_context(
            mcp, tool_funcs[tool_id], tool_id, description, get_context_fn
        )


__all__ = [
    "TOOLS",
    "bifrost_policy_rule_create",
    "bifrost_policy_rule_delete",
    "bifrost_policy_rule_get",
    "bifrost_policy_rule_list",
    "bifrost_policy_rule_update",
    "bifrost_policy_rule_usage_list",
    "register_tools",
]
