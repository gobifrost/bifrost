"""An old delivery must receive a fresh timeout window when retried."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from src.models.enums import EventDeliveryStatus
from src.models.orm.events import Event, EventDelivery
from src.repositories.events import EventDeliveryRepository
from src.routers.events import retry_delivery
from src.services.events.processor import EventProcessor
from tests.e2e.api.test_events import (
    event_source as event_source,
    subscription as subscription,
    test_workflow as test_workflow,
)


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_retry_timeout_uses_attempt_age(
    event_source, subscription, test_workflow, db_session, monkeypatch
):
    old = datetime.now(timezone.utc) - timedelta(days=1)
    event = Event(
        id=uuid4(),
        event_source_id=UUID(event_source["id"]),
        event_type="retry.age",
        received_at=old,
        data={},
    )
    delivery = EventDelivery(
        id=uuid4(),
        event_id=event.id,
        event_subscription_id=UUID(subscription["id"]),
        workflow_id=UUID(test_workflow["id"]),
        status=EventDeliveryStatus.FAILED,
        created_at=old,
        completed_at=old,
    )
    db_session.add_all([event, delivery])
    await db_session.flush()
    monkeypatch.setattr(EventProcessor, "queue_event_deliveries", AsyncMock(return_value=1))

    response = await retry_delivery(delivery.id, None, None, db_session)

    assert response.status == "pending"
    assert delivery.completed_at is None
    repo = EventDeliveryRepository(db_session)
    assert delivery.id not in {row.id for row in await repo.get_stuck_deliveries(15)}

    delivery.attempt_started_at = old
    await db_session.flush()
    assert delivery.id in {row.id for row in await repo.get_stuck_deliveries(15)}

    delivery.attempt_started_at = None
    await db_session.flush()
    assert delivery.id in {row.id for row in await repo.get_stuck_deliveries(15)}
