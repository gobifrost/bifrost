"""Atomic, source-scoped acceptance for an authenticated webhook identity.

Reuse the existing retained Event and its delivery records. No token, provider,
tenant-routing, retention job or workflow-engine behavior belongs here.
"""

import hashlib
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.enums import EventStatus
from src.models.orm.events import Event, EventSource
from src.services.webhooks.protocol import Deliver, Rejected, WebhookRequest


async def accept_identified_webhook(
    db: AsyncSession, source: EventSource, delivery: Deliver, request: WebhookRequest,
) -> tuple[Event, bool] | Rejected:
    """Claim and lock one event until the caller commits its staged deliveries.

    PostgreSQL's unique-index upsert serializes concurrent duplicates. A no-op
    update locks the existing row and returns its identity atomically, including
    when retention is deleting it. Never replace an existing payload.
    """
    assert delivery.delivery_id is not None
    key = f"webhook:{source.id}:" + hashlib.sha256(delivery.delivery_id.encode()).hexdigest()
    candidate_id = uuid4()
    statement = insert(Event).values(
        id=candidate_id, event_source_id=source.id, organization_id=source.organization_id,
        occurrence_key=key, event_type=delivery.event_type, data=delivery.data,
        raw_body=request.body, headers=delivery.raw_headers, source_ip=request.client_ip,
        received_at=datetime.now(timezone.utc), status=EventStatus.RECEIVED,
    )
    event_id = (await db.execute(statement.on_conflict_do_update(
        index_elements=[Event.occurrence_key], index_where=Event.occurrence_key.is_not(None),
        set_={"occurrence_key": statement.excluded.occurrence_key},
    ).returning(Event.id))).scalar_one()
    event = await db.get(Event, event_id, populate_existing=True)
    assert event is not None
    if event.raw_body != request.body or event.event_type != delivery.event_type:
        return Rejected("Delivery identity conflicts with a retained event", 409)
    return event, event_id == candidate_id
