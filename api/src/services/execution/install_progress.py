"""Fleet-aware install progress aggregation.

Each worker in the fanout reports its phase for a given install run into a
per-run Redis hash. After each write the worker computes an aggregate over all
reporting workers (out of the live worker count) and publishes ONE summary
message to the shared ``package:install`` WebSocket channel. The frontend
collapses consecutive identical summaries, so N workers reporting the same
aggregate render as a single line.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, cast

from src.core.pubsub import manager as pubsub_manager
from src.core.redis_client import get_redis_client

logger = logging.getLogger(__name__)

CHANNEL = "package:install"
_HASH_PREFIX = "bifrost:pkg-install:"
_HASH_TTL_SECONDS = 120
_PHASES = ("installing", "installed", "recycling", "recycled", "failed")


@dataclass
class WorkerPhase:
    phase: str
    package: str | None = None
    error: str | None = None


async def _raw_redis():  # pragma: no cover - thin accessor, patched in tests
    client = get_redis_client()
    return await client._get_redis()


async def _live_worker_count(redis: Any) -> int:
    """Count pool registration keys (exactly bifrost:pool:{id}, 3 parts)."""
    cursor = 0
    count = 0
    while True:
        cursor, keys = await cast(
            Awaitable[tuple[int, list[str]]],
            redis.scan(cursor, match="bifrost:pool:*", count=100),
        )
        for key in keys:
            if key.count(":") == 2:
                count += 1
        if cursor == 0:
            break
    return count


def aggregate_phases(phases: dict[str, WorkerPhase], total: int) -> dict[str, Any]:
    """Reduce per-worker phases into counts + failure detail."""
    counts = {p: 0 for p in _PHASES}
    failures: list[dict[str, Any]] = []
    for worker_id, wp in phases.items():
        if wp.phase in counts:
            counts[wp.phase] += 1
        if wp.phase == "failed":
            failures.append(
                {"worker": worker_id, "package": wp.package, "error": wp.error}
            )
    return {
        "total": max(total, len(phases)),
        "installing": counts["installing"],
        "installed": counts["installed"] + counts["recycling"] + counts["recycled"],
        "recycling": counts["recycling"],
        "recycled": counts["recycled"],
        "failed": counts["failed"],
        "failures": failures,
    }


def summary_line(agg: dict[str, Any], action: str) -> str:
    """Human-readable single line for the terminal view."""
    verb = "Installing" if action == "install" else "Uninstalling"
    total = agg["total"]
    if agg["installing"]:
        base = f"{verb} on {agg['installing']}/{total} workers…"
    else:
        # 'installed' is the folded count (installed + recycling + recycled):
        # how many workers have gotten past the install step.
        done_verb = verb.replace("ing", "ed")
        base = f"{done_verb} on {agg['installed']}/{total} workers"
    if agg["failed"]:
        pkgs = ", ".join(
            f"{f['worker']}: {f['package']}" for f in agg["failures"] if f.get("package")
        ) or f"{agg['failed']} worker(s)"
        base += f" — {agg['failed']} failed ({pkgs})"
    return base


async def report_phase(
    run_id: str,
    worker_id: str,
    phase: str,
    action: str = "install",
    package: str | None = None,
    error: str | None = None,
) -> None:
    """Write this worker's phase, then publish one aggregate summary.

    Best-effort: never raises (a progress-reporting failure must not abort the
    install).
    """
    try:
        redis = await _raw_redis()
        key = f"{_HASH_PREFIX}{run_id or 'current'}"
        field_val = json.dumps(
            {"phase": phase, "package": package, "error": (error or "")[:500]}
        )
        await redis.hset(key, worker_id, field_val)  # type: ignore[misc]
        await redis.expire(key, _HASH_TTL_SECONDS)

        raw = await cast(Awaitable[dict[str, str]], redis.hgetall(key))
        phases: dict[str, WorkerPhase] = {}
        for wid, val in raw.items():
            try:
                d = json.loads(val)
                phases[wid] = WorkerPhase(
                    phase=d.get("phase", ""),
                    package=d.get("package"),
                    error=d.get("error"),
                )
            except (json.JSONDecodeError, TypeError):
                continue

        total = await _live_worker_count(redis)
        agg = aggregate_phases(phases, total)
        message = {
            "type": "progress",
            "action": action,
            "line": summary_line(agg, action),
            **agg,
        }
        await pubsub_manager.broadcast(CHANNEL, message)
    except Exception as e:  # noqa: BLE001 - progress reporting must never break install
        logger.warning(f"[pkg-install] progress report failed: {e}")
