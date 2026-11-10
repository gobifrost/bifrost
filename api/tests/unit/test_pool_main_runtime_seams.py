"""Runtime boundaries for a PoolWorker hosted by the API process."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.services import work_dispatch
from src.services.memory_requests import MIB, MemoryBudget
from src.worker import pool_main
from src.worker.pool_main import PoolWorker


@pytest.mark.parametrize("lane", ["normal", "fast_path"])
async def test_an_api_hosted_worker_owns_its_runtime_and_consumers(monkeypatch, lane) -> None:  # type: ignore[no-untyped-def]
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
    worker = ApiHostedWorker(execution_lane=lane)
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
        "initialize", "workflow consumer", "package consumer", "measure",
        "control", "close",
    ]
    assert workflow_pool.memory_kill_growth_bytes == 2 * 1024 * MIB - pool_main.WORKFLOW_REQUEST_MARGIN_BYTES
    assert worker._start_service_claim_loop.await_count == (1 if lane == "normal" else 0)
    assert worker.admission is not None
    assert worker.admission._under_pressure() is False
    assert calls[-1] == "pressure"
    close_db.assert_not_awaited()


async def test_pool_wake_listener_routes_each_family_to_its_claim_loop(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Workflow acceptance does not wake package, service, or platform claims."""
    received: dict[str, asyncio.Event] = {}

    async def listen(_redis_url: str, wakes: dict[str, asyncio.Event]) -> None:
        received.update(wakes)

    monkeypatch.setattr(work_dispatch, "listen_for_pool_wake", listen)
    worker = PoolWorker()

    await worker._wake_listener()

    assert {family for family, event in received.items() if event is worker._wake} == set(
        work_dispatch.DISPATCH_WAKE_FAMILIES
    )
    assert received[work_dispatch.WAKE_PLATFORM_JOB] is worker._platform_wake
    assert received[work_dispatch.WAKE_SERVICE] is worker._service_wake
    assert received[work_dispatch.WAKE_PACKAGE] is worker._package_wake
