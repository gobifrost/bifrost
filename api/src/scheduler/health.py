"""Event-loop heartbeat used by the scheduler container liveness probe."""

from __future__ import annotations

import argparse
import asyncio
import time
from pathlib import Path

HEARTBEAT_PATH = Path("/tmp/bifrost-scheduler-heartbeat")
READY_PATH = Path("/tmp/bifrost-scheduler-ready")


def write_heartbeat() -> None:
    HEARTBEAT_PATH.touch()


def clear_ready() -> None:
    READY_PATH.unlink(missing_ok=True)


def mark_ready() -> None:
    READY_PATH.touch()


async def heartbeat_loop(interval_seconds: float = 10) -> None:
    while True:
        write_heartbeat()
        await asyncio.sleep(interval_seconds)


def heartbeat_is_fresh(max_age_seconds: float = 60) -> bool:
    try:
        age = time.time() - HEARTBEAT_PATH.stat().st_mtime
    except OSError:
        return False
    return age <= max_age_seconds


def is_ready(max_age_seconds: float = 60) -> bool:
    """Ready once startup gates have passed and the event loop is still alive."""
    return READY_PATH.exists() and heartbeat_is_fresh(max_age_seconds)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-age", type=float, default=60)
    parser.add_argument(
        "--ready",
        action="store_true",
        help="Readiness check: also require startup gates to have passed.",
    )
    args = parser.parse_args()
    check = is_ready if args.ready else heartbeat_is_fresh
    return 0 if check(args.max_age) else 1


if __name__ == "__main__":
    raise SystemExit(main())
