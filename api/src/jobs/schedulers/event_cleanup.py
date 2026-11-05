"""
Stuck Event Delivery Cleanup

Reconciles event deliveries whose active run is gone (every 5 minutes). Old
events are deleted by the run.retention platform job.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from src.core.database import get_db_context
from src.models.enums import EventDeliveryStatus, EventStatus
from src.models.orm.events import Event, EventDelivery
from src.repositories.events import EventDeliveryRepository

logger = logging.getLogger(__name__)

# Timeout for stuck deliveries (matches workflow execution timeout)
STUCK_DELIVERY_TIMEOUT_MINUTES = 5


async def cleanup_stuck_events() -> dict[str, Any]:
    """
    Reconcile stale deliveries after their owning run reaches a terminal state.

    Active execution and agent rows are intentionally left to their canonical
    timeout and recovery paths. A terminal run completes its delivery using
    the normal callback mapping; a delivery with no run is marked failed.

    Returns:
        Summary of cleanup results
    """
    start_time = datetime.now(timezone.utc)
    logger.info("▶ Stuck event cleanup starting")

    results: dict[str, Any] = {
        "timeout_minutes": STUCK_DELIVERY_TIMEOUT_MINUTES,
        "deliveries_failed": 0,
        "deliveries_reconciled": 0,
        "events_updated": 0,
        "stale_events_fixed": 0,
        "errors": [],
    }

    try:
        # Collect data for WebSocket broadcasts (published after session closes)
        broadcast_data: list[dict] = []

        async with get_db_context() as db:
            delivery_repo = EventDeliveryRepository(db)
            cutoff = datetime.now(timezone.utc) - timedelta(minutes=STUCK_DELIVERY_TIMEOUT_MINUTES)

            # Find stuck deliveries
            stuck_deliveries = await delivery_repo.get_stuck_deliveries(
                timeout_minutes=STUCK_DELIVERY_TIMEOUT_MINUTES
            )

            # Track unique events to update
            event_ids: set = set()
            broadcast_deliveries: list[EventDelivery] = []

            # Complete terminal runs whose callback was missed, or fail
            # deliveries that have no active run to own them.
            if stuck_deliveries:
                for delivery in stuck_deliveries:
                    if delivery.execution is not None:
                        # Reuse the execution callback so a recovered terminal
                        # failure emits the same built-in event and WebSocket
                        # update as a normal completion.
                        from src.services.events.processor import update_delivery_from_execution

                        updated = await update_delivery_from_execution(
                            str(delivery.execution.id),
                            delivery.execution.status.value,
                            error_message=delivery.execution.error_message,
                            session=db,
                        )
                        if updated is not None:
                            results["deliveries_reconciled"] += 1
                    elif delivery.agent_run is not None:
                        delivery_status = (
                            EventDeliveryStatus.SUCCESS
                            if delivery.agent_run.status == "completed"
                            else EventDeliveryStatus.FAILED
                        )
                        updated = await delivery_repo.complete_from_agent_run(
                            delivery.id,
                            delivery.agent_run.id,
                            delivery_status,
                            delivery.agent_run.error,
                        )
                        if updated is not None:
                            results["deliveries_reconciled"] += 1
                            event_ids.add(updated.event_id)
                            broadcast_deliveries.append(updated)
                    else:
                        original_status = (
                            delivery.status.value
                            if hasattr(delivery.status, "value")
                            else delivery.status
                        )
                        delivery.status = EventDeliveryStatus.FAILED
                        delivery.error_message = (
                            f"Execution timeout: delivery stuck in {original_status} status "
                            f"for >{STUCK_DELIVERY_TIMEOUT_MINUTES} minutes"
                        )
                        results["deliveries_failed"] += 1
                        delivery.completed_at = datetime.now(timezone.utc)
                        event_ids.add(delivery.event_id)
                        broadcast_deliveries.append(delivery)

                await db.flush()

                # Update parent event statuses
                for event_id in event_ids:
                    await delivery_repo.update_event_status(event_id)
                    results["events_updated"] += 1

            # Also fix events stuck in PROCESSING/RECEIVED with no pending deliveries
            # This catches orphaned events from before the cleanup job was added
            stale_events_result = await db.execute(
                select(Event)
                .where(Event.status.in_([EventStatus.PROCESSING, EventStatus.RECEIVED]))
                .where(Event.created_at < cutoff)
            )
            stale_events = stale_events_result.scalars().all()

            for event in stale_events:
                if event.id not in event_ids:  # Skip events we already updated
                    await delivery_repo.update_event_status(event.id)
                    results["stale_events_fixed"] += 1

            # Collect broadcast data while we still have the session
            for delivery in broadcast_deliveries:
                if delivery.event and delivery.event.event_source_id:
                    try:
                        deliveries = await delivery_repo.get_by_event(delivery.event_id)
                        success_count = sum(
                            1 for d in deliveries if d.status == EventDeliveryStatus.SUCCESS
                        )
                        failed_count = sum(
                            1 for d in deliveries if d.status == EventDeliveryStatus.FAILED
                        )
                        broadcast_data.append({
                            "channel": f"event-source:{delivery.event.event_source_id}",
                            "event_id": str(delivery.event_id),
                            "event_source_id": str(delivery.event.event_source_id),
                            "event_type": delivery.event.event_type,
                            "status": getattr(delivery.event.status, "value", delivery.event.status),
                            "success_count": success_count,
                            "failed_count": failed_count,
                            "delivery_count": len(deliveries),
                        })
                    except Exception as e:
                        logger.warning(f"Failed to collect broadcast data: {e}")

            await db.commit()

        # Broadcast WebSocket updates AFTER session is closed (no DB connection held)
        from src.core.pubsub import manager

        for data in broadcast_data:
            try:
                await manager.publish(
                    channel=data["channel"],
                    message={
                        "type": "event_update",
                        "event": {
                            "id": data["event_id"],
                            "event_source_id": data["event_source_id"],
                            "event_type": data["event_type"],
                            "status": data["status"],
                            "success_count": data["success_count"],
                            "failed_count": data["failed_count"],
                            "delivery_count": data["delivery_count"],
                        },
                    },
                )
            except Exception as e:
                logger.warning(f"Failed to broadcast event update: {e}")

        # Calculate duration
        end_time = datetime.now(timezone.utc)
        duration_seconds = (end_time - start_time).total_seconds()
        results["duration_seconds"] = duration_seconds
        results["start_time"] = start_time.isoformat()
        results["end_time"] = end_time.isoformat()

        # Log completion
        if (
            results["deliveries_failed"] > 0
            or results["deliveries_reconciled"] > 0
            or results["stale_events_fixed"] > 0
        ):
            logger.info(
                f"✓ Stuck event cleanup completed: "
                f"{results['deliveries_failed']} deliveries marked failed, "
                f"{results['deliveries_reconciled']} terminal deliveries reconciled, "
                f"{results['events_updated']} events updated, "
                f"{results['stale_events_fixed']} stale events fixed ({duration_seconds:.1f}s)"
            )
        else:
            logger.info("✓ Stuck event cleanup completed: nothing to clean up")

    except Exception as e:
        logger.error(f"✗ Stuck event cleanup failed: {e}", exc_info=True)
        results["errors"].append({"error": str(e)})

    return results
