from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.scheduler import main as scheduler_main
from src.scheduler.main import PLATFORM_JOB_CONCURRENCY, Scheduler


def _with_control_host(monkeypatch: pytest.MonkeyPatch, host: str) -> None:
    monkeypatch.setattr(
        scheduler_main,
        "get_settings",
        lambda: SimpleNamespace(control_plane_host=host, environment="testing"),
    )


def test_scheduler_uses_internal_platform_job_concurrency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _with_control_host(monkeypatch, "scheduler")
    scheduler = Scheduler(control_plane=MagicMock())

    assert scheduler._job_slots == PLATFORM_JOB_CONCURRENCY == 2


def test_scheduler_hosts_control_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    _with_control_host(monkeypatch, "scheduler")
    created: list[dict] = []

    class RecordingControl:
        def __init__(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
            created.append(kwargs)
            self.owner_id = "scheduler-host:1"

    monkeypatch.setattr(scheduler_main, "ControlPlane", RecordingControl)
    scheduler = Scheduler()

    assert scheduler.hosts_control
    assert created[0]["host"] == "scheduler"
    # Diagnostics recognises the leader by this shared identity.
    assert scheduler.replica_id == "scheduler-host:1"


def test_scheduler_only_executes_jobs_when_api_hosts_control(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _with_control_host(monkeypatch, "api")
    monkeypatch.setattr(
        scheduler_main,
        "ControlPlane",
        MagicMock(side_effect=AssertionError("scheduler must not start control")),
    )

    scheduler = Scheduler()

    assert not scheduler.hosts_control


@pytest.mark.asyncio
async def test_stop_surrenders_control_before_local_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _with_control_host(monkeypatch, "scheduler")
    order: list[str] = []
    control = MagicMock()
    control.owner_id = "scheduler-host:1"

    async def stop_control() -> None:
        order.append("control")

    control.stop = stop_control
    monkeypatch.setattr(scheduler_main, "remove_scheduler_replica", AsyncMock())
    monkeypatch.setattr(scheduler_main, "close_db", AsyncMock())
    scheduler = Scheduler(control_plane=control)
    scheduler.running = True

    async def platform_loop() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            order.append("platform-jobs")
            raise

    scheduler._platform_job_tasks = [asyncio.create_task(platform_loop())]
    await asyncio.sleep(0)
    await scheduler.stop()

    assert order == ["control", "platform-jobs"]


@pytest.mark.asyncio
async def test_background_task_exit_signals_replica_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _with_control_host(monkeypatch, "api")
    scheduler = Scheduler()
    scheduler.running = True

    async def stop_unexpectedly() -> None:
        return

    task = asyncio.create_task(stop_unexpectedly())
    task.add_done_callback(scheduler._background_task_done)
    result = await task
    assert result is None
    await asyncio.sleep(0)

    assert scheduler._shutdown_event.is_set()
