"""
CRON Scheduler

Processes schedule event sources based on their CRON expressions.
Replaces the Azure Timer trigger version with APScheduler cron job.

Checks each ScheduleSource and fires events for matching schedules,
creating Event records and queuing deliveries for subscribed workflows.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import sqlalchemy as sa
from croniter import croniter
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from src.core.database import get_db_context
from src.models.enums import EventDeliveryStatus, EventSourceType, EventStatus, ScheduleOverlapPolicy
from src.models.orm.events import Event, EventDelivery, EventSource
from src.repositories.events import EventSubscriptionRepository

logger = logging.getLogger(__name__)


async def process_schedule_sources() -> dict[str, Any]:
    """
    Process schedule event sources.

    Checks each ScheduleSource and fires events for matching schedules.
    Creates Event records and queues deliveries for subscribed workflows.

    Returns:
        Summary of processing results
    """
    from src.services.cron_parser import is_cron_expression_valid

    logger.info("Schedule sources processor started")

    results: dict[str, Any] = {
        "total_sources": 0,
        "events_created": 0,
        "deliveries_queued": 0,
        "errors": [],
    }

    try:
        async with get_db_context() as db:
            # Query active schedule sources
            query = (
                select(EventSource)
                .options(
                    joinedload(EventSource.schedule_source),
                    joinedload(EventSource.subscriptions),
                )
                .where(
                    EventSource.source_type == EventSourceType.SCHEDULE,
                    EventSource.is_active.is_(True),
                )
            )
            result = await db.execute(query)
            sources = result.unique().scalars().all()

            results["total_sources"] = len(sources)
            now = datetime.now(timezone.utc)

            for source in sources:
                try:
                    if not source.schedule_source or not source.schedule_source.enabled:
                        continue

                    ss = source.schedule_source
                    cron_expression = ss.cron_expression

                    # Validate CRON expression
                    if not is_cron_expression_valid(cron_expression):
                        logger.warning(
                            f"Invalid cron for schedule source {source.id}: {cron_expression}"
                        )
                        results["errors"].append({
                            "source_id": str(source.id),
                            "source_name": source.name,
                            "error": f"Invalid CRON expression: {cron_expression}",
                        })
                        continue

                    # Check if schedule interval is too frequent
                    try:
                        cron = croniter(cron_expression, now)
                        first_run = cron.get_next(datetime)
                        second_run = cron.get_next(datetime)
                        interval_seconds = (second_run - first_run).total_seconds()

                        if interval_seconds < 300:  # Less than 5 minutes
                            logger.warning(
                                f"Schedule interval for source {source.name} is "
                                f"{interval_seconds}s (< 5 minutes)"
                            )
                    except Exception as e:
                        logger.error(
                            f"Failed to validate schedule interval for source {source.id}: {e}"
                        )

                    # Check if the most recent cron match is within our polling window.
                    # We poll every 1 minute, so check if the last match was < 60s ago.
                    cron_iter = croniter(cron_expression, now)
                    prev_run = cron_iter.get_prev(datetime)
                    seconds_since_last = (now - prev_run).total_seconds()
                    if seconds_since_last >= 60:
                        continue  # Last match was outside our polling window

                    # Check overlap policy: skip if a prior delivery is still active.
                    # Query EventDelivery.status directly — covers both workflow-target
                    # (execution_id set) and agent-target (agent_run_id set) subscriptions,
                    # and catches deliveries that have been queued before their downstream
                    # Execution / AgentRun row has materialized.
                    overlap_policy = ss.overlap_policy
                    active_count = await db.scalar(
                        sa.select(sa.func.count(EventDelivery.id))
                        .join(Event, Event.id == EventDelivery.event_id)
                        .where(
                            Event.event_source_id == source.id,
                            EventDelivery.status.in_([
                                EventDeliveryStatus.PENDING,
                                EventDeliveryStatus.QUEUED,
                            ]),
                        )
                    )
                    if active_count and active_count > 0:
                        if overlap_policy != ScheduleOverlapPolicy.SKIP:
                            logger.warning(
                                "schedule_overlap_policy_not_implemented",
                                extra={
                                    "schedule_id": str(source.id),
                                    "schedule_name": source.name,
                                    "policy": str(overlap_policy),
                                    "behavior": "treated as SKIP for v1",
                                },
                            )
                        logger.info(
                            "schedule_skipped_overlap",
                            extra={
                                "schedule_id": str(source.id),
                                "schedule_name": source.name,
                                "active_executions": active_count,
                            },
                        )
                        results["skipped_overlap"] = results.get("skipped_overlap", 0) + 1
                        continue

                    logger.info(f"Firing schedule source: {source.name} ({source.id})")

                    # Create event record
                    event = Event(
                        id=uuid.uuid4(),
                        event_source_id=source.id,
                        event_type="schedule.fired",
                        received_at=now,
                        data={
                            "cron_expression": cron_expression,
                            "timezone": ss.timezone,
                            "scheduled_time": now.isoformat(),
                        },
                        status=EventStatus.PROCESSING,
                    )
                    db.add(event)
                    await db.flush()
                    results["events_created"] += 1

                    # Get active subscriptions for this source
                    sub_repo = EventSubscriptionRepository(db)
                    subscriptions = await sub_repo.get_active_for_event(
                        source_id=source.id,
                        event_type=None,  # Match all subscriptions for schedule events
                    )

                    if not subscriptions:
                        # No subscriptions - mark event as completed (nothing to deliver)
                        event.status = EventStatus.COMPLETED
                        await db.flush()
                        logger.info(f"No subscriptions for schedule source: {source.id}")
                        continue

                    # Create deliveries for each subscription
                    deliveries_for_event = 0
                    for sub in subscriptions:
                        target_type = getattr(sub, "target_type", "workflow") or "workflow"

                        if target_type == "agent":
                            if not sub.agent_id:
                                logger.warning(
                                    f"Subscription {sub.id} is agent type but has no agent_id, skipping"
                                )
                                continue
                        else:
                            if not sub.workflow_id:
                                logger.warning(
                                    f"Subscription {sub.id} has no workflow, skipping"
                                )
                                continue

                        delivery = EventDelivery(
                            id=uuid.uuid4(),
                            event_id=event.id,
                            event_subscription_id=sub.id,
                            workflow_id=sub.workflow_id,  # None for agent targets
                            status=EventDeliveryStatus.PENDING,
                        )
                        db.add(delivery)
                        deliveries_for_event += 1

                    await db.flush()

                    logger.info(
                        f"Created {deliveries_for_event} deliveries for schedule event: {event.id}"
                    )

                    # Queue the deliveries using the event processor
                    from src.services.events.processor import EventProcessor

                    processor = EventProcessor(db)
                    queued = await processor.queue_event_deliveries(event.id)
                    results["deliveries_queued"] += queued

                    # Source has been processed and its deliveries queued (if any).
                    event.status = EventStatus.COMPLETED

                except Exception as source_error:
                    error_info = {
                        "source_id": str(source.id),
                        "source_name": source.name,
                        "error": str(source_error),
                    }
                    results["errors"].append(error_info)
                    logger.error(
                        "Error processing schedule source",
                        extra=error_info,
                        exc_info=True,
                    )

            await db.commit()

    except Exception as e:
        logger.error(f"Schedule sources processor failed: {e}", exc_info=True)
        results["errors"].append({"error": str(e)})

    logger.info(
        f"Schedule sources processor completed: "
        f"Sources={results['total_sources']}, "
        f"Events={results['events_created']}, "
        f"Deliveries={results['deliveries_queued']}, "
        f"Errors={len(results['errors'])}"
    )

    return results
