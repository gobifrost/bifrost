"""Organization MCP tools backed by the canonical REST API."""

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


async def _resolve_organization(context: Any, organization_ref: str) -> str:
    from bifrost.refs import RefResolver

    async with rest_client(context) as http:
        return await RefResolver(http).resolve("org", organization_ref)


async def bifrost_list_organizations(
    context: Any,
    include_inactive: bool = False,
) -> ToolResult:
    """List Organizations through ``GET /api/organizations``."""

    status_code, body = await call_rest(
        context,
        "GET",
        "/api/organizations",
        params={"include_inactive": include_inactive},
    )
    if status_code != 200:
        return _rest_error("List Organizations", status_code, body)
    organizations = body if isinstance(body, list) else []
    return success_result(
        f"Found {len(organizations)} Organization(s)",
        {"organizations": organizations, "count": len(organizations)},
    )


async def bifrost_get_organization(
    context: Any,
    organization_ref: str,
) -> ToolResult:
    """Get one Organization by UUID or name."""

    if not organization_ref:
        return error_result("organization_ref is required")
    try:
        organization_id = await _resolve_organization(context, organization_ref)
    except Exception as exc:
        return error_result(
            f"Could not resolve Organization {organization_ref!r}",
            _ref_error_payload(exc),
        )
    status_code, body = await call_rest(
        context,
        "GET",
        f"/api/organizations/{organization_id}",
    )
    if status_code != 200:
        return _rest_error("Get Organization", status_code, body)
    payload = body if isinstance(body, dict) else {"body": body}
    return success_result(
        f"Organization: {payload.get('name', organization_id)}",
        payload,
    )


async def bifrost_create_organization(
    context: Any,
    name: str,
    is_active: bool | None = None,
) -> ToolResult:
    """Create an Organization through the shared ``OrganizationCreate`` DTO."""

    from bifrost.dto_flags import DTO_EXCLUDES, assemble_body
    from bifrost.refs import RefResolver
    from src.models.contracts.organizations import OrganizationCreate

    fields: dict[str, Any] = {"name": name, "is_active": is_active}
    exclude = DTO_EXCLUDES.get("OrganizationCreate", set())
    try:
        async with rest_client(context) as http:
            body = await assemble_body(
                OrganizationCreate,
                {key: value for key, value in fields.items() if key not in exclude},
                resolver=RefResolver(http),
            )
    except Exception as exc:
        return error_result(
            f"Invalid Organization input: {exc}",
            _ref_error_payload(exc),
        )

    status_code, response = await call_rest(
        context,
        "POST",
        "/api/organizations",
        json_body=body,
    )
    if status_code != 201:
        return _rest_error("Create Organization", status_code, response)
    payload = response if isinstance(response, dict) else {"body": response}
    return success_result(
        f"Created Organization: {payload.get('name', name)}",
        payload,
    )


async def bifrost_update_organization(
    context: Any,
    organization_ref: str,
    name: str | None = None,
    is_active: bool | None = None,
) -> ToolResult:
    """Update an Organization through the shared ``OrganizationUpdate`` DTO."""

    if not organization_ref:
        return error_result("organization_ref is required")

    from bifrost.dto_flags import DTO_EXCLUDES, assemble_body
    from bifrost.refs import RefResolver
    from src.models.contracts.organizations import OrganizationUpdate

    exclude = DTO_EXCLUDES.get("OrganizationUpdate", set())
    fields: dict[str, Any] = {"name": name, "is_active": is_active}
    try:
        async with rest_client(context) as http:
            resolver = RefResolver(http)
            organization_id = await resolver.resolve("org", organization_ref)
            body = await assemble_body(
                OrganizationUpdate,
                {key: value for key, value in fields.items() if key not in exclude},
                resolver=resolver,
            )
    except Exception as exc:
        return error_result(
            f"Invalid Organization input: {exc}",
            _ref_error_payload(exc),
        )
    if not body:
        return error_result("No updates provided")

    status_code, response = await call_rest(
        context,
        "PATCH",
        f"/api/organizations/{organization_id}",
        json_body=body,
    )
    if status_code != 200:
        return _rest_error("Update Organization", status_code, response)
    payload = response if isinstance(response, dict) else {"body": response}
    return success_result(
        f"Updated Organization: {payload.get('name', organization_id)}",
        payload,
    )


async def bifrost_delete_organization(
    context: Any,
    organization_ref: str,
) -> ToolResult:
    """Soft-delete an Organization through its canonical REST endpoint."""

    if not organization_ref:
        return error_result("organization_ref is required")
    try:
        organization_id = await _resolve_organization(context, organization_ref)
    except Exception as exc:
        return error_result(
            f"Could not resolve Organization {organization_ref!r}",
            _ref_error_payload(exc),
        )
    status_code, response = await call_rest(
        context,
        "DELETE",
        f"/api/organizations/{organization_id}",
    )
    if status_code not in {200, 204}:
        return _rest_error("Delete Organization", status_code, response)
    return success_result(
        f"Deleted Organization {organization_id}",
        {"deleted": organization_id},
    )


TOOLS = [
    (
        "bifrost_list_organizations",
        "List Organizations",
        "List active Organizations, optionally including inactive records.",
    ),
    (
        "bifrost_get_organization",
        "Get Organization",
        "Get one Organization by UUID or name.",
    ),
    (
        "bifrost_create_organization",
        "Create Organization",
        "Create a client Organization through the canonical REST policy boundary.",
    ),
    (
        "bifrost_update_organization",
        "Update Organization",
        "Update an Organization through the canonical REST policy boundary.",
    ),
    (
        "bifrost_delete_organization",
        "Delete Organization",
        "Soft-delete an Organization through the canonical REST policy boundary.",
    ),
]


def register_tools(mcp: Any, get_context_fn: Any) -> None:
    """Register canonical Organization tools with FastMCP."""

    from src.services.mcp_server.generators.fastmcp_generator import (
        register_tool_with_context,
    )

    tool_funcs = {
        "bifrost_list_organizations": bifrost_list_organizations,
        "bifrost_get_organization": bifrost_get_organization,
        "bifrost_create_organization": bifrost_create_organization,
        "bifrost_update_organization": bifrost_update_organization,
        "bifrost_delete_organization": bifrost_delete_organization,
    }
    for tool_id, _name, description in TOOLS:
        register_tool_with_context(
            mcp,
            tool_funcs[tool_id],
            tool_id,
            description,
            get_context_fn,
        )


__all__ = [
    "TOOLS",
    "bifrost_create_organization",
    "bifrost_delete_organization",
    "bifrost_get_organization",
    "bifrost_list_organizations",
    "bifrost_update_organization",
    "register_tools",
]
