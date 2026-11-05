"""Worker container entry point for the physical remote launcher.

It keeps the established worker readiness contract while the API owns durable
claims and remote-execution control. Keep module imports stdlib-only so SIGTERM
is handled before the launcher imports application code.
"""
import asyncio
import signal
import sys
from pathlib import Path


SERVICE_ACCOUNT_TOKEN_PATH = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")


def _exit_cleanly_on_startup_sigterm(signum, frame) -> None:
    raise SystemExit(0)


def run() -> None:
    signal.signal(signal.SIGTERM, _exit_cleanly_on_startup_sigterm)
    from src.worker.health import clear_ready, mark_ready
    from src.worker.remote.launcher import run_launcher

    clear_ready()
    try:
        async def run_launcher_with_legacy_control() -> int:
            if not SERVICE_ACCOUNT_TOKEN_PATH.is_file():
                return await run_launcher(on_ready=mark_ready, on_stop=clear_ready)
            from src.scheduler.kubernetes_host import legacy_control_process

            async with legacy_control_process():
                return await run_launcher(on_ready=mark_ready, on_stop=clear_ready)

        code = asyncio.run(run_launcher_with_legacy_control())
    finally:
        clear_ready()
    sys.exit(code)


if __name__ == "__main__":
    run()
