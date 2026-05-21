"""
Unit tests for EventProcessor.emit_topic.

Tests:
- emit_topic creates an Event row with event_type, event_source_id from the topic source
- given a subscription on the topic source, delivery is created and workflow enqueued
- no topic source found => no-op (returns generated UUID, 0 subscribers)
- no subscribers => event logged, zero deliveries, returns (event_id, 0)
- payload is round-trippable: workflow receives it under _event.body
- stamped org: explicit override takes precedence over source.organization_id
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.enums import EventDeliveryStatus


def _make_source(topic: str, org_id=None):
    source = MagicMock()
    source.id = uuid.uuid4()
    source.event_type = topic
    source.organization_id = org_id
    return source


def _make_processor(source=None, subscriptions=None):
    """Create an EventProcessor with repos mocked out."""
    if subscriptions is None:
        subscriptions = []

    mock_session = AsyncMock()

    with (
        patch("src.services.events.processor.EventDeliveryRepository"),
        patch("src.services.events.processor.EventRepository"),
        patch("src.services.events.processor.EventSubscriptionRepository"),
        patch("src.services.events.processor.EventSourceRepository"),
        patch("src.services.events.processor.WebhookSourceRepository"),
    ):
        from src.services.events.processor import EventProcessor

        processor = EventProcessor(mock_session)

        processor._source_repo = AsyncMock()
        processor._source_repo.get_by_topic = AsyncMock(return_value=source)

        processor._subscription_repo = AsyncMock()
        processor._subscription_repo.get_active_for_event = AsyncMock(
            return_value=subscriptions
        )

        processor._event_repo = AsyncMock()
        processor._delivery_repo = AsyncMock()

        return processor, mock_session


def _make_workflow_subscription(workflow_id=None, org_id=None):
    sub = MagicMock()
    sub.id = uuid.uuid4()
    sub.target_type = "workflow"
    sub.workflow_id = workflow_id or uuid.uuid4()
    sub.agent_id = None
    sub.input_mapping = None

    workflow = MagicMock()
    workflow.id = sub.workflow_id
    workflow.organization_id = org_id or uuid.uuid4()
    sub.workflow = workflow

    return sub


@pytest.mark.asyncio
async def test_emit_topic_no_source_is_noop():
    """When no topic source exists, emit is a no-op: returns (uuid, 0)."""
    processor, session = _make_processor(source=None, subscriptions=[])
    added_objects = []
    session.add = lambda obj: added_objects.append(obj)
    session.flush = AsyncMock()

    event_id, count = await processor.emit_topic(
        topic="user.invited",
        data={"user_id": "abc"},
    )

    assert event_id is not None
    assert count == 0
    # No Event row should have been added
    from src.models.orm.events import Event
    events = [o for o in added_objects if isinstance(o, Event)]
    assert len(events) == 0


@pytest.mark.asyncio
async def test_emit_topic_creates_event_row():
    """emit_topic adds an Event row with correct source_id and event_type."""
    org_id = uuid.uuid4()
    source = _make_source("user.invited", org_id=org_id)
    sub = _make_workflow_subscription(org_id=org_id)
    processor, session = _make_processor(source=source, subscriptions=[sub])

    added_objects = []
    session.add = lambda obj: added_objects.append(obj)
    session.flush = AsyncMock()

    event_id, count = await processor.emit_topic(
        topic="user.invited",
        data={"user_id": "abc123", "email": "user@example.com"},
        organization_id=org_id,
    )

    from src.models.orm.events import Event
    events = [obj for obj in added_objects if isinstance(obj, Event)]
    assert len(events) == 1

    event = events[0]
    assert event.event_type == "user.invited"
    assert event.event_source_id == source.id
    assert event.data == {"user_id": "abc123", "email": "user@example.com"}
    assert event.id == event_id
    assert count == 1


@pytest.mark.asyncio
async def test_emit_topic_no_subscribers_logs_and_returns():
    """With no subscriptions, event is logged and zero deliveries created."""
    source = _make_source("user.invited")
    processor, session = _make_processor(source=source, subscriptions=[])

    added_objects = []
    session.add = lambda obj: added_objects.append(obj)
    session.flush = AsyncMock()

    event_id, count = await processor.emit_topic(
        topic="user.invited",
        data={"user_id": "abc"},
    )

    assert event_id is not None
    assert count == 0
    from src.models.orm.events import Event, EventDelivery
    events = [o for o in added_objects if isinstance(o, Event)]
    deliveries = [o for o in added_objects if isinstance(o, EventDelivery)]
    assert len(events) == 1
    assert len(deliveries) == 0


@pytest.mark.asyncio
async def test_emit_topic_with_subscriber_creates_delivery():
    """A matching subscription produces one delivery record."""
    org_id = uuid.uuid4()
    source = _make_source("user.invited", org_id=org_id)
    sub = _make_workflow_subscription(org_id=org_id)
    processor, session = _make_processor(source=source, subscriptions=[sub])

    added_objects = []
    session.add = lambda obj: added_objects.append(obj)
    session.flush = AsyncMock()

    event_id, count = await processor.emit_topic(
        topic="user.invited",
        data={"email": "new@example.com"},
        organization_id=org_id,
    )

    from src.models.orm.events import EventDelivery
    deliveries = [o for o in added_objects if isinstance(o, EventDelivery)]
    assert len(deliveries) == 1
    assert deliveries[0].event_id == event_id
    assert deliveries[0].event_subscription_id == sub.id
    assert deliveries[0].status == EventDeliveryStatus.PENDING
    assert count == 1


@pytest.mark.asyncio
async def test_emit_topic_org_override():
    """Explicit organization_id overrides source.organization_id."""
    source_org = uuid.uuid4()
    override_org = uuid.uuid4()
    source = _make_source("user.invited", org_id=source_org)
    processor, session = _make_processor(source=source, subscriptions=[])

    added_objects = []
    session.add = lambda obj: added_objects.append(obj)
    session.flush = AsyncMock()

    await processor.emit_topic(
        topic="user.invited",
        data={},
        organization_id=override_org,
    )

    from src.models.orm.events import Event
    events = [o for o in added_objects if isinstance(o, Event)]
    assert len(events) == 1
    assert events[0].organization_id == override_org


@pytest.mark.asyncio
async def test_emit_topic_uses_source_org_when_no_override():
    """When organization_id is None, source.organization_id is used."""
    source_org = uuid.uuid4()
    source = _make_source("user.invited", org_id=source_org)
    processor, session = _make_processor(source=source, subscriptions=[])

    added_objects = []
    session.add = lambda obj: added_objects.append(obj)
    session.flush = AsyncMock()

    await processor.emit_topic(
        topic="user.invited",
        data={},
        organization_id=None,
    )

    from src.models.orm.events import Event
    events = [o for o in added_objects if isinstance(o, Event)]
    assert len(events) == 1
    assert events[0].organization_id == source_org


@pytest.mark.asyncio
async def test_emit_topic_payload_under_event_key():
    """The workflow receives payload under _event.body when queued."""
    org_id = uuid.uuid4()
    source = _make_source("user.invited", org_id=org_id)
    sub = _make_workflow_subscription(org_id=org_id)
    processor, session = _make_processor(source=source, subscriptions=[sub])

    added_objects = []
    session.add = lambda obj: added_objects.append(obj)
    session.flush = AsyncMock()

    payload = {"user_id": "u1", "email": "u@example.com", "reason": "created"}

    with patch(
        "src.services.execution.async_executor.enqueue_system_workflow_execution",
        new_callable=AsyncMock,
        return_value=str(uuid.uuid4()),
    ) as mock_enqueue:
        event_mock = MagicMock()
        event_mock.id = uuid.uuid4()
        event_mock.event_type = "user.invited"
        event_mock.data = payload
        event_mock.headers = None
        event_mock.received_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        event_mock.source_ip = None

        delivery_mock = MagicMock()
        delivery_mock.id = uuid.uuid4()
        delivery_mock.workflow = sub.workflow
        delivery_mock.subscription = sub
        delivery_mock.execution_id = None

        await processor._queue_workflow_execution(delivery_mock, event_mock)

        assert mock_enqueue.called
        call_kwargs = mock_enqueue.call_args.kwargs
        params = call_kwargs.get("parameters") or (
            mock_enqueue.call_args.args[1] if mock_enqueue.call_args.args else call_kwargs.get("parameters")
        )
        assert params is not None
        assert "_event" in params
        assert params["_event"]["body"] == payload
        assert params["_event"]["type"] == "user.invited"
