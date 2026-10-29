"""When a wake-driven loop (dispatch reconciler, worker controller) runs its next pass."""

from __future__ import annotations

import asyncio
import time

from src.services.work_dispatch import wait_for_next_pass


async def _wait(wake: asyncio.Event, *, since: float, stop: asyncio.Event | None = None) -> float:
    started = time.monotonic()
    await wait_for_next_pass(stop or asyncio.Event(), wake, since=since, spacing=0.2, sweep=0.5)
    return time.monotonic() - started


async def test_without_a_wake_up_the_next_pass_is_the_sweep() -> None:
    assert 0.45 <= await _wait(asyncio.Event(), since=time.monotonic()) < 0.7


async def test_a_wake_up_runs_the_next_pass_no_sooner_than_the_spacing() -> None:
    wake = asyncio.Event()
    wake.set()
    assert 0.15 <= await _wait(wake, since=time.monotonic()) < 0.35
    assert not wake.is_set(), "the wake-up is consumed"


async def test_a_wake_up_after_the_spacing_runs_the_next_pass_at_once() -> None:
    wake = asyncio.Event()
    wake.set()
    assert await _wait(wake, since=time.monotonic() - 1.0) < 0.05


async def test_stop_ends_the_wait() -> None:
    stop = asyncio.Event()
    stop.set()
    assert await _wait(asyncio.Event(), since=time.monotonic(), stop=stop) < 0.05
