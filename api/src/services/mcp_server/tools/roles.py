"""Role MCP Tools — thin wrappers around the REST API.

Implements Task 6 of the CLI mutation surface + MCP parity plan:
``list_roles``, ``create_role``, ``update_role``, ``delete_role``.

These tools are **thin wrappers**: they validate minimal inputs, resolve
user-supplied refs, then call the corresponding REST endpoint via the
in-process HTTP bridge (:mod:`_http_bridge`). No ORM, no repositories, no
``AsyncSession``. All side effects (audit logs, cache invalidation, role
sync) happen behind the REST handler — same path as a CLI invocation.

Tool parameters mirror the writable fields of ``RoleCreate`` /
``RoleUpdate`` (declared as Python kwargs); the parity test in
``tests/e2e/mcp/test_mcp_parity.py`` introspects the function signatures
against the DTOs to catch drift.
"""

from __future__ import annotations

import logging
from typing import Any

from fastmcp.tools import ToolResult

from src.services.mcp_server.tool_result import error_result, success_result
from src.services.mcp_server.tools._http_bridge import call_rest, rest_client

logger = logging.getLogger(__name__)


def _ref_error_payload(exc: Exception) -> dict[str, Any]:
    """Format a ref-resolution error for the ToolResult structured body."""
    from bifrost.refs import AmbiguousRefError, RefNotFoundError

    if isinstance(exc, AmbiguousRefError):
        return {"kind": exc.kind, "value": exc.value, "candidates": exc.candidates}
    if isinstance(exc, RefNotFoundError):
        return {"kind": exc.kind, "value": exc.value}
    return {"detail": str(exc)}


async def _assemble_role_body(
    context: Any, fields: dict[str, Any], *, is_update: bool
) -> dict[str, Any]:
    """Validate + assemble the REST payload using the shared DTO generator."""
    from bifrost.dto_flags import DTO_EXCLUDES, assemble_body
    from bifrost.refs import RefResolver
    from src.models.contracts.users import RoleCreate, RoleUpdate

    model_cls = RoleUpdate if is_update else RoleCreate
    exclude = DTO_EXCLUDES.get(model_cls.__name__, set())

    async with rest_client(context) as http:
        resolver = RefResolver(http)
        return await assemble_body(
            model_cls,
            {k: v for k, v in fields.items() if k not in exclude},
            resolver=resolver,
        )


async def list_roles(context: Any) -> ToolResult:
    """List all roles — thin wrapper over ``GET /api/roles``."""
    logger.info("MCP list_roles (HTTP bridge)")
    status_code, body = await call_rest(context, "GET", "/api/roles")
    if status_code != 200:
        return error_result(f"list_roles failed: HTTP {status_code}", {"body": body})
    items = body if isinstance(body, list) else []
    return success_result(
        f"Found {len(items)} role(s)",
        {"roles": items, "count": len(items)},
    )


async def get_role(context: Any, role_ref: str) -> ToolResult:
    """Get a single role — thin wrapper over ``GET /api/roles/{uuid}``.

    ``role_ref`` is a UUID or role name. Names are resolved via the shared
    :class:`RefResolver`.
    """
    if not role_ref:
        return error_result("role_ref is required")

    from bifrost.refs import RefResolver

    async with rest_client(context) as http:
        resolver = RefResolver(http)
        try:
            role_uuid = await resolver.resolve("role", role_ref)
        except Exception as exc:
            return error_result(
                f"could not resolve role {role_ref!r}", _ref_error_payload(exc)
            )

    status_code, body = await call_rest(context, "GET", f"/api/roles/{role_uuid}")
    if status_code != 200:
        return error_result(f"get_role failed: HTTP {status_code}", {"body": body})
    return success_result(
        f"Role: {body.get('name') if isinstance(body, dict) else role_uuid}",
        body if isinstance(body, dict) else {"body": body},
    )


async def create_role(
    context: Any,
    name: str,
    description: str | None = None,
    permissions: dict[str, Any] | None = None,
) -> ToolResult:
    """Create a role — thin wrapper over ``POST /api/roles``."""
    if not name:
        return error_result("name is required")

    fields = {"name": name, "description": description, "permissions": permissions}
    try:
        body = await _assemble_role_body(context, fields, is_update=False)
    except Exception as exc:
        return error_result(f"invalid input: {exc}", _ref_error_payload(exc))

    status_code, resp = await call_rest(context, "POST", "/api/roles", json_body=body)
    if status_code not in (200, 201):
        return error_result(f"create_role failed: HTTP {status_code}", {"body": resp})
    return success_result(f"Created role: {resp.get('name') if isinstance(resp, dict) else ''}", resp if isinstance(resp, dict) else {"body": resp})


async def update_role(
    context: Any,
    role_ref: str,
    name: str | None = None,
    description: str | None = None,
    permissions: dict[str, Any] | None = None,
) -> ToolResult:
    """Update a role — thin wrapper over ``PATCH /api/roles/{uuid}``.

    ``role_ref`` is a UUID or role name. Names are resolved via the
    shared :class:`RefResolver`.
    """
    if not role_ref:
        return error_result("role_ref is required")

    from bifrost.refs import RefResolver

    async with rest_client(context) as http:
        resolver = RefResolver(http)
        try:
            role_uuid = await resolver.resolve("role", role_ref)
        except Exception as exc:
            return error_result(
                f"could not resolve role {role_ref!r}", _ref_error_payload(exc)
            )

    fields = {"name": name, "description": description, "permissions": permissions}
    try:
        body = await _assemble_role_body(context, fields, is_update=True)
    except Exception as exc:
        return error_result(f"invalid input: {exc}", _ref_error_payload(exc))

    status_code, resp = await call_rest(
        context, "PATCH", f"/api/roles/{role_uuid}", json_body=body
    )
    if status_code != 200:
        return error_result(f"update_role failed: HTTP {status_code}", {"body": resp})
    return success_result(
        f"Updated role {role_uuid}",
        resp if isinstance(resp, dict) else {"body": resp},
    )


async def delete_role(context: Any, role_ref: str) -> ToolResult:
    """Delete a role — thin wrapper over ``DELETE /api/roles/{uuid}``.

    CASCADE removes all role assignments (matches the REST handler).
    """
    if not role_ref:
        return error_result("role_ref is required")

    from bifrost.refs import RefResolver

    async with rest_client(context) as http:
        resolver = RefResolver(http)
        try:
            role_uuid = await resolver.resolve("role", role_ref)
        except Exception as exc:
            return error_result(
                f"could not resolve role {role_ref!r}", _ref_error_payload(exc)
            )

    status_code, resp = await call_rest(context, "DELETE", f"/api/roles/{role_uuid}")
    if status_code not in (200, 204):
        return error_result(f"delete_role failed: HTTP {status_code}", {"body": resp})
    return success_result(f"Deleted role {role_uuid}", {"deleted": role_uuid})


TOOLS = [
    ("list_roles", "List Roles", "List all roles in the platform."),
    ("get_role", "Get Role", "Get a single role by UUID or name."),
    ("create_role", "Create Role", "Create a new role."),
    ("update_role", "Update Role", "Update a role (name, description, permissions)."),
    ("delete_role", "Delete Role", "Delete a role (CASCADE removes all assignments)."),
]


def register_tools(mcp: Any, get_context_fn: Any) -> None:
    """Register all roles parity tools with FastMCP."""
    from src.services.mcp_server.generators.fastmcp_generator import (
        register_tool_with_context,
    )

    tool_funcs = {
        "list_roles": list_roles,
        "get_role": get_role,
        "create_role": create_role,
        "update_role": update_role,
        "delete_role": delete_role,
    }

    for tool_id, _name, description in TOOLS:
        register_tool_with_context(
            mcp, tool_funcs[tool_id], tool_id, description, get_context_fn
        )


__all__ = [
    "TOOLS",
    "create_role",
    "delete_role",
    "get_role",
    "list_roles",
    "register_tools",
    "update_role",
]
