"""A delivery's run is accepted in the delivery's own transaction (#769 stage 4d).

Against the real database: a crash before the commit leaves neither a run
nor a delivery record of one; the commit makes both visible together.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.models.enums import EventDeliveryStatus, EventSourceType, EventStatus
from src.models.orm.events import Event, EventDelivery, EventSource, EventSubscription
from src.models.orm.executions import Execution
from src.models.orm.work_dispatches import WorkDispatch
from src.models.orm.workflows import Workflow
from src.services.events.processor import EventProcessor


@pytest.fixture
async def world(async_session_factory: async_sessionmaker[AsyncSession]):
    """An event with deliveries to workflows; everything removed afterwards."""
    source_id = uuid4()
    event_id = uuid4()
    workflows: list[UUID] = []

    async with async_session_factory() as db:
        db.add(
            EventSource(
                id=source_id,
                name=f"acceptance-{source_id.hex[:6]}",
                source_type=EventSourceType.SCHEDULE,
                is_active=True,
                created_by="test",
            )
        )
        db.add(
            Event(
                id=event_id,
                event_source_id=source_id,
                event_type="schedule.fired",
                received_at=datetime.now(timezone.utc),
                data={},
                status=EventStatus.PROCESSING,
            )
        )
        await db.commit()

    async def delivery(
        *, workflow_type: str = "workflow", status: EventDeliveryStatus = EventDeliveryStatus.PENDING
    ) -> UUID:
        workflow_id, subscription_id, delivery_id = uuid4(), uuid4(), uuid4()
        async with async_session_factory() as db:
            db.add(
                Workflow(
                    id=workflow_id,
                    name=f"acceptance_{workflow_id.hex[:8]}",
                    function_name=f"acceptance_{workflow_id.hex[:8]}",
                    path=f"workflows/acceptance_{workflow_id.hex[:8]}.py",
                    type=workflow_type,
                )
            )
            await db.flush()
            db.add(
                EventSubscription(
                    id=subscription_id,
                    event_source_id=source_id,
                    workflow_id=workflow_id,
                    target_type="workflow",
                    is_active=True,
                    created_by="test",
                )
            )
            await db.flush()
            db.add(
                EventDelivery(
                    id=delivery_id,
                    event_id=event_id,
                    event_subscription_id=subscription_id,
                    workflow_id=workflow_id,
                    status=status,
                )
            )
            await db.commit()
        workflows.append(workflow_id)
        return delivery_id

    async def read(delivery_id: UUID) -> EventDelivery:
        async with async_session_factory() as db:
            row = await db.get(EventDelivery, delivery_id)
            assert row is not None
            return row

    async def run_exists(execution_id: UUID) -> tuple[bool, bool]:
        async with async_session_factory() as db:
            execution = await db.get(Execution, execution_id)
            dispatch = await db.scalar(
                select(WorkDispatch.id).where(WorkDispatch.subject_id == execution_id)
            )
            return execution is not None, dispatch is not None

    yield type(
        "World",
        (),
        {
            "sessions": async_session_factory,
            "event_id": event_id,
            "delivery": staticmethod(delivery),
            "read": staticmethod(read),
            "run_exists": staticmethod(run_exists),
        },
    )

    async with async_session_factory() as db:
        executions = select(EventDelivery.execution_id).where(EventDelivery.event_id == event_id)
        await db.execute(delete(WorkDispatch).where(WorkDispatch.subject_id.in_(executions)))
        await db.execute(delete(Execution).where(Execution.id.in_(executions)))
        await db.execute(delete(EventDelivery).where(EventDelivery.event_id == event_id))
        await db.execute(delete(Event).where(Event.id == event_id))
        await db.execute(delete(EventSubscription).where(EventSubscription.event_source_id == source_id))
        await db.execute(delete(EventSource).where(EventSource.id == source_id))
        await db.execute(delete(Workflow).where(Workflow.id.in_(workflows or [uuid4()])))
        await db.commit()


async def _queue(world, **kwargs) -> tuple[AsyncSession, object]:  # type: ignore[no-untyped-def]
    db = world.sessions()
    queued = await EventProcessor(db).queue_event_deliveries(world.event_id, **kwargs)
    return db, queued


async def test_a_crash_before_the_commit_leaves_no_run_and_a_pending_delivery(world) -> None:  # type: ignore[no-untyped-def]
    delivery_id = await world.delivery()
    db, queued = await _queue(world)
    staged = await db.get(EventDelivery, delivery_id)
    execution_id = staged.execution_id
    assert execution_id is not None and queued.count == 1

    await db.rollback()  # the process dies before committing
    await db.close()

    after = await world.read(delivery_id)
    assert after.status == EventDeliveryStatus.PENDING
    assert after.execution_id is None
    assert await world.run_exists(execution_id) == (False, False), "no orphan run"


async def test_the_commit_makes_the_run_and_its_delivery_record_visible_together(world) -> None:  # type: ignore[no-untyped-def]
    delivery_id = await world.delivery()
    db, queued = await _queue(world)
    await db.commit()
    await db.close()

    after = await world.read(delivery_id)
    assert after.status == EventDeliveryStatus.QUEUED
    assert after.execution_id is not None
    assert await world.run_exists(after.execution_id) == (True, True)


async def test_one_delivery_that_cannot_be_queued_does_not_undo_the_others(world) -> None:  # type: ignore[no-untyped-def]
    broken = await world.delivery(workflow_type="service")
    healthy = await world.delivery()
    db, queued = await _queue(world)
    await db.commit()
    await db.close()

    assert queued.count == 1
    assert (await world.read(broken)).status == EventDeliveryStatus.FAILED
    ok = await world.read(healthy)
    assert ok.status == EventDeliveryStatus.QUEUED
    assert await world.run_exists(ok.execution_id) == (True, True)


async def test_a_retry_never_queues_a_pending_sibling(world) -> None:  # type: ignore[no-untyped-def]
    retried = await world.delivery(status=EventDeliveryStatus.FAILED)
    sibling = await world.delivery()

    async with world.sessions() as db:
        delivery = await db.get(EventDelivery, retried)
        message, queued = await EventProcessor(db).retry_delivery(delivery)
        await db.commit()

    assert message == "Delivery queued for retry"
    assert queued.count == 1
    assert (await world.read(retried)).status == EventDeliveryStatus.QUEUED
    untouched = await world.read(sibling)
    assert untouched.status == EventDeliveryStatus.PENDING
    assert untouched.execution_id is None
