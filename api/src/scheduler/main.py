"""Scheduler container entry point for the physical remote launcher.

The API owns platform control and claims. This process retains the scheduler
heartbeat and readiness files expected by existing Kubernetes probes while it
connects to the API as a physical launcher.
"""

import asyncio
import logging
import sys
from pathlib import Path

from src.scheduler.health import clear_ready, heartbeat_loop, mark_ready, write_heartbeat


SERVICE_ACCOUNT_TOKEN_PATH = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)


async def main() -> int:
    from src.worker.remote.launcher import run_launcher

    clear_ready()
    write_heartbeat()
    heartbeat = asyncio.create_task(heartbeat_loop(), name="scheduler-liveness")
    try:
        if not SERVICE_ACCOUNT_TOKEN_PATH.is_file():
            return await run_launcher(on_ready=mark_ready, on_stop=clear_ready)
        from src.scheduler.kubernetes_host import legacy_control_process

        async with legacy_control_process():
            return await run_launcher(on_ready=mark_ready, on_stop=clear_ready)
    finally:
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)
        clear_ready()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
