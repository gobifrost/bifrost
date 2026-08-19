"""Policy Rules MCP Tools — thin wrappers around the REST API.

Thin HTTP bridge tools (no ORM, no repositories, no AsyncSession) covering the
full REST surface: list, get, create, update, delete, and usages.

A policy rule's identity is the pair ``(domain, name)``, not a UUID — ``domain``
is ``'file'`` or ``'table'`` and selects which access surface may reference the
rule, so the same name can exist independently in both domains.

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


def _rule_url(domain: str, name: str, organization_id: str | None, suffix: str = "") -> str:
    """Build a ``(domain, name)`` rule URL, optionally scoped to an org."""
    url = f"/api/policy-rules/{domain}/{name}{suffix}"
    if organization_id is not None:
        url = f"{url}?organization_id={organization_id}"
    return url


async def bifrost_list_policy_rules(context: Any, domain: str | None = None) -> ToolResult:
    """List policy rules visible to the caller — ``GET /api/policy-rules``.

    ``domain`` optionally filters by ``'file'`` or ``'table'``.
    """
    logger.info("MCP bifrost_list_policy_rules (HTTP bridge)")
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


async def bifrost_create_policy_rule(
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


async def bifrost_delete_policy_rule(
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

    status_code, resp = await call_rest(
        context, "DELETE", _rule_url(domain, name, organization_id)
    )
    if status_code not in (200, 204):
        return error_result(f"delete_policy_rule failed: HTTP {status_code}", {"body": resp})
    return success_result(
        f"Deleted policy rule: {domain}/{name}",
        {"deleted": f"{domain}/{name}"},
    )


async def bifrost_get_policy_rule(
    context: Any,
    domain: str,
    name: str,
    organization_id: str | None = None,
) -> ToolResult:
    """Get one policy rule — ``GET /api/policy-rules/{domain}/{name}``.

    ``domain`` is ``'file'`` or ``'table'``. ``organization_id`` scopes the
    lookup (omit for global).
    """
    if not domain:
        return error_result("domain is required")
    if not name:
        return error_result("name is required")

    status_code, body = await call_rest(
        context, "GET", _rule_url(domain, name, organization_id)
    )
    if status_code != 200:
        return error_result(f"get_policy_rule failed: HTTP {status_code}", {"body": body})
    if not isinstance(body, dict):
        return error_result(
            "get_policy_rule returned an unexpected payload", {"body": body}
        )
    return success_result(f"Policy rule: {domain}/{name}", body)


async def bifrost_update_policy_rule(
    context: Any,
    domain: str,
    name: str,
    new_name: str | None = None,
    description: str | None = None,
    body: dict | None = None,
    organization_id: str | None = None,
) -> ToolResult:
    """Update a policy rule — ``PUT /api/policy-rules/{domain}/{name}``.

    ``domain`` is immutable; the server re-validates ``body`` against the
    stored domain. ``new_name`` renames the rule (the wire field is ``name``,
    renamed here so it does not collide with the ``name`` path argument).
    Unset fields are omitted and the server preserves existing values.
    Fails with HTTP 409 if the rule is a built-in.
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

    status_code, resp = await call_rest(
        context,
        "PUT",
        _rule_url(domain, name, organization_id),
        json_body=payload,
    )
    if status_code != 200:
        return error_result(f"update_policy_rule failed: HTTP {status_code}", {"body": resp})
    return success_result(
        f"Updated policy rule: {domain}/{name}",
        resp if isinstance(resp, dict) else {"body": resp},
    )


async def bifrost_list_policy_rule_usages(
    context: Any,
    domain: str,
    name: str,
    organization_id: str | None = None,
) -> ToolResult:
    """List what references a rule — ``GET .../{domain}/{name}/usages``.

    Returns every file policy and table that references the rule. A rule with
    usages cannot be deleted, so this is the way to find out what blocks a
    delete before attempting one.
    """
    if not domain:
        return error_result("domain is required")
    if not name:
        return error_result("name is required")

    status_code, body = await call_rest(
        context, "GET", _rule_url(domain, name, organization_id, "/usages")
    )
    if status_code != 200:
        return error_result(
            f"list_policy_rule_usages failed: HTTP {status_code}", {"body": body}
        )
    if not isinstance(body, dict):
        return error_result(
            "list_policy_rule_usages returned an unexpected payload", {"body": body}
        )
    return success_result(
        f"Policy rule {domain}/{name} has {body.get('total', 0)} usage(s)", body
    )


TOOLS = [
    ("bifrost_list_policy_rules", "List Policy Rules", "List named policy rules visible to the caller."),
    ("bifrost_get_policy_rule", "Get Policy Rule", "Get one named policy rule by domain and name."),
    ("bifrost_create_policy_rule", "Create Policy Rule", "Create a named, reusable policy rule."),
    ("bifrost_update_policy_rule", "Update Policy Rule", "Update a named policy rule by domain and name."),
    ("bifrost_delete_policy_rule", "Delete Policy Rule", "Delete a named policy rule by domain and name."),
    ("bifrost_list_policy_rule_usages", "List Policy Rule Usages", "List the file policies and tables that reference a policy rule."),
]


def register_tools(mcp: Any, get_context_fn: Any) -> None:
    """Register all policy_rules parity tools with FastMCP."""
    from src.services.mcp_server.generators.fastmcp_generator import (
        register_tool_with_context,
    )

    tool_funcs = {
        "bifrost_list_policy_rules": bifrost_list_policy_rules,
        "bifrost_get_policy_rule": bifrost_get_policy_rule,
        "bifrost_create_policy_rule": bifrost_create_policy_rule,
        "bifrost_update_policy_rule": bifrost_update_policy_rule,
        "bifrost_delete_policy_rule": bifrost_delete_policy_rule,
        "bifrost_list_policy_rule_usages": bifrost_list_policy_rule_usages,
    }

    for tool_id, _name, description in TOOLS:
        register_tool_with_context(
            mcp, tool_funcs[tool_id], tool_id, description, get_context_fn
        )


__all__ = [
    "TOOLS",
    "bifrost_create_policy_rule",
    "bifrost_delete_policy_rule",
    "bifrost_get_policy_rule",
    "bifrost_list_policy_rule_usages",
    "bifrost_list_policy_rules",
    "bifrost_update_policy_rule",
    "register_tools",
]
