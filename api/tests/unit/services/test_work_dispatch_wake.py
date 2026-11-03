"""When a wake-driven loop (dispatch reconciler, worker controller) runs its next pass."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock

import redis.asyncio as aioredis

from src.services.work_dispatch import (
    POOL_WAKE_CHANNEL,
    WAKE_PACKAGE,
    WAKE_WORKFLOW,
    listen_for_pool_wake,
    wait_for_next_pass,
)


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


async def test_pool_wake_listener_sets_only_the_event_for_the_published_family(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A workflow hand-off must not start package installation work."""
    workflow = asyncio.Event()
    package = asyncio.Event()
    workflow_delivered = asyncio.Event()
    continue_messages = asyncio.Event()

    class PubSub:
        subscribed: tuple[str, ...]

        async def subscribe(self, *channels: str) -> None:
            self.subscribed = channels

        async def listen(self):  # type: ignore[no-untyped-def]
            yield {"type": "message", "data": WAKE_WORKFLOW}
            workflow_delivered.set()
            await continue_messages.wait()
            yield {"type": "message", "data": WAKE_PACKAGE}
            await asyncio.Event().wait()

    pubsub = PubSub()

    class Redis:
        def __init__(self) -> None:
            self.aclose = AsyncMock()

        def pubsub(self) -> PubSub:
            return pubsub

    monkeypatch.setattr(aioredis, "from_url", lambda _url: Redis())
    listener = asyncio.create_task(
        listen_for_pool_wake("redis://test", {WAKE_WORKFLOW: workflow, WAKE_PACKAGE: package})
    )
    try:
        await asyncio.wait_for(workflow_delivered.wait(), timeout=1)
        assert workflow.is_set()
        assert not package.is_set()
        assert pubsub.subscribed == (POOL_WAKE_CHANNEL,)

        continue_messages.set()
        await asyncio.wait_for(package.wait(), timeout=1)
    finally:
        listener.cancel()
        await asyncio.gather(listener, return_exceptions=True)
