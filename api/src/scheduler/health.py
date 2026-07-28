"""Event-loop heartbeat used by the scheduler container liveness probe."""

from __future__ import annotations

import argparse
import asyncio
import time
from pathlib import Path

HEARTBEAT_PATH = Path("/tmp/bifrost-scheduler-heartbeat")


def write_heartbeat() -> None:
    HEARTBEAT_PATH.touch()


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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-age", type=float, default=60)
    args = parser.parse_args()
    return 0 if heartbeat_is_fresh(args.max_age) else 1


if __name__ == "__main__":
    raise SystemExit(main())
