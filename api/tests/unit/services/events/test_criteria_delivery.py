"""Regression tests for criteria decisions at the delivery boundary."""

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.models.enums import EventDeliveryStatus, EventStatus
from src.services.events import processor as processor_module


def _event(*, priority: object) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        event_source_id=uuid.uuid4(),
        event_type="ticket.created",
        organization_id=uuid.uuid4(),
        received_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        headers={},
        data={"priority": priority},
        source_ip="10.0.0.5",
        status=EventStatus.PROCESSING,
        event_source=None,
    )


def _subscription(*, operator: str = "equals", value: object = "high") -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        criteria={
            "version": 1,
            "root": {
                "kind": "condition",
                "field": "event.body.priority",
                "operator": operator,
                "value": value,
            },
        },
    )


def test_match_creates_queueable_delivery_with_durable_decision():
    delivery = processor_module.build_event_delivery(
        event=_event(priority="high"),
        subscription=_subscription(),
    )

    assert delivery.status is EventDeliveryStatus.PENDING
    assert delivery.rule_decision == {
        "criteria_version": 1,
        "outcome": "matched",
        "code": "criteria_matched",
    }
    assert delivery.completed_at is None


def test_non_match_is_terminal_and_never_queueable():
    delivery = processor_module.build_event_delivery(
        event=_event(priority="low"),
        subscription=_subscription(),
    )

    assert delivery.status is EventDeliveryStatus.SKIPPED
    assert delivery.rule_decision["outcome"] == "not_matched"
    assert delivery.execution_id is None
    assert delivery.agent_run_id is None
    assert delivery.completed_at is not None


def test_type_error_fails_closed_without_persisting_event_value():
    delivery = processor_module.build_event_delivery(
        event=_event(priority="secret-value"),
        subscription=_subscription(operator="greater_than", value=10),
    )

    assert delivery.status is EventDeliveryStatus.SKIPPED
    assert delivery.rule_decision["outcome"] == "evaluation_error"
    assert delivery.error_message == "Rule criteria could not be evaluated"
    assert "secret-value" not in repr(delivery.rule_decision)


@pytest.mark.asyncio
async def test_queue_ignores_recorded_non_match():
    event = _event(priority="low")
    delivery = SimpleNamespace(
        id=uuid.uuid4(),
        event_id=event.id,
        event=event,
        subscription=SimpleNamespace(target_type="workflow"),
        workflow_id=uuid.uuid4(),
        execution_id=None,
        agent_run_id=None,
        status=EventDeliveryStatus.SKIPPED,
        rule_decision={
            "criteria_version": 1,
            "outcome": "not_matched",
            "code": "criteria_not_matched",
        },
        error_message=None,
        completed_at=datetime.now(timezone.utc),
    )
    session = AsyncMock()
    processor = processor_module.EventProcessor(session)
    processor._event_repo = AsyncMock()
    processor._event_repo.get_by_id = AsyncMock(return_value=event)
    processor._delivery_repo = AsyncMock()
    processor._delivery_repo.get_by_event = AsyncMock(return_value=[delivery])
    processor._broadcast_event_update = AsyncMock()
    processor._queue_workflow_execution = AsyncMock()

    assert await processor.queue_event_deliveries(event.id) == 0
    processor._queue_workflow_execution.assert_not_awaited()
    processor._delivery_repo.update_event_status.assert_awaited_once_with(event.id)
