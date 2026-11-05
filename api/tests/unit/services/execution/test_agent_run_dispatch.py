"""Durable acceptance of agent runs against real PostgreSQL."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import AsyncGenerator
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.models.orm.agent_runs import AgentRun
from src.models.orm.work_dispatches import WorkDispatch
from src.services import work_dispatch
from src.services.execution import agent_run_dispatch, agent_run_service
from src.services.execution.agent_run_service import enqueue_agent_run


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
    monkeypatch.setattr(agent_run_service, "get_dispatch_db_context", context)
    monkeypatch.setattr(work_dispatch, "wake_pool_workers", AsyncMock())
    redis = AsyncMock()

    @asynccontextmanager
    async def redis_context():
        yield redis

    monkeypatch.setattr(agent_run_dispatch, "get_redis", redis_context)
    return redis


async def _run(async_session_factory, run_id: str) -> AgentRun:  # type: ignore[no-untyped-def]
    async with async_session_factory() as session:
        return (
            await session.execute(select(AgentRun).where(AgentRun.id == UUID(run_id)))
        ).scalar_one()


async def _dispatch(async_session_factory, run_id: str) -> WorkDispatch | None:  # type: ignore[no-untyped-def]
    async with async_session_factory() as session:
        return (
            await session.execute(
                select(WorkDispatch).where(WorkDispatch.subject_id == UUID(run_id))
            )
        ).scalar_one_or_none()


async def _enqueue(**overrides) -> str:  # type: ignore[no-untyped-def]
    kwargs = dict(agent_id=None, trigger_type="sdk", input_data={"task": "t"}, lineage=None)
    kwargs.update(overrides)
    return await enqueue_agent_run(**kwargs)


@pytest.fixture(autouse=True)
async def _clean(async_session_factory: async_sessionmaker[AsyncSession]):
    yield
    async with async_session_factory() as session:
        await session.execute(delete(WorkDispatch))
        await session.commit()


async def test_agent_run_and_context_are_accepted_together(
    harness, async_session_factory
) -> None:
    run_id = await _enqueue()

    run = await _run(async_session_factory, run_id)
    dispatch = await _dispatch(async_session_factory, run_id)
    assert run.status == "queued"
    assert dispatch is not None
    assert dispatch.assigned_worker_id is None
    assert dispatch.payload["input"] == {"task": "t"}


async def test_unclaimed_agent_run_fails_visibly_and_wakes_its_caller(
    harness, async_session_factory
) -> None:
    run_id = await _enqueue(sync=True)
    async with async_session_factory() as session:
        await session.execute(
            update(WorkDispatch)
            .where(WorkDispatch.subject_id == UUID(run_id))
            .values(
                created_at=datetime.now(timezone.utc) - work_dispatch.POOL_CLAIM_DEADLINE * 2,
            )
        )
        await session.commit()

    await work_dispatch.reconcile_work_dispatches()

    run = await _run(async_session_factory, run_id)
    assert run.status == "failed"
    assert "No worker capacity" in (run.error or "")
    assert await _dispatch(async_session_factory, run_id) is None
    harness.lpush.assert_awaited_once()


async def test_cancelled_agent_run_dispatch_is_dropped(harness, async_session_factory) -> None:
    run_id = await _enqueue()
    async with async_session_factory() as session:
        await session.execute(
            update(AgentRun).where(AgentRun.id == UUID(run_id)).values(status="cancelled")
        )
        await session.execute(
            update(WorkDispatch)
            .where(WorkDispatch.subject_id == UUID(run_id))
            .values(created_at=datetime.now(timezone.utc) - timedelta(minutes=5))
        )
        await session.commit()

    await work_dispatch.reconcile_work_dispatches()
    assert await _dispatch(async_session_factory, run_id) is None
    assert (await _run(async_session_factory, run_id)).status == "cancelled"


async def test_a_run_whose_fork_was_refused_goes_back_to_waiting(
    harness, async_session_factory, monkeypatch
) -> None:
    from src.models.orm.execution_workers import ExecutionWorker, ExecutionWorkerClaim

    monkeypatch.setattr(agent_run_dispatch, "get_db_context", async_session_factory)
    run_id = await _enqueue()
    worker_id = uuid4()
    async with async_session_factory() as session:
        session.add(ExecutionWorker(
            id=worker_id, form="static", state="ready", resource_class="standard", slots=0,
        ))
        await session.flush()
        session.add(ExecutionWorkerClaim(
            worker_id=worker_id, family="agent_run", subject_id=UUID(run_id),
        ))
        await session.execute(
            update(WorkDispatch)
            .where(WorkDispatch.subject_id == UUID(run_id))
            .values(assigned_worker_id=worker_id)
        )
        await session.commit()
    dispatch = await _dispatch(async_session_factory, run_id)
    assert dispatch is not None
    try:
        await agent_run_dispatch.release_agent_run(
            worker_id=worker_id, run_id=UUID(run_id), dispatch_id=dispatch.id,
        )

        released = await _dispatch(async_session_factory, run_id)
        assert released is not None and released.assigned_worker_id is None
        assert (await _run(async_session_factory, run_id)).status == "queued"
        async with async_session_factory() as session:
            claims = (await session.execute(
                select(ExecutionWorkerClaim).where(ExecutionWorkerClaim.worker_id == worker_id)
            )).scalars().all()
        assert claims == []
    finally:
        async with async_session_factory() as session:
            await session.execute(delete(ExecutionWorker).where(ExecutionWorker.id == worker_id))
            await session.commit()
