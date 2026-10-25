"""Physical worker scaling must not spend the API's agent memory budget."""

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


async def _agent_dispatch(db, request: int):
    async with db.context() as session:
        row = work_dispatch.stage_dispatch(
            session, family="agent_run", subject_id=uuid4(), handler="agent.run",
            handler_version=1, message={}, payload={}, memory_request_bytes=request,
        )
        await session.flush()
        return row.id


async def test_api_agent_capacity_does_not_cover_a_workflow_launch(db) -> None:
    await _executor(db, "api", ["agent.run", "agent.summarize"], GIB)
    await _accept_pool_run()
    pool = controller.WorkerPoolController(FakeCluster(), SETTINGS, SPEC)  # type: ignore[arg-type]

    launched = await pool.launch_for_demand(POLICY)

    assert len(launched) == 1
    async with db.context() as session:
        worker = await session.get(ExecutionWorker, launched[0])
        assert worker is not None
        assert worker.supported_handlers == ["workflow.execute"]


async def test_waiting_agent_work_does_not_launch_physical_workers(db) -> None:
    await _agent_dispatch(db, 32 * MIB)
    pool = controller.WorkerPoolController(FakeCluster(), SETTINGS, SPEC)  # type: ignore[arg-type]

    assert await pool.launch_for_demand(POLICY) == []


async def test_largest_budget_uses_only_the_handlers_actual_memory_domain(db) -> None:
    await _executor(db, "api", ["agent.run", "agent.summarize"], 512 * MIB)
    await _executor(db, "launcher", ["workflow.execute"], 2 * GIB)
    async with get_redis() as redis:
        await redis.set(LAUNCHABLE_KEY, str(4 * GIB))

    async with db.context() as session:
        assert await work_dispatch.largest_budget(session, "agent.run") == 512 * MIB
        assert await work_dispatch.largest_budget(session, "workflow.execute") == 4 * GIB


async def test_remote_capacity_does_not_prove_an_agent_request_can_ever_fit(db) -> None:
    await _executor(db, "launcher", ["workflow.execute"], 2 * GIB)
    async with get_redis() as redis:
        await redis.set(LAUNCHABLE_KEY, str(4 * GIB))

    async with db.context() as session:
        assert await work_dispatch.largest_budget(session, "agent.run") is None


async def test_reconciler_reports_oversized_agent_against_api_capacity(db, monkeypatch) -> None:
    await _executor(db, "api", ["agent.run"], 16 * MIB)
    await _executor(db, "launcher", ["workflow.execute"], 2 * GIB)
    dispatch_id = await _agent_dispatch(db, 32 * MIB)
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
