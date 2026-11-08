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


@pytest.mark.asyncio
async def test_failed_admission_rolls_back_the_claim_before_a_normal_500_response(async_session_factory):
    """A healthy partial transaction must not commit a poisoned identity."""
    import hashlib
    import hmac

    from sqlalchemy import delete, func, select

    from src.models.enums import EventSourceType
    from src.models.orm.events import Event, EventSource
    from src.repositories.events import EventSubscriptionRepository
    from src.services.events.processor import EventProcessor
    from src.services.webhooks.signing_secret import with_signing_secret

    source_id = uuid4()
    async with async_session_factory() as db:
        db.add(EventSource(id=source_id, name=f"failed-admission-{source_id}",
                           source_type=EventSourceType.WEBHOOK, is_active=True, created_by="test"))
        await db.commit()
    try:
        async with async_session_factory() as db:
            source = await db.get(EventSource, source_id)
            webhook = SimpleNamespace(adapter_name="generic", config={"delivery_id_header": "X-Delivery-ID"},
                state=with_signing_secret({}, "test-secret"), rate_limit_enabled=False,
                rate_limit_per_minute=None, rate_limit_window_seconds=60)
            request = MagicMock(method="POST", query_params={}, client=None)
            request.body = AsyncMock(return_value=b"{}")
            request.headers = {"x-delivery-id": "one", "x-signature-256": "sha256=" +
                hmac.new(b"test-secret", b"{}", hashlib.sha256).hexdigest()}
            with (
                patch("src.routers.hooks.resolve_webhook_source", AsyncMock(return_value=(source, webhook))),
                patch.object(EventProcessor, "_broadcast_event_update", AsyncMock()),
                patch.object(EventSubscriptionRepository, "get_active_for_event",
                             AsyncMock(side_effect=RuntimeError("fault after identity insertion"))),
            ):
                response = await receive_webhook(str(source_id), request, db)
            assert response.status_code == 500
            await db.commit()  # DbSession's normal-response behavior.
        async with async_session_factory() as db:
            count = await db.scalar(select(func.count()).select_from(Event).where(Event.event_source_id == source_id))
            assert count == 0, "Failed admission committed a partial Event/identity"
    finally:
        async with async_session_factory() as db:
            await db.execute(delete(EventSource).where(EventSource.id == source_id))
            await db.commit()
