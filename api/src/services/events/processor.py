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

import base64
import logging
import re
import uuid
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from shared.event_filters import EventFilterDecision, evaluate_event_filter
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from src.core.error_messages import format_exception_message
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


@dataclass
class QueuedDeliveries:
    """Runs accepted in the caller's transaction, to hand off once it commits."""

    hand_offs: list[Callable[[], Awaitable[object]]] = field(default_factory=list)
    broadcast: Callable[[], Awaitable[None]] | None = None

    @property
    def count(self) -> int:
        return len(self.hand_offs)

    async def hand_off(self) -> None:
        """After commit; best-effort, since the reconciler republishes a lost one."""
        for hand_off in self.hand_offs:
            try:
                await hand_off()
            except Exception:
                logger.warning("Event delivery hand-off failed; the reconciler will retry", exc_info=True)
        if self.broadcast is not None:
            await self.broadcast()


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

    @staticmethod
    def _evaluate_subscription_filter(
        subscription: EventSubscription,
        payload: dict[str, Any],
        event_id: uuid.UUID,
    ) -> EventFilterDecision:
        """Evaluate and log the dispatch decision for one subscription."""
        decision = evaluate_event_filter(subscription.filter_expression, payload)
        if decision.matches:
            return decision

        log_context = {
            "event_id": str(event_id),
            "subscription_id": str(subscription.id),
        }
        if decision.error:
            logger.warning(
                "Event subscription filter evaluation failed; delivery skipped",
                extra={**log_context, "filter_error": log_safe(decision.error)},
            )
        else:
            logger.info(
                "Event subscription filter did not match; delivery skipped",
                extra=log_context,
            )
        return decision

    def _build_subscription_delivery(
        self,
        event: Event,
        subscription: EventSubscription,
    ) -> tuple[EventDelivery, bool]:
        """Build a pending or observably skipped delivery for a subscription."""
        source_workflow = (
            event.data.get("workflow") if isinstance(event.data, dict) else None
        )
        if (
            event.event_type == "workflow.completed"
            and isinstance(source_workflow, dict)
            and source_workflow.get("id") is not None
            and (getattr(subscription, "target_type", "workflow") or "workflow")
            == "workflow"
            and subscription.workflow_id is not None
            and str(source_workflow["id"]) == str(subscription.workflow_id)
        ):
            return (
                EventDelivery(
                    id=uuid.uuid4(),
                    event_id=event.id,
                    event_subscription_id=subscription.id,
                    workflow_id=subscription.workflow_id,
                    status=EventDeliveryStatus.SKIPPED,
                    error_message="Workflow completion event cannot trigger the same workflow",
                    completed_at=datetime.now(timezone.utc),
                ),
                False,
            )

        filter_decision = self._evaluate_subscription_filter(
            subscription,
            event.data,
            event.id,
        )
        if filter_decision.matches:
            status = EventDeliveryStatus.PENDING
            error_message = None
            completed_at = None
        else:
            status = EventDeliveryStatus.SKIPPED
            error_message = (
                "Subscription filter did not match the event payload"
                if filter_decision.error is None
                else "Subscription filter could not be evaluated"
            )
            completed_at = datetime.now(timezone.utc)

        return (
            EventDelivery(
                id=uuid.uuid4(),
                event_id=event.id,
                event_subscription_id=subscription.id,
                workflow_id=subscription.workflow_id,
                status=status,
                error_message=error_message,
                completed_at=completed_at,
            ),
            filter_decision.matches,
        )

    async def retry_delivery(self, delivery: EventDelivery) -> tuple[str, QueuedDeliveries]:
        """Reset and queue one failed delivery as a new timed attempt.

        Only this delivery is queued. Commit, then hand off the result.
        """
        delivery.status = EventDeliveryStatus.PENDING
        delivery.error_message = None
        delivery.execution_id = None
        delivery.agent_run_id = None
        delivery.completed_at = None
        delivery.attempt_started_at = datetime.now(timezone.utc)
        await self.session.flush()

        try:
            queued = await self.queue_event_deliveries(
                delivery.event_id, delivery_ids={delivery.id}
            )
            if delivery.status == EventDeliveryStatus.FAILED:
                delivery.completed_at = datetime.now(timezone.utc)
                await self.session.flush()
                return f"Failed to queue retry: {delivery.error_message}", queued
            return "Delivery queued for retry", queued
        except Exception as exc:
            error_message = format_exception_message(exc, context="queueing event delivery retry")
            logger.error("Failed to queue retry: %s", error_message, exc_info=True)
            delivery.status = EventDeliveryStatus.FAILED
            delivery.error_message = error_message
            delivery.completed_at = datetime.now(timezone.utc)
            await self.session.flush()
            return f"Failed to queue retry: {error_message}", QueuedDeliveries()

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
        solution_id: UUID | None = None,
        triggered_by: str | None = None,
    ) -> tuple[UUID, int]:
        """Emit a topic event. Returns (event_id, subscribers_notified).

        Looks up the topic EventSource by event_type, stamps Event.organization_id
        from the source row (or the explicit override), creates deliveries for all
        active subscriptions, and marks them PENDING for queue_event_deliveries.
        """
        validate_topic(topic)

        source = await self._source_repo.get_by_topic(topic, solution_id=solution_id)
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

        deliveries_created = 0
        matching_subscriptions = 0
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

            delivery, filter_matches = self._build_subscription_delivery(
                event,
                subscription,
            )
            self.session.add(delivery)
            deliveries_created += 1
            if filter_matches:
                matching_subscriptions += 1

        event.status = (
            EventStatus.PROCESSING
            if matching_subscriptions
            else EventStatus.COMPLETED
        )
        await self.session.flush()
        logger.info(
            f"Created deliveries for topic '{log_safe(topic)}': {event.id}",
            extra={
                "event_id": str(event.id),
                "delivery_count": deliveries_created,
                "matching_subscription_count": matching_subscriptions,
                "skipped_subscription_count": deliveries_created
                - matching_subscriptions,
            },
        )
        return event.id, matching_subscriptions

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
            raw_body=request.body,
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
                event_id=event.id,
            )

        deliveries_created = 0
        matching_subscriptions = 0
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

            delivery, filter_matches = self._build_subscription_delivery(
                event,
                subscription,
            )
            self.session.add(delivery)
            deliveries_created += 1
            if filter_matches:
                matching_subscriptions += 1

        event.status = (
            EventStatus.PROCESSING
            if matching_subscriptions
            else EventStatus.COMPLETED
        )
        await self.session.flush()

        logger.info(
            f"Created {deliveries_created} deliveries for event: {event.id}",
            extra={
                "event_id": str(event.id),
                "delivery_count": deliveries_created,
                "matching_subscription_count": matching_subscriptions,
                "skipped_subscription_count": deliveries_created
                - matching_subscriptions,
            },
        )

        # Return success - actual execution happens after commit
        return Deliver(
            data=deliver.data,
            event_type=deliver.event_type,
            event_id=event.id,
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
        skipped_count: int = 0,
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
            skipped_count: Number of deliveries skipped by subscription filters
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
                "skipped_count": skipped_count,
                "delivery_count": success_count
                + failed_count
                + queued_count
                + pending_count
                + skipped_count,
            },
        }

        try:
            await manager.broadcast(channel, message)
            logger.debug(f"Broadcast {update_type} to channel {channel}")
        except Exception as e:
            # Don't fail event processing if broadcast fails
            logger.warning(f"Failed to broadcast event update: {e}")

    async def queue_event_deliveries(
        self,
        event_id: uuid.UUID,
        *,
        delivery_ids: Collection[uuid.UUID] | None = None,
    ) -> QueuedDeliveries:
        """Accept the runs for an event's pending deliveries, in this session.

        Each run is staged in the caller's transaction together with its
        delivery's ``QUEUED`` status and run id, so a crash can never leave a
        run without its delivery's record of it (or the reverse). One
        delivery that cannot be queued is marked ``FAILED`` without undoing
        the others. Commit, then call ``hand_off()`` on the result.

        ``delivery_ids`` limits queueing to those deliveries (a retry or a
        manual delivery), so other pending deliveries of the event are never
        queued twice.
        """
        batch = QueuedDeliveries()
        deliveries = await self._delivery_repo.get_by_event(event_id)

        # Get the event data (once, outside the loop)
        event_obj = await self._event_repo.get_by_id(event_id)
        if not event_obj:
            logger.error(f"Event not found when queueing deliveries: {log_safe(event_id)}")
            return batch

        for delivery in deliveries:
            if delivery.status != EventDeliveryStatus.PENDING:
                continue
            if delivery_ids is not None and delivery.id not in delivery_ids:
                continue

            subscription = delivery.subscription
            filter_decision = self._evaluate_subscription_filter(
                subscription,
                event_obj.data,
                event_obj.id,
            )
            if not filter_decision.matches:
                delivery.status = EventDeliveryStatus.SKIPPED
                delivery.error_message = (
                    "Subscription filter did not match the event payload"
                    if filter_decision.error is None
                    else "Subscription filter could not be evaluated"
                )
                delivery.completed_at = datetime.now(timezone.utc)
                continue

            try:
                async with self.session.begin_nested():
                    if subscription and subscription.target_type == "agent":
                        hand_off = await self._queue_agent_run(delivery, event_obj)
                    else:
                        hand_off = await self._queue_workflow_execution(delivery, event_obj)
                    delivery.status = EventDeliveryStatus.QUEUED
                    await self.session.flush()
                batch.hand_offs.append(hand_off)
            except Exception as e:
                error_message = format_exception_message(
                    e,
                    context="queueing event delivery",
                )
                logger.error(
                    f"Failed to queue delivery {delivery.id}: {error_message}",
                    exc_info=True,
                )
                delivery.status = EventDeliveryStatus.FAILED
                delivery.error_message = error_message

        await self.session.flush()
        await self._delivery_repo.update_event_status(event_id)

        counts = {
            status: sum(1 for d in deliveries if d.status == status)
            for status in (
                EventDeliveryStatus.SUCCESS,
                EventDeliveryStatus.FAILED,
                EventDeliveryStatus.QUEUED,
                EventDeliveryStatus.PENDING,
                EventDeliveryStatus.SKIPPED,
            )
        }
        event_source_id = event_obj.event_source_id

        async def broadcast() -> None:
            await self._broadcast_event_update(
                event_source_id=event_source_id,
                event=event_obj,
                update_type="deliveries_queued",
                success_count=counts[EventDeliveryStatus.SUCCESS],
                failed_count=counts[EventDeliveryStatus.FAILED],
                queued_count=counts[EventDeliveryStatus.QUEUED],
                pending_count=counts[EventDeliveryStatus.PENDING],
                skipped_count=counts[EventDeliveryStatus.SKIPPED],
            )

        batch.broadcast = broadcast
        return batch

    async def _queue_workflow_execution(
        self,
        delivery: EventDelivery,
        event: Event,
    ) -> Callable[[], Awaitable[object]]:
        """
        Stage a workflow run for an event delivery; return its hand-off.

        Uses the same execution infrastructure as the rest of the platform.

        If the subscription has an input_mapping defined, it is processed to build
        workflow parameters using template substitution. Otherwise, the parsed event
        payload is used as parameters (legacy behavior).
        """
        from shared.run_lineage import unattended_lineage
        from src.services.execution.async_executor import stage_system_workflow_execution
        from src.services.execution.workflow_dispatch import hand_off_workflow_run

        # Get workflow details
        workflow = delivery.workflow
        if not workflow:
            raise ValueError(f"Delivery {delivery.id} has no workflow")

        # Safety net: services cannot be one-shot targets. Creation paths
        # reject them, but rows converted after subscribing (or written around
        # the guards) must fail loudly here instead of executing as workflows.
        if workflow.type == "service":
            raise ValueError(
                f"Delivery {delivery.id} targets service '{workflow.name}' "
                "(type='service'), which cannot be executed one-shot. "
                "Remove the subscription or convert the workflow."
            )

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
            # Legacy behavior: extract parsed payload fields as flat parameters
            if isinstance(event.data, dict):
                parameters.update(event.data)

        # Always include parsed payload and original accepted bytes under the reserved key.
        parameters["_event"] = {
            "id": str(event.id),
            "type": event.event_type,
            "body": event.data,  # Parsed payload (dict, list, string, whatever)
            "raw_body_base64": (
                base64.b64encode(event.raw_body).decode("ascii")
                if event.raw_body is not None
                else None
            ),
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
        accepted = await stage_system_workflow_execution(
            self.session,
            workflow_id=str(workflow.id),
            parameters=parameters,
            org_id=str(workflow.organization_id) if workflow.organization_id else None,
            event=event_context,
            lineage=await unattended_lineage(self.session, workflow.id),
        )
        execution_id = accepted.pending["execution_id"]

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
        return lambda: hand_off_workflow_run(accepted)

    async def _queue_agent_run(
        self,
        delivery: EventDelivery,
        event: Event,
    ) -> Callable[[], Awaitable[object]]:
        """Stage an agent run for an event delivery; return its hand-off."""
        from shared.run_lineage import identity_lineage
        from src.services.authorization.agent_runtime import authorize_agent_execution
        from src.services.authorization.callers import load_human_caller
        from src.services.execution.agent_run_service import (
            hand_off_staged_agent_run,
            stage_agent_run_request,
        )

        subscription = delivery.subscription
        agent = subscription.agent

        if not agent:
            raise ValueError(f"Delivery {delivery.id} subscription has no agent")

        # Build parameters from input mapping or parsed event data
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
            "raw_body_base64": (
                base64.b64encode(event.raw_body).decode("ascii")
                if event.raw_body is not None
                else None
            ),
            "headers": event.headers,
            "received_at": event.received_at.isoformat() if event.received_at else None,
            "source_ip": event.source_ip,
        }

        org_id = str(agent.organization_id) if agent.organization_id else None
        lineage = await identity_lineage(self.session, agent.organization_id)
        caller = await load_human_caller(self.session, lineage.run_user_id)
        await authorize_agent_execution(self.session, caller, agent.id)

        staged = await stage_agent_run_request(
            self.session,
            agent_id=str(agent.id),
            trigger_type="event",
            trigger_source=f"event: {event.event_type or 'webhook'}",
            input_data=parameters,
            org_id=org_id,
            event_delivery_id=str(delivery.id),
            lineage=lineage,
        )

        run_id = staged.run_id
        delivery.agent_run_id = uuid.UUID(run_id)

        logger.info(
            "Queued agent run for event delivery",
            extra={
                "run_id": run_id,
                "delivery_id": str(delivery.id),
                "agent_id": str(agent.id),
                "event_id": str(event.id),
            },
        )
        return lambda: hand_off_staged_agent_run(staged)


async def update_delivery_from_execution(
    execution_id: str,
    status: str,
    error_message: str | None = None,
    session: AsyncSession | None = None,
) -> EventDelivery | None:
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
    # Map execution status to delivery status
    status_map = {
        "Success": EventDeliveryStatus.SUCCESS,
        "Failed": EventDeliveryStatus.FAILED,
        "Timeout": EventDeliveryStatus.FAILED,
        "Cancelled": EventDeliveryStatus.FAILED,
    }

    delivery_status = status_map.get(status, EventDeliveryStatus.FAILED)

    async def _do_update(db: AsyncSession) -> EventDelivery | None:
        delivery_repo = EventDeliveryRepository(db)
        delivery = await delivery_repo.complete_from_execution(
            uuid.UUID(execution_id),
            delivery_status,
            error_message,
        )

        if not delivery:
            return None

        # Update the parent event status
        await delivery_repo.update_event_status(delivery.event_id)

        logger.info(
            "Updated event delivery status",
            extra={
                "delivery_id": str(delivery.id),
                "execution_id": execution_id,
                "status": delivery_status.value,
            },
        )

        if delivery_status == EventDeliveryStatus.FAILED:
            event_obj = delivery.event or await EventRepository(db).get_by_id(delivery.event_id)
            subscription = delivery.subscription
            target_type = getattr(subscription, "target_type", "workflow") or "workflow"
            target_id = (
                getattr(subscription, "agent_id", None)
                if target_type == "agent"
                else delivery.workflow_id
            )
            from src.services.events.builtins import emit_event_delivery_retry_exhausted

            await emit_event_delivery_retry_exhausted(
                event_id=delivery.event_id,
                event_type=event_obj.event_type if event_obj else None,
                source_id=event_obj.event_source_id if event_obj else None,
                organization_id=event_obj.organization_id if event_obj else None,
                delivery_id=delivery.id,
                target_type=target_type,
                target_id=target_id,
                attempt=delivery.attempt_count,
                max_attempts=delivery.attempt_count,
                error_type="DeliveryError",
                error_message=error_message or "Delivery failed after all retry attempts.",
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

        return delivery

    if session is not None:
        # Use provided session (caller manages commit)
        return await _do_update(session)
    else:
        # Create own session
        from src.core.database import get_session_factory

        session_factory = get_session_factory()
        async with session_factory() as db:
            delivery = await _do_update(db)
            await db.commit()
            return delivery
