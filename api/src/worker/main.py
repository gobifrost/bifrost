"""Worker container entry point: a static executor.

The ``worker`` and ``scheduler`` containers run the same executor
(``src.worker.pool_main``); this entry point keeps the worker's readiness file
(``python -m src.worker.health --ready``) so existing healthchecks and probes
keep passing.

KEEP THIS MODULE STDLIB-ONLY AT MODULE LEVEL so the startup signal handler
can be installed before importing the executor. The execution template has
its own clean subprocess entrypoint. Import-boundary tests protect both
entrypoints from accidental eager application imports.
"""
import asyncio
import signal
import sys


def _exit_cleanly_on_startup_sigterm(signum, frame) -> None:
    raise SystemExit(0)


def run() -> None:
    signal.signal(signal.SIGTERM, _exit_cleanly_on_startup_sigterm)
    from src.worker.health import clear_ready, mark_ready
    from src.worker.pool_main import run_executor

    clear_ready()
    try:
        code = asyncio.run(run_executor(on_ready=mark_ready))
    finally:
        clear_ready()
    sys.exit(code)


if __name__ == "__main__":
    run()
