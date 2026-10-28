"""Physical workers run workflow and agent runs; the API's budget runs summaries."""

from unittest.mock import AsyncMock
from uuid import uuid4

from sqlalchemy import select

from src.core.cache.redis_client import get_redis
from src.models.orm.execution_workers import ExecutionWorker
from src.services import work_dispatch
from src.services.memory_requests import LAUNCHABLE_KEY, MIB
from src.services.worker_pool import claims, controller
from tests.unit.services.worker_pool.test_worker_pool import (
    GIB,
    POLICY,
    SETTINGS,
    SPEC,
    FakeCluster,
    _accept_pool_run,
    _clean as _clean,
    db as db_fixture,
)

db = db_fixture


async def _executor(db, name: str, handlers: list[str], budget: int) -> None:
    worker_id = await claims.register_static_worker(
        hostname=name, supported_handlers=handlers,
    )
    assert await claims.mark_static_ready(worker_id, memory_budget_bytes=budget)


async def _dispatch(db, handler: str, request: int):
    async with db.context() as session:
        row = work_dispatch.stage_dispatch(
            session, family="agent_run" if handler == "agent.run" else "agent_summary",
            subject_id=uuid4(), handler=handler,
            handler_version=1, message={}, payload={}, memory_request_bytes=request,
        )
        await session.flush()
        return row.id


async def test_launched_jobs_advertise_workflow_and_agent_runs(db) -> None:
    await _accept_pool_run()
    pool = controller.WorkerPoolController(FakeCluster(), SETTINGS, SPEC)  # type: ignore[arg-type]

    [launched] = await pool.launch_for_demand(POLICY)

    async with db.context() as session:
        worker = await session.get(ExecutionWorker, launched)
        assert worker is not None
        assert worker.supported_handlers == ["workflow.execute", "agent.run"]


async def test_waiting_agent_work_with_no_free_worker_launches_one(db) -> None:
    await _dispatch(db, "agent.run", 32 * MIB)
    pool = controller.WorkerPoolController(FakeCluster(), SETTINGS, SPEC)  # type: ignore[arg-type]

    assert len(await pool.launch_for_demand(POLICY)) == 1


async def test_agent_work_fits_a_ready_worker_that_claims_agent_runs(db) -> None:
    await _executor(db, "launcher", ["workflow.execute", "agent.run"], GIB)
    await _dispatch(db, "agent.run", 32 * MIB)
    pool = controller.WorkerPoolController(FakeCluster(), SETTINGS, SPEC)  # type: ignore[arg-type]

    assert await pool.launch_for_demand(POLICY) == []


async def test_api_summary_capacity_never_covers_a_launch_and_summaries_never_launch(db) -> None:
    await _executor(db, "api-agent", ["agent.summarize", "agent.summarize_backfill"], GIB)
    await _dispatch(db, "agent.summarize", 16 * MIB)
    pool = controller.WorkerPoolController(FakeCluster(), SETTINGS, SPEC)  # type: ignore[arg-type]
    assert await pool.launch_for_demand(POLICY) == []

    await _dispatch(db, "agent.run", 32 * MIB)
    assert len(await pool.launch_for_demand(POLICY)) == 1


async def test_largest_budget_counts_launchable_workers_for_agent_runs(db) -> None:
    await _executor(db, "api-agent", ["agent.summarize"], 512 * MIB)
    await _executor(db, "launcher", ["workflow.execute", "agent.run"], 2 * GIB)
    async with get_redis() as redis:
        await redis.set(LAUNCHABLE_KEY, str(4 * GIB))

    async with db.context() as session:
        assert await work_dispatch.largest_budget(session, "agent.run") == 4 * GIB
        assert await work_dispatch.largest_budget(session, "workflow.execute") == 4 * GIB
        assert await work_dispatch.largest_budget(session, "agent.summarize") == 512 * MIB


async def test_reconciler_reports_an_agent_run_larger_than_any_worker(db, monkeypatch) -> None:
    await _executor(db, "launcher", ["workflow.execute", "agent.run"], 16 * MIB)
    dispatch_id = await _dispatch(db, "agent.run", 32 * MIB)
    fail, report = AsyncMock(), AsyncMock()
    monkeypatch.setattr(work_dispatch, "_families", lambda: {
        "agent_run": work_dispatch.DispatchFamily(
            lock_waiting=AsyncMock(return_value=True), fail=fail, report_failed=report,
        ),
    })

    await work_dispatch.reconcile_work_dispatches()

    fail.assert_awaited_once()
    report.assert_awaited_once()
    async with db.context() as session:
        from src.models.orm.work_dispatches import WorkDispatch

        assert await session.scalar(select(WorkDispatch.id).where(WorkDispatch.id == dispatch_id)) is None
