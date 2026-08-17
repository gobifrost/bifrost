"""Event administration MCP tools backed by the canonical REST API.

Event Source, Event Subscription, and webhook-adapter operations use the same
authorization, validation, Solution guards, audit, manifest, and scheduler
state as the web client and CLI. This module only resolves portable references,
builds the nested REST DTOs, and translates responses into ``ToolResult``.
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


async def _resolve_scope(context: Any, scope: str | None) -> dict[str, Any]:
    if scope is None:
        return {}
    if scope.lower() == "global":
        return {"organization_id": None}
    return {"organization_id": await _resolve_ref(context, "org", scope)}


def _webhook_body(
    *,
    adapter_name: str | None,
    integration_id: str | None,
    webhook_config: dict[str, Any] | None,
    rate_limit_per_minute: int | None,
    rate_limit_window_seconds: int | None,
    rate_limit_enabled: bool | None,
    clear_integration: bool = False,
    clear_rate_limit: bool = False,
) -> dict[str, Any] | None:
    values = {
        "adapter_name": adapter_name,
        "integration_id": integration_id,
        "config": webhook_config,
        "rate_limit_per_minute": rate_limit_per_minute,
        "rate_limit_window_seconds": rate_limit_window_seconds,
        "rate_limit_enabled": rate_limit_enabled,
    }
    body = {key: value for key, value in values.items() if value is not None}
    if clear_integration:
        body["integration_id"] = None
    if clear_rate_limit:
        body["rate_limit_per_minute"] = None
    return body or None


def _schedule_body(
    *,
    cron_expression: str | None,
    timezone: str | None,
    schedule_enabled: bool | None,
    overlap_policy: str | None,
) -> dict[str, Any] | None:
    values = {
        "cron_expression": cron_expression,
        "timezone": timezone,
        "enabled": schedule_enabled,
        "overlap_policy": overlap_policy,
    }
    body = {key: value for key, value in values.items() if value is not None}
    return body or None


async def bifrost_list_event_sources(
    context: Any,
    source_type: str | None = None,
    scope: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> ToolResult:
    """List Event Sources through the canonical REST endpoint."""

    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if source_type is not None:
        params["source_type"] = source_type
    try:
        target = await _resolve_scope(context, scope)
    except Exception as exc:
        return error_result(f"Invalid Event Source scope: {exc}", _ref_error_payload(exc))
    if "organization_id" in target:
        if target["organization_id"] is None:
            params["scope"] = "global"
        else:
            params["organization_id"] = target["organization_id"]

    status_code, body = await call_rest(
        context,
        "GET",
        "/api/events/sources",
        params=params,
    )
    if status_code != 200 or not isinstance(body, dict):
        return _rest_error("List Event Sources", status_code, body)
    items = body.get("items", [])
    return success_result(
        f"Found {len(items)} Event Source(s)",
        {"items": items, "total": body.get("total", len(items))},
    )


async def bifrost_get_event_source(context: Any, source_ref: str) -> ToolResult:
    """Get one Event Source by UUID or unambiguous name."""

    if not source_ref:
        return error_result("source_ref is required")
    try:
        source_id = await _resolve_ref(context, "event_source", source_ref)
    except Exception as exc:
        return error_result(
            f"Could not resolve Event Source {source_ref!r}",
            _ref_error_payload(exc),
        )
    status_code, body = await call_rest(
        context,
        "GET",
        f"/api/events/sources/{source_id}",
    )
    if status_code != 200:
        return _rest_error("Get Event Source", status_code, body)
    payload = body if isinstance(body, dict) else {"body": body}
    return success_result(
        f"Event Source: {payload.get('name', source_id)}",
        payload,
    )


async def bifrost_create_event_source(
    context: Any,
    name: str,
    source_type: str,
    event_type: str | None = None,
    scope: str | None = None,
    adapter_name: str | None = None,
    integration_id: str | None = None,
    webhook_config: dict[str, Any] | None = None,
    rate_limit_per_minute: int | None = None,
    rate_limit_window_seconds: int | None = None,
    rate_limit_enabled: bool | None = None,
    cron_expression: str | None = None,
    timezone: str | None = None,
    schedule_enabled: bool | None = None,
    overlap_policy: str | None = None,
) -> ToolResult:
    """Create an Event Source; use a separate subscription operation to wire targets."""

    try:
        resolved_integration = (
            await _resolve_ref(context, "integration", integration_id)
            if integration_id
            else None
        )
        body: dict[str, Any] = {
            "name": name,
            "source_type": source_type,
            **await _resolve_scope(context, scope),
        }
        if event_type is not None:
            body["event_type"] = event_type
        webhook = _webhook_body(
            adapter_name=adapter_name,
            integration_id=resolved_integration,
            webhook_config=webhook_config,
            rate_limit_per_minute=rate_limit_per_minute,
            rate_limit_window_seconds=rate_limit_window_seconds,
            rate_limit_enabled=rate_limit_enabled,
            clear_integration=False,
            clear_rate_limit=False,
        )
        if webhook is not None or source_type == "webhook":
            body["webhook"] = webhook or {}
        schedule = _schedule_body(
            cron_expression=cron_expression,
            timezone=timezone,
            schedule_enabled=schedule_enabled,
            overlap_policy=overlap_policy,
        )
        if schedule is not None or source_type == "schedule":
            body["schedule"] = schedule or {}
    except Exception as exc:
        return error_result(f"Invalid Event Source input: {exc}", _ref_error_payload(exc))

    status_code, response = await call_rest(
        context,
        "POST",
        "/api/events/sources",
        json_body=body,
    )
    if status_code != 201:
        return _rest_error("Create Event Source", status_code, response)
    payload = response if isinstance(response, dict) else {"body": response}
    return success_result(
        f"Created Event Source: {payload.get('name', name)}",
        payload,
    )


async def bifrost_update_event_source(
    context: Any,
    source_ref: str,
    name: str | None = None,
    is_active: bool | None = None,
    scope: str | None = None,
    adapter_name: str | None = None,
    integration_id: str | None = None,
    webhook_config: dict[str, Any] | None = None,
    rate_limit_per_minute: int | None = None,
    rate_limit_window_seconds: int | None = None,
    rate_limit_enabled: bool | None = None,
    clear_webhook_integration: bool = False,
    clear_rate_limit: bool = False,
    cron_expression: str | None = None,
    timezone: str | None = None,
    schedule_enabled: bool | None = None,
    overlap_policy: str | None = None,
) -> ToolResult:
    """Update one Event Source through the canonical REST endpoint."""

    if not source_ref:
        return error_result("source_ref is required")
    if clear_webhook_integration and integration_id is not None:
        return error_result(
            "integration_id and clear_webhook_integration are mutually exclusive"
        )
    if clear_rate_limit and rate_limit_per_minute is not None:
        return error_result(
            "rate_limit_per_minute and clear_rate_limit are mutually exclusive"
        )
    try:
        source_id = await _resolve_ref(context, "event_source", source_ref)
        body: dict[str, Any] = {
            key: value
            for key, value in {"name": name, "is_active": is_active}.items()
            if value is not None
        }
        body.update(await _resolve_scope(context, scope))
        resolved_integration = (
            await _resolve_ref(context, "integration", integration_id)
            if integration_id
            else None
        )
        webhook = _webhook_body(
            adapter_name=adapter_name,
            integration_id=resolved_integration,
            webhook_config=webhook_config,
            rate_limit_per_minute=rate_limit_per_minute,
            rate_limit_window_seconds=rate_limit_window_seconds,
            rate_limit_enabled=rate_limit_enabled,
            clear_integration=clear_webhook_integration,
            clear_rate_limit=clear_rate_limit,
        )
        if webhook is not None:
            body["webhook"] = webhook
        schedule = _schedule_body(
            cron_expression=cron_expression,
            timezone=timezone,
            schedule_enabled=schedule_enabled,
            overlap_policy=overlap_policy,
        )
        if schedule is not None:
            body["schedule"] = schedule
    except Exception as exc:
        return error_result(f"Invalid Event Source input: {exc}", _ref_error_payload(exc))
    if not body:
        return error_result("No updates provided")

    status_code, response = await call_rest(
        context,
        "PATCH",
        f"/api/events/sources/{source_id}",
        json_body=body,
    )
    if status_code != 200:
        return _rest_error("Update Event Source", status_code, response)
    payload = response if isinstance(response, dict) else {"body": response}
    return success_result(
        f"Updated Event Source: {payload.get('name', source_id)}",
        payload,
    )


async def bifrost_delete_event_source(context: Any, source_ref: str) -> ToolResult:
    """Permanently delete one Event Source through REST."""

    if not source_ref:
        return error_result("source_ref is required")
    try:
        source_id = await _resolve_ref(context, "event_source", source_ref)
    except Exception as exc:
        return error_result(
            f"Could not resolve Event Source {source_ref!r}",
            _ref_error_payload(exc),
        )
    status_code, body = await call_rest(
        context,
        "DELETE",
        f"/api/events/sources/{source_id}",
    )
    if status_code != 204:
        return _rest_error("Delete Event Source", status_code, body)
    return success_result("Deleted Event Source", {"success": True, "id": source_id})


async def bifrost_list_event_subscriptions(
    context: Any,
    source_ref: str,
    limit: int = 100,
    offset: int = 0,
) -> ToolResult:
    """List subscriptions under one Event Source."""

    if not source_ref:
        return error_result("source_ref is required")
    try:
        source_id = await _resolve_ref(context, "event_source", source_ref)
    except Exception as exc:
        return error_result(
            f"Could not resolve Event Source {source_ref!r}",
            _ref_error_payload(exc),
        )
    status_code, body = await call_rest(
        context,
        "GET",
        f"/api/events/sources/{source_id}/subscriptions",
        params={"limit": limit, "offset": offset},
    )
    if status_code != 200 or not isinstance(body, dict):
        return _rest_error("List Event Subscriptions", status_code, body)
    items = body.get("items", [])
    return success_result(
        f"Found {len(items)} Event Subscription(s)",
        {"items": items, "total": body.get("total", len(items))},
    )


async def bifrost_get_event_subscription(
    context: Any,
    source_ref: str,
    subscription_id: str,
) -> ToolResult:
    """Get one Event Subscription under its parent source."""

    if not source_ref or not subscription_id:
        return error_result("source_ref and subscription_id are required")
    try:
        source_id = await _resolve_ref(context, "event_source", source_ref)
    except Exception as exc:
        return error_result(
            f"Could not resolve Event Source {source_ref!r}",
            _ref_error_payload(exc),
        )
    status_code, body = await call_rest(
        context,
        "GET",
        f"/api/events/sources/{source_id}/subscriptions/{subscription_id}",
    )
    if status_code != 200:
        return _rest_error("Get Event Subscription", status_code, body)
    payload = body if isinstance(body, dict) else {"body": body}
    return success_result("Event Subscription", payload)


async def bifrost_create_event_subscription(
    context: Any,
    source_ref: str,
    workflow_id: str | None = None,
    agent_id: str | None = None,
    event_type: str | None = None,
    filter_expression: str | None = None,
    input_mapping: dict[str, Any] | None = None,
) -> ToolResult:
    """Subscribe exactly one Workflow or Agent to an Event Source."""

    if not source_ref:
        return error_result("source_ref is required")
    if bool(workflow_id) == bool(agent_id):
        return error_result("Exactly one of workflow_id or agent_id is required")
    try:
        source_id = await _resolve_ref(context, "event_source", source_ref)
        resolved_workflow = (
            await _resolve_ref(context, "workflow", workflow_id)
            if workflow_id
            else None
        )
        resolved_agent = (
            await _resolve_ref(context, "agent", agent_id) if agent_id else None
        )
    except Exception as exc:
        return error_result(
            f"Invalid Event Subscription target: {exc}",
            _ref_error_payload(exc),
        )
    body = {
        "target_type": "agent" if resolved_agent else "workflow",
        "workflow_id": resolved_workflow,
        "agent_id": resolved_agent,
        "event_type": event_type,
        "filter_expression": filter_expression,
        "input_mapping": input_mapping,
    }
    body = {key: value for key, value in body.items() if value is not None}
    status_code, response = await call_rest(
        context,
        "POST",
        f"/api/events/sources/{source_id}/subscriptions",
        json_body=body,
    )
    if status_code != 201:
        return _rest_error("Create Event Subscription", status_code, response)
    payload = response if isinstance(response, dict) else {"body": response}
    return success_result("Created Event Subscription", payload)


async def bifrost_update_event_subscription(
    context: Any,
    source_ref: str,
    subscription_id: str,
    event_type: str | None = None,
    filter_expression: str | None = None,
    input_mapping: dict[str, Any] | None = None,
    is_active: bool | None = None,
    clear_event_type: bool = False,
    clear_filter_expression: bool = False,
    clear_input_mapping: bool = False,
) -> ToolResult:
    """Update one Event Subscription's mutable delivery settings."""

    if not source_ref or not subscription_id:
        return error_result("source_ref and subscription_id are required")
    clears = {
        "event_type": clear_event_type,
        "filter_expression": clear_filter_expression,
        "input_mapping": clear_input_mapping,
    }
    provided = {
        "event_type": event_type,
        "filter_expression": filter_expression,
        "input_mapping": input_mapping,
    }
    conflicts = sorted(
        name for name, clear in clears.items() if clear and provided[name] is not None
    )
    if conflicts:
        return error_result(
            "Cannot set and clear the same Event Subscription field: "
            + ", ".join(conflicts)
        )
    try:
        source_id = await _resolve_ref(context, "event_source", source_ref)
    except Exception as exc:
        return error_result(
            f"Could not resolve Event Source {source_ref!r}",
            _ref_error_payload(exc),
        )
    body = {
        key: value
        for key, value in {
            "event_type": event_type,
            "filter_expression": filter_expression,
            "input_mapping": input_mapping,
            "is_active": is_active,
        }.items()
        if value is not None
    }
    for name, clear in clears.items():
        if clear:
            body[name] = None
    if not body:
        return error_result("No updates provided")
    status_code, response = await call_rest(
        context,
        "PATCH",
        f"/api/events/sources/{source_id}/subscriptions/{subscription_id}",
        json_body=body,
    )
    if status_code != 200:
        return _rest_error("Update Event Subscription", status_code, response)
    payload = response if isinstance(response, dict) else {"body": response}
    return success_result("Updated Event Subscription", payload)


async def bifrost_delete_event_subscription(
    context: Any,
    source_ref: str,
    subscription_id: str,
) -> ToolResult:
    """Permanently delete one Event Subscription through REST."""

    if not source_ref or not subscription_id:
        return error_result("source_ref and subscription_id are required")
    try:
        source_id = await _resolve_ref(context, "event_source", source_ref)
    except Exception as exc:
        return error_result(
            f"Could not resolve Event Source {source_ref!r}",
            _ref_error_payload(exc),
        )
    status_code, body = await call_rest(
        context,
        "DELETE",
        f"/api/events/sources/{source_id}/subscriptions/{subscription_id}",
    )
    if status_code != 204:
        return _rest_error("Delete Event Subscription", status_code, body)
    return success_result(
        "Deleted Event Subscription",
        {"success": True, "id": subscription_id},
    )


async def bifrost_list_event_webhook_adapters(context: Any) -> ToolResult:
    """List webhook adapters available for Event Source configuration."""

    status_code, body = await call_rest(context, "GET", "/api/events/adapters")
    if status_code != 200 or not isinstance(body, dict):
        return _rest_error("List Event webhook adapters", status_code, body)
    adapters = body.get("adapters", [])
    return success_result(
        f"Found {len(adapters)} Event webhook adapter(s)",
        {"adapters": adapters, "count": len(adapters)},
    )


TOOLS = [
    (
        "bifrost_list_event_sources",
        "List Event Sources",
        "List Event Sources with optional type and organization filters.",
    ),
    (
        "bifrost_get_event_source",
        "Get Event Source",
        "Get one Event Source by UUID or unambiguous name.",
    ),
    (
        "bifrost_create_event_source",
        "Create Event Source",
        "Create a webhook, schedule, or topic Event Source.",
    ),
    (
        "bifrost_update_event_source",
        "Update Event Source",
        "Update an Event Source's metadata or type-specific configuration.",
    ),
    (
        "bifrost_delete_event_source",
        "Delete Event Source",
        "Permanently delete an Event Source and its dependent records.",
    ),
    (
        "bifrost_list_event_subscriptions",
        "List Event Subscriptions",
        "List subscriptions under one Event Source.",
    ),
    (
        "bifrost_get_event_subscription",
        "Get Event Subscription",
        "Get one Event Subscription under its parent Event Source.",
    ),
    (
        "bifrost_create_event_subscription",
        "Create Event Subscription",
        "Subscribe exactly one Workflow or Agent to an Event Source.",
    ),
    (
        "bifrost_update_event_subscription",
        "Update Event Subscription",
        "Update an Event Subscription's filters, mapping, or activation.",
    ),
    (
        "bifrost_delete_event_subscription",
        "Delete Event Subscription",
        "Permanently delete one Event Subscription.",
    ),
    (
        "bifrost_list_event_webhook_adapters",
        "List Event Webhook Adapters",
        "List webhook adapters available to Event Sources.",
    ),
]


def register_tools(mcp: Any, get_context_fn: Any) -> None:
    """Register canonical Event tools with FastMCP."""

    from src.services.mcp_server.generators.fastmcp_generator import (
        register_tool_with_context,
    )

    tool_funcs = {
        "bifrost_list_event_sources": bifrost_list_event_sources,
        "bifrost_get_event_source": bifrost_get_event_source,
        "bifrost_create_event_source": bifrost_create_event_source,
        "bifrost_update_event_source": bifrost_update_event_source,
        "bifrost_delete_event_source": bifrost_delete_event_source,
        "bifrost_list_event_subscriptions": bifrost_list_event_subscriptions,
        "bifrost_get_event_subscription": bifrost_get_event_subscription,
        "bifrost_create_event_subscription": bifrost_create_event_subscription,
        "bifrost_update_event_subscription": bifrost_update_event_subscription,
        "bifrost_delete_event_subscription": bifrost_delete_event_subscription,
        "bifrost_list_event_webhook_adapters": bifrost_list_event_webhook_adapters,
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
    "bifrost_create_event_source",
    "bifrost_create_event_subscription",
    "bifrost_delete_event_source",
    "bifrost_delete_event_subscription",
    "bifrost_get_event_source",
    "bifrost_get_event_subscription",
    "bifrost_list_event_sources",
    "bifrost_list_event_subscriptions",
    "bifrost_list_event_webhook_adapters",
    "bifrost_update_event_source",
    "bifrost_update_event_subscription",
    "register_tools",
]
