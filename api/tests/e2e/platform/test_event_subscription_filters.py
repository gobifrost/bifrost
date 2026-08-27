import uuid

import pytest
from sqlalchemy import select

from src.models.enums import EventDeliveryStatus, EventSourceType, EventStatus
from src.models.orm.events import Event, EventDelivery, EventSource, EventSubscription
from src.models.orm.workflows import Workflow
from src.services.events.processor import EventProcessor


pytestmark = pytest.mark.e2e


@pytest.mark.asyncio
async def test_topic_filter_non_match_persists_skipped_delivery(db_session) -> None:
    topic = f"ticket.filter_{uuid.uuid4().hex[:8]}"
    workflow = Workflow(
        id=uuid.uuid4(),
        name=f"Event Filter Test {uuid.uuid4().hex[:8]}",
        function_name="event_filter_test",
        path=f"workflows/event_filter_{uuid.uuid4().hex[:8]}.py",
        type="workflow",
        access_level="authenticated",
        is_active=True,
    )
    source = EventSource(
        id=uuid.uuid4(),
        name=f"Event Filter Source {uuid.uuid4().hex[:8]}",
        source_type=EventSourceType.TOPIC,
        event_type=topic,
        organization_id=None,
        is_active=True,
        created_by="event-filter-test@gobifrost.com",
    )
    subscription = EventSubscription(
        id=uuid.uuid4(),
        event_source_id=source.id,
        target_type="workflow",
        workflow_id=workflow.id,
        event_type=topic,
        filter_expression="$.priority == 'high'",
        is_active=True,
        created_by="event-filter-test@gobifrost.com",
    )
    db_session.add_all([workflow, source, subscription])
    await db_session.flush()

    processor = EventProcessor(db_session)
    event_id, matching_subscribers = await processor.emit_topic(
        topic=topic,
        data={"priority": "low"},
    )
    queued = await processor.queue_event_deliveries(event_id)

    event = await db_session.get(Event, event_id)
    delivery = (
        await db_session.execute(
            select(EventDelivery).where(EventDelivery.event_id == event_id)
        )
    ).scalar_one()

    assert matching_subscribers == 0
    assert queued == 0
    assert event is not None
    assert event.status == EventStatus.COMPLETED
    assert delivery.status == EventDeliveryStatus.SKIPPED
    assert delivery.execution_id is None
    assert delivery.error_message == "Subscription filter did not match the event payload"
