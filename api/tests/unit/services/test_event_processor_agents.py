"""
Unit tests for EventProcessor agent target routing.

Verifies that queue_event_deliveries correctly routes deliveries
to either _queue_agent_run or _queue_workflow_execution based on
the subscription's target_type, and that _queue_agent_run calls
enqueue_agent_run with the expected parameters.
"""

import uuid
import sys
import types
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.enums import EventDeliveryStatus

if TYPE_CHECKING:
    from src.services.events.processor import EventProcessor


def _make_event(
    event_id: uuid.UUID | None = None,
    event_type: str = "ticket.created",
    data: dict | None = None,
    event_source_org_id: uuid.UUID | None = None,
) -> MagicMock:
    event = MagicMock()
    event.id = event_id or uuid.uuid4()
    event.event_type = event_type
    event.event_source_id = uuid.uuid4()
    event.event_source.organization_id = event_source_org_id
    event.data = data or {"ticket_id": "123"}
    event.headers = {"content-type": "application/json"}
    event.received_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    event.source_ip = "10.0.0.1"
    event.status = "processing"
    return event


def _make_delivery(
    status: EventDeliveryStatus = EventDeliveryStatus.PENDING,
    target_type: str = "workflow",
    agent: MagicMock | None = None,
    workflow: MagicMock | None = None,
) -> MagicMock:
    delivery = MagicMock()
    delivery.id = uuid.uuid4()
    delivery.status = status
    delivery.error_message = None

    subscription = MagicMock()
    subscription.target_type = target_type
    subscription.input_mapping = None
    delivery.subscription = subscription

    if target_type == "agent":
        if agent is None:
            agent = MagicMock()
            agent.id = uuid.uuid4()
            agent.organization_id = uuid.uuid4()
        subscription.agent = agent
    else:
        if workflow is None:
            workflow = MagicMock()
            workflow.id = uuid.uuid4()
            workflow.organization_id = uuid.uuid4()
        delivery.workflow = workflow

    return delivery


def _create_processor() -> "EventProcessor":
    """Create an EventProcessor with all repos mocked out."""
    with (
        patch("src.services.events.processor.EventDeliveryRepository"),
        patch("src.services.events.processor.EventRepository"),
        patch("src.services.events.processor.EventSubscriptionRepository"),
        patch("src.services.events.processor.WebhookSourceRepository"),
    ):
        from src.services.events.processor import EventProcessor

        session = AsyncMock()
        processor = EventProcessor(session)
        processor._delivery_repo = MagicMock()
        processor._event_repo = MagicMock()
        processor._subscription_repo = MagicMock()
        processor._webhook_repo = MagicMock()
        return processor


def _fake_module(name: str, **attributes: object) -> types.ModuleType:
    module = types.ModuleType(name)
    for key, value in attributes.items():
        setattr(module, key, value)
    return module


@pytest.mark.asyncio
async def test_queue_deliveries_routes_agent_subscription():
    """Delivery with target_type='agent' should call _queue_agent_run."""
    processor = _create_processor()

    event_id = uuid.uuid4()
    event = _make_event(event_id=event_id)
    delivery = _make_delivery(target_type="agent")

    processor._delivery_repo.get_by_event = AsyncMock(return_value=[delivery])
    processor._event_repo.get_by_id = AsyncMock(return_value=event)
    processor._queue_agent_run = AsyncMock()
    processor._queue_workflow_execution = AsyncMock()
    processor._broadcast_event_update = AsyncMock()

    count = await processor.queue_event_deliveries(event_id)

    assert count == 1
    assert delivery.status == EventDeliveryStatus.QUEUED
    processor._queue_agent_run.assert_awaited_once_with(delivery, event)
    processor._queue_workflow_execution.assert_not_awaited()


@pytest.mark.asyncio
async def test_queue_deliveries_routes_workflow_subscription():
    """Delivery with target_type='workflow' should call _queue_workflow_execution."""
    processor = _create_processor()

    event_id = uuid.uuid4()
    event = _make_event(event_id=event_id)
    delivery = _make_delivery(target_type="workflow")

    processor._delivery_repo.get_by_event = AsyncMock(return_value=[delivery])
    processor._event_repo.get_by_id = AsyncMock(return_value=event)
    processor._queue_agent_run = AsyncMock()
    processor._queue_workflow_execution = AsyncMock()
    processor._broadcast_event_update = AsyncMock()

    count = await processor.queue_event_deliveries(event_id)

    assert count == 1
    assert delivery.status == EventDeliveryStatus.QUEUED
    processor._queue_workflow_execution.assert_awaited_once_with(delivery, event)
    processor._queue_agent_run.assert_not_awaited()


@pytest.mark.asyncio
async def test_queue_deliveries_skips_non_pending():
    """Deliveries that are not PENDING should be skipped entirely."""
    processor = _create_processor()

    event_id = uuid.uuid4()
    event = _make_event(event_id=event_id)
    delivery = _make_delivery(status=EventDeliveryStatus.QUEUED)

    processor._delivery_repo.get_by_event = AsyncMock(return_value=[delivery])
    processor._event_repo.get_by_id = AsyncMock(return_value=event)
    processor._queue_agent_run = AsyncMock()
    processor._queue_workflow_execution = AsyncMock()
    processor._broadcast_event_update = AsyncMock()

    count = await processor.queue_event_deliveries(event_id)

    assert count == 0
    processor._queue_agent_run.assert_not_awaited()
    processor._queue_workflow_execution.assert_not_awaited()
    # Status should remain unchanged
    assert delivery.status == EventDeliveryStatus.QUEUED


@pytest.mark.asyncio
async def test_queue_agent_run_calls_enqueue():
    """_queue_agent_run should call enqueue_agent_run with correct parameters."""
    processor = _create_processor()

    agent = MagicMock()
    agent.id = uuid.uuid4()
    agent.organization_id = uuid.uuid4()

    delivery = _make_delivery(target_type="agent", agent=agent)
    event = _make_event(data={"ticket_id": "456", "priority": "high"})

    mock_enqueue = AsyncMock(return_value="run-abc-123")
    fake_agent_run_service = _fake_module(
        "src.services.execution.agent_run_service",
        enqueue_agent_run=mock_enqueue,
    )

    with patch.dict(sys.modules, {"src.services.execution.agent_run_service": fake_agent_run_service}):
        await processor._queue_agent_run(delivery, event)

        mock_enqueue.assert_awaited_once()
        call_kwargs = mock_enqueue.call_args.kwargs

        assert call_kwargs["agent_id"] == str(agent.id)
        assert call_kwargs["trigger_type"] == "event"
        assert "event:" in call_kwargs["trigger_source"]
        assert call_kwargs["org_id"] == str(agent.organization_id)
        assert call_kwargs["event_delivery_id"] == str(delivery.id)

        # Verify input_data includes event data and _event context
        input_data = call_kwargs["input_data"]
        assert input_data["ticket_id"] == "456"
        assert input_data["priority"] == "high"
        assert "_event" in input_data
        assert input_data["_event"]["id"] == str(event.id)
        assert input_data["_event"]["type"] == event.event_type
        assert input_data["_event"]["body"] == event.data
        assert input_data["_event"]["headers"] == event.headers
        assert input_data["_event"]["source_ip"] == event.source_ip


@pytest.mark.asyncio
async def test_queue_workflow_execution_inherits_event_source_org_for_global_workflow():
    """Global workflows triggered by org-scoped event sources should run in the source org."""
    processor = _create_processor()

    event_source_org_id = uuid.uuid4()
    workflow = MagicMock()
    workflow.id = uuid.uuid4()
    workflow.organization_id = None
    delivery = _make_delivery(target_type="workflow", workflow=workflow)
    event = _make_event(event_source_org_id=event_source_org_id)

    mock_enqueue = AsyncMock(return_value=str(uuid.uuid4()))
    fake_async_executor = _fake_module(
        "src.services.execution.async_executor",
        enqueue_system_workflow_execution=mock_enqueue,
    )

    with patch.dict(sys.modules, {"src.services.execution.async_executor": fake_async_executor}):
        await processor._queue_workflow_execution(delivery, event)

        mock_enqueue.assert_awaited_once()
        assert mock_enqueue.call_args.kwargs["org_id"] == str(event_source_org_id)


@pytest.mark.asyncio
async def test_queue_workflow_execution_prefers_workflow_org():
    """Org-scoped workflows keep their own scope even if the event source is also scoped."""
    processor = _create_processor()

    event_source_org_id = uuid.uuid4()
    workflow_org_id = uuid.uuid4()
    workflow = MagicMock()
    workflow.id = uuid.uuid4()
    workflow.organization_id = workflow_org_id
    delivery = _make_delivery(target_type="workflow", workflow=workflow)
    event = _make_event(event_source_org_id=event_source_org_id)

    mock_enqueue = AsyncMock(return_value=str(uuid.uuid4()))
    fake_async_executor = _fake_module(
        "src.services.execution.async_executor",
        enqueue_system_workflow_execution=mock_enqueue,
    )

    with patch.dict(sys.modules, {"src.services.execution.async_executor": fake_async_executor}):
        await processor._queue_workflow_execution(delivery, event)

        mock_enqueue.assert_awaited_once()
        assert mock_enqueue.call_args.kwargs["org_id"] == str(workflow_org_id)


@pytest.mark.asyncio
async def test_queue_workflow_execution_defaults_to_provider_org():
    """Global workflow + global event source should still run in provider org."""
    processor = _create_processor()

    workflow = MagicMock()
    workflow.id = uuid.uuid4()
    workflow.organization_id = None
    delivery = _make_delivery(target_type="workflow", workflow=workflow)
    event = _make_event(event_source_org_id=None)

    mock_enqueue = AsyncMock(return_value=str(uuid.uuid4()))
    fake_async_executor = _fake_module(
        "src.services.execution.async_executor",
        enqueue_system_workflow_execution=mock_enqueue,
    )

    with patch.dict(sys.modules, {"src.services.execution.async_executor": fake_async_executor}):
        await processor._queue_workflow_execution(delivery, event)

        mock_enqueue.assert_awaited_once()
        assert mock_enqueue.call_args.kwargs["org_id"] == "00000000-0000-0000-0000-000000000002"
