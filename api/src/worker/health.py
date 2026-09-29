"""Readiness marker for the worker container.

Liveness needs no check (the process is alive while it waits at the schema
gate); readiness is the marker written once consumers are running.
"""

from __future__ import annotations

import argparse
from pathlib import Path

READY_PATH = Path("/tmp/bifrost-worker-ready")


def clear_ready() -> None:
    READY_PATH.unlink(missing_ok=True)


def mark_ready() -> None:
    READY_PATH.touch()


def is_ready() -> bool:
    return READY_PATH.exists()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ready", action="store_true", required=True)
    parser.parse_args()
    return 0 if is_ready() else 1


if __name__ == "__main__":
    raise SystemExit(main())
