"""
Event Processor Service

Handles the core event processing logic:
1. Receive webhook requests
2. Route to appropriate adapter
3. Create Event records
4. Find matching subscriptions
5. Create EventDelivery records
6. Queue workflow executions

Events are always processed asynchronously and return 202 immediately.
"""

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from src.core.log_safety import log_safe
from src.models.enums import EventDeliveryStatus, EventSourceType, EventStatus
from src.models.orm.events import (
    Event,
    EventDelivery,
    EventSource,
    EventSubscription,
    WebhookSource,
)
from src.repositories.events import (
    EventDeliveryRepository,
    EventRepository,
    EventSourceRepository,
    EventSubscriptionRepository,
    WebhookSourceRepository,
)
from src.services.events.validation import validate_topic
from src.services.webhooks.protocol import (
    Deliver,
    HandleResult,
    Rejected,
    ValidationResponse,
    WebhookRequest,
)
from src.services.webhooks.registry import get_adapter

logger = logging.getLogger(__name__)


async def resolve_webhook_source(
    db: AsyncSession,
    source_id: str,
) -> tuple[EventSource, WebhookSource] | None:
    """Look up the EventSource + WebhookSource for a given source_id.

    Returns None if the source doesn't exist, isn't active, or isn't a webhook source.
    """
    try:
        source_uuid = UUID(source_id)
    except ValueError:
        return None

    stmt = (
        sa.select(EventSource)
        .options(
            joinedload(EventSource.webhook_source).joinedload(
                WebhookSource.integration
            ),
        )
        .where(
            EventSource.id == source_uuid,
            EventSource.is_active.is_(True),
            EventSource.source_type == EventSourceType.WEBHOOK,
        )
    )
    result = await db.execute(stmt)
    event_source = result.unique().scalar_one_or_none()
    if not event_source or not event_source.webhook_source:
        return None
    return event_source, event_source.webhook_source


def _render_template(template: str, context: dict) -> Any:
    """
    Simple template rendering for {{ variable.path }} expressions.

    Supports dot-notation access into nested dictionaries.
    If the entire template is a single variable, returns the actual value (preserving type).
    For mixed content or multiple variables, returns a string with all variables substituted.

    Args:
        template: String containing {{ variable.path }} expressions
        context: Dictionary of available variables for substitution

    Returns:
        The resolved value (preserving type for single variables) or substituted string
    """

    def resolve_path(var_path: str, ctx: dict) -> tuple[Any, bool]:
        """Resolve a dot-notation path to its value. Returns (value, found)."""
        parts = var_path.split(".")
        value = ctx
        for part in parts:
            if isinstance(value, dict):
                if part in value:
                    value = value[part]
                else:
                    return None, False
            else:
                return None, False
        return value, True

    def replace_var(match: re.Match) -> str:
        """Replace a single {{ var }} match with its string value."""
        var_path = match.group(1).strip()
        value, found = resolve_path(var_path, context)
        if not found:
            return match.group(0)  # Keep original if can't resolve
        return str(value) if value is not None else ""

    # If entire template is just one variable, return the actual value (not stringified)
    # This preserves types like int, dict, list, etc.
    single_var = re.match(r"^\{\{\s*([^}]+)\s*\}\}$", template)
    if single_var:
        var_path = single_var.group(1).strip()
        value, found = resolve_path(var_path, context)
        if found:
            return value
        return template  # Keep original if can't resolve

    # Multiple variables or mixed content - substitute all as strings
    return re.sub(r"\{\{\s*([^}]+)\s*\}\}", replace_var, template)


def _process_input_mapping(
    input_mapping: dict,
    event: Event,
    subscription: EventSubscription,
) -> dict:
    """
    Process input_mapping templates to build workflow parameters.

    Supports:
    - Static values: {"report_type": "daily"}
    - Template expressions: {"as_of_date": "{{ scheduled_time }}", "user_email": "{{ payload.user.email }}"}

    Available template variables:
    - `scheduled_time` - ISO timestamp when schedule fired (for schedule events)
    - `cron_expression` - The cron expression that triggered (for schedule events)
    - `payload` - Full event payload
    - `headers` - Request headers (for webhooks)

    Args:
        input_mapping: Dictionary mapping output keys to values or template expressions
        event: The Event being processed
        subscription: The EventSubscription with context

    Returns:
        Dictionary of processed workflow parameters
    """
    # Build context for template substitution
    context: dict[str, Any] = {
        "payload": event.data,
        "headers": event.headers or {},
        "scheduled_time": (
            event.received_at.isoformat()
            if event.received_at
            else datetime.now(timezone.utc).isoformat()
        ),
    }

    # Get cron_expression from schedule source if available
    if (
        event.event_source
        and event.event_source.schedule_source
        and event.event_source.schedule_source.cron_expression
    ):
        context["cron_expression"] = event.event_source.schedule_source.cron_expression

    result: dict[str, Any] = {}
    for key, value in input_mapping.items():
        if isinstance(value, str) and "{{" in value:
            # Template expression - render it
            result[key] = _render_template(value, context)
        else:
            # Static value - pass through as-is
            result[key] = value

    return result


class EventProcessor:
    """
    Core event processor for webhook events.

    Handles the lifecycle of incoming webhook requests:
    - Adapter routing and request handling
    - Event logging
    - Subscription matching
    - Delivery tracking
    - Workflow execution queueing
    """

    def __init__(self, session: AsyncSession):
        self.session = session
        self._webhook_repo = WebhookSourceRepository(session)
        self._source_repo = EventSourceRepository(session)
        self._subscription_repo = EventSubscriptionRepository(session)
        self._event_repo = EventRepository(session)
        self._delivery_repo = EventDeliveryRepository(session)

    async def process_webhook(
        self,
        event_source: EventSource,
        webhook_source: WebhookSource,
        request: WebhookRequest,
    ) -> HandleResult:
        """
        Process an incoming webhook request.

        Args:
            event_source: The resolved EventSource ORM object
            webhook_source: The resolved WebhookSource ORM object
            request: The incoming webhook request data

        Returns:
            HandleResult indicating how to respond:
            - ValidationResponse: Return specific response (for handshakes)
            - Deliver: Event was accepted and will be processed
            - Rejected: Request was rejected (invalid signature, etc.)
        """
        source_id = str(event_source.id)

        # Get the adapter for this webhook
        adapter = get_adapter(webhook_source.adapter_name)
        if not adapter:
            logger.error(f"Adapter not found: {log_safe(webhook_source.adapter_name)}")
            return Rejected(
                message="Webhook adapter not configured",
                status_code=500,
            )

        # Let adapter handle the request
        config = webhook_source.config or {}
        state = webhook_source.state or {}

        try:
            result = await adapter.handle_request(request, config, state)
        except Exception as e:
            logger.error(f"Adapter error handling webhook: {e}", exc_info=True)
            return Rejected(
                message="Error processing webhook",
                status_code=500,
            )

        # Handle adapter result
        if isinstance(result, ValidationResponse):
            # Validation/handshake response - return directly without logging event
            logger.debug(f"Webhook validation response: {log_safe(source_id)}")
            return result

        if isinstance(result, Rejected):
            # Request rejected by adapter (invalid signature, etc.)
            logger.warning(f"Webhook rejected: {log_safe(source_id)} - {log_safe(result.message)}")
            return result

        if isinstance(result, Deliver):
            # Process the event
            return await self._process_delivery(
                webhook_source=webhook_source,
                event_source=event_source,
                deliver=result,
                request=request,
            )

        # Unknown result type
        logger.error(f"Unknown adapter result type: {type(result)}")
        return Rejected(
            message="Internal error",
            status_code=500,
        )

    async def emit_topic(
        self,
        *,
        topic: str,
        data: dict,
        organization_id: UUID | None = None,
        triggered_by: str | None = None,
    ) -> tuple[UUID, int]:
        """Emit a topic event. Returns (event_id, subscribers_notified).

        Looks up the topic EventSource by event_type, stamps Event.organization_id
        from the source row (or the explicit override), creates deliveries for all
        active subscriptions, and marks them PENDING for queue_event_deliveries.
        """
        validate_topic(topic)

        source = await self._source_repo.get_by_topic(topic)
        if source is None:
            logger.info(
                f"No active topic source for '{log_safe(topic)}'; emit is a no-op",
                extra={"topic": log_safe(topic), "triggered_by": triggered_by},
            )
            return uuid.uuid4(), 0

        stamped_org = organization_id if organization_id is not None else source.organization_id

        event = Event(
            id=uuid.uuid4(),
            event_source_id=source.id,
            event_type=topic,
            organization_id=stamped_org,
            received_at=datetime.now(timezone.utc),
            headers=None,
            data=data,
            source_ip=None,
            status=EventStatus.RECEIVED,
        )
        self.session.add(event)
        await self.session.flush()

        logger.info(
            f"Topic event emitted: {event.id} (topic={log_safe(topic)})",
            extra={
                "event_id": str(event.id),
                "topic": log_safe(topic),
                "triggered_by": triggered_by,
            },
        )

        subscriptions = await self._subscription_repo.get_active_for_event(
            source_id=source.id,
            event_type=topic,
        )

        if not subscriptions:
            event.status = EventStatus.COMPLETED
            await self.session.flush()
            logger.info(f"No subscriptions for topic '{log_safe(topic)}': {event.id}")
            return event.id, 0

        event.status = EventStatus.PROCESSING
        await self.session.flush()

        for subscription in subscriptions:
            target_type = getattr(subscription, "target_type", "workflow") or "workflow"
            if target_type == "agent":
                if not subscription.agent_id:
                    logger.warning(
                        f"Subscription {subscription.id} is agent type but has no agent_id, skipping"
                    )
                    continue
            else:
                if not subscription.workflow_id or not subscription.workflow:
                    logger.warning(
                        f"Subscription {subscription.id} has no workflow, skipping"
                    )
                    continue

            delivery = EventDelivery(
                id=uuid.uuid4(),
                event_id=event.id,
                event_subscription_id=subscription.id,
                workflow_id=subscription.workflow_id,
                status=EventDeliveryStatus.PENDING,
            )
            self.session.add(delivery)

        await self.session.flush()
        logger.info(
            f"Created deliveries for topic '{log_safe(topic)}': {event.id}",
            extra={
                "event_id": str(event.id),
                "subscription_count": len(subscriptions),
            },
        )
        return event.id, len(subscriptions)

    async def _process_delivery(
        self,
        webhook_source: WebhookSource,
        event_source: EventSource,
        deliver: Deliver,
        request: WebhookRequest,
    ) -> HandleResult:
        """
        Process a delivery request from an adapter.

        Creates event record, finds subscriptions, creates deliveries,
        and queues workflow executions.
        """
        # Create event record
        event = Event(
            id=uuid.uuid4(),
            event_source_id=event_source.id,
            event_type=deliver.event_type,
            received_at=datetime.now(timezone.utc),
            headers=deliver.raw_headers,
            data=deliver.data,
            source_ip=request.client_ip,
            status=EventStatus.RECEIVED,
        )
        self.session.add(event)
        await self.session.flush()

        logger.info(
            f"Event received: {event.id}",
            extra={
                "event_id": str(event.id),
                "event_source_id": str(event_source.id),
                "event_type": deliver.event_type,
            },
        )

        # Broadcast event_created to WebSocket subscribers
        await self._broadcast_event_update(
            event_source_id=event_source.id,
            event=event,
            update_type="event_created",
        )

        # Find active subscriptions that match this event
        subscriptions = await self._subscription_repo.get_active_for_event(
            source_id=event_source.id,
            event_type=deliver.event_type,
        )

        if not subscriptions:
            # No subscriptions - mark event as completed (nothing to deliver)
            event.status = EventStatus.COMPLETED
            await self.session.flush()
            logger.info(f"No subscriptions for event: {event.id}")
            return Deliver(
                data=deliver.data,
                event_type=deliver.event_type,
            )

        # Create deliveries and queue executions
        event.status = EventStatus.PROCESSING
        await self.session.flush()

        deliveries_created = 0
        for subscription in subscriptions:
            target_type = getattr(subscription, "target_type", "workflow") or "workflow"

            if target_type == "agent":
                if not subscription.agent_id:
                    logger.warning(
                        f"Subscription {subscription.id} is agent type but has no agent_id, skipping"
                    )
                    continue
            else:
                if not subscription.workflow_id or not subscription.workflow:
                    logger.warning(
                        f"Subscription {subscription.id} has no workflow, skipping"
                    )
                    continue

            # Create delivery record
            delivery = EventDelivery(
                id=uuid.uuid4(),
                event_id=event.id,
                event_subscription_id=subscription.id,
                workflow_id=subscription.workflow_id,  # None for agent targets
                status=EventDeliveryStatus.PENDING,
            )
            self.session.add(delivery)
            deliveries_created += 1

        await self.session.flush()

        logger.info(
            f"Created {deliveries_created} deliveries for event: {event.id}",
            extra={
                "event_id": str(event.id),
                "delivery_count": deliveries_created,
            },
        )

        # Return success - actual execution happens after commit
        return Deliver(
            data=deliver.data,
            event_type=deliver.event_type,
        )

    async def _broadcast_event_update(
        self,
        event_source_id: uuid.UUID,
        event: Event,
        update_type: str,
        success_count: int = 0,
        failed_count: int = 0,
        queued_count: int = 0,
        pending_count: int = 0,
    ) -> None:
        """
        Broadcast event update to WebSocket subscribers.

        Args:
            event_source_id: The event source UUID
            event: The Event model
            update_type: Type of update (event_created, event_updated, deliveries_queued)
            success_count: Number of successful deliveries
            failed_count: Number of failed deliveries
            queued_count: Number of queued deliveries
            pending_count: Number of pending deliveries
        """
        from src.core.pubsub import manager

        channel = f"event-source:{event_source_id}"

        # Build event payload
        message = {
            "type": update_type,
            "event": {
                "id": str(event.id),
                "event_source_id": str(event.event_source_id),
                "event_type": event.event_type,
                "status": getattr(event.status, "value", event.status),
                "received_at": event.received_at.isoformat() if event.received_at else None,
                "source_ip": event.source_ip,
                "success_count": success_count,
                "failed_count": failed_count,
                "queued_count": queued_count,
                "pending_count": pending_count,
                "delivery_count": success_count + failed_count + queued_count + pending_count,
            },
        }

        try:
            await manager.broadcast(channel, message)
            logger.debug(f"Broadcast {update_type} to channel {channel}")
        except Exception as e:
            # Don't fail event processing if broadcast fails
            logger.warning(f"Failed to broadcast event update: {e}")

    async def queue_event_deliveries(self, event_id: uuid.UUID) -> int:
        """
        Queue workflow executions for all pending deliveries of an event.

        This should be called after the transaction commits to ensure
        delivery records exist before queueing.

        Args:
            event_id: Event UUID

        Returns:
            Number of deliveries queued
        """
        # Get all pending deliveries for this event
        deliveries = await self._delivery_repo.get_by_event(event_id)

        # Get the event data (once, outside the loop)
        event_obj = await self._event_repo.get_by_id(event_id)
        if not event_obj:
            logger.error(f"Event not found when queueing deliveries: {log_safe(event_id)}")
            return 0

        queued = 0
        for delivery in deliveries:
            if delivery.status != EventDeliveryStatus.PENDING:
                continue

            try:
                subscription = delivery.subscription
                if subscription and subscription.target_type == "agent":
                    await self._queue_agent_run(delivery, event_obj)
                else:
                    await self._queue_workflow_execution(delivery, event_obj)
                delivery.status = EventDeliveryStatus.QUEUED
                queued += 1
            except Exception as e:
                logger.error(
                    f"Failed to queue delivery {delivery.id}: {e}",
                    exc_info=True,
                )
                delivery.status = EventDeliveryStatus.FAILED
                delivery.error_message = str(e)

        await self.session.flush()

        # Broadcast update after queueing (use already-loaded deliveries)
        success_count = sum(
            1 for d in deliveries if d.status == EventDeliveryStatus.SUCCESS
        )
        failed_count = sum(
            1 for d in deliveries if d.status == EventDeliveryStatus.FAILED
        )
        queued_count = sum(
            1 for d in deliveries if d.status == EventDeliveryStatus.QUEUED
        )
        pending_count = sum(
            1 for d in deliveries if d.status == EventDeliveryStatus.PENDING
        )

        await self._broadcast_event_update(
            event_source_id=event_obj.event_source_id,
            event=event_obj,
            update_type="deliveries_queued",
            success_count=success_count,
            failed_count=failed_count,
            queued_count=queued_count,
            pending_count=pending_count,
        )

        return queued

    async def _queue_workflow_execution(
        self,
        delivery: EventDelivery,
        event: Event,
    ) -> None:
        """
        Queue a workflow execution for an event delivery.

        Uses the same execution infrastructure as the rest of the platform.

        If the subscription has an input_mapping defined, it is processed to build
        workflow parameters using template substitution. Otherwise, the raw event
        data is used as parameters (legacy behavior).
        """
        from src.services.execution.async_executor import enqueue_system_workflow_execution

        # Get workflow details
        workflow = delivery.workflow
        if not workflow:
            raise ValueError(f"Delivery {delivery.id} has no workflow")

        # Get subscription for input_mapping
        subscription = delivery.subscription

        # Build parameters for workflow
        parameters: dict[str, Any] = {}

        if subscription and subscription.input_mapping:
            # Use input_mapping to build parameters via template substitution
            parameters = _process_input_mapping(
                input_mapping=subscription.input_mapping,
                event=event,
                subscription=subscription,
            )
            logger.debug(
                "Built workflow parameters from input_mapping",
                extra={
                    "delivery_id": str(delivery.id),
                    "input_mapping_keys": list(subscription.input_mapping.keys()),
                    "result_keys": list(parameters.keys()),
                },
            )
        else:
            # Legacy behavior: extract body fields as flat params
            if isinstance(event.data, dict):
                parameters.update(event.data)

        # Always include full event context under reserved key
        # This includes the COMPLETE raw body for complex/non-dict payloads
        parameters["_event"] = {
            "id": str(event.id),
            "type": event.event_type,
            "body": event.data,  # Full raw body (dict, list, string, whatever)
            "headers": event.headers,
            "received_at": event.received_at.isoformat() if event.received_at else None,
            "source_ip": event.source_ip,
        }

        from src.sdk.context import EventContext

        event_context = EventContext(
            id=str(event.id),
            type=event.event_type or "",
            data=event.data if isinstance(event.data, dict) else {},
            organization_id=str(event.organization_id) if getattr(event, "organization_id", None) else None,
            received_at=event.received_at.isoformat() if event.received_at else "",
        )

        # Use the centralized system execution helper
        # Use workflow's org_id so org-scoped workflows only access their org's data
        execution_id = await enqueue_system_workflow_execution(
            workflow_id=str(workflow.id),
            parameters=parameters,
            source="Event System",
            org_id=str(workflow.organization_id) if workflow.organization_id else None,
            event=event_context,
        )

        # Store the execution ID on the delivery for tracking
        delivery.execution_id = uuid.UUID(execution_id)

        logger.info(
            "Queued workflow execution for event delivery",
            extra={
                "execution_id": execution_id,
                "delivery_id": str(delivery.id),
                "workflow_id": str(workflow.id),
                "event_id": str(event.id),
            },
        )

    async def _queue_agent_run(
        self,
        delivery: EventDelivery,
        event: Event,
    ) -> None:
        """Queue an agent run for an event delivery targeting an agent."""
        from src.services.execution.agent_run_service import enqueue_agent_run

        subscription = delivery.subscription
        agent = subscription.agent

        if not agent:
            raise ValueError(f"Delivery {delivery.id} subscription has no agent")

        # Build parameters from input mapping or raw event data
        parameters: dict[str, Any] = {}
        if subscription.input_mapping:
            parameters = _process_input_mapping(
                input_mapping=subscription.input_mapping,
                event=event,
                subscription=subscription,
            )
        else:
            if isinstance(event.data, dict):
                parameters.update(event.data)

        # Include event context
        parameters["_event"] = {
            "id": str(event.id),
            "type": event.event_type,
            "body": event.data,
            "headers": event.headers,
            "received_at": event.received_at.isoformat() if event.received_at else None,
            "source_ip": event.source_ip,
        }

        org_id = str(agent.organization_id) if agent.organization_id else None

        run_id = await enqueue_agent_run(
            agent_id=str(agent.id),
            trigger_type="event",
            trigger_source=f"event: {event.event_type or 'webhook'}",
            input_data=parameters,
            org_id=org_id,
            event_delivery_id=str(delivery.id),
        )

        # agent_run_id will be set by the consumer after creating the AgentRun record

        logger.info(
            "Queued agent run for event delivery",
            extra={
                "run_id": run_id,
                "delivery_id": str(delivery.id),
                "agent_id": str(agent.id),
                "event_id": str(event.id),
            },
        )



async def update_delivery_from_execution(
    execution_id: str,
    status: str,
    error_message: str | None = None,
    session: AsyncSession | None = None,
) -> None:
    """
    Update EventDelivery status based on workflow execution completion.

    Called by the workflow execution consumer when an execution completes.
    Looks up the EventDelivery by execution_id and updates its status.

    Args:
        execution_id: The workflow execution ID
        status: The execution status (Success, Failed, Timeout, etc.)
        error_message: Error message if failed
        session: Optional database session. If provided, uses it and
                 caller is responsible for commit. If None, creates own session.
    """
    from sqlalchemy import select

    # Map execution status to delivery status
    status_map = {
        "Success": EventDeliveryStatus.SUCCESS,
        "Failed": EventDeliveryStatus.FAILED,
        "Timeout": EventDeliveryStatus.FAILED,
        "Cancelled": EventDeliveryStatus.FAILED,
    }

    delivery_status = status_map.get(status, EventDeliveryStatus.FAILED)

    async def _do_update(db: AsyncSession) -> None:
        # Find delivery by execution_id
        result = await db.execute(
            select(EventDelivery).where(
                EventDelivery.execution_id == uuid.UUID(execution_id)
            )
        )
        delivery = result.scalar_one_or_none()

        if not delivery:
            # Not an event-triggered execution, nothing to update
            return

        # Update delivery status
        delivery.status = delivery_status
        delivery.completed_at = datetime.now(timezone.utc)
        delivery.attempt_count += 1

        if error_message:
            delivery.error_message = error_message

        await db.flush()

        # Update the parent event status
        delivery_repo = EventDeliveryRepository(db)
        await delivery_repo.update_event_status(delivery.event_id)

        logger.info(
            "Updated event delivery status",
            extra={
                "delivery_id": str(delivery.id),
                "execution_id": execution_id,
                "status": delivery_status.value,
            },
        )

        # Broadcast event status update to WebSocket subscribers
        event = await EventRepository(db).get_by_id(delivery.event_id)
        if event and event.event_source:
            from src.core.pubsub import manager

            # Get delivery counts for this event
            deliveries = await delivery_repo.get_by_event(delivery.event_id)
            success_count = sum(1 for d in deliveries if d.status == EventDeliveryStatus.SUCCESS)
            failed_count = sum(1 for d in deliveries if d.status == EventDeliveryStatus.FAILED)

            channel = f"event-source:{event.event_source_id}"
            message = {
                "type": "event_updated",
                "event": {
                    "id": str(event.id),
                    "event_source_id": str(event.event_source_id),
                    "event_type": event.event_type,
                    "status": getattr(event.status, "value", event.status),
                    "received_at": event.received_at.isoformat() if event.received_at else None,
                    "source_ip": event.source_ip,
                    "success_count": success_count,
                    "failed_count": failed_count,
                    "delivery_count": len(deliveries),
                },
            }

            try:
                await manager.broadcast(channel, message)
                logger.debug(f"Broadcast event_updated for {event.id}")
            except Exception as e:
                logger.warning(f"Failed to broadcast event update: {e}")

    if session is not None:
        # Use provided session (caller manages commit)
        await _do_update(session)
    else:
        # Create own session
        from src.core.database import get_session_factory

        session_factory = get_session_factory()
        async with session_factory() as db:
            await _do_update(db)
            await db.commit()
