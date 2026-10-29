"""
CRON Scheduler

Processes schedule event sources based on their CRON expressions.
Replaces the Azure Timer trigger version with APScheduler cron job.

Checks each ScheduleSource and fires events for matching schedules,
creating Event records and queuing deliveries for subscribed workflows.

Occurrence semantics survive trigger-leader failover:

- Each run scans at least its own cadence (``DEFAULT_SCAN_WINDOW``), extended
  back to the previous successful scan (a durable cursor on the
  ``schedule_processor`` task state) and capped at ``MAX_CATCH_UP``. An
  occurrence missed while no leader was running fires once on the next scan;
  several missed occurrences of one source coalesce into the latest. A cursor
  written by a leader whose clock runs ahead never narrows the window.
- Every fired occurrence carries a unique ``occurrence_key`` (source + UTC
  occurrence time), so two overlapping leaders cannot fire it twice. An
  unkeyed event at the same instant (fired by an older image during a
  mixed-version rollout or rollback) also counts as fired.
- Occurrences are computed in the source's timezone and keyed in UTC, so DST
  transitions neither merge nor duplicate distinct instants.
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import sqlalchemy as sa
from croniter import croniter
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from src.core.database import get_db_context
from src.models.enums import EventDeliveryStatus, EventSourceType, EventStatus, ScheduleOverlapPolicy
from src.models.orm.events import Event, EventDelivery, EventSource
from src.models.orm.scheduler_diagnostics import SchedulerTaskState
from src.repositories.events import EventSubscriptionRepository
from src.services.cron_parser import get_schedule_zone

logger = logging.getLogger(__name__)

SCHEDULE_PROCESSOR_TASK_ID = "schedule_processor"
# Window scanned when no previous scan is recorded (the processor's cadence).
DEFAULT_SCAN_WINDOW = timedelta(seconds=60)
# Longest outage whose missed occurrences still fire; matches the trigger
# misfire grace so a delayed run and a failover behave the same way.
MAX_CATCH_UP = timedelta(minutes=10)


def _next_interval_seconds(cron_expression: str, now_utc: datetime, timezone_name: str) -> float:
    zone = get_schedule_zone(timezone_name)
    local_now = now_utc.astimezone(zone)
    cron = croniter(cron_expression, local_now)
    first_run = cron.get_next(datetime)
    second_run = cron.get_next(datetime)
    return (
        second_run.astimezone(timezone.utc)
        - first_run.astimezone(timezone.utc)
    ).total_seconds()


def _latest_due_run_utc(
    cron_expression: str,
    now_utc: datetime,
    timezone_name: str,
    window_start: datetime,
) -> datetime | None:
    """Latest occurrence in ``(window_start, now_utc]``, coalescing earlier ones."""
    zone = get_schedule_zone(timezone_name)
    local_now = now_utc.astimezone(zone)
    cron_iter = croniter(cron_expression, local_now)
    prev_run = cron_iter.get_prev(datetime)
    prev_run_utc = prev_run.astimezone(timezone.utc)
    if prev_run_utc <= window_start:
        return None
    return prev_run_utc


def occurrence_key(source_id: uuid.UUID, scheduled_at_utc: datetime) -> str:
    return f"schedule:{source_id}:{scheduled_at_utc.astimezone(timezone.utc).isoformat()}"


async def _scan_window_start(db: AsyncSession, now_utc: datetime) -> datetime:
    cursor = await db.scalar(
        select(SchedulerTaskState.cursor_at).where(
            SchedulerTaskState.task_id == SCHEDULE_PROCESSOR_TASK_ID
        )
    )
    window_start = now_utc - DEFAULT_SCAN_WINDOW
    if cursor is not None:
        window_start = min(window_start, cursor)
    return max(window_start, now_utc - MAX_CATCH_UP)


async def _advance_scan_cursor(db: AsyncSession, now_utc: datetime) -> None:
    await db.execute(
        update(SchedulerTaskState)
        .where(
            SchedulerTaskState.task_id == SCHEDULE_PROCESSOR_TASK_ID,
            sa.or_(
                SchedulerTaskState.cursor_at.is_(None),
                SchedulerTaskState.cursor_at < now_utc,
            ),
        )
        .values(cursor_at=now_utc)
    )


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
            now_utc = datetime.now(timezone.utc)
            window_start = await _scan_window_start(db, now_utc)

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
                        interval_seconds = _next_interval_seconds(
                            cron_expression,
                            now_utc,
                            ss.timezone,
                        )

                        if interval_seconds < 300:  # Less than 5 minutes
                            logger.warning(
                                f"Schedule interval for source {source.name} is "
                                f"{interval_seconds}s (< 5 minutes)"
                            )
                    except Exception as e:
                        logger.error(
                            f"Failed to validate schedule interval for source {source.id}: {e}"
                        )

                    # Fire the most recent occurrence since the previous scan.
                    try:
                        scheduled_at_utc = _latest_due_run_utc(
                            cron_expression,
                            now_utc,
                            ss.timezone,
                            window_start,
                        )
                    except ValueError as e:
                        logger.warning(
                            f"Invalid timezone for schedule source {source.id}: {ss.timezone}"
                        )
                        results["errors"].append({
                            "source_id": str(source.id),
                            "source_name": source.name,
                            "error": str(e),
                        })
                        continue

                    if scheduled_at_utc is None:
                        continue  # No occurrence since the previous scan

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

                    # A leader running an older image fires occurrences
                    # without a key, which the unique index cannot see.
                    if await db.scalar(
                        sa.select(sa.exists().where(
                            Event.event_source_id == source.id,
                            Event.event_type == "schedule.fired",
                            Event.received_at == scheduled_at_utc,
                            Event.occurrence_key.is_(None),
                        ))
                    ):
                        results["duplicates_skipped"] = (
                            results.get("duplicates_skipped", 0) + 1
                        )
                        continue

                    # Claim the occurrence. A conflict means another leader
                    # (or an earlier scan) already fired it.
                    event_id = await db.scalar(
                        insert(Event)
                        .values(
                            id=uuid.uuid4(),
                            event_source_id=source.id,
                            event_type="schedule.fired",
                            received_at=scheduled_at_utc,
                            occurrence_key=occurrence_key(source.id, scheduled_at_utc),
                            data={
                                "cron_expression": cron_expression,
                                "timezone": ss.timezone,
                                "scheduled_time": scheduled_at_utc.isoformat(),
                            },
                            status=EventStatus.PROCESSING,
                        )
                        .on_conflict_do_nothing(
                            index_elements=[Event.occurrence_key],
                            index_where=Event.occurrence_key.is_not(None),
                        )
                        .returning(Event.id)
                    )
                    if event_id is None:
                        results["duplicates_skipped"] = (
                            results.get("duplicates_skipped", 0) + 1
                        )
                        continue
                    event = await db.get(Event, event_id)
                    if event is None:
                        raise RuntimeError("Inserted schedule event was not readable")
                    logger.info(f"Firing schedule source: {source.name} ({source.id})")
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

                    # Each delivery's run commits with this source's event and
                    # the delivery's record of it, in one transaction per
                    # source. If queueing fails outright, the event is still
                    # recorded with pending deliveries before the error is
                    # reported for this source.
                    from src.services.events.processor import EventProcessor

                    processor = EventProcessor(db)
                    try:
                        async with db.begin_nested():
                            queued = await processor.queue_event_deliveries(event.id)
                    except Exception:
                        await db.commit()
                        raise
                    await db.commit()
                    await queued.hand_off()
                    results["deliveries_queued"] += queued.count

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

            await _advance_scan_cursor(db, now_utc)
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
