"""Worker and scheduler are alive but not ready while waiting at the schema gate."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.scheduler import health as scheduler_health
from src.scheduler.main import Scheduler
from src.worker import health as worker_health


async def _eventually(predicate) -> None:  # type: ignore[no-untyped-def]
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_scheduler_keeps_heartbeat_but_is_not_ready_while_gated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(scheduler_health, "HEARTBEAT_PATH", tmp_path / "heartbeat")
    monkeypatch.setattr(scheduler_health, "READY_PATH", tmp_path / "ready")
    gate_open = asyncio.Event()

    async def gate(_engine: object) -> None:
        await gate_open.wait()

    async def fast_heartbeat() -> None:
        await scheduler_health.heartbeat_loop(0.01)

    async def idle(*_args: object, **_kwargs: object) -> None:
        await asyncio.Event().wait()

    scheduler = Scheduler(leadership_lease=MagicMock())
    scheduler._diagnostics_heartbeat_loop = idle  # type: ignore[method-assign]
    scheduler._leadership_loop = idle  # type: ignore[method-assign]

    with (
        patch("src.scheduler.main.init_db", new=AsyncMock()),
        patch("src.scheduler.main.get_engine", return_value=object()),
        patch("src.scheduler.main.wait_for_schema", new=gate),
        patch("src.scheduler.main.heartbeat_loop", new=fast_heartbeat),
        patch("src.scheduler.main.platform_job_worker_loop", new=idle),
    ):
        start_task = asyncio.create_task(scheduler.start())
        await _eventually(scheduler_health.HEARTBEAT_PATH.exists)
        first = os.stat(scheduler_health.HEARTBEAT_PATH).st_mtime_ns
        await _eventually(
            lambda: os.stat(scheduler_health.HEARTBEAT_PATH).st_mtime_ns != first
        )
        assert not scheduler_health.READY_PATH.exists()

        gate_open.set()
        await _eventually(scheduler_health.READY_PATH.exists)
        assert scheduler_health.is_ready()

        scheduler._shutdown_event.set()
        scheduler.running = False
        await start_task
        for task in (scheduler._heartbeat_task, *scheduler._platform_job_tasks):
            if task is not None:
                task.cancel()
        if scheduler._leadership_task is not None:
            scheduler._leadership_task.cancel()
        if scheduler._diagnostics_task is not None:
            scheduler._diagnostics_task.cancel()


@pytest.mark.asyncio
async def test_worker_is_not_ready_until_gate_passes_and_consumers_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.worker.app import Worker

    monkeypatch.setattr(worker_health, "READY_PATH", tmp_path / "ready")
    worker_health.mark_ready()
    gate_open = asyncio.Event()

    async def gate(_engine: object) -> None:
        await gate_open.wait()

    worker = Worker()
    worker.settings = MagicMock(environment="test")

    with (
        patch("src.worker.app.init_db", new=AsyncMock()),
        patch("src.worker.app.get_engine", return_value=object()),
        patch("src.worker.app.wait_for_schema", new=gate),
        patch.object(worker, "_start_consumers", new=AsyncMock()),
    ):
        start_task = asyncio.create_task(worker.start())
        await asyncio.sleep(0.05)
        assert not worker_health.is_ready()

        gate_open.set()
        await _eventually(worker_health.is_ready)

        worker._shutdown_event.set()
        await start_task
