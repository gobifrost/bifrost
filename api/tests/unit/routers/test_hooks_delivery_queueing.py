"""Regression tests for exact webhook event-to-delivery queueing."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.routers.hooks import receive_webhook
from src.services.webhooks.protocol import Deliver


@asynccontextmanager
async def _savepoint():
    yield


@pytest.mark.asyncio
async def test_webhook_queues_the_exact_event_returned_by_the_processor():
    """Concurrent webhooks must not re-query and queue the newest sibling event."""
    source_id = uuid4()
    exact_event_id = uuid4()
    event_source = SimpleNamespace(id=source_id, is_active=True)
    webhook_source = SimpleNamespace(
        rate_limit_enabled=False,
        rate_limit_per_minute=None,
        rate_limit_window_seconds=60,
    )
    request = MagicMock()
    request.method = "POST"
    request.headers = {}
    request.query_params = {}
    request.client = None
    request.body = AsyncMock(return_value=b"{}")
    order: list[str] = []
    db = AsyncMock()
    db.begin_nested = MagicMock(side_effect=_savepoint)
    db.commit.side_effect = lambda: order.append("commit")

    with (
        patch(
            "src.routers.hooks.resolve_webhook_source",
            return_value=(event_source, webhook_source),
        ),
        patch("src.routers.hooks.EventProcessor") as processor_class,
    ):
        processor = processor_class.return_value
        processor.process_webhook = AsyncMock(
            return_value=Deliver(
                data={"alert": "one of two simultaneous requests"},
                event_type="alert",
                event_id=exact_event_id,
            )
        )
        queued = MagicMock(
            count=1, hand_off=AsyncMock(side_effect=lambda: order.append("hand_off"))
        )
        processor.queue_event_deliveries = AsyncMock(return_value=queued)

        response = await receive_webhook(str(source_id), request, db)

    assert response.status_code == 202
    processor.queue_event_deliveries.assert_awaited_once_with(exact_event_id)
    db.execute.assert_not_awaited()
    # The runs commit with the event's deliveries, then go to workers.
    assert order == ["commit", "hand_off"]
