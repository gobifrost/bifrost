"""Runtime boundaries for a PoolWorker hosted by the API process."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from src.services.memory_requests import MIB, MemoryBudget
from src.worker import pool_main
from src.worker.pool_main import PoolWorker


async def test_an_api_hosted_worker_owns_its_runtime_and_consumers(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The API host reuses its database and supplies its canonical pool."""

    calls: list[str] = []
    workflow_pool = SimpleNamespace(
        service_processes={},
        active_execution_count=lambda: 0,
        active_execution_ids=set,
        workflow_growth=dict,
    )
    workflows = SimpleNamespace(
        _pool=workflow_pool,
        owner_worker_id=None,
        start_pool=AsyncMock(),
        stop_pool=AsyncMock(),
    )

    class ApiHostedWorker(PoolWorker):
        async def _initialize_runtime(self) -> bool:
            calls.append("initialize")
            self.incarnation_id = uuid4()
            return True

        async def _close_runtime(self) -> None:
            calls.append("close")

        async def _start_background_control(self) -> list[asyncio.Task[None]]:
            calls.append("control")
            return []

        def _make_workflow_consumer(self):  # type: ignore[no-untyped-def]
            calls.append("workflow consumer")
            return workflows

        def _make_package_consumer(self):  # type: ignore[no-untyped-def]
            calls.append("package consumer")
            return object()

        def _measure_budget(self) -> MemoryBudget:
            calls.append("measure")
            return MemoryBudget(limit_bytes=4 * 1024 * MIB, baseline_bytes=0, threshold=0.5)

        def _under_pressure(self) -> bool:
            calls.append("pressure")
            return False

    class MustUseFactory:
        def __init__(self) -> None:
            raise AssertionError("the API host must supply this consumer")

    from src.jobs.consumers import agent_run, package_install, workflow_execution

    monkeypatch.setattr(workflow_execution, "WorkflowExecutionConsumer", MustUseFactory)
    monkeypatch.setattr(package_install, "PackageInstallConsumer", MustUseFactory)
    monkeypatch.setattr(agent_run, "AgentRunConsumer", object)
    monkeypatch.setattr(pool_main, "current_package_generation", AsyncMock(return_value=0))
    monkeypatch.setattr(pool_main, "mark_static_ready", AsyncMock(return_value=True))
    monkeypatch.setattr(pool_main, "retire_worker", AsyncMock())
    monkeypatch.setattr(pool_main, "remove_scheduler_replica", AsyncMock())
    monkeypatch.setattr(pool_main, "replica_heartbeat_loop", AsyncMock())
    close_db = AsyncMock()
    monkeypatch.setattr(pool_main, "close_db", close_db)
    worker = ApiHostedWorker()
    for loop in (
        "_heartbeat_loop",
        "_wake_listener",
        "_package_loop",
        "_platform_job_loop",
        "_start_service_claim_loop",
        "_claim_loop",
        "_drain",
    ):
        monkeypatch.setattr(worker, loop, AsyncMock())

    assert await worker.run() == 0

    assert calls == [
        "initialize",
        "workflow consumer",
        "package consumer",
        "measure",
        "control",
        "close",
    ]
    assert worker.admission is not None
    assert worker.admission._under_pressure() is False
    assert calls[-1] == "pressure"
    close_db.assert_not_awaited()
