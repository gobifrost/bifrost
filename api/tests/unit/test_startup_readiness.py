"""The scheduler and worker entry points keep their health contracts.

Both containers run the executor. The scheduler container's liveness
heartbeat beats while the executor waits at the schema gate; each entry
point reports ready only once the executor can claim work.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from pathlib import Path

import pytest

from src.scheduler import health as scheduler_health
from src.worker import health as worker_health


async def _eventually(predicate) -> None:  # type: ignore[no-untyped-def]
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_scheduler_entry_beats_while_gated_and_is_ready_once_the_executor_is(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.scheduler import main as scheduler_main

    monkeypatch.setattr(scheduler_health, "HEARTBEAT_PATH", tmp_path / "heartbeat")
    monkeypatch.setattr(scheduler_health, "READY_PATH", tmp_path / "ready")
    gate_open, stop = asyncio.Event(), asyncio.Event()

    async def launcher(
        *, on_ready: Callable[[], None] | None = None, on_stop: Callable[[], None] | None = None,
    ) -> int:
        await gate_open.wait()  # the schema gate
        assert on_ready is not None
        on_ready()
        await stop.wait()
        if on_stop:
            on_stop()
        return 0

    async def fast_heartbeat() -> None:
        await scheduler_health.heartbeat_loop(0.01)

    monkeypatch.setattr("src.worker.remote.launcher.run_launcher", launcher)
    monkeypatch.setattr(scheduler_main, "SERVICE_ACCOUNT_TOKEN_PATH", tmp_path / "no-token")
    monkeypatch.setattr(scheduler_main, "heartbeat_loop", fast_heartbeat)

    entry = asyncio.create_task(scheduler_main.main())
    await _eventually(scheduler_health.HEARTBEAT_PATH.exists)
    first = os.stat(scheduler_health.HEARTBEAT_PATH).st_mtime_ns
    await _eventually(lambda: os.stat(scheduler_health.HEARTBEAT_PATH).st_mtime_ns != first)
    assert not scheduler_health.READY_PATH.exists()

    gate_open.set()
    await _eventually(scheduler_health.is_ready)

    stop.set()
    assert await entry == 0
    assert not scheduler_health.READY_PATH.exists(), "a stopped executor is not ready"


def test_worker_entry_is_ready_only_while_the_executor_is(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.worker import main as worker_main

    monkeypatch.setattr(worker_health, "READY_PATH", tmp_path / "ready")
    worker_health.mark_ready()  # left over from a previous start
    seen: list[bool] = []

    async def launcher(
        *, on_ready: Callable[[], None] | None = None, on_stop: Callable[[], None] | None = None,
    ) -> int:
        seen.append(worker_health.is_ready())
        assert on_ready is not None
        on_ready()
        seen.append(worker_health.is_ready())
        if on_stop:
            on_stop()
        return 0

    monkeypatch.setattr("src.worker.remote.launcher.run_launcher", launcher)
    monkeypatch.setattr(worker_main, "SERVICE_ACCOUNT_TOKEN_PATH", tmp_path / "no-token")

    with pytest.raises(SystemExit) as exited:
        worker_main.run()

    assert exited.value.code == 0
    assert seen == [False, True]
    assert not worker_health.is_ready()
