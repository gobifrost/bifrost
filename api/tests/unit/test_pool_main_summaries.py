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
        assert worker.active_handler_counts()["agent.summarize_backfill"] == 1
        assert worker.active_handler_counts()["agent.summarize"] == 0
    finally:
        task.cancel()


async def test_full_agent_stage_budget_keeps_excess_runs_queued_until_release() -> None:
    worker = PoolWorker()
    tasks = [asyncio.create_task(asyncio.sleep(60)) for _ in range(4)]
    worker._tasks['agent.run'].update(tasks)
    try:
        assert ('agent.run', 1) not in worker._claimable()
        assert ('agent.summarize', 1) not in worker._claimable()
        assert ('agent.summarize_backfill', 1) not in worker._claimable()
        assert ('workflow.execute', 1) in worker._claimable()
        tasks[0].cancel()
        assert ('agent.run', 1) not in worker._claimable(), 'exiting work still owns its slot'
        await asyncio.gather(tasks[0], return_exceptions=True)
        worker._tasks['agent.run'].remove(tasks[0])
        assert ('agent.run', 1) in worker._claimable()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
