"""Physical worker capacity covers execution and summary dispatches."""

from unittest.mock import AsyncMock
from uuid import UUID, uuid4
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from src.core.cache.redis_client import get_redis
from src.models.orm.execution_workers import ExecutionWorker
from src.models.orm.organizations import Organization
from src.models.orm.services import ServiceAttempt, ServiceDefinition
from src.models.orm.workflows import Workflow
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


@pytest_asyncio.fixture
async def pending_service(db):
    org_id, workflow_id, service_id = uuid4(), uuid4(), uuid4()
    async with db.context() as session:
        session.add(Organization(id=org_id, name=f"Capacity {org_id}", created_by="test"))
        await session.flush()
        session.add(Workflow(
            id=workflow_id, name=f"capacity_{workflow_id.hex}",
            path=f"workflows/{workflow_id.hex}.py", function_name="service",
            type="service", is_active=True, organization_id=org_id,
            memory_request_mib=256,
        ))
        await session.flush()
        session.add(ServiceDefinition(
            id=service_id, workflow_id=workflow_id, organization_id=org_id,
            enabled=True, desired_state="running", created_by="test",
        ))
    try:
        yield service_id, workflow_id
    finally:
        async with db.context() as session:
            await session.execute(delete(ServiceDefinition).where(ServiceDefinition.id == service_id))
            await session.execute(delete(Workflow).where(Workflow.id == workflow_id))
            await session.execute(delete(Organization).where(Organization.id == org_id))


async def test_pending_service_launches_capacity_without_a_dispatch(db, pending_service) -> None:
    pool = controller.WorkerPoolController(FakeCluster(), SETTINGS, SPEC)  # type: ignore[arg-type]

    assert len(await pool.launch_for_demand(POLICY)) == 1
    # The reserved worker covers the demand until it starts; no duplicate launch.
    assert await pool.launch_for_demand(POLICY) == []


async def test_pending_service_uses_existing_physical_memory(db, pending_service) -> None:
    await _executor(db, "launcher", ["workflow.execute"], GIB)
    pool = controller.WorkerPoolController(FakeCluster(), SETTINGS, SPEC)  # type: ignore[arg-type]

    assert await pool.launch_for_demand(POLICY) == []


async def test_large_pending_service_launches_a_sized_worker(db, pending_service) -> None:
    _, workflow_id = pending_service
    async with db.context() as session:
        workflow = await session.get(Workflow, workflow_id)
        workflow.memory_request_mib = 4096
    pool = controller.WorkerPoolController(FakeCluster(), SETTINGS, SPEC)  # type: ignore[arg-type]

    [worker_id] = await pool.launch_for_demand(POLICY)
    async with db.context() as session:
        worker = await session.get(ExecutionWorker, worker_id)
        assert worker.resource_class == "sized"
        assert worker.reserved_for_dispatch_id is None
        assert pool._sizing(POLICY, []).budget_bytes(worker.memory_limit_bytes) >= 4096 * MIB


@pytest.mark.parametrize("reason", [
    "disabled", "stopped", "blocked", "backoff", "inactive", "converted", "global", "scope_mismatch", "live",
])
async def test_ineligible_service_does_not_launch_capacity(db, pending_service, reason) -> None:
    service_id, workflow_id = pending_service
    async with db.context() as session:
        service = await session.get(ServiceDefinition, service_id)
        workflow = await session.get(Workflow, workflow_id)
        if reason == "disabled":
            service.enabled = False
        elif reason == "stopped":
            service.desired_state = "stopped"
        elif reason == "blocked":
            service.blocked_reason = "crash_loop"
        elif reason == "backoff":
            service.restart_eligible_at = datetime.now(timezone.utc) + timedelta(hours=1)
        elif reason == "inactive":
            workflow.is_active = False
        elif reason == "converted":
            workflow.type = "workflow"
        elif reason == "global":
            service.organization_id = workflow.organization_id = None
        elif reason == "scope_mismatch":
            workflow.organization_id = None
        elif reason == "live":
            session.add(ServiceAttempt(
                service_id=service_id, state="running", lease_token="test",
                lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
            ))
    pool = controller.WorkerPoolController(FakeCluster(), SETTINGS, SPEC)  # type: ignore[arg-type]

    assert await pool.launch_for_demand(POLICY) == []


async def _executor(db, name: str, handlers: list[str], budget: int) -> UUID:
    worker_id = await claims.register_static_worker(
        hostname=name, supported_handlers=handlers,
    )
    assert await claims.mark_static_ready(worker_id, memory_budget_bytes=budget)
    assert await claims.heartbeat_worker(
        worker_id, active_runs=0, runs_handled=0, package_generation=0,
        memory_pressured=False, memory_budget_bytes=budget, memory_reserved_bytes=0,
        active_handler_counts={},
    ) == "ready"
    return worker_id


async def test_busy_summary_slot_launches_capacity_despite_free_memory(db) -> None:
    worker_id = await _executor(db, "launcher", ["workflow.execute", "agent.summarize"], GIB)
    assert await claims.heartbeat_worker(
        worker_id, active_runs=1, runs_handled=0, package_generation=0,
        memory_pressured=False, memory_budget_bytes=GIB, memory_reserved_bytes=16 * MIB,
        active_handler_counts={"agent.summarize": 1},
    ) == "ready"
    await _dispatch(db, "agent.summarize", 16 * MIB)
    pool = controller.WorkerPoolController(FakeCluster(), SETTINGS, SPEC)  # type: ignore[arg-type]

    assert len(await pool.launch_for_demand(POLICY)) == 1
    async with db.context() as session:
        worker = await session.get(ExecutionWorker, worker_id)
        assert worker.active_handler_counts == {"agent.summarize": 1}


async def test_backfill_occupancy_does_not_cover_or_block_live_summary_capacity(db) -> None:
    worker_id = await _executor(
        db, "launcher", ["workflow.execute", "agent.summarize", "agent.summarize_backfill"], GIB,
    )
    assert await claims.heartbeat_worker(
        worker_id, active_runs=1, runs_handled=0, package_generation=0,
        memory_pressured=False, memory_budget_bytes=GIB, memory_reserved_bytes=16 * MIB,
        active_handler_counts={"agent.summarize_backfill": 1},
    ) == "ready"
    await _dispatch(db, "agent.summarize", 16 * MIB)
    pool = controller.WorkerPoolController(FakeCluster(), SETTINGS, SPEC)  # type: ignore[arg-type]
    assert await pool.launch_for_demand(POLICY) == []

    await _dispatch(db, "agent.summarize_backfill", 16 * MIB)
    assert len(await pool.launch_for_demand(POLICY)) == 1


async def test_each_new_worker_covers_only_one_live_summary(db) -> None:
    await _dispatch(db, "agent.summarize", 16 * MIB)
    await _dispatch(db, "agent.summarize", 16 * MIB)
    pool = controller.WorkerPoolController(FakeCluster(), SETTINGS, SPEC)  # type: ignore[arg-type]

    assert len(await pool.launch_for_demand(POLICY)) == 2
    assert await pool.launch_for_demand(POLICY) == []


async def test_unreported_summary_occupancy_does_not_cover_demand(db) -> None:
    worker_id = await claims.register_static_worker(
        hostname="unreported", supported_handlers=["workflow.execute", "agent.summarize"],
    )
    assert await claims.mark_static_ready(worker_id, memory_budget_bytes=GIB)
    await _dispatch(db, "agent.summarize", 16 * MIB)
    pool = controller.WorkerPoolController(FakeCluster(), SETTINGS, SPEC)  # type: ignore[arg-type]

    assert len(await pool.launch_for_demand(POLICY)) == 1


async def _dispatch(db, handler: str, request: int):
    async with db.context() as session:
        row = work_dispatch.stage_dispatch(
            session, family="agent_run" if handler == "agent.run" else "agent_summary",
            subject_id=uuid4(), handler=handler,
            handler_version=1, message={}, payload={}, memory_request_bytes=request,
        )
        await session.flush()
        return row.id


async def test_launched_jobs_advertise_all_execution_handlers(db) -> None:
    await _accept_pool_run()
    pool = controller.WorkerPoolController(FakeCluster(), SETTINGS, SPEC)  # type: ignore[arg-type]

    [launched] = await pool.launch_for_demand(POLICY)

    async with db.context() as session:
        worker = await session.get(ExecutionWorker, launched)
        assert worker is not None
        assert worker.supported_handlers == [
            "workflow.execute", "agent.run", "agent.summarize", "agent.summarize_backfill",
        ]


async def test_waiting_agent_work_with_no_free_worker_launches_one(db) -> None:
    await _dispatch(db, "agent.run", 32 * MIB)
    pool = controller.WorkerPoolController(FakeCluster(), SETTINGS, SPEC)  # type: ignore[arg-type]

    assert len(await pool.launch_for_demand(POLICY)) == 1


async def test_agent_work_fits_a_ready_worker_that_claims_agent_runs(db) -> None:
    await _executor(db, "launcher", ["workflow.execute", "agent.run"], GIB)
    await _dispatch(db, "agent.run", 32 * MIB)
    pool = controller.WorkerPoolController(FakeCluster(), SETTINGS, SPEC)  # type: ignore[arg-type]

    assert await pool.launch_for_demand(POLICY) == []


async def test_legacy_api_summary_capacity_does_not_cover_physical_summary_demand(db) -> None:
    await _executor(db, "api-agent", ["agent.summarize", "agent.summarize_backfill"], GIB)
    await _dispatch(db, "agent.summarize", 16 * MIB)
    pool = controller.WorkerPoolController(FakeCluster(), SETTINGS, SPEC)  # type: ignore[arg-type]
    assert len(await pool.launch_for_demand(POLICY)) == 1


async def test_backfill_summary_without_a_worker_launches_capacity(db) -> None:
    await _dispatch(db, "agent.summarize_backfill", 16 * MIB)
    pool = controller.WorkerPoolController(FakeCluster(), SETTINGS, SPEC)  # type: ignore[arg-type]

    assert len(await pool.launch_for_demand(POLICY)) == 1


async def test_summary_fits_a_capable_physical_worker(db) -> None:
    await _executor(db, "launcher", ["workflow.execute", "agent.summarize"], GIB)
    await _dispatch(db, "agent.summarize", 16 * MIB)
    pool = controller.WorkerPoolController(FakeCluster(), SETTINGS, SPEC)  # type: ignore[arg-type]

    assert await pool.launch_for_demand(POLICY) == []


async def test_largest_budget_counts_launchable_workers_for_agent_runs(db) -> None:
    await _executor(db, "api-agent", ["agent.summarize"], 512 * MIB)
    await _executor(db, "launcher", ["workflow.execute", "agent.run"], 2 * GIB)
    async with get_redis() as redis:
        await redis.set(LAUNCHABLE_KEY, str(4 * GIB))

    async with db.context() as session:
        assert await work_dispatch.largest_budget(session, "agent.run") == 4 * GIB
        assert await work_dispatch.largest_budget(session, "workflow.execute") == 4 * GIB
        assert await work_dispatch.largest_budget(session, "agent.summarize") == 4 * GIB


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
