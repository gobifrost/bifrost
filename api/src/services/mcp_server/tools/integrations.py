"""
Integration MCP Tools

Thin wrappers over the REST API for listing, getting, creating, and updating
integrations, plus creating and updating integration↔organization mappings
(Task 6 of the CLI mutation surface + MCP parity plan).

Every tool here routes through the in-process REST bridge (no ORM /
repositories / ``AsyncSession``). ``GET /api/integrations`` is platform-admin
only (``CurrentSuperuser``) in REST, so ``bifrost_integration_list`` inherits
that gate exactly — it no longer widens the list to a non-admin's mapped
integrations the way the old ORM-backed tool did.
"""

import logging
from typing import Any

from fastmcp.tools import ToolResult

from src.services.mcp_server.tool_result import error_result, success_result
from src.services.mcp_server.tools._http_bridge import call_rest, rest_client

logger = logging.getLogger(__name__)


def _ref_error_payload(exc: Exception) -> dict[str, Any]:
    from bifrost.refs import AmbiguousRefError, RefNotFoundError

    if isinstance(exc, AmbiguousRefError):
        return {"kind": exc.kind, "value": exc.value, "candidates": exc.candidates}
    if isinstance(exc, RefNotFoundError):
        return {"kind": exc.kind, "value": exc.value}
    return {"detail": str(exc)}


async def bifrost_integration_list(context: Any) -> ToolResult:
    """List all integrations — thin wrapper over ``GET /api/integrations``.

    Platform-admin only, matching REST (``CurrentSuperuser``).
    """
    logger.info("MCP bifrost_integration_list (HTTP bridge)")

    status_code, body = await call_rest(context, "GET", "/api/integrations")
    if status_code != 200:
        return error_result(
            f"bifrost_integration_list failed: HTTP {status_code}", {"body": body}
        )
    items = body.get("items", []) if isinstance(body, dict) else []
    integration_list = [
        {
            "id": item.get("id"),
            "name": item.get("name"),
            "has_oauth": item.get("has_oauth_config"),
            "entity_id_name": item.get("entity_id_name"),
        }
        for item in items
    ]
    display_text = f"Found {len(integration_list)} integration(s)"
    return success_result(
        display_text, {"integrations": integration_list, "count": len(integration_list)}
    )


# ---------------------------------------------------------------------------
# Thin-wrapper parity tools (Task 6)
# ---------------------------------------------------------------------------


async def _assemble_integration_body(
    context: Any,
    fields: dict[str, Any],
    *,
    model_name: str,
) -> dict[str, Any]:
    """Shared DTO body assembly for integrations + mappings."""
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
        resolver = RefResolver(http)
        return await assemble_body(
            model_cls,
            {k: v for k, v in fields.items() if k not in exclude},
            resolver=resolver,
        )


async def bifrost_integration_get(context: Any, integration_ref: str) -> ToolResult:
    """Get a single integration — thin wrapper over ``GET /api/integrations/{uuid}``.

    ``integration_ref`` is a UUID or integration name. Returns the integration
    detail payload (mappings, OAuth config, config schema).
    """
    if not integration_ref:
        return error_result("integration_ref is required")

    from bifrost.refs import RefResolver

    async with rest_client(context) as http:
        resolver = RefResolver(http)
        try:
            integration_uuid = await resolver.resolve("integration", integration_ref)
        except Exception as exc:
            return error_result(
                f"could not resolve integration {integration_ref!r}",
                _ref_error_payload(exc),
            )

    status_code, body = await call_rest(
        context, "GET", f"/api/integrations/{integration_uuid}"
    )
    if status_code != 200:
        return error_result(
            f"bifrost_integration_get failed: HTTP {status_code}", {"body": body}
        )
    return success_result(
        f"Integration: {body.get('name') if isinstance(body, dict) else integration_uuid}",
        body if isinstance(body, dict) else {"body": body},
    )


async def bifrost_integration_create(
    context: Any,
    name: str,
    description: str | None = None,
    config_schema: list[dict[str, Any]] | None = None,
    entity_id: str | None = None,
    entity_id_name: str | None = None,
    default_entity_id: str | None = None,
) -> ToolResult:
    """Create an integration — ``POST /api/integrations``.

    No OAuth provider configuration (out of scope for this surface; use
    the UI for OAuth flows).
    """
    if not name:
        return error_result("name is required")

    fields: dict[str, Any] = {
        "name": name,
        "description": description,
        "config_schema": config_schema,
        "entity_id": entity_id,
        "entity_id_name": entity_id_name,
        "default_entity_id": default_entity_id,
    }
    try:
        body = await _assemble_integration_body(
            context, fields, model_name="IntegrationCreate"
        )
    except Exception as exc:
        return error_result(f"invalid input: {exc}", _ref_error_payload(exc))

    status_code, resp = await call_rest(
        context, "POST", "/api/integrations", json_body=body
    )
    if status_code not in (200, 201):
        return error_result(
            f"bifrost_integration_create failed: HTTP {status_code}", {"body": resp}
        )
    return success_result(
        f"Created integration: {name}",
        resp if isinstance(resp, dict) else {"body": resp},
    )


async def bifrost_integration_update(
    context: Any,
    integration_ref: str,
    name: str | None = None,
    description: str | None = None,
    list_entities_data_provider: str | None = None,
    config_schema: list[dict[str, Any]] | None = None,
    entity_id: str | None = None,
    entity_id_name: str | None = None,
    default_entity_id: str | None = None,
    force_remove_keys: bool = False,
) -> ToolResult:
    """Update an integration — ``PUT /api/integrations/{uuid}``.

    ``integration_ref`` is a UUID or integration name.
    ``list_entities_data_provider`` is a workflow ref (UUID, name, or
    ``path::func``); it maps to the ``list_entities_data_provider_id``
    field on the REST payload via :func:`assemble_body`.

    If the new ``config_schema`` drops keys that have saved ``Config``
    values, REST returns 409 ``integration_schema_removal_requires_confirmation``
    with the affected count unless ``force_remove_keys=True``.
    """
    if not integration_ref:
        return error_result("integration_ref is required")

    from bifrost.refs import RefResolver

    async with rest_client(context) as http:
        resolver = RefResolver(http)
        try:
            integration_uuid = await resolver.resolve("integration", integration_ref)
        except Exception as exc:
            return error_result(
                f"could not resolve integration {integration_ref!r}",
                _ref_error_payload(exc),
            )

    fields: dict[str, Any] = {
        "name": name,
        "description": description,
        "list_entities_data_provider_id": list_entities_data_provider,
        "config_schema": config_schema,
        "entity_id": entity_id,
        "entity_id_name": entity_id_name,
        "default_entity_id": default_entity_id,
    }
    try:
        body = await _assemble_integration_body(
            context, fields, model_name="IntegrationUpdate"
        )
    except Exception as exc:
        return error_result(f"invalid input: {exc}", _ref_error_payload(exc))

    url = f"/api/integrations/{integration_uuid}"
    if force_remove_keys:
        url = f"{url}?force_remove_keys=true"
    status_code, resp = await call_rest(context, "PUT", url, json_body=body)
    if status_code != 200:
        return error_result(
            f"bifrost_integration_update failed: HTTP {status_code}", {"body": resp}
        )
    return success_result(
        f"Updated integration {integration_uuid}",
        resp if isinstance(resp, dict) else {"body": resp},
    )


async def bifrost_integration_mapping_create(
    context: Any,
    integration_ref: str,
    organization: str,
    entity_id: str,
    entity_name: str | None = None,
    config: dict[str, Any] | None = None,
) -> ToolResult:
    """Create an integration mapping — ``POST /api/integrations/{uuid}/mappings``.

    ``integration_ref`` is a UUID or integration name. ``organization`` is an
    org ref (UUID or name). ``oauth_token_id`` is intentionally not a parameter:
    tokens are owned by the OAuth flow in the UI (see ``DTO_EXCLUDES``).
    """
    if not integration_ref:
        return error_result("integration_ref is required")
    if not organization:
        return error_result("organization is required")
    if not entity_id:
        return error_result("entity_id is required")

    from bifrost.refs import RefResolver

    async with rest_client(context) as http:
        resolver = RefResolver(http)
        try:
            integration_uuid = await resolver.resolve("integration", integration_ref)
        except Exception as exc:
            return error_result(
                f"could not resolve integration {integration_ref!r}",
                _ref_error_payload(exc),
            )

    fields: dict[str, Any] = {
        "organization_id": organization,
        "entity_id": entity_id,
        "entity_name": entity_name,
        "config": config,
    }
    try:
        body = await _assemble_integration_body(
            context, fields, model_name="IntegrationMappingCreate"
        )
    except Exception as exc:
        return error_result(f"invalid input: {exc}", _ref_error_payload(exc))

    status_code, resp = await call_rest(
        context,
        "POST",
        f"/api/integrations/{integration_uuid}/mappings",
        json_body=body,
    )
    if status_code not in (200, 201):
        return error_result(
            f"bifrost_integration_mapping_create failed: HTTP {status_code}", {"body": resp}
        )
    return success_result(
        f"Created mapping for integration {integration_uuid}",
        resp if isinstance(resp, dict) else {"body": resp},
    )


async def bifrost_integration_mapping_update(
    context: Any,
    integration_ref: str,
    mapping_id: str,
    entity_id: str | None = None,
    entity_name: str | None = None,
    config: dict[str, Any] | None = None,
) -> ToolResult:
    """Update a mapping — ``PUT /api/integrations/{uuid}/mappings/{mapping_id}``.

    ``mapping_id`` must be a UUID (mappings have no name). ``oauth_token_id``
    is excluded by design: tokens are owned by the OAuth flow.
    """
    if not integration_ref:
        return error_result("integration_ref is required")
    if not mapping_id:
        return error_result("mapping_id is required")

    from uuid import UUID

    from bifrost.refs import RefResolver

    try:
        UUID(mapping_id)
    except (TypeError, ValueError):
        return error_result(f"mapping_id must be a UUID, got {mapping_id!r}")

    async with rest_client(context) as http:
        resolver = RefResolver(http)
        try:
            integration_uuid = await resolver.resolve("integration", integration_ref)
        except Exception as exc:
            return error_result(
                f"could not resolve integration {integration_ref!r}",
                _ref_error_payload(exc),
            )

    fields: dict[str, Any] = {
        "entity_id": entity_id,
        "entity_name": entity_name,
        "config": config,
    }
    try:
        body = await _assemble_integration_body(
            context, fields, model_name="IntegrationMappingUpdate"
        )
    except Exception as exc:
        return error_result(f"invalid input: {exc}", _ref_error_payload(exc))

    status_code, resp = await call_rest(
        context,
        "PUT",
        f"/api/integrations/{integration_uuid}/mappings/{mapping_id}",
        json_body=body,
    )
    if status_code != 200:
        return error_result(
            f"bifrost_integration_mapping_update failed: HTTP {status_code}", {"body": resp}
        )
    return success_result(
        f"Updated mapping {mapping_id}",
        resp if isinstance(resp, dict) else {"body": resp},
    )


# Tool metadata for registration
TOOLS = [
    ("bifrost_integration_list", "List Integrations", "List available integrations that can be used in workflows."),
    ("bifrost_integration_get", "Get Integration", "Get integration detail (mappings, OAuth config, schema) by UUID or name."),
    ("bifrost_integration_create", "Create Integration", "Create a new integration (platform admin)."),
    ("bifrost_integration_update", "Update Integration", "Update an integration by UUID or name."),
    ("bifrost_integration_mapping_create", "Add Integration Mapping", "Create an integration↔organization mapping."),
    ("bifrost_integration_mapping_update", "Update Integration Mapping", "Update an integration mapping by ID."),
]


def register_tools(mcp: Any, get_context_fn: Any) -> None:
    """Register all integrations tools with FastMCP."""
    from src.services.mcp_server.generators.fastmcp_generator import register_tool_with_context

    tool_funcs = {
        "bifrost_integration_list": bifrost_integration_list,
        "bifrost_integration_get": bifrost_integration_get,
        "bifrost_integration_create": bifrost_integration_create,
        "bifrost_integration_update": bifrost_integration_update,
        "bifrost_integration_mapping_create": bifrost_integration_mapping_create,
        "bifrost_integration_mapping_update": bifrost_integration_mapping_update,
    }

    for tool_id, name, description in TOOLS:
        register_tool_with_context(mcp, tool_funcs[tool_id], tool_id, description, get_context_fn)
