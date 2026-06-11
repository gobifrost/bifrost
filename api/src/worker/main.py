"""Thin worker entry point.

KEEP THIS MODULE STDLIB-ONLY AT MODULE LEVEL. The execution template
process is started with the multiprocessing "spawn" context, and spawn
re-imports this module (the parent's __main__) into the child during
prepare(). Any module-level import here is paid by the ~97MB template
process. tests/unit/test_import_hygiene.py enforces this.
"""
import asyncio


def run() -> None:
    from src.worker.app import main
    asyncio.run(main())


if __name__ == "__main__":
    run()
