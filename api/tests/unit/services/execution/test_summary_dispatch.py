"""Agent-run summaries as a durable dispatch family (#769 stage 4b)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.jobs.platform.base import PlatformJobDeferred
from src.jobs.platform.summary_backfill import SummaryBackfillPayload, run_summary_backfill
from src.jobs.summarize_worker import handle_summarize_message
from src.models.orm.agent_runs import AgentRun
from src.models.orm.agents import Agent
from src.models.orm.execution_workers import ExecutionWorker, ExecutionWorkerClaim
from src.models.orm.summary_backfill_job import SummaryBackfillJob
from src.models.orm.work_dispatches import WorkDispatch
from src.services.execution import backfill_tracker, summary_dispatch
from src.services.execution.summary_dispatch import (
    BACKFILL_HANDLER,
    FAMILY,
    claim_summary,
    lock_waiting_summary,
    report_unclaimed_summary,
    stage_summary,
)
from src.services.worker_pool.recovery import end_runs_of_lost_worker


@pytest.fixture
async def world(async_session_factory: async_sessionmaker[AsyncSession]):
    """An agent, plus helpers for runs and backfill jobs; all removed after."""
    agent_id = uuid4()
    runs: list[UUID] = []
    jobs: list[UUID] = []
    workers: list[UUID] = []
    async with async_session_factory() as db:
        db.add(
            Agent(
                id=agent_id,
                name=f"Summary dispatch {agent_id.hex[:8]}",
                description="summary dispatch unit test",
                system_prompt="test",
                access_level="authenticated",
                created_by="test",
            )
        )
        await db.commit()

    async def run(
        *,
        status: str = "completed",
        summary_status: str = "pending",
        backfill_job_id: UUID | None = None,
    ) -> UUID:
        run_id = uuid4()
        async with async_session_factory() as db:
            db.add(
                AgentRun(
                    id=run_id,
                    agent_id=agent_id,
                    trigger_type="test",
                    status=status,
                    iterations_used=1,
                    tokens_used=1,
                    summary_status=summary_status,
                    summary_backfill_job_id=backfill_job_id,
                )
            )
            await db.commit()
        runs.append(run_id)
        return run_id

    async def backfill_job() -> UUID:
        job_id = uuid4()
        async with async_session_factory() as db:
            db.add(SummaryBackfillJob(id=job_id, requested_by=uuid4(), status="running", total=1))
            await db.commit()
        jobs.append(job_id)
        return job_id

    async def stage(run_id: UUID, *, backfill: bool = False) -> WorkDispatch | None:
        async with async_session_factory() as db:
            dispatch = await stage_summary(db, run_id, backfill=backfill)
            await db.commit()
        return dispatch

    async def dispatches(run_id: UUID) -> list[WorkDispatch]:
        async with async_session_factory() as db:
            return list(
                await db.scalars(
                    select(WorkDispatch).where(
                        WorkDispatch.family == FAMILY, WorkDispatch.subject_id == run_id
                    )
                )
            )

    async def summary_status(run_id: UUID) -> str:
        async with async_session_factory() as db:
            return (await db.get(AgentRun, run_id)).summary_status  # type: ignore[union-attr]

    yield type(
        "World",
        (),
        {
            "sessions": async_session_factory,
            "run": staticmethod(run),
            "backfill_job": staticmethod(backfill_job),
            "stage": staticmethod(stage),
            "dispatches": staticmethod(dispatches),
            "summary_status": staticmethod(summary_status),
            "workers": workers,
        },
    )

    async with async_session_factory() as db:
        await db.execute(
            delete(WorkDispatch).where(
                WorkDispatch.family == FAMILY, WorkDispatch.subject_id.in_(runs or [uuid4()])
            )
        )
        await db.execute(delete(ExecutionWorker).where(ExecutionWorker.id.in_(workers or [uuid4()])))
        await db.execute(delete(AgentRun).where(AgentRun.id.in_(runs or [uuid4()])))
        await db.execute(delete(SummaryBackfillJob).where(SummaryBackfillJob.id.in_(jobs or [uuid4()])))
        await db.execute(delete(Agent).where(Agent.id == agent_id))
        await db.commit()


async def test_a_dispatched_summary_is_claimed_once(world) -> None:  # type: ignore[no-untyped-def]
    run_id = await world.run()
    dispatch = await world.stage(run_id)
    assert dispatch is not None

    async with world.sessions() as db:
        assert await claim_summary(db, run_id, dispatch.id)
        await db.commit()
    async with world.sessions() as db:
        assert not await claim_summary(db, run_id, dispatch.id), "a duplicate delivery skips"
        await db.commit()

    assert await world.summary_status(run_id) == "generating"
    assert await world.dispatches(run_id) == []


async def test_a_second_request_while_one_is_pending_is_a_no_op(world) -> None:  # type: ignore[no-untyped-def]
    run_id = await world.run()
    assert await world.stage(run_id) is not None
    assert await world.stage(run_id) is None
    assert len(await world.dispatches(run_id)) == 1


async def test_a_run_that_moved_on_is_not_claimed(world) -> None:  # type: ignore[no-untyped-def]
    run_id = await world.run(summary_status="completed")
    dispatch = await world.stage(run_id)
    assert dispatch is not None

    async with world.sessions() as db:
        assert not await claim_summary(db, run_id, dispatch.id)
        await db.commit()

    assert await world.summary_status(run_id) == "completed"
    assert await world.dispatches(run_id) == [], "the stale dispatch is taken away"


async def test_the_reconciler_waits_only_on_pending_summaries_of_completed_runs(world) -> None:  # type: ignore[no-untyped-def]
    waiting = await world.run()
    summarized = await world.run(summary_status="completed")
    failed_run = await world.run(status="failed")
    async with world.sessions() as db:
        assert await lock_waiting_summary(db, waiting) is True
        assert await lock_waiting_summary(db, summarized) is False
        assert await lock_waiting_summary(db, failed_run) is False
        assert await lock_waiting_summary(db, uuid4()) is False


async def test_a_backfill_stages_its_summaries_before_handing_them_off(world, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def context() -> AsyncGenerator[AsyncSession, None]:
        async with world.sessions() as session:
            yield session
            await session.commit()

    monkeypatch.setattr("src.core.database.get_db_context", context)
    hand_off = AsyncMock(return_value=True)
    monkeypatch.setattr(summary_dispatch, "hand_off", hand_off)
    job_id = await world.backfill_job()
    run_ids = [await world.run(backfill_job_id=job_id), await world.run(backfill_job_id=job_id)]

    with pytest.raises(PlatformJobDeferred):
        await run_summary_backfill(
            AsyncMock(), SummaryBackfillPayload(backfill_job_id=job_id, run_ids=run_ids)
        )

    for run_id in run_ids:
        [dispatch] = await world.dispatches(run_id)
        assert dispatch.handler == BACKFILL_HANDLER
    assert hand_off.await_count == 2


async def test_the_handler_counts_a_claimed_summary_against_its_backfill(world, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    job_id = await world.backfill_job()
    run_id = await world.run(backfill_job_id=job_id)
    dispatch = await world.stage(run_id, backfill=True)
    assert dispatch is not None
    summarize = AsyncMock()
    record = AsyncMock()
    monkeypatch.setattr("src.jobs.summarize_worker.summarize_run", summarize)
    monkeypatch.setattr(backfill_tracker, "record_backfill_outcome", record)
    message = dict(dispatch.message)

    await handle_summarize_message(message, session_factory=world.sessions)
    await handle_summarize_message(message, session_factory=world.sessions)

    summarize.assert_awaited_once()
    record.assert_awaited_once()
    assert record.await_args.kwargs["job_id"] == job_id
    assert record.await_args.kwargs["succeeded"] is True


async def test_a_lost_executor_s_summary_is_requested_again(world) -> None:  # type: ignore[no-untyped-def]
    job_id = await world.backfill_job()
    run_id = await world.run(summary_status="generating", backfill_job_id=job_id)
    worker_id = uuid4()
    world.workers.append(worker_id)
    async with world.sessions() as db:
        db.add(ExecutionWorker(id=worker_id, form="static", state="lost", slots=1))
        await db.flush()
        db.add(ExecutionWorkerClaim(worker_id=worker_id, family=FAMILY, subject_id=run_id))
        await db.commit()

    async with world.sessions() as db:
        await end_runs_of_lost_worker(db, worker_id)
        await db.commit()

    assert await world.summary_status(run_id) == "pending"
    [dispatch] = await world.dispatches(run_id)
    assert dispatch.handler == BACKFILL_HANDLER


async def test_an_unclaimed_backfill_summary_counts_as_failed(world, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    job_id = await world.backfill_job()
    run_id = await world.run(backfill_job_id=job_id)
    live_run = await world.run()
    record = AsyncMock()
    monkeypatch.setattr(backfill_tracker, "record_backfill_outcome", record)
    monkeypatch.setattr("src.core.database.get_session_factory", lambda: world.sessions)

    await report_unclaimed_summary(run_id, {}, "no capacity")
    await report_unclaimed_summary(live_run, {}, "no capacity")

    record.assert_awaited_once()
    assert record.await_args.kwargs["job_id"] == job_id
    assert record.await_args.kwargs["succeeded"] is False

