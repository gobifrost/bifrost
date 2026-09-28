"""Config MCP Tools — thin wrappers around the REST API.

Implements Task 6 of the CLI mutation surface + MCP parity plan:
``bifrost_config_list``, ``bifrost_config_create``, ``bifrost_config_update``, ``bifrost_config_delete``.

Same rules as :mod:`roles`: validate minimal inputs, resolve refs, then
call the REST endpoint via the in-process HTTP bridge. No ORM, no
repositories, no ``AsyncSession``.

DTO-driven: parameters mirror :class:`ConfigCreate` / :class:`ConfigUpdate`
with the ``config_type`` → ``type`` wire alias applied by
:func:`bifrost.dto_flags.assemble_body`.
"""

from __future__ import annotations

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


async def bifrost_config_list(context: Any) -> ToolResult:
    """List configs visible to the caller — ``GET /api/config``."""
    logger.info("MCP bifrost_config_list (HTTP bridge)")
    status_code, body = await call_rest(context, "GET", "/api/config")
    if status_code != 200:
        return error_result(f"bifrost_config_list failed: HTTP {status_code}", {"body": body})
    items = body if isinstance(body, list) else []
    return success_result(
        f"Found {len(items)} config(s)",
        {"configs": items, "count": len(items)},
    )


async def bifrost_config_get(context: Any, config_ref: str) -> ToolResult:
    """Get a single config by UUID or key — thin wrapper over
    ``GET /api/config/{uuid}``.

    Resolves the ref via the shared :class:`RefResolver`, then reads the
    per-id endpoint directly (no more list-and-filter). Secret values stay
    masked exactly as in the list.
    """
    if not config_ref:
        return error_result("config_ref is required")

    from bifrost.refs import RefResolver

    async with rest_client(context) as http:
        resolver = RefResolver(http)
        try:
            config_uuid = await resolver.resolve("config", config_ref)
        except Exception as exc:
            return error_result(
                f"could not resolve config {config_ref!r}",
                _ref_error_payload(exc),
            )

    status_code, body = await call_rest(context, "GET", f"/api/config/{config_uuid}")
    if status_code != 200:
        return error_result(f"bifrost_config_get failed: HTTP {status_code}", {"body": body})
    return success_result(
        f"Config: {body.get('key') if isinstance(body, dict) else config_uuid}",
        body if isinstance(body, dict) else {"body": body},
    )


async def bifrost_config_create(
    context: Any,
    key: str,
    value: str,
    config_type: str | None = None,
    description: str | None = None,
    organization_id: str | None = None,
) -> ToolResult:
    """Create a config — ``POST /api/config``.

    ``value`` is a string per the server's :class:`SetConfigRequest` contract
    (the REST endpoint treats the value as a string even for JSON-typed
    configs — the caller serializes any structured data). ``config_type``
    accepts the :class:`ConfigType` enum values (``string``, ``integer``,
    ``boolean``, ``json``, ``secret``). ``organization_id`` is a ref (UUID,
    name) or ``None`` for global scope — resolved via :class:`RefResolver`.
    """
    if not key:
        return error_result("key is required")

    # The internal ``ConfigCreate`` DTO declares ``value: dict`` but the
    # public ``SetConfigRequest`` endpoint expects ``value: str``. Build
    # the body manually (mirroring ``bifrost configs set``) instead of
    # routing through ``assemble_body(ConfigCreate, ...)``, which would
    # attempt ``json.loads(value)`` on the string.
    body: dict[str, Any] = {"key": key, "value": value}
    if config_type is not None:
        body["type"] = config_type  # wire key is ``type``, not ``config_type``
    if description is not None:
        body["description"] = description
    if organization_id is not None:
        try:
            async with rest_client(context) as http:
                from bifrost.refs import RefResolver
                resolver = RefResolver(http)
                body["organization_id"] = await resolver.resolve(
                    "org", organization_id
                )
        except Exception as exc:
            return error_result(
                f"could not resolve organization {organization_id!r}",
                _ref_error_payload(exc),
            )

    status_code, resp = await call_rest(context, "POST", "/api/config", json_body=body)
    if status_code not in (200, 201):
        return error_result(f"bifrost_config_create failed: HTTP {status_code}", {"body": resp})
    return success_result(
        f"Created config: {key}",
        resp if isinstance(resp, dict) else {"body": resp},
    )


async def bifrost_config_update(
    context: Any,
    config_ref: str,
    value: str | None = None,
    config_type: str | None = None,
    description: str | None = None,
) -> ToolResult:
    """Update a config — ``PUT /api/config/{uuid}``.

    ``config_ref`` is a UUID or config ``key``. ``value`` is a string (the
    REST endpoint stores values as strings). Omitting ``value`` preserves
    the stored value (the server honours unset-means-omit; particularly
    important for secret-type configs).
    """
    if not config_ref:
        return error_result("config_ref is required")

    from bifrost.refs import RefResolver

    async with rest_client(context) as http:
        resolver = RefResolver(http)
        try:
            config_uuid = await resolver.resolve("config", config_ref)
        except Exception as exc:
            return error_result(
                f"could not resolve config {config_ref!r}",
                _ref_error_payload(exc),
            )

    # Same DTO/wire-shape mismatch as bifrost_config_create — build the body
    # manually. Unset fields are omitted so the server's omit-unset
    # semantics preserve the stored value (critical for secret configs).
    body: dict[str, Any] = {}
    if value is not None:
        body["value"] = value
    if config_type is not None:
        body["type"] = config_type
    if description is not None:
        body["description"] = description

    status_code, resp = await call_rest(
        context, "PUT", f"/api/config/{config_uuid}", json_body=body
    )
    if status_code != 200:
        return error_result(f"bifrost_config_update failed: HTTP {status_code}", {"body": resp})
    return success_result(
        f"Updated config {config_uuid}",
        resp if isinstance(resp, dict) else {"body": resp},
    )


async def bifrost_config_delete(context: Any, config_ref: str) -> ToolResult:
    """Delete a config — ``DELETE /api/config/{uuid}``.

    ``config_ref`` is a UUID or config key. No ``--confirm`` guard here:
    the MCP surface returns REST errors straight through; the CLI layers
    a secret-aware confirm prompt on top.
    """
    if not config_ref:
        return error_result("config_ref is required")

    from bifrost.refs import RefResolver

    async with rest_client(context) as http:
        resolver = RefResolver(http)
        try:
            config_uuid = await resolver.resolve("config", config_ref)
        except Exception as exc:
            return error_result(
                f"could not resolve config {config_ref!r}",
                _ref_error_payload(exc),
            )

    status_code, resp = await call_rest(
        context, "DELETE", f"/api/config/{config_uuid}"
    )
    if status_code not in (200, 204):
        return error_result(f"bifrost_config_delete failed: HTTP {status_code}", {"body": resp})
    return success_result(f"Deleted config {config_uuid}", {"deleted": config_uuid})


TOOLS = [
    ("bifrost_config_list", "List Configs", "List configuration values for the caller's scope."),
    ("bifrost_config_get", "Get Config", "Get a single configuration value by UUID or key."),
    ("bifrost_config_create", "Create Config", "Create a configuration value."),
    ("bifrost_config_update", "Update Config", "Update a configuration value by UUID or key."),
    ("bifrost_config_delete", "Delete Config", "Delete a configuration value by UUID or key."),
]


def register_tools(mcp: Any, get_context_fn: Any) -> None:
    """Register all configs parity tools with FastMCP."""
    from src.services.mcp_server.generators.fastmcp_generator import (
        register_tool_with_context,
    )

    tool_funcs = {
        "bifrost_config_list": bifrost_config_list,
        "bifrost_config_get": bifrost_config_get,
        "bifrost_config_create": bifrost_config_create,
        "bifrost_config_update": bifrost_config_update,
        "bifrost_config_delete": bifrost_config_delete,
    }

    for tool_id, _name, description in TOOLS:
        register_tool_with_context(
            mcp, tool_funcs[tool_id], tool_id, description, get_context_fn
        )


__all__ = [
    "TOOLS",
    "bifrost_config_create",
    "bifrost_config_delete",
    "bifrost_config_get",
    "bifrost_config_list",
    "register_tools",
    "bifrost_config_update",
]
