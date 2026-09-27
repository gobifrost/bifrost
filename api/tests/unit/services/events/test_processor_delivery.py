import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.events import processor as p
from src.services.webhooks.protocol import Deliver, WebhookRequest


@pytest.mark.asyncio
async def test_process_delivery_returns_the_exact_persisted_event_id():
    session = AsyncMock()
    session.add = MagicMock()
    processor = p.EventProcessor(session)
    workflow_id = uuid.uuid4()
    subscription = SimpleNamespace(
        id=uuid.uuid4(),
        target_type="workflow",
        agent_id=None,
        filter_expression=None,
        workflow_id=workflow_id,
        workflow=SimpleNamespace(id=workflow_id),
    )
    processor._subscription_repo.get_active_for_event = AsyncMock(
        return_value=[subscription]
    )
    processor._broadcast_event_update = AsyncMock()
    event_source = SimpleNamespace(id=uuid.uuid4())
    webhook_source = SimpleNamespace()
    incoming = Deliver(data={"id": 1}, event_type="ticket.created")
    request = WebhookRequest("POST", "/webhooks/source", {}, {}, b"{}")

    result = await processor._process_delivery(
        webhook_source=webhook_source,
        event_source=event_source,
        deliver=incoming,
        request=request,
    )

    persisted_event = session.add.call_args_list[0].args[0]
    assert result.event_id == persisted_event.id


@pytest.mark.asyncio
async def test_queue_service_target_fails_loudly():
    """A delivery targeting a service fails with an attributable error."""
    session = AsyncMock()
    session.add = MagicMock()
    processor = p.EventProcessor(session)
    workflow_id = uuid.uuid4()
    delivery = SimpleNamespace(
        id=uuid.uuid4(),
        workflow=SimpleNamespace(id=workflow_id, type="service", name="telegram_bridge"),
    )
    event = SimpleNamespace(
        id=uuid.uuid4(),
        event_type="telegram.message",
        data={},
        headers=None,
        received_at=None,
        source_ip=None,
        organization_id=None,
    )

    with pytest.raises(ValueError, match="type='service'"):
        await processor._queue_workflow_execution(delivery, event)
