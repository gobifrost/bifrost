"""An executor also hosts services and local platform jobs (#769 stage 4c)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from src.jobs.schedulers import platform_jobs
from src.worker.pool_main import PoolWorker


def _worker_with_pool(services: int = 0) -> PoolWorker:
    worker = PoolWorker()
    pool = SimpleNamespace(
        service_processes={f"svc-{i}": object() for i in range(services)},
        active_execution_count=lambda: 0,
    )
    worker._workflow_consumer = SimpleNamespace(_pool=pool)  # type: ignore[assignment]
    return worker


def test_services_and_platform_jobs_keep_an_executor_busy_but_free_no_run_slots() -> None:
    idle = _worker_with_pool()
    assert not idle.busy()

    with_service = _worker_with_pool(services=1)
    assert with_service.busy()
    assert with_service.active_runs() == 0, "capacity planning counts runs only"

    with_job = _worker_with_pool()
    with_job._platform_jobs.add(uuid4())
    assert with_job.busy()
    assert with_job.active_runs() == 0


async def test_a_platform_job_is_active_only_while_it_runs(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    claim = SimpleNamespace(id=uuid4())
    seen_during_run: list[bool] = []
    active: set = set()

    async def run(claimed) -> bool:  # type: ignore[no-untyped-def]
        seen_during_run.append(claimed.id in active)
        return True

    monkeypatch.setattr(platform_jobs, "recover_expired_platform_jobs", AsyncMock(return_value=(0, 0)))
    monkeypatch.setattr(platform_jobs, "claim_platform_job", AsyncMock(return_value=claim))
    monkeypatch.setattr(platform_jobs, "run_claimed_platform_job", run)

    assert await platform_jobs.process_platform_jobs(active) == (1, 0)
    assert seen_during_run == [True]
    assert active == set()


async def test_draining_hands_services_over_first_and_waits_for_platform_jobs(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    worker = _worker_with_pool(services=1)
    order: list[str] = []
    service_loop = SimpleNamespace(stop=AsyncMock(side_effect=lambda: order.append("services")))
    worker._service_claim_loop = service_loop  # type: ignore[assignment]
    job = uuid4()
    worker._platform_jobs.add(job)

    drain = asyncio.create_task(worker._drain())
    await asyncio.sleep(0.1)
    assert order == ["services"]
    assert worker._platform_stop.is_set(), "no new platform job is claimed"
    assert not drain.done(), "a running platform job is waited for"

    worker._platform_jobs.discard(job)
    await asyncio.wait_for(drain, timeout=2)


async def test_an_incarnation_makes_a_job_worker_and_none_a_static_one(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from src.worker import pool_main

    formed: list[tuple[bool, object]] = []

    async def run(self) -> int:  # type: ignore[no-untyped-def]
        formed.append((self.static, self._on_ready))
        return 0

    monkeypatch.setattr(pool_main.PoolWorker, "run", run)
    ready = object()

    monkeypatch.delenv("BIFROST_WORKER_INCARNATION_ID", raising=False)
    assert await pool_main.run_executor(on_ready=ready) == 0  # type: ignore[arg-type]

    monkeypatch.setenv("BIFROST_WORKER_INCARNATION_ID", str(uuid4()))
    monkeypatch.setenv("BIFROST_KUBERNETES_JOB_UID", "job-uid")
    monkeypatch.setenv("BIFROST_KUBERNETES_POD_UID", "pod-uid")
    assert await pool_main.run_executor() == 0

    assert formed == [(True, ready), (False, None)]
