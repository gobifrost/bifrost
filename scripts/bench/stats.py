"""Execution timing statistics from platform-recorded timestamps.

Queue wait, run time and end-to-end come from the record's own
``created_at`` / ``started_at`` / ``completed_at``, never from driver clocks,
so they measure what a customer experiences.
"""

from __future__ import annotations

import math
from datetime import datetime

SUCCESS_STATUSES = frozenset({"success", "completed"})


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def percentile(values: list[float], fraction: float) -> float:
    """Nearest-rank percentile; ``values`` need not be sorted."""
    ordered = sorted(values)
    rank = max(1, math.ceil(fraction * len(ordered)))
    return ordered[rank - 1]


def _distribution(values: list[float]) -> dict[str, float]:
    if not values:
        return {"p50": 0.0, "p95": 0.0, "max": 0.0}
    return {
        "p50": round(percentile(values, 0.50), 1),
        "p95": round(percentile(values, 0.95), 1),
        "max": round(max(values), 1),
    }


def timings(records: list[dict]) -> dict:
    """Summarize one class's records; failed records are counted, not timed."""
    queue: list[float] = []
    run: list[float] = []
    e2e: list[float] = []
    failed: list[dict] = []
    for record in records:
        status = str(record.get("status", "")).lower()
        if status not in SUCCESS_STATUSES or not record.get("started_at") or not record.get("completed_at"):
            failed.append({"id": record.get("id"), "status": record.get("status"), "error": record.get("error")})
            continue
        created = _parse(record["created_at"])
        started = _parse(record["started_at"])
        completed = _parse(record["completed_at"])
        queue.append((started - created).total_seconds() * 1000)
        run.append((completed - started).total_seconds() * 1000)
        e2e.append((completed - created).total_seconds() * 1000)
    return {
        "runs": len(records),
        "succeeded": len(e2e),
        "failed": len(failed),
        "failures": failed[:5],
        "queue_ms": _distribution(queue),
        "run_ms": _distribution(run),
        "e2e_ms": _distribution(e2e),
    }
