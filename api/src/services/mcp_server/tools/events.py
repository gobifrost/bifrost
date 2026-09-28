"""Event MCP Tools — thin wrappers around the REST API.

``bifrost_event_source_list``, ``bifrost_event_source_get``,
``bifrost_event_source_create``, ``bifrost_event_source_update``,
``bifrost_event_source_delete``, ``bifrost_event_subscription_list``,
``bifrost_event_subscription_get``, ``bifrost_event_subscription_create``,
``bifrost_event_subscription_update``, ``bifrost_event_subscription_delete``,
``bifrost_event_webhook_adapter_list``.

These tools are thin wrappers: they resolve human refs, assemble the shared
Event Source / Event Subscription DTOs via ``bifrost/dto_flags.py``, then
call the corresponding REST endpoint through the in-process HTTP bridge
(``_http_bridge``). No ORM, no repositories, no ``AsyncSession`` — all side
effects (audit logs, target/scope validation, provider subscribe/unsubscribe)
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


def _build_schedule_config(
    cron_expression: str | None,
    timezone: str | None,
    enabled: bool | None,
) -> dict[str, Any] | None:
    if cron_expression is None and timezone is None and enabled is None:
        return None
    config: dict[str, Any] = {}
    if cron_expression is not None:
        config["cron_expression"] = cron_expression
    if timezone is not None:
        config["timezone"] = timezone
    if enabled is not None:
        config["enabled"] = enabled
    return config


async def _build_webhook_config(
    context: Any,
    adapter_name: str | None,
    integration_ref: str | None,
    config: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if adapter_name is None and integration_ref is None and config is None:
        return None
    webhook: dict[str, Any] = {}
    if adapter_name is not None:
        webhook["adapter_name"] = adapter_name
    if integration_ref is not None:
        webhook["integration_id"] = await _resolve_ref(context, "integration", integration_ref)
    if config is not None:
        webhook["config"] = config
    return webhook


async def _assemble_event_source_body(
    context: Any,
    fields: dict[str, Any],
    *,
    is_update: bool,
    scope: str | None,
) -> dict[str, Any]:
    from bifrost.dto_flags import assemble_body
    from src.models.contracts.events import EventSourceCreate, EventSourceUpdate

    model_cls = EventSourceUpdate if is_update else EventSourceCreate
    async with rest_client(context) as http:
        from bifrost.refs import RefResolver

        resolver = RefResolver(http)
        body = await assemble_body(model_cls, fields, resolver=resolver)
        if not is_update and scope is not None:
            if scope == "global":
                body["organization_id"] = None
            else:
                body["organization_id"] = await resolver.resolve("org", scope)
    return body


async def bifrost_event_source_list(
    context: Any, source_type: str | None = None, scope: str | None = None
) -> ToolResult:
    """List Event Sources — thin wrapper over ``GET /api/events/sources``
    (platform admin only)."""
    params: dict[str, Any] = {}
    if source_type is not None:
        params["source_type"] = source_type
    if scope is not None:
        params["scope"] = scope
    status_code, body = await call_rest(context, "GET", "/api/events/sources", params=params)
    if status_code != 200:
        return _rest_error("List Event Sources", status_code, body)
    items = body.get("items") if isinstance(body, dict) else None
    items = items if isinstance(items, list) else []
    return success_result(f"Found {len(items)} event source(s)", {"sources": items, "count": len(items)})


async def bifrost_event_source_get(context: Any, source_ref: str) -> ToolResult:
    """Get one Event Source by UUID or accessible name — thin wrapper over
    ``GET /api/events/sources/{uuid}`` (platform admin only)."""
    if not source_ref:
        return error_result("source_ref is required")
    try:
        source_id = await _resolve_ref(context, "event_source", source_ref)
    except Exception as exc:
        return error_result(f"could not resolve event source {source_ref!r}", _ref_error_payload(exc))

    status_code, body = await call_rest(context, "GET", f"/api/events/sources/{source_id}")
    if status_code != 200:
        return _rest_error("Get Event Source", status_code, body)
    payload = body if isinstance(body, dict) else {"body": body}
    return success_result(f"Event source: {payload.get('name', source_id)}", payload)


async def bifrost_event_source_create(
    context: Any,
    name: str,
    source_type: str,
    event_type: str | None = None,
    cron_expression: str | None = None,
    timezone: str | None = None,
    schedule_enabled: bool | None = None,
    adapter_name: str | None = None,
    integration_ref: str | None = None,
    webhook_config: dict[str, Any] | None = None,
    scope: str | None = None,
) -> ToolResult:
    """Create an Event Source through ``POST /api/events/sources`` (platform
    admin only). ``source_type`` is ``webhook``, ``schedule``, or ``topic``.
    """
    fields = {"name": name, "source_type": source_type, "event_type": event_type}
    try:
        body = await _assemble_event_source_body(context, fields, is_update=False, scope=scope)
        schedule = _build_schedule_config(cron_expression, timezone, schedule_enabled)
        if schedule is not None:
            body["schedule"] = schedule
        webhook = await _build_webhook_config(context, adapter_name, integration_ref, webhook_config)
        if webhook is not None:
            body["webhook"] = webhook
    except Exception as exc:
        return error_result(f"invalid Event Source input: {exc}", _ref_error_payload(exc))

    status_code, resp = await call_rest(context, "POST", "/api/events/sources", json_body=body)
    if status_code not in (200, 201):
        return _rest_error("Create Event Source", status_code, resp)
    payload = resp if isinstance(resp, dict) else {"body": resp}
    return success_result(f"Created event source: {payload.get('name', name)}", payload)


async def bifrost_event_source_update(
    context: Any,
    source_ref: str,
    name: str | None = None,
    is_active: bool | None = None,
    cron_expression: str | None = None,
    timezone: str | None = None,
    schedule_enabled: bool | None = None,
    adapter_name: str | None = None,
    integration_ref: str | None = None,
    webhook_config: dict[str, Any] | None = None,
    scope: str | None = None,
) -> ToolResult:
    """Update an Event Source through ``PATCH /api/events/sources/{uuid}``
    (platform admin only).

    Changing ``adapter_name``, ``integration_ref``, or ``webhook_config``
    resubscribes the webhook with the provider: the new subscription is
    created first, and only unsubscribes the old one after that succeeds —
    a provider failure on the new subscribe leaves the Event Source
    unchanged and returns a clear error.
    """
    if not source_ref:
        return error_result("source_ref is required")
    try:
        source_id = await _resolve_ref(context, "event_source", source_ref)
        fields = {"name": name, "is_active": is_active}
        body = await _assemble_event_source_body(context, fields, is_update=True, scope=scope)
        schedule = _build_schedule_config(cron_expression, timezone, schedule_enabled)
        if schedule is not None:
            body["schedule"] = schedule
        webhook = await _build_webhook_config(context, adapter_name, integration_ref, webhook_config)
        if webhook is not None:
            body["webhook"] = webhook
    except Exception as exc:
        return error_result(f"invalid Event Source input: {exc}", _ref_error_payload(exc))
    if not body:
        return error_result("No updates provided")

    status_code, resp = await call_rest(
        context, "PATCH", f"/api/events/sources/{source_id}", json_body=body
    )
    if status_code != 200:
        return _rest_error("Update Event Source", status_code, resp)
    payload = resp if isinstance(resp, dict) else {"body": resp}
    return success_result(f"Updated event source: {payload.get('name', source_id)}", payload)


async def bifrost_event_source_delete(context: Any, source_ref: str) -> ToolResult:
    """Delete an Event Source — thin wrapper over
    ``DELETE /api/events/sources/{uuid}`` (platform admin only)."""
    if not source_ref:
        return error_result("source_ref is required")
    try:
        source_id = await _resolve_ref(context, "event_source", source_ref)
    except Exception as exc:
        return error_result(f"could not resolve event source {source_ref!r}", _ref_error_payload(exc))

    status_code, resp = await call_rest(context, "DELETE", f"/api/events/sources/{source_id}")
    if status_code not in (200, 204):
        return _rest_error("Delete Event Source", status_code, resp)
    return success_result(f"Deleted event source {source_id}", {"deleted": source_id})


async def bifrost_event_subscription_list(context: Any, source_ref: str) -> ToolResult:
    """List subscriptions for an Event Source — thin wrapper over
    ``GET /api/events/sources/{uuid}/subscriptions`` (platform admin only)."""
    if not source_ref:
        return error_result("source_ref is required")
    try:
        source_id = await _resolve_ref(context, "event_source", source_ref)
    except Exception as exc:
        return error_result(f"could not resolve event source {source_ref!r}", _ref_error_payload(exc))

    status_code, body = await call_rest(
        context, "GET", f"/api/events/sources/{source_id}/subscriptions"
    )
    if status_code != 200:
        return _rest_error("List Event Subscriptions", status_code, body)
    items = body.get("items") if isinstance(body, dict) else None
    items = items if isinstance(items, list) else []
    return success_result(
        f"Found {len(items)} subscription(s)", {"subscriptions": items, "count": len(items)}
    )


async def bifrost_event_subscription_get(
    context: Any, source_ref: str, subscription_id: str
) -> ToolResult:
    """Get one Event Subscription — thin wrapper over
    ``GET /api/events/sources/{uuid}/subscriptions/{uuid}`` (platform admin only)."""
    if not source_ref:
        return error_result("source_ref is required")
    if not subscription_id:
        return error_result("subscription_id is required")
    try:
        source_id = await _resolve_ref(context, "event_source", source_ref)
    except Exception as exc:
        return error_result(f"could not resolve event source {source_ref!r}", _ref_error_payload(exc))

    status_code, body = await call_rest(
        context, "GET", f"/api/events/sources/{source_id}/subscriptions/{subscription_id}"
    )
    if status_code != 200:
        return _rest_error("Get Event Subscription", status_code, body)
    payload = body if isinstance(body, dict) else {"body": body}
    return success_result(f"Event subscription {subscription_id}", payload)


async def bifrost_event_subscription_create(
    context: Any,
    source_ref: str,
    target_type: str = "workflow",
    workflow_id: str | None = None,
    agent_id: str | None = None,
    event_type: str | None = None,
    filter_expression: str | None = None,
    input_mapping: dict[str, Any] | None = None,
) -> ToolResult:
    """Create an Event Subscription through
    ``POST /api/events/sources/{uuid}/subscriptions`` (platform admin only).
    """
    if not source_ref:
        return error_result("source_ref is required")
    from bifrost.dto_flags import assemble_body
    from bifrost.refs import RefResolver
    from src.models.contracts.events import EventSubscriptionCreate

    try:
        source_id = await _resolve_ref(context, "event_source", source_ref)
        fields = {
            "target_type": target_type,
            "workflow_id": workflow_id,
            "agent_id": agent_id,
            "event_type": event_type,
            "filter_expression": filter_expression,
            "input_mapping": input_mapping,
        }
        async with rest_client(context) as http:
            resolver = RefResolver(http)
            body = await assemble_body(EventSubscriptionCreate, fields, resolver=resolver)
    except Exception as exc:
        return error_result(f"invalid Event Subscription input: {exc}", _ref_error_payload(exc))

    status_code, resp = await call_rest(
        context, "POST", f"/api/events/sources/{source_id}/subscriptions", json_body=body
    )
    if status_code not in (200, 201):
        return _rest_error("Create Event Subscription", status_code, resp)
    payload = resp if isinstance(resp, dict) else {"body": resp}
    return success_result(f"Created event subscription {payload.get('id', '')}", payload)


async def bifrost_event_subscription_update(
    context: Any,
    source_ref: str,
    subscription_id: str,
    event_type: str | None = None,
    filter_expression: str | None = None,
    is_active: bool | None = None,
    input_mapping: dict[str, Any] | None = None,
) -> ToolResult:
    """Update an Event Subscription through
    ``PATCH /api/events/sources/{uuid}/subscriptions/{uuid}`` (platform admin only).
    """
    if not source_ref:
        return error_result("source_ref is required")
    if not subscription_id:
        return error_result("subscription_id is required")
    from bifrost.dto_flags import assemble_body
    from bifrost.refs import RefResolver
    from src.models.contracts.events import EventSubscriptionUpdate

    try:
        source_id = await _resolve_ref(context, "event_source", source_ref)
        fields = {
            "event_type": event_type,
            "filter_expression": filter_expression,
            "is_active": is_active,
            "input_mapping": input_mapping,
        }
        async with rest_client(context) as http:
            resolver = RefResolver(http)
            body = await assemble_body(EventSubscriptionUpdate, fields, resolver=resolver)
    except Exception as exc:
        return error_result(f"invalid Event Subscription input: {exc}", _ref_error_payload(exc))
    if not body:
        return error_result("No updates provided")

    status_code, resp = await call_rest(
        context,
        "PATCH",
        f"/api/events/sources/{source_id}/subscriptions/{subscription_id}",
        json_body=body,
    )
    if status_code != 200:
        return _rest_error("Update Event Subscription", status_code, resp)
    payload = resp if isinstance(resp, dict) else {"body": resp}
    return success_result(f"Updated event subscription {subscription_id}", payload)


async def bifrost_event_subscription_delete(
    context: Any, source_ref: str, subscription_id: str
) -> ToolResult:
    """Delete an Event Subscription — thin wrapper over
    ``DELETE /api/events/sources/{uuid}/subscriptions/{uuid}`` (platform admin only)."""
    if not source_ref:
        return error_result("source_ref is required")
    if not subscription_id:
        return error_result("subscription_id is required")
    try:
        source_id = await _resolve_ref(context, "event_source", source_ref)
    except Exception as exc:
        return error_result(f"could not resolve event source {source_ref!r}", _ref_error_payload(exc))

    status_code, resp = await call_rest(
        context, "DELETE", f"/api/events/sources/{source_id}/subscriptions/{subscription_id}"
    )
    if status_code not in (200, 204):
        return _rest_error("Delete Event Subscription", status_code, resp)
    return success_result(f"Deleted event subscription {subscription_id}", {"deleted": subscription_id})


async def bifrost_event_webhook_adapter_list(context: Any) -> ToolResult:
    """List available webhook adapters — thin wrapper over
    ``GET /api/events/adapters`` (platform admin only)."""
    status_code, body = await call_rest(context, "GET", "/api/events/adapters")
    if status_code != 200:
        return _rest_error("List Webhook Adapters", status_code, body)
    adapters = body.get("adapters") if isinstance(body, dict) else None
    adapters = adapters if isinstance(adapters, list) else []
    return success_result(f"Found {len(adapters)} webhook adapter(s)", {"adapters": adapters})


TOOLS = [
    ("bifrost_event_source_list", "List Event Sources", "List event sources with optional filters by type and scope."),
    ("bifrost_event_source_get", "Get Event Source", "Get an Event Source by UUID or accessible name."),
    ("bifrost_event_source_create", "Create Event Source", "Create a new event source (webhook, schedule, or topic)."),
    ("bifrost_event_source_update", "Update Event Source", "Update an existing event source, resubscribing the webhook when the adapter/integration/config changes."),
    ("bifrost_event_source_delete", "Delete Event Source", "Delete an event source."),
    ("bifrost_event_subscription_list", "List Event Subscriptions", "List subscriptions for an event source."),
    ("bifrost_event_subscription_get", "Get Event Subscription", "Get a single event subscription by ID."),
    ("bifrost_event_subscription_create", "Create Event Subscription", "Create a subscription linking an event source to a workflow or agent."),
    ("bifrost_event_subscription_update", "Update Event Subscription", "Update an event subscription."),
    ("bifrost_event_subscription_delete", "Delete Event Subscription", "Delete an event subscription."),
    ("bifrost_event_webhook_adapter_list", "List Webhook Adapters", "List available webhook adapters."),
]


def register_tools(mcp: Any, get_context_fn: Any) -> None:
    """Register all Event tools with FastMCP."""
    from src.services.mcp_server.generators.fastmcp_generator import register_tool_with_context

    tool_funcs = {
        "bifrost_event_source_list": bifrost_event_source_list,
        "bifrost_event_source_get": bifrost_event_source_get,
        "bifrost_event_source_create": bifrost_event_source_create,
        "bifrost_event_source_update": bifrost_event_source_update,
        "bifrost_event_source_delete": bifrost_event_source_delete,
        "bifrost_event_subscription_list": bifrost_event_subscription_list,
        "bifrost_event_subscription_get": bifrost_event_subscription_get,
        "bifrost_event_subscription_create": bifrost_event_subscription_create,
        "bifrost_event_subscription_update": bifrost_event_subscription_update,
        "bifrost_event_subscription_delete": bifrost_event_subscription_delete,
        "bifrost_event_webhook_adapter_list": bifrost_event_webhook_adapter_list,
    }

    for tool_id, _name, description in TOOLS:
        register_tool_with_context(mcp, tool_funcs[tool_id], tool_id, description, get_context_fn)


__all__ = [
    "TOOLS",
    "bifrost_event_source_create",
    "bifrost_event_source_delete",
    "bifrost_event_source_get",
    "bifrost_event_source_list",
    "bifrost_event_source_update",
    "bifrost_event_subscription_create",
    "bifrost_event_subscription_delete",
    "bifrost_event_subscription_get",
    "bifrost_event_subscription_list",
    "bifrost_event_subscription_update",
    "bifrost_event_webhook_adapter_list",
    "register_tools",
]
