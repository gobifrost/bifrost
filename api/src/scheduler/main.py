"""Scheduler container entry point: a static executor.

The ``scheduler`` and ``worker`` containers run the same executor
(``src.worker.pool_main``): workflows, agent runs, summaries, services and
local platform jobs. Platform control (trigger leadership and dispatch
reconciliation) runs in the API (``src.scheduler.control``); the Kubernetes
loops run wherever a process may manage Jobs, which on an install that bound
the controller Role to this container includes its executor.

This entry point keeps the scheduler's health files: the liveness heartbeat
starts before the schema gate, and readiness is marked once the executor can
claim work, so existing healthchecks and probes keep passing.
"""

import asyncio
import logging
import sys

from src.scheduler.health import clear_ready, heartbeat_loop, mark_ready, write_heartbeat

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)


async def main() -> int:
    from src.worker.pool_main import run_executor

    clear_ready()
    write_heartbeat()
    heartbeat = asyncio.create_task(heartbeat_loop(), name="scheduler-liveness")
    try:
        return await run_executor(on_ready=mark_ready)
    finally:
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)
        clear_ready()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
