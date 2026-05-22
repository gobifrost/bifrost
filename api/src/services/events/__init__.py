"""
Event processing services for the Bifrost event system.
"""

from uuid import UUID

from src.services.events.processor import (
    EventProcessor,
    resolve_webhook_source,
    update_delivery_from_execution,
)


async def emit_event(
    topic: str,
    data: dict,
    *,
    organization_id: UUID | None = None,
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
            triggered_by=triggered_by,
        )
        if count > 0:
            # Queue deliveries in the same transaction as emit so that
            # delivery.execution_id is persisted; otherwise the subsequent
            # update_delivery_from_execution lookup fails and a sweeper
            # eventually marks the delivery FAILED with a phantom timeout.
            await processor.queue_event_deliveries(event_id)
        await db.commit()
        return event_id, count


__all__ = [
    "EventProcessor",
    "emit_event",
    "resolve_webhook_source",
    "update_delivery_from_execution",
]
