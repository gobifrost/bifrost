"""Inspect one bounded, dry-run-only page of agent-run step Redis streams.

Run inside an API container:
``python scripts/inspect_agent_run_step_streams.py --cursor 0 --limit 100``.
The report contains aggregate counts only and this script never mutates Redis
or the database.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from src.core.cache.redis_client import get_redis
from src.core.database import get_db_context
from src.services.agent_run_step_streams import (
    AGENT_RUN_STEP_STREAM_SCAN_LIMIT,
    inspect_orphaned_agent_run_step_streams,
)


def _nonnegative(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be non-negative")
    return parsed


def _bounded_limit(value: str) -> int:
    parsed = _nonnegative(value)
    if not 1 <= parsed <= AGENT_RUN_STEP_STREAM_SCAN_LIMIT:
        raise argparse.ArgumentTypeError(
            f"must be between 1 and {AGENT_RUN_STEP_STREAM_SCAN_LIMIT}"
        )
    return parsed


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cursor", type=_nonnegative, default=0)
    parser.add_argument(
        "--limit",
        type=_bounded_limit,
        default=AGENT_RUN_STEP_STREAM_SCAN_LIMIT,
    )
    return parser.parse_args(argv)


async def inspect(cursor: int, limit: int) -> dict[str, int]:
    async with get_db_context() as db, get_redis() as redis:
        return await inspect_orphaned_agent_run_step_streams(
            db,
            redis,
            cursor=cursor,
            limit=limit,
        )


async def run(args: argparse.Namespace) -> int:
    try:
        report = await inspect(args.cursor, args.limit)
    except Exception as exc:
        print(
            json.dumps(
                {"error": "agent_run_step_stream_inspection_failed", "exception_type": type(exc).__name__}
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(parse_args())))
