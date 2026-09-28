"""Table MCP Tools — thin wrappers around the REST API.

``bifrost_table_list``, ``bifrost_table_get``, ``bifrost_table_create``,
``bifrost_table_update``, ``bifrost_table_delete``.

These tools are thin wrappers: they resolve human refs, assemble the shared
Table DTO via ``bifrost/dto_flags.py``, then call the corresponding REST
endpoint through the in-process HTTP bridge (``_http_bridge``). No ORM, no
repositories, no ``AsyncSession`` — all side effects (audit logs, target-org
validation, policy-claim validation, rename/rescope conflict handling)
happen behind the REST handler, matching the CLI's path exactly.
"""

from __future__ import annotations

from typing import Any

from fastmcp.tools import ToolResult

from src.services.mcp_server.tool_result import error_result, success_result
from src.services.mcp_server.tools._http_bridge import call_rest, rest_client


def _ref_error_payload(exc: Exception) -> dict[str, Any]:
    from bifrost.refs import AmbiguousRefError, RefNotFoundError

    if isinstance(exc, AmbiguousRefError):
        return {"kind": exc.kind, "value": exc.value, "candidates": exc.candidates}
    if isinstance(exc, RefNotFoundError):
        return {"kind": exc.kind, "value": exc.value}
    return {"detail": str(exc)}


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


async def _resolve_ref(context: Any, kind: str, value: str) -> str:
    from bifrost.refs import RefResolver

    async with rest_client(context) as http:
        return await RefResolver(http).resolve(kind, value)  # type: ignore[arg-type]


async def _assemble_table_body(
    context: Any,
    fields: dict[str, Any],
    *,
    is_update: bool,
    scope: str | None,
) -> dict[str, Any]:
    """Assemble a shared Table DTO payload. Only keys the caller actually
    passed are sent (``assemble_body`` drops ``None`` values).
    """
    from bifrost.dto_flags import assemble_body
    from bifrost.refs import RefResolver
    from src.models.contracts.tables import TableCreate, TableUpdate

    model_cls = TableUpdate if is_update else TableCreate
    async with rest_client(context) as http:
        resolver = RefResolver(http)
        body = await assemble_body(model_cls, fields, resolver=resolver)
        if not is_update and scope is not None:
            if scope == "global":
                body["organization_id"] = None
            else:
                body["organization_id"] = await resolver.resolve("org", scope)
    return body


async def bifrost_table_list(context: Any, scope: str | None = None) -> ToolResult:
    """List Tables — thin wrapper over ``GET /api/tables`` (platform admin only).

    ``scope`` is ``global`` (global tables only), an organization UUID/name,
    or omitted for every table visible in the caller's boundary.
    """
    params: dict[str, Any] = {}
    if scope is not None:
        params["scope"] = scope
    status_code, body = await call_rest(context, "GET", "/api/tables", params=params)
    if status_code != 200:
        return _rest_error("List Tables", status_code, body)
    tables = body.get("tables") if isinstance(body, dict) else None
    tables = tables if isinstance(tables, list) else []
    return success_result(
        f"Found {len(tables)} table(s)",
        {"tables": tables, "count": len(tables)},
    )


async def bifrost_table_get(context: Any, table_ref: str) -> ToolResult:
    """Get one Table by UUID or accessible name — thin wrapper over
    ``GET /api/tables/{uuid}`` (platform admin only)."""
    if not table_ref:
        return error_result("table_ref is required")
    try:
        table_id = await _resolve_ref(context, "table", table_ref)
    except Exception as exc:
        return error_result(f"could not resolve table {table_ref!r}", _ref_error_payload(exc))

    status_code, body = await call_rest(context, "GET", f"/api/tables/{table_id}")
    if status_code != 200:
        return _rest_error("Get Table", status_code, body)
    payload = body if isinstance(body, dict) else {"body": body}
    return success_result(f"Table: {payload.get('name', table_id)}", payload)


async def bifrost_table_create(
    context: Any,
    name: str,
    description: str | None = None,
    schema: dict[str, Any] | None = None,
    policies: dict[str, Any] | None = None,
    scope: str | None = None,
) -> ToolResult:
    """Create a Table through ``POST /api/tables`` (platform admin only).

    ``scope`` is ``global``, an organization UUID/name, or omitted for the
    caller's home organization.
    """
    fields = {"name": name, "description": description, "schema": schema, "policies": policies}
    try:
        body = await _assemble_table_body(context, fields, is_update=False, scope=scope)
    except Exception as exc:
        return error_result(f"invalid Table input: {exc}", _ref_error_payload(exc))

    status_code, resp = await call_rest(context, "POST", "/api/tables", json_body=body)
    if status_code not in (200, 201):
        return _rest_error("Create Table", status_code, resp)
    payload = resp if isinstance(resp, dict) else {"body": resp}
    return success_result(f"Created table: {payload.get('name', name)}", payload)


async def bifrost_table_update(
    context: Any,
    table_ref: str,
    name: str | None = None,
    description: str | None = None,
    schema: dict[str, Any] | None = None,
    policies: dict[str, Any] | None = None,
) -> ToolResult:
    """Update a Table through ``PATCH /api/tables/{uuid}`` (platform admin only).

    Table rescoping (moving between organizations) is not supported through
    this endpoint — ``organization_id`` is not a writable field on Table
    updates.
    """
    if not table_ref:
        return error_result("table_ref is required")
    try:
        table_id = await _resolve_ref(context, "table", table_ref)
    except Exception as exc:
        return error_result(f"could not resolve table {table_ref!r}", _ref_error_payload(exc))

    fields = {"name": name, "description": description, "schema": schema, "policies": policies}
    try:
        body = await _assemble_table_body(context, fields, is_update=True, scope=None)
    except Exception as exc:
        return error_result(f"invalid Table input: {exc}", _ref_error_payload(exc))
    if not body:
        return error_result("No updates provided")

    status_code, resp = await call_rest(context, "PATCH", f"/api/tables/{table_id}", json_body=body)
    if status_code != 200:
        return _rest_error("Update Table", status_code, resp)
    payload = resp if isinstance(resp, dict) else {"body": resp}
    return success_result(f"Updated table: {payload.get('name', table_id)}", payload)


async def bifrost_table_delete(context: Any, table_ref: str) -> ToolResult:
    """Delete a Table and its documents — thin wrapper over
    ``DELETE /api/tables/{uuid}`` (platform admin only)."""
    if not table_ref:
        return error_result("table_ref is required")
    try:
        table_id = await _resolve_ref(context, "table", table_ref)
    except Exception as exc:
        return error_result(f"could not resolve table {table_ref!r}", _ref_error_payload(exc))

    status_code, resp = await call_rest(context, "DELETE", f"/api/tables/{table_id}")
    if status_code not in (200, 204):
        return _rest_error("Delete Table", status_code, resp)
    return success_result(f"Deleted table {table_id}", {"deleted": table_id})


TOOLS = [
    ("bifrost_table_list", "List Tables", "List Tables in the platform. Platform admin only."),
    ("bifrost_table_get", "Get Table", "Get a Table by UUID or accessible name, including schema."),
    ("bifrost_table_create", "Create Table", "Create a new Table with an optional schema and policies."),
    ("bifrost_table_update", "Update Table", "Update a Table's name, description, schema, or policies."),
    ("bifrost_table_delete", "Delete Table", "Delete a Table and all of its documents."),
]


def register_tools(mcp: Any, get_context_fn: Any) -> None:
    """Register all Table tools with FastMCP."""
    from src.services.mcp_server.generators.fastmcp_generator import register_tool_with_context

    tool_funcs = {
        "bifrost_table_list": bifrost_table_list,
        "bifrost_table_get": bifrost_table_get,
        "bifrost_table_create": bifrost_table_create,
        "bifrost_table_update": bifrost_table_update,
        "bifrost_table_delete": bifrost_table_delete,
    }

    for tool_id, _name, description in TOOLS:
        register_tool_with_context(mcp, tool_funcs[tool_id], tool_id, description, get_context_fn)


__all__ = [
    "TOOLS",
    "bifrost_table_create",
    "bifrost_table_delete",
    "bifrost_table_get",
    "bifrost_table_list",
    "bifrost_table_update",
    "register_tools",
]
