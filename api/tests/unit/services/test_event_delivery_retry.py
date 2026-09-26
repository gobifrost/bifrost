"""Regression coverage for delivery attempt timestamps."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.models.enums import EventDeliveryStatus
from src.services.events.processor import EventProcessor


@pytest.mark.asyncio
@pytest.mark.parametrize("queue_error", [None, RuntimeError("queue down")])
async def test_retry_delivery_starts_a_fresh_attempt(queue_error):
    delivery = SimpleNamespace(
        event_id=uuid4(),
        status=EventDeliveryStatus.FAILED,
        error_message="previous failure",
        execution_id=uuid4(),
        completed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        attempt_started_at=None,
    )
    session = AsyncMock()
    processor = EventProcessor(session)
    processor.queue_event_deliveries = AsyncMock(side_effect=queue_error)
    before = datetime.now(timezone.utc)

    message = await processor.retry_delivery(delivery)

    assert before <= delivery.attempt_started_at <= datetime.now(timezone.utc)
    assert delivery.execution_id is None
    processor.queue_event_deliveries.assert_awaited_once_with(delivery.event_id)
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
        event_id=uuid4(),
        status=EventDeliveryStatus.FAILED,
        error_message="previous failure",
        execution_id=uuid4(),
        completed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        attempt_started_at=None,
    )
    session = AsyncMock()
    processor = EventProcessor(session)

    async def fail_delivery(_event_id):
        delivery.status = EventDeliveryStatus.FAILED
        delivery.error_message = "queue down"
        return 0

    processor.queue_event_deliveries = AsyncMock(side_effect=fail_delivery)

    message = await processor.retry_delivery(delivery)

    assert message == "Failed to queue retry: queue down"
    assert delivery.completed_at >= delivery.attempt_started_at
