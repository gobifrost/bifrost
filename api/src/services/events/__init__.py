"""
Event processing services for the Bifrost event system.
"""

from uuid import UUID

from src.services.events.processor import (
    EventProcessor,
    QueuedDeliveries,
    resolve_webhook_source,
    update_delivery_from_execution,
)


async def emit_event(
    topic: str,
    data: dict,
    *,
    organization_id: UUID | None = None,
    solution_id: UUID | None = None,
    triggered_by: str | None = None,
) -> tuple[UUID, int]:
    """Emit a topic event and return (event_id, subscribers_notified).

    Opens its own DB session and commits, so callers don't need to manage
    transactions. Safe to call from within a request handler that has its
    own open session.
    """
    from src.core.database import get_session_factory

    session_factory = get_session_factory()
    async with session_factory() as db:
        processor = EventProcessor(db)
        event_id, count = await processor.emit_topic(
            topic=topic,
            data=data,
            organization_id=organization_id,
            solution_id=solution_id,
            triggered_by=triggered_by,
        )
        queued = QueuedDeliveries()
        if count > 0:
            # Each delivery's run commits with the event and the delivery's
            # record of it. If queueing fails outright, the event is still
            # recorded with pending deliveries before the error surfaces.
            try:
                async with db.begin_nested():
                    queued = await processor.queue_event_deliveries(event_id)
            except Exception:
                await db.commit()
                raise
        await db.commit()
        await queued.hand_off()
        return event_id, count


__all__ = [
    "EventProcessor",
    "emit_event",
    "resolve_webhook_source",
    "update_delivery_from_execution",
]
