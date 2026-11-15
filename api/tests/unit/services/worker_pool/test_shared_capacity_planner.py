"""Contracts for the one locked capacity planner used by every provider mix."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

from sqlalchemy import func, select

from shared.models import CloudflareCapacityRules
from src.models.orm.execution_workers import ExecutionWorker
from src.services.worker_pool.coordinator import HybridWorkerPoolCoordinator
from src.services.worker_pool.external_controller import ExternalWorkerPoolController
from src.services.worker_pool.controller import WorkerPoolController, plan_capacity_launches
from tests.unit.services.worker_pool.test_external_capacity import capacity as capacity_fixture
from tests.unit.services.worker_pool.test_worker_pool import (
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
