"""An executor runs one live and one backfill summary at a time.

Regression guard: fleet-wide summary concurrency must equal the executor
count, not executors × slots. More silently melts the model provider and
re-introduces the burst-429 problem that cost ~525 runs in production on
2026-04-24 (then enforced by the broker consumers' prefetch of 1).
"""

from __future__ import annotations

import asyncio

from src.worker.pool_main import PoolWorker


async def test_a_running_backfill_summary_never_blocks_a_live_one() -> None:
    worker = PoolWorker()
    assert ("agent.summarize", 1) in worker._claimable()
    assert ("agent.summarize_backfill", 1) in worker._claimable()

    busy = asyncio.get_running_loop().create_future()
    task = asyncio.ensure_future(busy)
    worker._tasks["agent.summarize_backfill"].add(task)  # type: ignore[arg-type]
    try:
        claimable = worker._claimable()
        assert ("agent.summarize_backfill", 1) not in claimable
        assert ("agent.summarize", 1) in claimable
        assert worker.active_runs() == 1, "an executor with a summary in flight is not idle"
    finally:
        task.cancel()
