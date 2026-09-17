"""Thin worker entry point.

KEEP THIS MODULE STDLIB-ONLY AT MODULE LEVEL so the startup signal handler
can be installed before importing the worker application. The execution
template has its own clean subprocess entrypoint. Import-boundary tests
protect both entrypoints from accidental eager application imports.
"""
import asyncio
import signal


def _exit_cleanly_on_startup_sigterm(signum, frame) -> None:
    raise SystemExit(0)


def run() -> None:
    signal.signal(signal.SIGTERM, _exit_cleanly_on_startup_sigterm)
    from src.worker.app import main

    asyncio.run(main())


if __name__ == "__main__":
    run()
