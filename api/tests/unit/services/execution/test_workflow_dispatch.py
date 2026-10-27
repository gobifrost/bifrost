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

    monkeypatch.setattr(work_dispatch, "get_db_context", context)
    monkeypatch.setattr(async_executor, "get_dispatch_db_context", context)
    wake = AsyncMock()
    monkeypatch.setattr(work_dispatch, "wake_pool_workers", wake)
    redis = AsyncMock()
    monkeypatch.setattr(workflow_dispatch, "get_redis_client", lambda: redis)
    monkeypatch.setattr(
        "src.services.execution.queue_tracker.publish_queue_positions", AsyncMock()
    )
    updates = AsyncMock()
    monkeypatch.setattr("src.core.pubsub.publish_execution_update", updates)

    class Harness:
        sessions = async_session_factory
        wakes = wake
        cache = redis
        live_updates = updates

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


async def _message(harness, execution_id: str) -> dict:  # type: ignore[no-untyped-def]
    dispatch = await harness.dispatch(execution_id)
    assert dispatch is not None
    return dispatch.message


@pytest.fixture(autouse=True)
async def _clean(async_session_factory: async_sessionmaker[AsyncSession]):
    yield
    async with async_session_factory() as session:
        await session.execute(delete(WorkDispatch))
        await session.commit()


async def test_acceptance_commits_a_pending_run_before_waking_executors(harness) -> None:
    execution_id = await _accept()

    execution = await harness.execution(execution_id)
    assert execution is not None
    assert execution.status == ExecutionStatus.PENDING
    assert execution.started_at is None
    dispatch = await harness.dispatch(execution_id)
    assert dispatch is not None
    assert dispatch.assigned_worker_id is None
    assert dispatch.payload["parameters"] == {"x": 1}
    assert dispatch.message["dispatch_id"] == str(dispatch.id)
    assert (dispatch.message["handler"], dispatch.message["handler_version"]) == (
        "workflow.execute",
        1,
    )
    harness.wakes.assert_awaited_once()
    from src.services.execution.queue_tracker import get_queued_executions

    async with harness.sessions() as session:
        queued = await get_queued_executions(session)
    assert [(entry.execution_id, entry.queued_at) for entry in queued] == [
        (UUID(execution_id), dispatch.created_at)
    ]


async def test_reconciler_leaves_a_young_dispatch_alone(harness) -> None:
    execution_id = await _accept()
    async with harness.sessions() as session:
        await session.execute(
            update(Execution)
            .where(Execution.id == UUID(execution_id))
            .values(status=ExecutionStatus.CANCELLED)
        )
        await session.commit()

    await work_dispatch.reconcile_work_dispatches()

    assert await harness.dispatch(execution_id) is not None


async def test_queue_snapshot_outage_does_not_fail_acceptance(harness, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        "src.services.execution.queue_tracker.publish_queue_positions",
        AsyncMock(side_effect=ConnectionError("queue snapshot unavailable")),
    )

    execution_id = await _accept()

    assert (await harness.execution(execution_id)).status == ExecutionStatus.PENDING
    assert await harness.dispatch(execution_id) is not None


async def test_claim_takes_the_context_once(harness) -> None:
    execution_id = await _accept()
    message = await _message(harness, execution_id)

    async with harness.sessions() as session:
        pending = await claim_workflow_dispatch(
            session, dispatch_id=UUID(message["dispatch_id"]), execution_id=UUID(execution_id)
        )
        await session.commit()
    assert pending is not None and pending["parameters"] == {"x": 1}
    assert (await harness.execution(execution_id)).status == ExecutionStatus.RUNNING
    assert await harness.dispatch(execution_id) is None
    from src.services.execution.queue_tracker import get_queued_executions

    async with harness.sessions() as session:
        assert await get_queued_executions(session) == []

    # A duplicate delivery finds nothing to claim.
    async with harness.sessions() as session:
        duplicate = await claim_workflow_dispatch(
            session, dispatch_id=UUID(message["dispatch_id"]), execution_id=UUID(execution_id)
        )
        await session.commit()
    assert duplicate is None


async def test_cancelled_before_claim_is_discarded(harness) -> None:
    execution_id = await _accept()
    message = await _message(harness, execution_id)
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


async def test_a_run_no_capacity_claimed_fails_visibly_and_wakes_its_caller(harness) -> None:
    execution_id = await _accept(sync=True)
    await harness.age(
        execution_id,
        created_at=datetime.now(timezone.utc) - work_dispatch.POOL_CLAIM_DEADLINE * 2,
    )

    await work_dispatch.reconcile_work_dispatches()
    execution = await harness.execution(execution_id)
    assert execution.status == ExecutionStatus.FAILED
    assert "No worker capacity" in (execution.error_message or "")
    assert await harness.dispatch(execution_id) is None
    # The live view and the waiting sync caller hear about it.
    harness.live_updates.assert_awaited()
    assert harness.cache.push_result.await_args.kwargs["status"] == "Failed"


async def test_reconciler_drops_the_dispatch_of_a_cancelled_run(harness) -> None:
    execution_id = await _accept()
    async with harness.sessions() as session:
        await session.execute(
            update(Execution)
            .where(Execution.id == UUID(execution_id))
            .values(status=ExecutionStatus.CANCELLED)
        )
        await session.commit()
    await harness.age(
        execution_id,
        created_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    )

    await work_dispatch.reconcile_work_dispatches()

    assert await harness.dispatch(execution_id) is None
    assert (await harness.execution(execution_id)).status == ExecutionStatus.CANCELLED


async def test_reconciler_skips_a_run_an_executor_is_claiming(harness) -> None:
    """Lock order matches the claim (run, then dispatch): no deadlock, no theft."""
    execution_id = await _accept()
    await harness.age(
        execution_id,
        created_at=datetime.now(timezone.utc) - work_dispatch.POOL_CLAIM_DEADLINE * 2,
    )
    async with harness.sessions() as claimer:
        await claimer.execute(
            select(Execution).where(Execution.id == UUID(execution_id)).with_for_update()
        )
        await work_dispatch.reconcile_work_dispatches()
        await claimer.rollback()
    assert await harness.dispatch(execution_id) is not None
    assert (await harness.execution(execution_id)).status == ExecutionStatus.PENDING

    await work_dispatch.reconcile_work_dispatches()

    assert await harness.dispatch(execution_id) is None
    assert (await harness.execution(execution_id)).status == ExecutionStatus.FAILED


async def test_cancelled_sync_run_wakes_its_caller(harness) -> None:
    from src.services.execution.workflow_dispatch import notify_discarded_sync_run

    execution_id = await _accept(sync=True)
    async with harness.sessions() as session:
        await session.execute(
            update(Execution)
            .where(Execution.id == UUID(execution_id))
            .values(status=ExecutionStatus.CANCELLED)
        )
        await session.commit()
        await notify_discarded_sync_run(session, UUID(execution_id))

    assert harness.cache.push_result.await_args.kwargs["status"] == "Cancelled"


async def test_duplicate_of_a_started_run_never_wakes_its_caller(harness) -> None:
    from src.services.execution.workflow_dispatch import notify_discarded_sync_run

    execution_id = await _accept(sync=True)
    message = await _message(harness, execution_id)
    async with harness.sessions() as session:
        await claim_workflow_dispatch(
            session, dispatch_id=UUID(message["dispatch_id"]), execution_id=UUID(execution_id)
        )
        await session.commit()
        await notify_discarded_sync_run(session, UUID(execution_id))

    harness.cache.push_result.assert_not_awaited()


async def test_pending_run_belongs_to_an_org_scoped_workflows_organization(harness) -> None:
    from src.models.orm.organizations import Organization
    from src.models.orm.workflows import Workflow

    async with harness.sessions() as session:
        org = Organization(name=f"Contoso {uuid4().hex[:8]}", created_by="dispatch-test")
        session.add(org)
        await session.flush()
        workflow = Workflow(
            name=f"wf_{uuid4().hex[:8]}",
            function_name="run",
            path="workflows/run.py",
            organization_id=org.id,
        )
        session.add(workflow)
        await session.commit()
        org_id, workflow_id, workflow_name = org.id, workflow.id, workflow.name

    execution_id = await _accept(workflow_id=str(workflow_id), org_id=None)

    execution = await harness.execution(execution_id)
    assert (execution.organization_id, execution.workflow_name) == (org_id, workflow_name)
