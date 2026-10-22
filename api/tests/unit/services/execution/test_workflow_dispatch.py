"""Durable acceptance of workflow runs against real PostgreSQL."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import AsyncGenerator
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.models.enums import ExecutionStatus
from src.models.orm.executions import Execution
from src.models.orm.work_dispatches import WorkDispatch
from src.services import work_dispatch
from src.services.execution import async_executor, workflow_dispatch
from src.services.execution.async_executor import _publish_pending
from src.services.execution.workflow_dispatch import claim_workflow_dispatch


@pytest.fixture
def harness(
    async_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
):
    @asynccontextmanager
    async def context() -> AsyncGenerator[AsyncSession, None]:
        async with async_session_factory() as session:
            yield session
            await session.commit()

    for module in (work_dispatch, async_executor):
        monkeypatch.setattr(module, "get_db_context", context)
    published: list[tuple[str, dict]] = []

    async def publish(queue: str, message: dict) -> None:
        published.append((queue, message))

    monkeypatch.setattr(work_dispatch, "publish_message", publish)
    redis = AsyncMock()
    monkeypatch.setattr(workflow_dispatch, "get_redis_client", lambda: redis)
    monkeypatch.setattr(
        "src.services.execution.queue_tracker.add_to_queue", AsyncMock()
    )

    class Harness:
        sessions = async_session_factory
        messages = published
        cache = redis

        async def execution(self, execution_id: str) -> Execution | None:
            async with async_session_factory() as session:
                return await session.get(Execution, UUID(execution_id))

        async def dispatch(self, execution_id: str) -> WorkDispatch | None:
            async with async_session_factory() as session:
                return (
                    await session.execute(
                        select(WorkDispatch).where(WorkDispatch.subject_id == UUID(execution_id))
                    )
                ).scalar_one_or_none()

        async def age(self, execution_id: str, **values) -> None:  # type: ignore[no-untyped-def]
            async with async_session_factory() as session:
                await session.execute(
                    update(WorkDispatch)
                    .where(WorkDispatch.subject_id == UUID(execution_id))
                    .values(**values)
                )
                await session.commit()

    yield Harness()


async def _accept(**overrides) -> str:  # type: ignore[no-untyped-def]
    execution_id = str(uuid4())
    kwargs = dict(
        execution_id=execution_id,
        workflow_id=None,
        parameters={"x": 1},
        org_id=None,
        user_id="00000000-0000-0000-0000-000000000001",
        user_name="Dev",
        user_email="dev@example.com",
        form_id=None,
        startup=None,
        form_inputs={},
        embed={},
        api_key_id=None,
        sync=False,
        is_platform_admin=True,
        file_path=None,
        lineage=None,
    )
    kwargs.update(overrides)
    await _publish_pending(**kwargs)
    return execution_id


@pytest.fixture(autouse=True)
async def _clean(async_session_factory: async_sessionmaker[AsyncSession]):
    yield
    async with async_session_factory() as session:
        await session.execute(delete(WorkDispatch))
        await session.commit()


async def test_acceptance_commits_a_pending_run_before_publishing(harness) -> None:
    execution_id = await _accept()

    execution = await harness.execution(execution_id)
    assert execution is not None
    assert execution.status == ExecutionStatus.PENDING
    assert execution.started_at is None
    dispatch = await harness.dispatch(execution_id)
    assert dispatch is not None and dispatch.status == "published"
    assert dispatch.payload["parameters"] == {"x": 1}
    [(queue, message)] = harness.messages
    assert queue == "workflow-executions"
    assert message["dispatch_id"] == str(dispatch.id)
    assert message["execution_record_exists"] is True
    assert (message["handler"], message["handler_version"]) == ("workflow.execute", 1)


async def test_broker_failure_does_not_lose_an_accepted_run(harness, monkeypatch) -> None:
    async def broker_down(_queue: str, _message: dict) -> None:
        raise ConnectionError("rabbitmq unavailable")

    monkeypatch.setattr(work_dispatch, "publish_message", broker_down)
    execution_id = await _accept()  # the caller still gets its id

    assert (await harness.execution(execution_id)).status == ExecutionStatus.PENDING
    assert (await harness.dispatch(execution_id)).status == "pending"

    async def broker_up(queue: str, message: dict) -> None:
        harness.messages.append((queue, message))

    monkeypatch.setattr(work_dispatch, "publish_message", broker_up)
    # Still inside the fast path's grace: not yet the reconciler's.
    assert await work_dispatch.reconcile_work_dispatches() == 0
    await harness.age(
        execution_id,
        created_at=datetime.now(timezone.utc) - work_dispatch.PENDING_GRACE * 2,
    )
    assert await work_dispatch.reconcile_work_dispatches() == 1
    assert harness.messages[-1][1]["execution_id"] == execution_id
    assert (await harness.dispatch(execution_id)).status == "published"


async def test_redis_outage_does_not_fail_acceptance(harness) -> None:
    harness.cache.store_pending_execution.side_effect = ConnectionError("redis down")

    execution_id = await _accept()

    assert (await harness.execution(execution_id)).status == ExecutionStatus.PENDING
    assert [message["execution_id"] for _, message in harness.messages] == [execution_id]


async def test_claim_takes_the_context_once(harness) -> None:
    execution_id = await _accept()
    message = harness.messages[0][1]

    async with harness.sessions() as session:
        pending = await claim_workflow_dispatch(
            session, dispatch_id=UUID(message["dispatch_id"]), execution_id=UUID(execution_id)
        )
        await session.commit()
    assert pending is not None and pending["parameters"] == {"x": 1}
    assert (await harness.execution(execution_id)).status == ExecutionStatus.RUNNING
    assert await harness.dispatch(execution_id) is None

    # A duplicate delivery finds nothing to claim.
    async with harness.sessions() as session:
        duplicate = await claim_workflow_dispatch(
            session, dispatch_id=UUID(message["dispatch_id"]), execution_id=UUID(execution_id)
        )
        await session.commit()
    assert duplicate is None


async def test_cancelled_before_claim_is_discarded(harness) -> None:
    execution_id = await _accept()
    message = harness.messages[0][1]
    async with harness.sessions() as session:
        await session.execute(
            update(Execution)
            .where(Execution.id == UUID(execution_id))
            .values(status=ExecutionStatus.CANCELLED)
        )
        await session.commit()

    async with harness.sessions() as session:
        pending = await claim_workflow_dispatch(
            session, dispatch_id=UUID(message["dispatch_id"]), execution_id=UUID(execution_id)
        )
        await session.commit()

    assert pending is None
    assert (await harness.execution(execution_id)).status == ExecutionStatus.CANCELLED
    assert await harness.dispatch(execution_id) is None


async def test_reconciler_republishes_lost_messages_then_fails_visibly(harness) -> None:
    execution_id = await _accept()
    stale = datetime.now(timezone.utc) - work_dispatch.REDELIVERY_WINDOW * 2

    await harness.age(execution_id, last_published_at=stale)
    assert await work_dispatch.reconcile_work_dispatches() == 1
    assert len(harness.messages) == 2

    await harness.age(
        execution_id, last_published_at=stale, publish_count=work_dispatch.MAX_PUBLISHES
    )
    assert await work_dispatch.reconcile_work_dispatches() == 0
    execution = await harness.execution(execution_id)
    assert execution.status == ExecutionStatus.FAILED
    assert "No executor claimed this run" in (execution.error_message or "")
    assert await harness.dispatch(execution_id) is None


async def test_reconciler_drops_dispatch_claimed_by_an_older_worker(harness) -> None:
    """An older worker claims through execution_record_exists, not the dispatch."""
    from shared.run_cancellation import claim_pending_run_for_dispatch

    execution_id = await _accept()
    async with harness.sessions() as session:
        assert await claim_pending_run_for_dispatch(session, UUID(execution_id))
        await session.commit()
    await harness.age(
        execution_id,
        last_published_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )

    assert await work_dispatch.reconcile_work_dispatches() == 0
    assert await harness.dispatch(execution_id) is None
    assert (await harness.execution(execution_id)).status == ExecutionStatus.RUNNING
