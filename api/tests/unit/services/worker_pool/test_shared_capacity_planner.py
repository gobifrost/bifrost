"""Contracts for the one locked capacity planner used by every provider mix."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock
from uuid import uuid4

from sqlalchemy import delete, func, select

from shared.models import CloudflareCapacityRules
from src.models.orm.execution_workers import ExecutionWorker
from src.models.orm.platform_jobs import PlatformJob
from src.models.orm.work_dispatches import WorkDispatch
from src.services.worker_pool.coordinator import HybridWorkerPoolCoordinator
from src.services.worker_pool.external_controller import ExternalWorkerPoolController
from src.services.worker_pool.controller import WorkerPoolController, plan_capacity_launches
from tests.unit.services.worker_pool.test_external_capacity import capacity as capacity_fixture
from tests.unit.services.worker_pool.test_worker_pool import (
    GIB,
    MIB,
    POLICY,
    SETTINGS,
    SPEC,
    FakeCluster,
    _accept_pool_run,
    _clean as _clean,
    _set_request,
    _static_executor,
    db as db_fixture,
)


db = db_fixture
capacity = capacity_fixture


async def test_single_provider_uses_the_shared_locked_planner(db):
    """The provider-neutral planner remains valid without an overflow provider."""
    run = await _accept_pool_run()
    await _set_request(db.sessions, run, 256 * MIB)
    kubernetes = WorkerPoolController(FakeCluster(), SETTINGS, SPEC)  # type: ignore[arg-type]

    launched = await plan_capacity_launches(replace(POLICY, warm_workers=0), (kubernetes,))

    assert len(launched[kubernetes]) == 1


async def test_concurrent_shared_planner_passes_do_not_over_reserve_global_capacity(db):
    """The advisory lock makes one demand snapshot win at the global ceiling."""
    first, second = await _accept_pool_run(), await _accept_pool_run()
    await _set_request(db.sessions, first, 256 * MIB)
    await _set_request(db.sessions, second, 256 * MIB)
    kubernetes = WorkerPoolController(FakeCluster(), SETTINGS, SPEC)  # type: ignore[arg-type]
    policy = replace(POLICY, warm_workers=0, max_workers=1)

    outcomes = await asyncio.gather(
        plan_capacity_launches(policy, (kubernetes,)),
        plan_capacity_launches(policy, (kubernetes,)),
    )

    assert sum(len(outcome[kubernetes]) for outcome in outcomes) == 1
    async with db.context() as session:
        assert await session.scalar(select(func.count()).select_from(ExecutionWorker).where(
            ExecutionWorker.form == "job",
        )) == 1


async def test_hybrid_planner_reuses_compatible_static_capacity_before_reserving(db, capacity):
    """A real ready executor covers work before either managed provider reserves."""
    run = await _accept_pool_run()
    await _set_request(db.sessions, run, 256 * MIB)
    await _static_executor("static-capacity")
    kubernetes = WorkerPoolController(FakeCluster(), SETTINGS, SPEC)  # type: ignore[arg-type]
    cloud = ExternalWorkerPoolController(capacity.client, SETTINGS, capacity.provider.profile, hybrid=True)

    await HybridWorkerPoolCoordinator(kubernetes, AsyncMock(return_value=cloud)).reconcile(
        replace(capacity.policy, warm_workers=0),
    )

    async with db.context() as session:
        managed = (await session.scalars(select(ExecutionWorker).where(
            ExecutionWorker.form.in_(("job", "external")),
        ))).all()
    assert managed == []


async def test_selected_workflow_starts_cloudflare_before_kubernetes(db, capacity):
    """Selected categories choose Cloudflare only for a new reservation."""
    run = await _accept_pool_run()
    await _set_request(db.sessions, run, 256 * MIB)
    kubernetes = WorkerPoolController(FakeCluster(), SETTINGS, SPEC)  # type: ignore[arg-type]
    cloud = ExternalWorkerPoolController(
        capacity.client,
        SETTINGS,
        capacity.provider.profile,
        hybrid=True,
        rules=CloudflareCapacityRules(workflows=True, agents=False, app_builds=False),
    )

    launched = await plan_capacity_launches(replace(capacity.policy, warm_workers=0), (kubernetes, cloud))

    assert launched[kubernetes] == []
    assert len(launched[cloud]) == 1


async def test_unselected_workflow_does_not_start_cloudflare_when_kubernetes_exists(db, capacity):
    """Rules are start triggers, so a local provider receives unselected work."""
    run = await _accept_pool_run()
    await _set_request(db.sessions, run, 256 * MIB)
    kubernetes = WorkerPoolController(FakeCluster(), SETTINGS, SPEC)  # type: ignore[arg-type]
    cloud = ExternalWorkerPoolController(
        capacity.client,
        SETTINGS,
        capacity.provider.profile,
        hybrid=True,
        rules=CloudflareCapacityRules(workflows=False, agents=True, app_builds=True),
    )

    launched = await plan_capacity_launches(replace(capacity.policy, warm_workers=0), (kubernetes, cloud))

    assert len(launched[kubernetes]) == 1
    assert launched[cloud] == []


async def test_prefer_kubernetes_changes_selected_workflow_reservation_order(db, capacity):
    """Preference changes only new-worker ordering after rules allow Cloudflare."""
    run = await _accept_pool_run()
    await _set_request(db.sessions, run, 256 * MIB)
    kubernetes = WorkerPoolController(FakeCluster(), SETTINGS, SPEC)  # type: ignore[arg-type]
    cloud = ExternalWorkerPoolController(
        capacity.client,
        SETTINGS,
        capacity.provider.profile,
        hybrid=True,
        rules=CloudflareCapacityRules(prefer_kubernetes=True),
    )

    launched = await plan_capacity_launches(replace(capacity.policy, warm_workers=0), (kubernetes, cloud))

    assert len(launched[kubernetes]) == 1
    assert launched[cloud] == []


async def test_cloudflare_without_local_capacity_ignores_start_categories(db, capacity):
    """Cloudflare remains the sole eligible provider when no local path exists."""
    run = await _accept_pool_run()
    await _set_request(db.sessions, run, 256 * MIB)
    cloud = ExternalWorkerPoolController(
        capacity.client,
        SETTINGS,
        capacity.provider.profile,
        rules=CloudflareCapacityRules(workflows=False, agents=False, app_builds=False),
    )

    launched = await plan_capacity_launches(replace(capacity.policy, warm_workers=0), (cloud,))

    assert len(launched[cloud]) == 1


async def test_sized_workflow_reservation_tracks_its_dispatch_and_clears_when_removed(db):
    """A large workflow worker must not remain reserved after cancellation."""
    run = await _accept_pool_run()
    await _set_request(db.sessions, run, 3 * GIB)
    kubernetes = WorkerPoolController(FakeCluster(), SETTINGS, SPEC)  # type: ignore[arg-type]

    [worker_id] = (await plan_capacity_launches(replace(POLICY, warm_workers=0), (kubernetes,)))[kubernetes]
    async with db.context() as session:
        dispatch_id = await session.scalar(
            select(WorkDispatch.id).where(WorkDispatch.subject_id == run)
        )
        worker = await session.get(ExecutionWorker, worker_id)
        assert worker is not None
        assert worker.reserved_for_dispatch_id == dispatch_id
        await session.execute(delete(WorkDispatch).where(WorkDispatch.id == dispatch_id))

    await plan_capacity_launches(replace(POLICY, warm_workers=0), (kubernetes,))

    async with db.context() as session:
        worker = await session.get(ExecutionWorker, worker_id)
        assert worker is not None
        assert worker.reserved_for_dispatch_id is None


async def test_sized_platform_job_does_not_use_a_work_dispatch_reservation(db):
    """Platform job IDs are not valid inputs to the workflow claim fence."""
    job = PlatformJob(
        job_type="application.deploy", payload={}, requested_by_user_id=str(uuid4()),
        requested_by_email="test@example.com", requested_by_name="Test", title="App deploy",
        memory_required_bytes=3 * GIB,
    )
    async with db.context() as session:
        session.add(job)

    kubernetes = WorkerPoolController(FakeCluster(), SETTINGS, SPEC)  # type: ignore[arg-type]
    [worker_id] = (await plan_capacity_launches(replace(POLICY, warm_workers=0), (kubernetes,)))[kubernetes]

    async with db.context() as session:
        worker = await session.get(ExecutionWorker, worker_id)
        assert worker is not None
        assert worker.reserved_for_dispatch_id is None
