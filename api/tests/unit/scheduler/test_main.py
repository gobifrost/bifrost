from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.scheduler.main import PLATFORM_JOB_CONCURRENCY, Scheduler


class FakeLeadershipLease:
    owner_id = "scheduler-test"

    def __init__(self) -> None:
        self.is_leader = False
        self.acquire_calls = 0
        self.release_calls = 0

    async def try_acquire(self) -> bool:
        self.acquire_calls += 1
        if self.acquire_calls > 1:
            return False
        self.is_leader = True
        return True

    async def renew(self) -> bool:
        self.is_leader = False
        return False

    async def release(self) -> None:
        self.release_calls += 1
        self.is_leader = False


def test_scheduler_uses_internal_platform_job_concurrency() -> None:
    scheduler = Scheduler(leadership_lease=FakeLeadershipLease())  # type: ignore[arg-type]

    assert scheduler._job_slots == PLATFORM_JOB_CONCURRENCY == 2


@pytest.mark.asyncio
async def test_trigger_services_run_only_while_lease_is_held(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = FakeLeadershipLease()
    scheduler = Scheduler(leadership_lease=lease)  # type: ignore[arg-type]
    scheduler.running = True
    scheduler._start_leader_services = AsyncMock()  # type: ignore[method-assign]
    scheduler._stop_leader_services = AsyncMock()  # type: ignore[method-assign]

    waits = 0

    async def wait_then_stop(_seconds: float) -> None:
        nonlocal waits
        waits += 1
        if waits == 2:
            scheduler._shutdown_event.set()

    monkeypatch.setattr(scheduler, "_wait_or_shutdown", wait_then_stop)

    await scheduler._leadership_loop()

    scheduler._start_leader_services.assert_awaited_once()
    assert scheduler._stop_leader_services.await_count >= 1
    assert lease.acquire_calls == 2
    assert lease.release_calls >= 1


@pytest.mark.asyncio
async def test_stopping_leader_services_cancels_running_scheduler_callback() -> None:
    scheduler = Scheduler(leadership_lease=FakeLeadershipLease())  # type: ignore[arg-type]
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def running_callback() -> None:
        started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    apscheduler = AsyncIOScheduler()
    apscheduler.add_job(
        running_callback,
        next_run_time=datetime.now(timezone.utc),
    )
    apscheduler.start()
    scheduler._scheduler = apscheduler

    await asyncio.wait_for(started.wait(), timeout=1)
    await scheduler._stop_leader_services()
    await asyncio.wait_for(cancelled.wait(), timeout=1)


@pytest.mark.asyncio
async def test_failed_scheduler_shutdown_is_reported() -> None:
    scheduler = Scheduler(leadership_lease=FakeLeadershipLease())  # type: ignore[arg-type]
    apscheduler = MagicMock()
    apscheduler.shutdown.side_effect = RuntimeError("scheduler still active")
    scheduler._scheduler = apscheduler

    with pytest.raises(RuntimeError, match="scheduler still active"):
        await scheduler._stop_leader_services()

    assert scheduler._scheduler is None
    apscheduler.shutdown.assert_called_once_with(wait=False)


@pytest.mark.asyncio
async def test_background_task_exit_signals_replica_shutdown() -> None:
    scheduler = Scheduler(leadership_lease=FakeLeadershipLease())  # type: ignore[arg-type]
    scheduler.running = True

    async def stop_unexpectedly() -> None:
        return

    task = asyncio.create_task(stop_unexpectedly())
    task.add_done_callback(scheduler._background_task_done)
    result = await task
    assert result is None
    await asyncio.sleep(0)

    assert scheduler._shutdown_event.is_set()
