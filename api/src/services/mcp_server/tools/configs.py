"""Config MCP Tools — thin wrappers around the REST API.

Implements Task 6 of the CLI mutation surface + MCP parity plan:
``bifrost_list_configs``, ``bifrost_create_config``,
``bifrost_update_config``, ``bifrost_delete_config``.

Same rules as :mod:`roles`: validate minimal inputs, resolve refs, then
call the REST endpoint via the in-process HTTP bridge. No ORM, no
repositories, no ``AsyncSession``.

DTO-driven: parameters mirror :class:`ConfigCreate` / :class:`ConfigUpdate`
with the ``config_type`` → ``type`` wire alias applied by
:func:`bifrost.dto_flags.assemble_body`.

``value`` is a string for every :class:`ConfigType`. Non-string types travel
serialized and are coerced on read using ``config_type`` (``int``/``bool`` are
cast, ``json`` is parsed), so callers serialize structured data themselves.
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


async def bifrost_list_configs(context: Any) -> ToolResult:
    """List configs visible to the caller — ``GET /api/config``."""
    logger.info("MCP bifrost_list_configs (HTTP bridge)")
    status_code, body = await call_rest(context, "GET", "/api/config")
    if status_code != 200:
        return error_result(f"list_configs failed: HTTP {status_code}", {"body": body})
    items = body if isinstance(body, list) else []
    return success_result(
        f"Found {len(items)} config(s)",
        {"configs": items, "count": len(items)},
    )


async def bifrost_get_config(context: Any, config_ref: str) -> ToolResult:
    """Get a single config by UUID or key — ``GET /api/config/{uuid}``.

    ``config_ref`` is a UUID or config ``key``; keys resolve via the shared
    :class:`RefResolver`. Secret values come back masked as ``[SECRET]``.
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

    status_code, body = await call_rest(
        context, "GET", f"/api/config/{config_uuid}"
    )
    if status_code != 200:
        return error_result(f"get_config failed: HTTP {status_code}", {"body": body})
    if not isinstance(body, dict):
        return error_result("get_config returned an unexpected payload", {"body": body})
    return success_result(f"Config: {body.get('key')}", body)


async def bifrost_create_config(
    context: Any,
    key: str,
    value: str,
    config_type: str | None = None,
    description: str | None = None,
    organization_id: str | None = None,
) -> ToolResult:
    """Create a config — ``POST /api/config``.

    ``value`` is a string for every config type, per the server's
    :class:`SetConfigRequest` contract. Non-string types travel serialized and
    are coerced on read using ``config_type``: ``int``/``bool`` are cast and
    ``json`` is parsed, so a JSON config's value is a serialized JSON string.
    ``config_type`` accepts the :class:`ConfigType` enum values (``string``,
    ``int``, ``bool``, ``json``, ``secret``). ``organization_id`` is a ref
    (UUID, name) or ``None`` for global scope — resolved via
    :class:`RefResolver`.
    """
    if not key:
        return error_result("key is required")

    from bifrost.contracts import ConfigCreate
    from bifrost.dto_flags import assemble_body
    from bifrost.refs import RefResolver

    fields = {
        "key": key,
        "value": value,
        "config_type": config_type,
        "description": description,
    }
    async with rest_client(context) as http:
        resolver = RefResolver(http)
        # ``assemble_body`` applies the ``config_type`` -> ``type`` wire alias
        # and drops unset fields.
        body = await assemble_body(ConfigCreate, fields, resolver=resolver)
        if organization_id is not None:
            try:
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
        return error_result(f"create_config failed: HTTP {status_code}", {"body": resp})
    return success_result(
        f"Created config: {key}",
        resp if isinstance(resp, dict) else {"body": resp},
    )


async def bifrost_update_config(
    context: Any,
    config_ref: str,
    value: str | None = None,
    config_type: str | None = None,
    description: str | None = None,
) -> ToolResult:
    """Update a config — ``PUT /api/config/{uuid}``.

    ``config_ref`` is a UUID or config ``key``. ``value`` is a string for
    every config type (see :func:`bifrost_create_config`). Omitting ``value``
    preserves the stored value (the server honours unset-means-omit;
    particularly important for secret-type configs).
    """
    if not config_ref:
        return error_result("config_ref is required")

    from bifrost.contracts import ConfigUpdate
    from bifrost.dto_flags import assemble_body
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
        # Unset fields are omitted so the server's omit-unset semantics
        # preserve the stored value (critical for secret configs).
        body = await assemble_body(
            ConfigUpdate,
            {"value": value, "config_type": config_type,
             "description": description},
            resolver=resolver,
        )

    status_code, resp = await call_rest(
        context, "PUT", f"/api/config/{config_uuid}", json_body=body
    )
    if status_code != 200:
        return error_result(f"update_config failed: HTTP {status_code}", {"body": resp})
    return success_result(
        f"Updated config {config_uuid}",
        resp if isinstance(resp, dict) else {"body": resp},
    )


async def bifrost_delete_config(context: Any, config_ref: str) -> ToolResult:
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
        return error_result(f"delete_config failed: HTTP {status_code}", {"body": resp})
    return success_result(f"Deleted config {config_uuid}", {"deleted": config_uuid})


TOOLS = [
    ("bifrost_list_configs", "List Configs", "List configuration values for the caller's scope."),
    ("bifrost_get_config", "Get Config", "Get a single configuration value by UUID or key."),
    ("bifrost_create_config", "Create Config", "Create a configuration value."),
    ("bifrost_update_config", "Update Config", "Update a configuration value by UUID or key."),
    ("bifrost_delete_config", "Delete Config", "Delete a configuration value by UUID or key."),
]


def register_tools(mcp: Any, get_context_fn: Any) -> None:
    """Register all configs parity tools with FastMCP."""
    from src.services.mcp_server.generators.fastmcp_generator import (
        register_tool_with_context,
    )

    tool_funcs = {
        "bifrost_list_configs": bifrost_list_configs,
        "bifrost_get_config": bifrost_get_config,
        "bifrost_create_config": bifrost_create_config,
        "bifrost_update_config": bifrost_update_config,
        "bifrost_delete_config": bifrost_delete_config,
    }

    for tool_id, _name, description in TOOLS:
        register_tool_with_context(
            mcp, tool_funcs[tool_id], tool_id, description, get_context_fn
        )


__all__ = [
    "TOOLS",
    "bifrost_create_config",
    "bifrost_delete_config",
    "bifrost_get_config",
    "bifrost_list_configs",
    "bifrost_update_config",
    "register_tools",
]
