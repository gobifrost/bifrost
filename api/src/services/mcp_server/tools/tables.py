"""Table metadata MCP tools backed by the canonical REST API.

These tools administer Table definitions. Application SDK table methods remain
the document-data surface. MCP resolves human references and assembles the
shared DTO, then reuses REST authorization, validation, audit, manifest, policy
publication, and Solution-management behavior.
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
    from bifrost.dto_flags import assemble_body
    from bifrost.refs import RefResolver
    from src.models.contracts.tables import TableCreate, TableUpdate

    model_cls = TableUpdate if is_update else TableCreate
    async with rest_client(context) as http:
        resolver = RefResolver(http)
        body = await assemble_body(model_cls, fields, resolver=resolver)
        if scope is not None:
            if scope == "global":
                body["organization_id"] = None
            else:
                body["organization_id"] = await resolver.resolve("org", scope)
    return body


async def bifrost_list_tables(
    context: Any,
    scope: str | None = None,
) -> ToolResult:
    """List Table definitions through ``GET /api/tables``.

    ``scope`` is ``global``, an organization UUID, or omitted for every Table
    visible to the platform administrator.
    """

    params = {"scope": scope} if scope is not None else None
    status_code, body = await call_rest(
        context,
        "GET",
        "/api/tables",
        params=params,
    )
    if status_code != 200:
        return _rest_error("List Tables", status_code, body)
    tables = body.get("tables", []) if isinstance(body, dict) else []
    return success_result(
        f"Found {len(tables)} Table(s)",
        {"tables": tables, "count": len(tables)},
    )


async def bifrost_get_table(context: Any, table_ref: str) -> ToolResult:
    """Get one Table definition by UUID or unambiguous name."""

    if not table_ref:
        return error_result("table_ref is required")
    try:
        table_id = await _resolve_ref(context, "table", table_ref)
    except Exception as exc:
        return error_result(
            f"Could not resolve Table {table_ref!r}",
            _ref_error_payload(exc),
        )
    status_code, body = await call_rest(
        context,
        "GET",
        f"/api/tables/{table_id}",
    )
    if status_code != 200:
        return _rest_error("Get Table", status_code, body)
    payload = body if isinstance(body, dict) else {"body": body}
    return success_result(f"Table: {payload.get('name', table_id)}", payload)


async def bifrost_create_table(
    context: Any,
    name: str,
    description: str | None = None,
    schema: dict[str, Any] | None = None,
    policies: dict[str, Any] | None = None,
    scope: str | None = None,
) -> ToolResult:
    """Create a Table definition through ``POST /api/tables``.

    ``scope`` is ``global``, an organization UUID/name, or omitted for the
    caller's home organization. ``schema`` and ``policies`` use the same wire
    shapes as the REST API, CLI, and Solution manifest.
    """

    try:
        body = await _assemble_table_body(
            context,
            {
                "name": name,
                "description": description,
                "schema": schema,
                "policies": policies,
            },
            is_update=False,
            scope=scope,
        )
    except Exception as exc:
        return error_result(f"Invalid Table input: {exc}", _ref_error_payload(exc))
    status_code, response = await call_rest(
        context,
        "POST",
        "/api/tables",
        json_body=body,
    )
    if status_code != 201:
        return _rest_error("Create Table", status_code, response)
    payload = response if isinstance(response, dict) else {"body": response}
    return success_result(f"Created Table: {payload.get('name', name)}", payload)


async def bifrost_update_table(
    context: Any,
    table_ref: str,
    name: str | None = None,
    description: str | None = None,
    schema: dict[str, Any] | None = None,
    policies: dict[str, Any] | None = None,
    scope: str | None = None,
) -> ToolResult:
    """Update a Table definition through ``PATCH /api/tables/{id}``.

    ``scope`` may move the Table to ``global`` or an organization UUID/name.
    Existing policies are revalidated against the destination organization.
    """

    if not table_ref:
        return error_result("table_ref is required")
    try:
        table_id = await _resolve_ref(context, "table", table_ref)
        body = await _assemble_table_body(
            context,
            {
                "name": name,
                "description": description,
                "schema": schema,
                "policies": policies,
            },
            is_update=True,
            scope=scope,
        )
    except Exception as exc:
        return error_result(f"Invalid Table input: {exc}", _ref_error_payload(exc))
    if not body:
        return error_result("No updates provided")
    status_code, response = await call_rest(
        context,
        "PATCH",
        f"/api/tables/{table_id}",
        json_body=body,
    )
    if status_code != 200:
        return _rest_error("Update Table", status_code, response)
    payload = response if isinstance(response, dict) else {"body": response}
    return success_result(
        f"Updated Table: {payload.get('name', table_id)}",
        payload,
    )


async def bifrost_delete_table(context: Any, table_ref: str) -> ToolResult:
    """Delete a Table definition and all of its documents through REST."""

    if not table_ref:
        return error_result("table_ref is required")
    try:
        table_id = await _resolve_ref(context, "table", table_ref)
    except Exception as exc:
        return error_result(
            f"Could not resolve Table {table_ref!r}",
            _ref_error_payload(exc),
        )
    status_code, response = await call_rest(
        context,
        "DELETE",
        f"/api/tables/{table_id}",
    )
    if status_code != 204:
        return _rest_error("Delete Table", status_code, response)
    return success_result("Deleted Table", {"success": True, "id": table_id})


TOOLS = [
    (
        "bifrost_list_tables",
        "List Tables",
        "List Table definitions. Platform admin only.",
    ),
    (
        "bifrost_get_table",
        "Get Table",
        "Get a Table definition by UUID or unambiguous name. Platform admin only.",
    ),
    (
        "bifrost_create_table",
        "Create Table",
        "Create a Table definition using the canonical schema and policy contract.",
    ),
    (
        "bifrost_update_table",
        "Update Table",
        "Update a Table definition using the canonical schema and policy contract.",
    ),
    (
        "bifrost_delete_table",
        "Delete Table",
        "Delete a Table definition and all of its documents.",
    ),
]


def register_tools(mcp: Any, get_context_fn: Any) -> None:
    """Register canonical Table tools with FastMCP."""

    from src.services.mcp_server.generators.fastmcp_generator import (
        register_tool_with_context,
    )

    tool_funcs = {
        "bifrost_list_tables": bifrost_list_tables,
        "bifrost_get_table": bifrost_get_table,
        "bifrost_create_table": bifrost_create_table,
        "bifrost_update_table": bifrost_update_table,
        "bifrost_delete_table": bifrost_delete_table,
    }
    for tool_id, _name, description in TOOLS:
        register_tool_with_context(
            mcp,
            tool_funcs[tool_id],
            tool_id,
            description,
            get_context_fn,
        )
