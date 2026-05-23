"""
Events MCP Tools

Tools for managing event sources (webhooks, schedules), subscriptions,
and webhook adapters.
"""

import logging
from datetime import datetime, timezone as _tz
from typing import Any
from uuid import UUID

from fastmcp.tools import ToolResult
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from src.services.mcp_server.tool_result import error_result, success_result
from src.services.mcp_server.tools.db import get_tool_db

logger = logging.getLogger(__name__)


def _require_platform_admin(context: Any) -> ToolResult | None:
    if not getattr(context, "is_platform_admin", False):
        return error_result("Platform administrator privileges are required for event tools.")
    return None


def _build_callback_url(source_id: UUID) -> str:
    """Build callback URL path from event source ID."""
    return f"/api/hooks/{source_id}"


async def list_event_sources(
    context: Any,
    source_type: str | None = None,
    organization_id: str | None = None,
    limit: int = 50,
) -> ToolResult:
    """List event sources with optional filters."""
    from src.models.enums import EventSourceType
    from src.repositories.events import EventSourceRepository, EventSubscriptionRepository

    logger.info(f"MCP list_event_sources called with type={source_type}, org={organization_id}")
    if denied := _require_platform_admin(context):
        return denied

    try:
        # Parse source_type enum
        source_type_enum = None
        if source_type:
            try:
                source_type_enum = EventSourceType(source_type)
            except ValueError:
                return error_result(
                    f"Invalid source_type: {source_type}. Valid values: webhook, schedule, internal"
                )

        org_id = UUID(organization_id) if organization_id else None

        async with get_tool_db(context) as db:
            repo = EventSourceRepository(db)
            sub_repo = EventSubscriptionRepository(db)

            sources = await repo.get_by_organization(
                organization_id=org_id,
                source_type=source_type_enum,
                include_global=org_id is None,
                limit=limit,
            )

            if not sources:
                return success_result("No event sources found", {"sources": [], "count": 0})

            source_list = []
            for s in sources:
                data: dict[str, Any] = {
                    "id": str(s.id),
                    "name": s.name,
                    "source_type": s.source_type.value if hasattr(s.source_type, "value") else str(s.source_type),
                    "organization_id": str(s.organization_id) if s.organization_id else None,
                    "is_active": s.is_active,
                    "subscription_count": await sub_repo.count_by_source(s.id, active_only=True),
                }

                if s.source_type == EventSourceType.WEBHOOK and s.webhook_source:
                    data["adapter_name"] = s.webhook_source.adapter_name or "generic"
                    data["callback_url"] = _build_callback_url(s.id)

                if s.source_type == EventSourceType.SCHEDULE and s.schedule_source:
                    data["cron_expression"] = s.schedule_source.cron_expression
                    data["timezone"] = s.schedule_source.timezone
                    data["schedule_enabled"] = s.schedule_source.enabled

                source_list.append(data)

            display_text = f"Found {len(source_list)} event source(s)"
            return success_result(display_text, {"sources": source_list, "count": len(source_list)})

    except Exception as e:
        logger.exception(f"Error listing event sources via MCP: {e}")
        return error_result(f"Error listing event sources: {str(e)}")


async def create_event_source(
    context: Any,
    name: str,
    source_type: str,
    organization_id: str | None = None,
    # Webhook params (flat)
    adapter_name: str | None = None,
    integration_id: str | None = None,
    webhook_config: dict | None = None,
    # Schedule params (flat)
    cron_expression: str | None = None,
    timezone: str = "UTC",
    schedule_enabled: bool = True,
    # Auto-subscription shortcut
    workflow_id: str | None = None,
) -> ToolResult:
    """Create a new event source (webhook or schedule).

    If workflow_id is provided, automatically creates a subscription linking
    the event source to that workflow. If an active event source with the same
    name, source_type, and organization already exists, reuses it and ensures
    a subscription to the workflow exists.
    """
    from src.models.enums import EventSourceType
    from src.models.orm.events import EventSource, EventSubscription, ScheduleSource, WebhookSource
    from src.services.webhooks.registry import get_adapter_registry

    logger.info(f"MCP create_event_source called: name={name}, type={source_type}, workflow_id={workflow_id}")
    if denied := _require_platform_admin(context):
        return denied

    try:
        source_type_enum = EventSourceType(source_type)
    except ValueError:
        return error_result(
            f"Invalid source_type: {source_type}. Valid values: webhook, schedule, internal"
        )

    # Validate type-specific params
    if source_type_enum == EventSourceType.WEBHOOK:
        pass  # adapter_name is optional (defaults to generic)
    elif source_type_enum == EventSourceType.SCHEDULE:
        if not cron_expression:
            return error_result("cron_expression is required for schedule source type")

    # Validate workflow_id format if provided
    if workflow_id:
        try:
            UUID(workflow_id)
        except ValueError:
            return error_result(f"Invalid workflow_id: {workflow_id}. Must be a valid UUID.")

    try:
        now = datetime.now(_tz.utc)
        user_email = getattr(context, "user_email", "") or getattr(context, "email", "mcp")

        async with get_tool_db(context) as db:
            org_uuid = UUID(organization_id) if organization_id else None

            # Upsert logic: if workflow_id provided, check for existing matching source
            existing_source = None
            if workflow_id:
                query = (
                    select(EventSource)
                    .options(
                        joinedload(EventSource.webhook_source),
                        joinedload(EventSource.schedule_source),
                    )
                    .where(
                        EventSource.name == name,
                        EventSource.source_type == source_type_enum,
                        EventSource.is_active.is_(True),
                    )
                )
                if org_uuid:
                    query = query.where(EventSource.organization_id == org_uuid)
                else:
                    query = query.where(EventSource.organization_id.is_(None))

                result = await db.execute(query)
                existing_source = result.unique().scalar_one_or_none()

            if existing_source:
                source = existing_source
                callback_url = _build_callback_url(source.id) if source_type_enum == EventSourceType.WEBHOOK else None
            else:
                # Create base event source
                source = EventSource(
                    name=name,
                    source_type=source_type_enum,
                    organization_id=org_uuid,
                    is_active=True,
                    created_by=user_email,
                    created_at=now,
                    updated_at=now,
                )
                db.add(source)
                await db.flush()

                callback_url = None

                # Handle webhook
                if source_type_enum == EventSourceType.WEBHOOK:
                    registry = get_adapter_registry()
                    adapter = registry.get(adapter_name)
                    if adapter_name and not adapter:
                        return error_result(f"Unknown webhook adapter: {adapter_name}")

                    webhook_source = WebhookSource(
                        event_source_id=source.id,
                        adapter_name=adapter_name,
                        integration_id=UUID(integration_id) if integration_id else None,
                        config=webhook_config or {},
                        created_at=now,
                        updated_at=now,
                    )

                    callback_url = _build_callback_url(source.id)

                    if adapter:
                        try:
                            result = await adapter.subscribe(
                                callback_url=callback_url,
                                config=webhook_config or {},
                                integration=None,  # TODO: load integration if needed
                            )
                            webhook_source.external_id = result.external_id
                            webhook_source.state = result.state
                            webhook_source.expires_at = result.expires_at
                        except Exception as e:
                            logger.error(f"Failed to subscribe webhook: {e}", exc_info=True)
                            source.error_message = str(e)

                    db.add(webhook_source)
                    await db.flush()

                # Handle schedule
                if source_type_enum == EventSourceType.SCHEDULE:
                    schedule_source = ScheduleSource(
                        event_source_id=source.id,
                        cron_expression=cron_expression,
                        timezone=timezone,
                        enabled=schedule_enabled,
                        created_at=now,
                        updated_at=now,
                    )
                    db.add(schedule_source)
                    await db.flush()

            # Auto-create subscription if workflow_id provided
            subscription_data = None
            if workflow_id:
                # Check if subscription already exists
                existing_sub = await db.execute(
                    select(EventSubscription)
                    .options(joinedload(EventSubscription.workflow))
                    .where(
                        EventSubscription.event_source_id == source.id,
                        EventSubscription.workflow_id == UUID(workflow_id),
                        EventSubscription.is_active.is_(True),
                    )
                )
                existing_sub_row = existing_sub.unique().scalar_one_or_none()

                if existing_sub_row:
                    subscription_data = {
                        "subscription_id": str(existing_sub_row.id),
                        "workflow_id": workflow_id,
                        "workflow_name": existing_sub_row.workflow.name if existing_sub_row.workflow else None,
                        "already_existed": True,
                    }
                else:
                    subscription = EventSubscription(
                        event_source_id=source.id,
                        workflow_id=UUID(workflow_id),
                        is_active=True,
                        created_by=user_email,
                        created_at=now,
                        updated_at=now,
                    )
                    db.add(subscription)
                    await db.flush()

                    # Reload with workflow name
                    sub_result = await db.execute(
                        select(EventSubscription)
                        .options(joinedload(EventSubscription.workflow))
                        .where(EventSubscription.id == subscription.id)
                    )
                    subscription = sub_result.unique().scalar_one()

                    subscription_data = {
                        "subscription_id": str(subscription.id),
                        "workflow_id": workflow_id,
                        "workflow_name": subscription.workflow.name if subscription.workflow else None,
                        "already_existed": False,
                    }

            response: dict[str, Any] = {
                "id": str(source.id),
                "name": source.name,
                "source_type": source_type,
                "organization_id": organization_id,
                "is_active": True,
            }

            if existing_source:
                response["already_existed"] = True

            if callback_url:
                response["callback_url"] = callback_url
            if source.error_message:
                response["error_message"] = source.error_message
            if source_type_enum == EventSourceType.SCHEDULE:
                response["cron_expression"] = cron_expression
                response["timezone"] = timezone
                response["schedule_enabled"] = schedule_enabled
            if subscription_data:
                response["subscription"] = subscription_data

            display_text = f"Created event source '{name}' ({source_type})"
            if existing_source:
                display_text = f"Reused existing event source '{name}' ({source_type})"
            if callback_url:
                display_text += f" - callback: {callback_url}"
            if subscription_data:
                wf_name = subscription_data.get("workflow_name") or workflow_id
                display_text += f" -> {wf_name}"

            return success_result(display_text, response)

    except Exception as e:
        logger.exception(f"Error creating event source via MCP: {e}")
        return error_result(f"Error creating event source: {str(e)}")


async def get_event_source(
    context: Any,
    source_id: str,
) -> ToolResult:
    """Get details of a specific event source."""
    from src.models.enums import EventSourceType
    from src.repositories.events import EventSourceRepository, EventSubscriptionRepository

    logger.info(f"MCP get_event_source called with id={source_id}")
    if denied := _require_platform_admin(context):
        return denied

    if not source_id:
        return error_result("source_id is required")

    try:
        async with get_tool_db(context) as db:
            repo = EventSourceRepository(db)
            source = await repo.get_by_id_with_details(UUID(source_id))

            if not source:
                return error_result(f"Event source not found: {source_id}")

            sub_repo = EventSubscriptionRepository(db)
            subscription_count = await sub_repo.count_by_source(source.id, active_only=True)

            data: dict[str, Any] = {
                "id": str(source.id),
                "name": source.name,
                "source_type": source.source_type.value if hasattr(source.source_type, "value") else str(source.source_type),
                "organization_id": str(source.organization_id) if source.organization_id else None,
                "is_active": source.is_active,
                "error_message": source.error_message,
                "subscription_count": subscription_count,
                "created_by": source.created_by,
                "created_at": source.created_at.isoformat() if source.created_at else None,
            }

            if source.source_type == EventSourceType.WEBHOOK and source.webhook_source:
                ws = source.webhook_source
                data["adapter_name"] = ws.adapter_name or "generic"
                data["callback_url"] = _build_callback_url(source.id)
                data["integration_id"] = str(ws.integration_id) if ws.integration_id else None
                data["external_id"] = ws.external_id
                if ws.expires_at:
                    data["expires_at"] = ws.expires_at.isoformat()

            if source.source_type == EventSourceType.SCHEDULE and source.schedule_source:
                ss = source.schedule_source
                data["cron_expression"] = ss.cron_expression
                data["timezone"] = ss.timezone
                data["schedule_enabled"] = ss.enabled

            display_text = f"Event source: {source.name} ({data['source_type']})"
            return success_result(display_text, data)

    except Exception as e:
        logger.exception(f"Error getting event source via MCP: {e}")
        return error_result(f"Error getting event source: {str(e)}")


async def update_event_source(
    context: Any,
    source_id: str,
    name: str | None = None,
    is_active: bool | None = None,
    # Schedule updates
    cron_expression: str | None = None,
    timezone: str | None = None,
    schedule_enabled: bool | None = None,
) -> ToolResult:
    """Update an existing event source."""
    from src.models.enums import EventSourceType
    from src.models.orm.events import EventSource, WebhookSource
    from src.repositories.events import EventSourceRepository

    logger.info(f"MCP update_event_source called with id={source_id}")
    if denied := _require_platform_admin(context):
        return denied

    if not source_id:
        return error_result("source_id is required")

    try:
        async with get_tool_db(context) as db:
            repo = EventSourceRepository(db)
            source = await repo.get_by_id_with_details(UUID(source_id))

            if not source:
                return error_result(f"Event source not found: {source_id}")

            # Update basic fields
            if name is not None:
                source.name = name
            if is_active is not None:
                source.is_active = is_active
                if is_active:
                    source.error_message = None

            source.updated_at = datetime.now(_tz.utc)

            # Update schedule fields
            if source.source_type == EventSourceType.SCHEDULE and source.schedule_source:
                ss = source.schedule_source
                if cron_expression is not None:
                    ss.cron_expression = cron_expression
                if timezone is not None:
                    ss.timezone = timezone
                if schedule_enabled is not None:
                    ss.enabled = schedule_enabled
                ss.updated_at = datetime.now(_tz.utc)

            await db.flush()

            # Reload
            result = await db.execute(
                select(EventSource)
                .options(
                    joinedload(EventSource.webhook_source).joinedload(WebhookSource.integration),
                    joinedload(EventSource.schedule_source),
                    joinedload(EventSource.organization),
                )
                .where(EventSource.id == UUID(source_id))
            )
            source = result.unique().scalar_one()

            data: dict[str, Any] = {
                "id": str(source.id),
                "name": source.name,
                "source_type": source.source_type.value if hasattr(source.source_type, "value") else str(source.source_type),
                "is_active": source.is_active,
            }

            if source.source_type == EventSourceType.SCHEDULE and source.schedule_source:
                data["cron_expression"] = source.schedule_source.cron_expression
                data["timezone"] = source.schedule_source.timezone
                data["schedule_enabled"] = source.schedule_source.enabled

            display_text = f"Updated event source: {source.name}"
            return success_result(display_text, data)

    except Exception as e:
        logger.exception(f"Error updating event source via MCP: {e}")
        return error_result(f"Error updating event source: {str(e)}")


async def delete_event_source(
    context: Any,
    source_id: str,
) -> ToolResult:
    """Permanently delete an event source."""
    from src.models.enums import EventSourceType
    from src.repositories.events import EventSourceRepository
    from src.services.webhooks.registry import get_adapter_registry

    logger.info(f"MCP delete_event_source called with id={source_id}")
    if denied := _require_platform_admin(context):
        return denied

    if not source_id:
        return error_result("source_id is required")

    try:
        async with get_tool_db(context) as db:
            repo = EventSourceRepository(db)
            source = await repo.get_by_id_with_details(UUID(source_id))

            if not source:
                return error_result(f"Event source not found: {source_id}")

            # Unsubscribe webhooks
            if source.source_type == EventSourceType.WEBHOOK and source.webhook_source:
                ws = source.webhook_source
                adapter = get_adapter_registry().get(ws.adapter_name)
                if adapter:
                    try:
                        await adapter.unsubscribe(
                            external_id=ws.external_id,
                            state=ws.state or {},
                            integration=ws.integration,
                        )
                    except Exception as e:
                        logger.warning(f"Failed to unsubscribe webhook: {e}")

            await db.delete(source)
            await db.flush()

            display_text = f"Deleted event source: {source.name}"
            return success_result(display_text, {"id": source_id, "deleted": True})

    except Exception as e:
        logger.exception(f"Error deleting event source via MCP: {e}")
        return error_result(f"Error deleting event source: {str(e)}")


async def list_event_subscriptions(
    context: Any,
    source_id: str,
) -> ToolResult:
    """List subscriptions for an event source."""
    from src.repositories.events import (
        EventDeliveryRepository,
        EventSourceRepository,
        EventSubscriptionRepository,
    )

    logger.info(f"MCP list_event_subscriptions called with source_id={source_id}")
    if denied := _require_platform_admin(context):
        return denied

    if not source_id:
        return error_result("source_id is required")

    try:
        async with get_tool_db(context) as db:
            from src.models.enums import EventDeliveryStatus

            source_repo = EventSourceRepository(db)
            source = await source_repo.get_by_id(UUID(source_id))

            if not source:
                return error_result(f"Event source not found: {source_id}")

            sub_repo = EventSubscriptionRepository(db)
            subscriptions = await sub_repo.get_by_source(UUID(source_id), active_only=False)

            if not subscriptions:
                return success_result(
                    "No subscriptions found",
                    {"source_id": source_id, "subscriptions": [], "count": 0},
                )

            delivery_repo = EventDeliveryRepository(db)
            sub_list = []
            for s in subscriptions:
                total = await delivery_repo.count_by_subscription(s.id)
                success = await delivery_repo.count_by_subscription(s.id, status=EventDeliveryStatus.SUCCESS)
                failed = await delivery_repo.count_by_subscription(s.id, status=EventDeliveryStatus.FAILED)

                sub_list.append({
                    "id": str(s.id),
                    "workflow_id": str(s.workflow_id),
                    "workflow_name": s.workflow.name if s.workflow else None,
                    "event_type": s.event_type,
                    "input_mapping": s.input_mapping,
                    "is_active": s.is_active,
                    "delivery_count": total,
                    "success_count": success,
                    "failed_count": failed,
                })

            display_text = f"Found {len(sub_list)} subscription(s) for source {source.name}"
            return success_result(
                display_text,
                {"source_id": source_id, "subscriptions": sub_list, "count": len(sub_list)},
            )

    except Exception as e:
        logger.exception(f"Error listing subscriptions via MCP: {e}")
        return error_result(f"Error listing subscriptions: {str(e)}")


async def create_event_subscription(
    context: Any,
    source_id: str,
    workflow_id: str,
    event_type: str | None = None,
    input_mapping: dict | None = None,
) -> ToolResult:
    """Create a subscription linking an event source to a workflow."""
    from src.models.orm.events import EventSubscription
    from src.repositories.events import EventSourceRepository

    logger.info(f"MCP create_event_subscription called: source={source_id}, workflow={workflow_id}")
    if denied := _require_platform_admin(context):
        return denied

    if not source_id:
        return error_result("source_id is required")
    if not workflow_id:
        return error_result("workflow_id is required")

    try:
        now = datetime.now(_tz.utc)
        user_email = getattr(context, "user_email", "") or getattr(context, "email", "mcp")

        async with get_tool_db(context) as db:
            # Verify source exists
            source_repo = EventSourceRepository(db)
            source = await source_repo.get_by_id(UUID(source_id))

            if not source:
                return error_result(f"Event source not found: {source_id}")

            subscription = EventSubscription(
                event_source_id=UUID(source_id),
                workflow_id=UUID(workflow_id),
                event_type=event_type,
                input_mapping=input_mapping,
                is_active=True,
                created_by=user_email,
                created_at=now,
                updated_at=now,
            )
            db.add(subscription)
            await db.flush()

            # Reload with workflow
            result = await db.execute(
                select(EventSubscription)
                .options(joinedload(EventSubscription.workflow))
                .where(EventSubscription.id == subscription.id)
            )
            subscription = result.unique().scalar_one()

            data = {
                "id": str(subscription.id),
                "source_id": source_id,
                "workflow_id": workflow_id,
                "workflow_name": subscription.workflow.name if subscription.workflow else None,
                "event_type": event_type,
                "input_mapping": input_mapping,
                "is_active": True,
            }

            workflow_name = subscription.workflow.name if subscription.workflow else workflow_id
            display_text = f"Created subscription: {source.name} -> {workflow_name}"
            return success_result(display_text, data)

    except Exception as e:
        logger.exception(f"Error creating subscription via MCP: {e}")
        return error_result(f"Error creating subscription: {str(e)}")


async def update_event_subscription(
    context: Any,
    source_id: str,
    subscription_id: str,
    event_type: str | None = None,
    input_mapping: dict | None = None,
    is_active: bool | None = None,
) -> ToolResult:
    """Update an event subscription."""
    from src.models.orm.events import EventSubscription

    logger.info(f"MCP update_event_subscription called: sub={subscription_id}")
    if denied := _require_platform_admin(context):
        return denied

    if not source_id or not subscription_id:
        return error_result("source_id and subscription_id are required")

    try:
        async with get_tool_db(context) as db:
            result = await db.execute(
                select(EventSubscription)
                .options(joinedload(EventSubscription.workflow))
                .where(
                    EventSubscription.id == UUID(subscription_id),
                    EventSubscription.event_source_id == UUID(source_id),
                )
            )
            subscription = result.unique().scalar_one_or_none()

            if not subscription:
                return error_result(f"Subscription not found: {subscription_id}")

            if event_type is not None:
                subscription.event_type = event_type
            if input_mapping is not None:
                subscription.input_mapping = input_mapping
            if is_active is not None:
                subscription.is_active = is_active

            subscription.updated_at = datetime.now(_tz.utc)
            await db.flush()

            data = {
                "id": str(subscription.id),
                "source_id": source_id,
                "workflow_id": str(subscription.workflow_id),
                "workflow_name": subscription.workflow.name if subscription.workflow else None,
                "event_type": subscription.event_type,
                "input_mapping": subscription.input_mapping,
                "is_active": subscription.is_active,
            }

            display_text = f"Updated subscription {subscription_id}"
            return success_result(display_text, data)

    except Exception as e:
        logger.exception(f"Error updating subscription via MCP: {e}")
        return error_result(f"Error updating subscription: {str(e)}")


async def delete_event_subscription(
    context: Any,
    source_id: str,
    subscription_id: str,
) -> ToolResult:
    """Permanently delete an event subscription."""
    from src.models.orm.events import EventSubscription

    logger.info(f"MCP delete_event_subscription called: sub={subscription_id}")
    if denied := _require_platform_admin(context):
        return denied

    if not source_id or not subscription_id:
        return error_result("source_id and subscription_id are required")

    try:
        async with get_tool_db(context) as db:
            result = await db.execute(
                select(EventSubscription).where(
                    EventSubscription.id == UUID(subscription_id),
                    EventSubscription.event_source_id == UUID(source_id),
                )
            )
            subscription = result.scalar_one_or_none()

            if not subscription:
                return error_result(f"Subscription not found: {subscription_id}")

            await db.delete(subscription)
            await db.flush()

            display_text = f"Deleted subscription {subscription_id}"
            return success_result(display_text, {"id": subscription_id, "deleted": True})

    except Exception as e:
        logger.exception(f"Error deleting subscription via MCP: {e}")
        return error_result(f"Error deleting subscription: {str(e)}")


async def list_webhook_adapters(
    context: Any,
) -> ToolResult:
    """List available webhook adapters."""
    from src.services.webhooks.registry import get_adapter_registry

    logger.info("MCP list_webhook_adapters called")
    if denied := _require_platform_admin(context):
        return denied

    try:
        registry = get_adapter_registry()
        adapters_info = registry.list_adapters()

        adapter_list = []
        for info in adapters_info:
            adapter_list.append({
                "name": info["name"],
                "display_name": info["display_name"],
                "description": info.get("description"),
                "requires_integration": info.get("requires_integration"),
                "supports_renewal": info.get("supports_renewal", False),
            })

        display_text = f"Found {len(adapter_list)} webhook adapter(s)"
        return success_result(display_text, {"adapters": adapter_list, "count": len(adapter_list)})

    except Exception as e:
        logger.exception(f"Error listing webhook adapters via MCP: {e}")
        return error_result(f"Error listing webhook adapters: {str(e)}")


# Tool metadata for registration
TOOLS = [
    ("list_event_sources", "List Event Sources", "List event sources with optional filters by type and organization."),
    ("create_event_source", "Create Event Source", "Create a new event source (webhook or schedule). Optionally pass workflow_id to auto-create a subscription in one call."),
    ("get_event_source", "Get Event Source", "Get details of a specific event source."),
    ("update_event_source", "Update Event Source", "Update an existing event source."),
    ("delete_event_source", "Delete Event Source", "Permanently delete an event source."),
    ("list_event_subscriptions", "List Event Subscriptions", "List subscriptions for an event source."),
    ("create_event_subscription", "Create Event Subscription", "Create a subscription linking an event source to a workflow."),
    ("update_event_subscription", "Update Event Subscription", "Update an event subscription."),
    ("delete_event_subscription", "Delete Event Subscription", "Permanently delete an event subscription."),
    ("list_webhook_adapters", "List Webhook Adapters", "List available webhook adapters."),
]


def register_tools(mcp: Any, get_context_fn: Any) -> None:
    """Register all event tools with FastMCP."""
    from src.services.mcp_server.generators.fastmcp_generator import register_tool_with_context

    tool_funcs = {
        "list_event_sources": list_event_sources,
        "create_event_source": create_event_source,
        "get_event_source": get_event_source,
        "update_event_source": update_event_source,
        "delete_event_source": delete_event_source,
        "list_event_subscriptions": list_event_subscriptions,
        "create_event_subscription": create_event_subscription,
        "update_event_subscription": update_event_subscription,
        "delete_event_subscription": delete_event_subscription,
        "list_webhook_adapters": list_webhook_adapters,
    }

    for tool_id, name, description in TOOLS:
        register_tool_with_context(mcp, tool_funcs[tool_id], tool_id, description, get_context_fn)
