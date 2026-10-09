import base64
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
    assert persisted_event.raw_body == request.body


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
        raw_body=None,
    )

    with pytest.raises(ValueError, match="type='service'"):
        await processor._queue_workflow_execution(delivery, event)


@pytest.mark.asyncio
async def test_queued_workflow_runs_for_the_workflow_identity(monkeypatch):
    """No person started it: the run is for the workflow's identity."""
    from shared.run_lineage import RunLineage

    session = AsyncMock()
    processor = p.EventProcessor(session)
    workflow_id = uuid.uuid4()
    identity = uuid.uuid4()
    delivery = SimpleNamespace(
        id=uuid.uuid4(),
        workflow=SimpleNamespace(id=workflow_id, type="workflow", name="nightly", organization_id=None),
        subscription=None,
    )
    event = SimpleNamespace(
        id=uuid.uuid4(),
        event_type="schedule",
        data={},
        headers=None,
        received_at=None,
        source_ip=None,
        organization_id=None,
        raw_body=None,
    )
    lineage = RunLineage(identity, identity, None)
    resolve = AsyncMock(return_value=lineage)
    monkeypatch.setattr("shared.run_lineage.unattended_lineage", resolve)
    enqueue = AsyncMock(return_value=str(uuid.uuid4()))
    monkeypatch.setattr(
        "src.services.execution.async_executor.enqueue_system_workflow_execution", enqueue
    )

    await processor._queue_workflow_execution(delivery, event)

    resolve.assert_awaited_once_with(session, workflow_id)
    assert enqueue.await_args.kwargs["lineage"] == lineage


@pytest.mark.asyncio
async def test_queued_workflow_receives_trusted_byte_exact_raw_webhook_body(monkeypatch):
    """Raw webhook bytes reach the reserved event context, not caller-controlled data."""
    from shared.run_lineage import RunLineage

    session = AsyncMock()
    processor = p.EventProcessor(session)
    workflow_id = uuid.uuid4()
    delivery = SimpleNamespace(
        id=uuid.uuid4(),
        workflow=SimpleNamespace(
            id=workflow_id,
            type="workflow",
            name="raw-body-consumer",
            organization_id=None,
        ),
        subscription=None,
    )
    raw_body = b'{"z":1,  "a" : [3,2,1]}'
    event = SimpleNamespace(
        id=uuid.uuid4(),
        event_type="webhook.received",
        data={"_event": {"raw_body_base64": "caller-spoofed"}, "z": 1, "a": [3, 2, 1]},
        raw_body=raw_body,
        headers=None,
        received_at=None,
        source_ip=None,
        organization_id=None,
    )
    identity = uuid.uuid4()
    monkeypatch.setattr(
        "shared.run_lineage.unattended_lineage",
        AsyncMock(return_value=RunLineage(identity, identity, None)),
    )
    enqueue = AsyncMock(return_value=str(uuid.uuid4()))
    monkeypatch.setattr(
        "src.services.execution.async_executor.enqueue_system_workflow_execution", enqueue
    )

    await processor._queue_workflow_execution(delivery, event)

    trusted_event = enqueue.await_args.kwargs["parameters"]["_event"]
    assert trusted_event["body"] == event.data
    assert trusted_event["raw_body_base64"] == base64.b64encode(raw_body).decode("ascii")


@pytest.mark.asyncio
async def test_queued_workflow_encodes_an_empty_accepted_body(monkeypatch):
    from shared.run_lineage import RunLineage

    session = AsyncMock()
    processor = p.EventProcessor(session)
    workflow_id = uuid.uuid4()
    delivery = SimpleNamespace(
        id=uuid.uuid4(),
        workflow=SimpleNamespace(
            id=workflow_id,
            type="workflow",
            name="empty-raw-body-consumer",
            organization_id=None,
        ),
        subscription=None,
    )
    event = SimpleNamespace(
        id=uuid.uuid4(),
        event_type="webhook.received",
        data={},
        raw_body=b"",
        headers=None,
        received_at=None,
        source_ip=None,
        organization_id=None,
    )
    identity = uuid.uuid4()
    monkeypatch.setattr(
        "shared.run_lineage.unattended_lineage",
        AsyncMock(return_value=RunLineage(identity, identity, None)),
    )
    enqueue = AsyncMock(return_value=str(uuid.uuid4()))
    monkeypatch.setattr(
        "src.services.execution.async_executor.enqueue_system_workflow_execution", enqueue
    )

    await processor._queue_workflow_execution(delivery, event)

    assert enqueue.await_args.kwargs["parameters"]["_event"]["raw_body_base64"] == ""


@pytest.mark.asyncio
async def test_event_websocket_update_excludes_raw_body(monkeypatch):
    processor = p.EventProcessor(AsyncMock())
    event = SimpleNamespace(
        id=uuid.uuid4(),
        event_source_id=uuid.uuid4(),
        event_type="webhook.received",
        status="received",
        received_at=None,
        source_ip=None,
        raw_body=b'{"secret":"not for the websocket"}',
    )
    broadcast = AsyncMock()
    monkeypatch.setattr("src.core.pubsub.manager.broadcast", broadcast)

    await processor._broadcast_event_update(event.event_source_id, event, "event_created")

    websocket_event = broadcast.await_args.args[1]["event"]
    assert "raw_body" not in websocket_event
    assert "raw_body_base64" not in websocket_event
