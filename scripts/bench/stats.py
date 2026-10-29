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
        if status not in SUCCESS_STATUSES or not all(record.get(k) for k in ("created_at", "started_at", "completed_at")):
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


def cpu_ms_per_run(before: list[dict], after: list[dict], runs: int) -> dict[str, float]:
    """CPU each role spent per run, from the kubelet's cumulative counters.

    Only containers present in both samples count (worker Jobs come and go).
    The window includes the settle period, so a role's idle CPU is included.
    """
    start = {(row["pod"], row["container"]): row for row in before}
    totals: dict[str, float] = {}
    for row in after:
        previous = start.get((row["pod"], row["container"]))
        if previous is None:
            continue
        totals[row["role"]] = totals.get(row["role"], 0.0) + row["cpu_core_seconds"] - previous["cpu_core_seconds"]
    return {role: round(seconds * 1000 / max(runs, 1), 1) for role, seconds in totals.items() if role in ("api", "worker")}


def db_delta(before: dict, after: dict, seconds: float) -> dict:
    """Postgres work between two counter samples; tables that didn't change are dropped."""
    tables: dict[str, list[int]] = {}
    for name, (writes, scans) in after["tables"].items():
        previous = before["tables"].get(name, [0, 0])
        change = [writes - previous[0], scans - previous[1]]
        if any(change):
            tables[name] = change
    return {
        "seconds": seconds,
        "xact": after["xact"] - before["xact"],
        "rows_written": after["rows_written"] - before["rows_written"],
        "wal_bytes": after["wal_bytes"] - before["wal_bytes"],
        "tables": tables,
    }


def db_rate(delta: dict) -> dict[str, float]:
    seconds = max(delta["seconds"], 1e-9)
    return {
        "xact_per_s": round(delta["xact"] / seconds, 2),
        "rows_written_per_s": round(delta["rows_written"] / seconds, 2),
        "wal_kib_per_s": round(delta["wal_bytes"] / 1024 / seconds, 2),
    }


def db_per_run(delta: dict, idle: dict[str, float], runs: int) -> dict[str, float]:
    """Postgres work per run, net of the idle rate over the same window."""
    seconds = delta["seconds"]
    runs = max(runs, 1)
    return {
        "xact": round((delta["xact"] - idle["xact_per_s"] * seconds) / runs, 1),
        "rows_written": round((delta["rows_written"] - idle["rows_written_per_s"] * seconds) / runs, 1),
        "wal_kib": round((delta["wal_bytes"] / 1024 - idle["wal_kib_per_s"] * seconds) / runs, 1),
    }


def top_tables(tables: dict[str, list[int]], count: int) -> list[tuple[str, int, int]]:
    """The tables with the most rows written plus scans, as (name, writes, scans)."""
    ranked = sorted(tables.items(), key=lambda item: item[1][0] + item[1][1], reverse=True)
    return [(name, writes, scans) for name, (writes, scans) in ranked[:count]]


def worker_children(samples: list[dict[str, list[dict]]]) -> dict:
    """Peaks over worker process samples: run children (depth 2+) and templates (depth 1)."""
    child_peak = template_peak = 0.0
    children_peak = 0
    for sample in samples:
        children = 0
        for processes in sample.values():
            for process in processes:
                if process["depth"] >= 2:
                    children += 1
                    child_peak = max(child_peak, process["private_mib"])
                elif process["depth"] == 1:
                    template_peak = max(template_peak, process["private_mib"])
        children_peak = max(children_peak, children)
    return {
        "samples": len(samples),
        "peak_child_private_mib": child_peak,
        "peak_children": children_peak,
        "peak_template_private_mib": template_peak,
    }
