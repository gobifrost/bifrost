"""Services reuse workflow overflow policy without becoming dispatches."""

from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

from shared.models import CloudflareCapacityRules
from src.models.orm.execution_workers import ExecutionWorker
from src.models.orm.services import ServiceAttempt
from src.services.worker_pool import claims
from src.services.worker_pool.controller import WorkerPoolController
from src.services.worker_pool.coordinator import HybridWorkerPoolCoordinator
from src.services.worker_pool.external_controller import ExternalWorkerPoolController
from tests.unit.services.worker_pool.test_external_capacity import capacity as capacity
from tests.unit.services.worker_pool.test_worker_capacity_domains import (
    _executor,
    pending_service as pending_service,
)
from tests.unit.services.worker_pool.test_worker_pool import (
    GIB,
    SETTINGS,
    SPEC,
    FakeCluster,
    _clean as _clean,
    db as db,
)


@pytest.mark.parametrize("workflow_overflow", [True, False])
async def test_pending_service_respects_workflow_overflow_when_local_worker_is_busy(
    db, capacity, pending_service, workflow_overflow,
) -> None:
    worker_id = await _executor(db, "busy-local", ["workflow.execute"], GIB)
    await claims.heartbeat_worker(
        worker_id, active_runs=1, runs_handled=0, package_generation=0,
        memory_pressured=False, memory_budget_bytes=GIB, memory_reserved_bytes=GIB,
        active_handler_counts={"workflow.execute": 1},
    )
    kubernetes = WorkerPoolController(FakeCluster(), SETTINGS, SPEC)  # type: ignore[arg-type]
    cloud = ExternalWorkerPoolController(
        capacity.client, SETTINGS, capacity.provider.profile, hybrid=True,
        rules=CloudflareCapacityRules(workflows=workflow_overflow),
    )
    coordinator = HybridWorkerPoolCoordinator(kubernetes, AsyncMock(return_value=cloud))

    await coordinator.reconcile(replace(capacity.policy, warm_workers=0, max_workers=3))

    async with db.context() as session:
        external = (await session.scalars(
            select(ExecutionWorker).where(ExecutionWorker.form == "external")
        )).all()
        assert len(external) == int(workflow_overflow)
        assert all(worker.reserved_for_dispatch_id is None for worker in external)
        assert await session.scalar(
            select(func.count()).select_from(ServiceAttempt).where(
                ServiceAttempt.service_id == pending_service[0],
            )
        ) == 0
