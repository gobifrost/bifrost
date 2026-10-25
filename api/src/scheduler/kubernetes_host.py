"""Run Kubernetes control loops for a legacy static scheduler-role pod.

Remote launchers never receive Kubernetes credentials.  This helper exists
only for installations that still mount a controller service-account token on
a static worker or scheduler pod; base manifests do not.  It owns no workflow,
agent, service, or platform-job execution.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

logger = logging.getLogger(__name__)
SERVICE_ACCOUNT_TOKEN_PATH = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")


def has_service_account_token() -> bool:
    """Whether this launcher pod was deliberately given Kubernetes access."""
    return SERVICE_ACCOUNT_TOKEN_PATH.is_file()


async def run_control_host(stop_event: asyncio.Event) -> int:
    """Own the existing loops after the database schema is usable."""
    # The physical launcher imports legacy_control_process from this module.
    # Only the separate controller process needs application dependencies.
    from shared.schema_gate import wait_for_schema
    from src.config import get_settings
    from src.core.database import close_db, get_engine, get_session_factory, init_db
    from src.services.kubernetes_control import start_kubernetes_loops
    from src.services.kubernetes_execution import import_kubernetes_policy_from_env

    settings = get_settings()
    tasks: list[asyncio.Task[None]] = []
    await init_db()
    try:
        await wait_for_schema(get_engine())
        async with get_session_factory()() as db:
            await import_kubernetes_policy_from_env(db, os.environ)
        tasks = await start_kubernetes_loops(stop_event, settings)
        if not tasks:
            # SSAR denied access (or the cluster is unavailable): this helper
            # is intentionally inert.  The launcher still serves its remote
            # work and an API replica with the Role can own control.
            return 0
        await stop_event.wait()
        return 0
    finally:
        stop_event.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await close_db()


async def main() -> int:
    if not has_service_account_token():
        return 0
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)
    return await run_control_host(stop_event)


@asynccontextmanager
async def legacy_control_process() -> AsyncIterator[None]:
    """Run one helper beside a launcher only when its pod has a token."""
    if not has_service_account_token():
        yield
        return

    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "src.scheduler.kubernetes_host",
    )
    try:
        yield
    finally:
        if process.returncode is None:
            process.terminate()
        await process.wait()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(asyncio.run(main()))
