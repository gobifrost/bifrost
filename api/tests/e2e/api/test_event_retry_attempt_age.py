"""An old delivery must receive a fresh timeout window when retried."""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from src.core.principal import UserPrincipal
from src.jobs.schedulers import event_cleanup
from src.models.enums import EventDeliveryStatus, EventStatus, ExecutionStatus
from src.models.orm.agent_runs import AgentRun
from src.models.orm.executions import Execution
from src.models.orm.events import Event, EventDelivery
from src.repositories.events import EventDeliveryRepository
from src.routers.events import retry_delivery
from src.services.events.processor import EventProcessor, QueuedDeliveries, update_delivery_from_execution
from tests.e2e.api.test_events import (
    event_source as event_source,
    subscription as subscription,
    test_workflow as test_workflow,
)


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_retry_timeout_uses_attempt_age(
    event_source, subscription, test_workflow, db_session, monkeypatch, platform_admin
):
    old = datetime.now(timezone.utc) - timedelta(days=1)
    event = Event(
        id=uuid4(),
        event_source_id=UUID(event_source["id"]),
        event_type="retry.age",
        received_at=old,
        data={},
    )
    delivery = EventDelivery(
        id=uuid4(),
        event_id=event.id,
        event_subscription_id=UUID(subscription["id"]),
        workflow_id=UUID(test_workflow["id"]),
        status=EventDeliveryStatus.FAILED,
        created_at=old,
        completed_at=old,
    )
    db_session.add_all([event, delivery])
    await db_session.flush()
    monkeypatch.setattr(
        EventProcessor, "queue_event_deliveries", AsyncMock(return_value=QueuedDeliveries())
    )

    principal = UserPrincipal(
        user_id=platform_admin.user_id,
        email=platform_admin.email,
        organization_id=platform_admin.organization_id,
    )
    response = await retry_delivery(delivery.id, None, principal, db_session)

    assert response.status == "pending"
    assert delivery.completed_at is None
    repo = EventDeliveryRepository(db_session)
    assert delivery.id not in {row.id for row in await repo.get_stuck_deliveries(15)}

    delivery.attempt_started_at = old
    await db_session.flush()
    assert delivery.id in {row.id for row in await repo.get_stuck_deliveries(15)}

    delivery.attempt_started_at = None
    await db_session.flush()
    assert delivery.id in {row.id for row in await repo.get_stuck_deliveries(15)}


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_stuck_delivery_cleanup_follows_attached_run_liveness(
    event_source, subscription, test_workflow, db_session, monkeypatch
):
    old = datetime.now(timezone.utc) - timedelta(minutes=10)
    event = Event(
        id=uuid4(),
        event_source_id=UUID(event_source["id"]),
        event_type="delivery.liveness",
        received_at=old,
        data={},
        status=EventStatus.PROCESSING,
        created_at=old,
    )
    running_execution = Execution(
        id=uuid4(),
        workflow_name="active-event-workflow",
        executed_by_name="event-test",
        status=ExecutionStatus.RUNNING,
        parameters={},
        started_at=old,
    )
    completed_execution = Execution(
        id=uuid4(),
        workflow_name="completed-event-workflow",
        executed_by_name="event-test",
        status=ExecutionStatus.SUCCESS,
        parameters={},
        started_at=old,
        completed_at=old,
    )
    failed_execution = Execution(
        id=uuid4(),
        workflow_name="failed-event-workflow",
        executed_by_name="event-test",
        status=ExecutionStatus.FAILED,
        parameters={},
        started_at=old,
        completed_at=old,
        error_message="authoritative workflow failure",
    )
    corrected_execution = Execution(
        id=uuid4(),
        workflow_name="corrected-event-workflow",
        executed_by_name="event-test",
        status=ExecutionStatus.SUCCESS,
        parameters={},
        started_at=old,
        completed_at=old,
    )
    running_agent = AgentRun(
        id=uuid4(),
        trigger_type="event",
        status="running",
    )
    completed_agent = AgentRun(
        id=uuid4(),
        trigger_type="event",
        status="completed",
        completed_at=old,
    )
    deliveries = {
        "running_execution": EventDelivery(
            id=uuid4(),
            event_id=event.id,
            event_subscription_id=UUID(subscription["id"]),
            workflow_id=UUID(test_workflow["id"]),
            execution_id=running_execution.id,
            status=EventDeliveryStatus.QUEUED,
            attempt_started_at=old,
        ),
        "completed_execution": EventDelivery(
            id=uuid4(),
            event_id=event.id,
            event_subscription_id=UUID(subscription["id"]),
            workflow_id=UUID(test_workflow["id"]),
            execution_id=completed_execution.id,
            status=EventDeliveryStatus.QUEUED,
            attempt_started_at=old,
        ),
        "running_agent": EventDelivery(
            id=uuid4(),
            event_id=event.id,
            event_subscription_id=UUID(subscription["id"]),
            workflow_id=UUID(test_workflow["id"]),
            agent_run_id=running_agent.id,
            status=EventDeliveryStatus.QUEUED,
            attempt_started_at=old,
        ),
        "failed_execution": EventDelivery(
            id=uuid4(),
            event_id=event.id,
            event_subscription_id=UUID(subscription["id"]),
            workflow_id=UUID(test_workflow["id"]),
            execution_id=failed_execution.id,
            status=EventDeliveryStatus.QUEUED,
            attempt_started_at=old,
        ),
        "completed_agent": EventDelivery(
            id=uuid4(),
            event_id=event.id,
            event_subscription_id=UUID(subscription["id"]),
            workflow_id=UUID(test_workflow["id"]),
            agent_run_id=completed_agent.id,
            status=EventDeliveryStatus.QUEUED,
            attempt_started_at=old,
        ),
        "missing_execution": EventDelivery(
            id=uuid4(),
            event_id=event.id,
            event_subscription_id=UUID(subscription["id"]),
            workflow_id=UUID(test_workflow["id"]),
            execution_id=uuid4(),
            status=EventDeliveryStatus.QUEUED,
            attempt_started_at=old,
        ),
        "corrected_execution": EventDelivery(
            id=uuid4(),
            event_id=event.id,
            event_subscription_id=UUID(subscription["id"]),
            workflow_id=UUID(test_workflow["id"]),
            execution_id=corrected_execution.id,
            status=EventDeliveryStatus.FAILED,
            attempt_count=1,
            completed_at=old,
            attempt_started_at=old,
            error_message="stale cleanup timeout",
        ),
    }
    db_session.add_all([
        event,
        running_execution,
        completed_execution,
        failed_execution,
        corrected_execution,
        running_agent,
        completed_agent,
        *deliveries.values(),
    ])
    await db_session.flush()

    @asynccontextmanager
    async def db_context():
        yield db_session

    monkeypatch.setattr(event_cleanup, "get_db_context", db_context)

    result = await event_cleanup.cleanup_stuck_events()

    for delivery in deliveries.values():
        await db_session.refresh(delivery)
    assert result["deliveries_failed"] == 1
    assert result["deliveries_reconciled"] == 3
    assert deliveries["running_execution"].status == EventDeliveryStatus.QUEUED
    assert deliveries["running_execution"].completed_at is None
    assert deliveries["running_agent"].status == EventDeliveryStatus.QUEUED
    assert deliveries["running_agent"].completed_at is None
    assert deliveries["completed_execution"].status == EventDeliveryStatus.SUCCESS
    assert deliveries["completed_execution"].completed_at is not None
    assert deliveries["completed_execution"].attempt_started_at == old
    assert deliveries["failed_execution"].status == EventDeliveryStatus.FAILED
    assert deliveries["failed_execution"].error_message == "authoritative workflow failure"
    assert deliveries["completed_agent"].status == EventDeliveryStatus.SUCCESS
    assert deliveries["completed_agent"].completed_at is not None
    assert deliveries["completed_agent"].attempt_started_at == old
    assert deliveries["missing_execution"].status == EventDeliveryStatus.FAILED

    await update_delivery_from_execution(
        str(completed_execution.id),
        ExecutionStatus.SUCCESS.value,
        session=db_session,
    )
    await db_session.refresh(deliveries["completed_execution"])
    assert deliveries["completed_execution"].attempt_count == 1

    await update_delivery_from_execution(
        str(corrected_execution.id),
        ExecutionStatus.SUCCESS.value,
        session=db_session,
    )
    await db_session.refresh(deliveries["corrected_execution"])
    assert deliveries["corrected_execution"].status == EventDeliveryStatus.SUCCESS
    assert deliveries["corrected_execution"].attempt_count == 2
    assert deliveries["corrected_execution"].error_message is None
    await db_session.refresh(event)
    assert event.status == EventStatus.PROCESSING
