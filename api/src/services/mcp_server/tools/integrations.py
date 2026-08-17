"""Integration MCP tools backed by the canonical REST API."""

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
        return await RefResolver(http).resolve(kind, value)


async def _assemble_integration_body(
    context: Any,
    fields: dict[str, Any],
    *,
    model_name: str,
) -> dict[str, Any]:
    """Assemble Integration and mapping bodies from the shared DTO metadata."""

    from bifrost.dto_flags import DTO_EXCLUDES, assemble_body
    from bifrost.refs import RefResolver
    from src.models.contracts.integrations import (
        IntegrationCreate,
        IntegrationMappingCreate,
        IntegrationMappingUpdate,
        IntegrationUpdate,
    )

    model_map = {
        "IntegrationCreate": IntegrationCreate,
        "IntegrationUpdate": IntegrationUpdate,
        "IntegrationMappingCreate": IntegrationMappingCreate,
        "IntegrationMappingUpdate": IntegrationMappingUpdate,
    }
    model_cls = model_map[model_name]
    exclude = DTO_EXCLUDES.get(model_name, set())

    async with rest_client(context) as http:
        return await assemble_body(
            model_cls,
            {key: value for key, value in fields.items() if key not in exclude},
            resolver=RefResolver(http),
        )


async def bifrost_list_integrations(context: Any) -> ToolResult:
    """List Integrations visible to the caller."""

    status_code, body = await call_rest(context, "GET", "/api/integrations")
    if status_code != 200:
        return _rest_error("List Integrations", status_code, body)
    payload = body if isinstance(body, dict) else {}
    integrations = payload.get("items")
    if not isinstance(integrations, list):
        integrations = []
    total = payload.get("total")
    count = total if isinstance(total, int) else len(integrations)
    return success_result(
        f"Found {count} Integration(s)",
        {"integrations": integrations, "count": count},
    )


async def bifrost_get_integration(
    context: Any,
    integration_ref: str,
) -> ToolResult:
    """Get one Integration by UUID or name."""

    if not integration_ref:
        return error_result("integration_ref is required")
    try:
        integration_id = await _resolve_ref(context, "integration", integration_ref)
    except Exception as exc:
        return error_result(
            f"Could not resolve Integration {integration_ref!r}",
            _ref_error_payload(exc),
        )
    status_code, body = await call_rest(
        context,
        "GET",
        f"/api/integrations/{integration_id}",
    )
    if status_code != 200:
        return _rest_error("Get Integration", status_code, body)
    payload = body if isinstance(body, dict) else {"body": body}
    return success_result(
        f"Integration: {payload.get('name', integration_id)}",
        payload,
    )


async def bifrost_create_integration(
    context: Any,
    name: str,
    config_schema: list[dict[str, Any]] | None = None,
    entity_id: str | None = None,
    entity_id_name: str | None = None,
    default_entity_id: str | None = None,
) -> ToolResult:
    """Create an Integration through the shared ``IntegrationCreate`` DTO."""

    fields: dict[str, Any] = {
        "name": name,
        "config_schema": config_schema,
        "entity_id": entity_id,
        "entity_id_name": entity_id_name,
        "default_entity_id": default_entity_id,
    }
    try:
        body = await _assemble_integration_body(
            context,
            fields,
            model_name="IntegrationCreate",
        )
    except Exception as exc:
        return error_result(
            f"Invalid Integration input: {exc}",
            _ref_error_payload(exc),
        )
    status_code, response = await call_rest(
        context,
        "POST",
        "/api/integrations",
        json_body=body,
    )
    if status_code != 201:
        return _rest_error("Create Integration", status_code, response)
    payload = response if isinstance(response, dict) else {"body": response}
    return success_result(
        f"Created Integration: {payload.get('name', name)}",
        payload,
    )


async def bifrost_update_integration(
    context: Any,
    integration_ref: str,
    name: str | None = None,
    list_entities_data_provider: str | None = None,
    config_schema: list[dict[str, Any]] | None = None,
    entity_id: str | None = None,
    entity_id_name: str | None = None,
    default_entity_id: str | None = None,
    force_remove_keys: bool = False,
) -> ToolResult:
    """Update an Integration, explicitly confirming destructive schema removal."""

    if not integration_ref:
        return error_result("integration_ref is required")
    try:
        integration_id = await _resolve_ref(context, "integration", integration_ref)
        body = await _assemble_integration_body(
            context,
            {
                "name": name,
                "list_entities_data_provider_id": list_entities_data_provider,
                "config_schema": config_schema,
                "entity_id": entity_id,
                "entity_id_name": entity_id_name,
                "default_entity_id": default_entity_id,
            },
            model_name="IntegrationUpdate",
        )
    except Exception as exc:
        return error_result(
            f"Invalid Integration input: {exc}",
            _ref_error_payload(exc),
        )
    if not body:
        return error_result("No updates provided")
    status_code, response = await call_rest(
        context,
        "PUT",
        f"/api/integrations/{integration_id}",
        params={"force_remove_keys": force_remove_keys},
        json_body=body,
    )
    if status_code != 200:
        return _rest_error("Update Integration", status_code, response)
    payload = response if isinstance(response, dict) else {"body": response}
    return success_result(
        f"Updated Integration: {payload.get('name', integration_id)}",
        payload,
    )


async def bifrost_create_integration_mapping(
    context: Any,
    integration_ref: str,
    organization: str,
    entity_id: str,
    entity_name: str | None = None,
    config: dict[str, Any] | None = None,
) -> ToolResult:
    """Create an Organization mapping for an Integration."""

    if not integration_ref:
        return error_result("integration_ref is required")
    try:
        integration_id = await _resolve_ref(context, "integration", integration_ref)
        body = await _assemble_integration_body(
            context,
            {
                "organization_id": organization,
                "entity_id": entity_id,
                "entity_name": entity_name,
                "config": config,
            },
            model_name="IntegrationMappingCreate",
        )
    except Exception as exc:
        return error_result(
            f"Invalid Integration mapping input: {exc}",
            _ref_error_payload(exc),
        )
    status_code, response = await call_rest(
        context,
        "POST",
        f"/api/integrations/{integration_id}/mappings",
        json_body=body,
    )
    if status_code != 201:
        return _rest_error("Create Integration mapping", status_code, response)
    payload = response if isinstance(response, dict) else {"body": response}
    return success_result(
        f"Created Integration mapping {payload.get('id', '')}".rstrip(),
        payload,
    )


async def bifrost_update_integration_mapping(
    context: Any,
    integration_ref: str,
    organization: str,
    entity_id: str | None = None,
    entity_name: str | None = None,
    config: dict[str, Any] | None = None,
) -> ToolResult:
    """Update an Integration mapping selected by Integration and Organization refs."""

    if not integration_ref:
        return error_result("integration_ref is required")
    if not organization:
        return error_result("organization is required")
    try:
        integration_id = await _resolve_ref(context, "integration", integration_ref)
        organization_id = await _resolve_ref(context, "org", organization)
        body = await _assemble_integration_body(
            context,
            {
                "entity_id": entity_id,
                "entity_name": entity_name,
                "config": config,
            },
            model_name="IntegrationMappingUpdate",
        )
    except Exception as exc:
        return error_result(
            f"Invalid Integration mapping input: {exc}",
            _ref_error_payload(exc),
        )
    if not body:
        return error_result("No updates provided")

    status_code, mapping = await call_rest(
        context,
        "GET",
        f"/api/integrations/{integration_id}/mappings/by-org/{organization_id}",
    )
    if status_code != 200:
        return _rest_error("Resolve Integration mapping", status_code, mapping)
    mapping_id = mapping.get("id") if isinstance(mapping, dict) else None
    if not mapping_id:
        return error_result("Integration mapping response did not include an id")

    status_code, response = await call_rest(
        context,
        "PUT",
        f"/api/integrations/{integration_id}/mappings/{mapping_id}",
        json_body=body,
    )
    if status_code != 200:
        return _rest_error("Update Integration mapping", status_code, response)
    payload = response if isinstance(response, dict) else {"body": response}
    return success_result(f"Updated Integration mapping {mapping_id}", payload)


TOOLS = [
    (
        "bifrost_list_integrations",
        "List Integrations",
        "List Integrations visible to the caller.",
    ),
    (
        "bifrost_get_integration",
        "Get Integration",
        "Get Integration detail by UUID or name.",
    ),
    (
        "bifrost_create_integration",
        "Create Integration",
        "Create an Integration through the canonical REST policy boundary.",
    ),
    (
        "bifrost_update_integration",
        "Update Integration",
        "Update an Integration through the canonical REST policy boundary.",
    ),
    (
        "bifrost_create_integration_mapping",
        "Create Integration Mapping",
        "Create an Integration-to-Organization mapping.",
    ),
    (
        "bifrost_update_integration_mapping",
        "Update Integration Mapping",
        "Update an Integration mapping selected by Integration and Organization.",
    ),
]


def register_tools(mcp: Any, get_context_fn: Any) -> None:
    """Register canonical Integration tools with FastMCP."""

    from src.services.mcp_server.generators.fastmcp_generator import (
        register_tool_with_context,
    )

    tool_funcs = {
        "bifrost_list_integrations": bifrost_list_integrations,
        "bifrost_get_integration": bifrost_get_integration,
        "bifrost_create_integration": bifrost_create_integration,
        "bifrost_update_integration": bifrost_update_integration,
        "bifrost_create_integration_mapping": bifrost_create_integration_mapping,
        "bifrost_update_integration_mapping": bifrost_update_integration_mapping,
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
    "bifrost_create_integration",
    "bifrost_create_integration_mapping",
    "bifrost_get_integration",
    "bifrost_list_integrations",
    "bifrost_update_integration",
    "bifrost_update_integration_mapping",
    "register_tools",
]
