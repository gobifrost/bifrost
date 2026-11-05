"""Regression coverage for delivery attempt timestamps."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.models.enums import EventDeliveryStatus
from src.services.events.processor import (
    EventProcessor,
    QueuedDeliveries,
    update_delivery_from_execution,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("queue_error", [None, RuntimeError("queue down")])
async def test_retry_delivery_starts_a_fresh_attempt(queue_error):
    delivery = SimpleNamespace(
        id=uuid4(),
        event_id=uuid4(),
        status=EventDeliveryStatus.FAILED,
        error_message="previous failure",
        execution_id=uuid4(),
        agent_run_id=uuid4(),
        completed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        attempt_started_at=None,
    )
    session = AsyncMock()
    processor = EventProcessor(session)
    processor.queue_event_deliveries = AsyncMock(
        side_effect=queue_error, return_value=QueuedDeliveries()
    )
    before = datetime.now(timezone.utc)

    message, _queued = await processor.retry_delivery(delivery)

    assert before <= delivery.attempt_started_at <= datetime.now(timezone.utc)
    assert delivery.execution_id is None
    assert delivery.agent_run_id is None
    # Only the retried delivery is queued, never its pending siblings.
    processor.queue_event_deliveries.assert_awaited_once_with(
        delivery.event_id, delivery_ids={delivery.id}
    )
    if queue_error is None:
        assert message == "Delivery queued for retry"
        assert delivery.status is EventDeliveryStatus.PENDING
        assert delivery.error_message is None
        assert delivery.completed_at is None
    else:
        assert message == "Failed to queue retry: queue down"
        assert delivery.status is EventDeliveryStatus.FAILED
        assert delivery.error_message == "queue down"
        assert delivery.completed_at >= delivery.attempt_started_at


@pytest.mark.asyncio
async def test_retry_delivery_reports_a_per_delivery_queue_failure():
    delivery = SimpleNamespace(
        id=uuid4(),
        event_id=uuid4(),
        status=EventDeliveryStatus.FAILED,
        error_message="previous failure",
        execution_id=uuid4(),
        agent_run_id=uuid4(),
        completed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        attempt_started_at=None,
    )
    session = AsyncMock()
    processor = EventProcessor(session)

    async def fail_delivery(_event_id, *, delivery_ids):  # type: ignore[no-untyped-def]
        delivery.status = EventDeliveryStatus.FAILED
        delivery.error_message = "queue down"
        return QueuedDeliveries()

    processor.queue_event_deliveries = AsyncMock(side_effect=fail_delivery)

    message, _queued = await processor.retry_delivery(delivery)

    assert message == "Failed to queue retry: queue down"
    assert delivery.completed_at >= delivery.attempt_started_at


@pytest.mark.asyncio
async def test_supplied_session_never_publishes_a_delivery_before_its_owner_commits():
    """A caller may roll back the supplied session, so publication is deferred."""
    delivery = SimpleNamespace(id=uuid4(), event_id=uuid4())
    repository = MagicMock()
    repository.complete_from_execution = AsyncMock(return_value=delivery)
    repository.update_event_status = AsyncMock()
    session = AsyncMock()
    broadcast = AsyncMock()

    with (
        patch(
            "src.services.events.processor.EventDeliveryRepository",
            return_value=repository,
        ),
        patch(
            "src.services.events.processor.broadcast_delivery_update_after_commit",
            new=broadcast,
        ),
    ):
        updated = await update_delivery_from_execution(
            str(uuid4()), "Success", session=session
        )

    assert updated is delivery
    session.commit.assert_not_awaited()
    broadcast.assert_not_awaited()


@pytest.mark.asyncio
async def test_self_owned_delivery_update_commits_before_publication():
    """The standalone helper owns both commit and the follow-up broadcast."""
    delivery = SimpleNamespace(id=uuid4(), event_id=uuid4())
    repository = MagicMock()
    repository.complete_from_execution = AsyncMock(return_value=delivery)
    repository.update_event_status = AsyncMock()
    session = AsyncMock()
    order: list[str] = []
    session.commit.side_effect = lambda: order.append("commit")
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=session)
    context.__aexit__ = AsyncMock(return_value=None)
    broadcast = AsyncMock(side_effect=lambda *_args: order.append("broadcast"))

    with (
        patch(
            "src.core.database.get_session_factory", return_value=lambda: context
        ),
        patch(
            "src.services.events.processor.EventDeliveryRepository",
            return_value=repository,
        ),
        patch(
            "src.services.events.processor.broadcast_delivery_update_after_commit",
            new=broadcast,
        ),
    ):
        updated = await update_delivery_from_execution(str(uuid4()), "Success")

    assert updated is delivery
    assert order == ["commit", "broadcast"]
    broadcast.assert_awaited_once_with(session, delivery)
