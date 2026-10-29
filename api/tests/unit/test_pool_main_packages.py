"""An executor applies every recorded package change newer than its own."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from src.worker import pool_main
from src.worker.pool_main import PoolWorker


class _Consumer:
    def __init__(self, fail_first: bool = False) -> None:
        self.applied: list[dict[str, Any]] = []
        self.fail_first = fail_first

    async def process_message(self, body: dict[str, Any]) -> None:
        if self.fail_first:
            self.fail_first = False
            raise RuntimeError("redis unavailable")
        self.applied.append(body)


def _change(generation: int, action: str, package: str) -> SimpleNamespace:
    return SimpleNamespace(id=generation, action=action, package=package, version=None, run_id=f"r{generation}")


async def _run_loop(worker: PoolWorker, until) -> None:  # type: ignore[no-untyped-def]
    task = asyncio.create_task(worker._package_loop())
    try:
        for _ in range(200):
            if until():
                return
            await asyncio.sleep(0.01)
        raise AssertionError("package loop did not converge")
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.fixture
def changes(monkeypatch):  # type: ignore[no-untyped-def]
    recorded = [_change(4, "install", "humanize"), _change(5, "uninstall", "humanize")]

    async def after(generation: int) -> list[SimpleNamespace]:
        return [c for c in recorded if c.id > generation]

    monkeypatch.setattr(pool_main, "package_changes_after", after)
    monkeypatch.setattr(pool_main, "SWEEP_SECONDS", 0.01)
    return recorded


async def test_newer_changes_are_applied_in_order_and_the_generation_advances(changes) -> None:  # type: ignore[no-untyped-def]
    worker = PoolWorker()
    worker.package_generation = 3
    consumer = _Consumer()
    worker._package_consumer = consumer  # type: ignore[assignment]

    await _run_loop(worker, lambda: worker.package_generation == 5)

    assert [(m["action"], m["package"], m["run_id"]) for m in consumer.applied] == [
        ("install", "humanize", "r4"),
        ("uninstall", "humanize", "r5"),
    ]


async def test_changes_at_or_below_the_boot_generation_are_not_reapplied(changes) -> None:  # type: ignore[no-untyped-def]
    worker = PoolWorker()
    worker.package_generation = 4
    consumer = _Consumer()
    worker._package_consumer = consumer  # type: ignore[assignment]

    await _run_loop(worker, lambda: worker.package_generation == 5)

    assert [m["run_id"] for m in consumer.applied] == ["r5"]


async def test_an_unexpected_error_retries_the_same_change(changes) -> None:  # type: ignore[no-untyped-def]
    worker = PoolWorker()
    worker.package_generation = 3
    consumer = _Consumer(fail_first=True)
    worker._package_consumer = consumer  # type: ignore[assignment]

    await _run_loop(worker, lambda: worker.package_generation == 5)

    assert [m["run_id"] for m in consumer.applied] == ["r4", "r5"]


async def test_an_idle_package_loop_reads_again_on_a_wake_up(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Between changes the loop does not poll; a recorded change's wake-up
    starts the next read."""
    reads: list[int] = []

    async def after(generation: int) -> list[SimpleNamespace]:
        reads.append(generation)
        return []

    async def until(count: int) -> None:
        for _ in range(200):
            if len(reads) == count:
                return
            await asyncio.sleep(0.01)
        raise AssertionError(f"expected {count} reads, saw {len(reads)}")

    monkeypatch.setattr(pool_main, "package_changes_after", after)
    monkeypatch.setattr(pool_main, "SWEEP_SECONDS", 30)
    worker = PoolWorker()
    worker._package_consumer = _Consumer()  # type: ignore[assignment]
    task = asyncio.create_task(worker._package_loop())
    try:
        await until(1)
        await asyncio.sleep(0.1)
        assert len(reads) == 1  # nothing read while idle

        worker._package_wake.set()
        await until(2)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
