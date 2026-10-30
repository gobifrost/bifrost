"""Read-only soak metrics executed inside the benchmark API pod.

The API pod already has the configured S3 transport and credentials.  This
script deliberately reports only visible current S3 object payload size/count; it
never prints settings, endpoints, or credential material.
"""

from __future__ import annotations

import asyncio
import json
import math
import sys
from pathlib import Path
from typing import Callable


TERMINAL_STATUSES = {
    "workflow": frozenset({"Success", "Failed", "Timeout", "Stuck", "CompletedWithErrors", "Cancelled"}),
    "agent": frozenset({"completed", "failed", "cancelled"}),
}


def terminal_counts(records: list[dict], outcomes: dict) -> dict[str, int]:
    """Count durable terminals separately from correct successes.

    ``search.to_records`` represents a durable terminal failure as ``failed``;
    an ``unfinished`` record has no durable terminal outcome and stays out of
    the growth denominator.
    """
    durable = outcomes.get("outcomes")
    if not isinstance(durable, dict):
        raise ValueError("soak requires raw durable outcomes")
    duplicates = outcomes.get("duplicates", {})
    if not isinstance(duplicates, dict) or duplicates:
        raise ValueError("soak durable outcome proof contains duplicate request ids")
    missing = outcomes.get("missing", [])
    if not isinstance(missing, list):
        raise ValueError("soak durable outcome proof has an invalid missing list")

    terminal: list[dict] = []
    unfinished = 0
    record_ids: set[str] = set()
    for record in records:
        request_id = record.get("id")
        if not isinstance(request_id, str) or not request_id or request_id in record_ids:
            raise ValueError("soak records lack unique request ids for durable outcome proof")
        record_ids.add(request_id)
        outcome = durable.get(request_id)
        if outcome is None:
            http_status = record.get("http_status")
            if isinstance(http_status, int) and 200 <= http_status < 300:
                raise ValueError(f"accepted request {request_id} has no durable outcome proof")
            continue
        if not isinstance(outcome, dict):
            raise ValueError(f"durable outcome {request_id} is not an object")
        kind, status, completed = outcome.get("kind"), outcome.get("status"), outcome.get("completed")
        if kind not in TERMINAL_STATUSES or not isinstance(status, str):
            raise ValueError(f"durable outcome {request_id} lacks canonical kind/status")
        if status in TERMINAL_STATUSES[kind]:
            if (
                not isinstance(completed, (int, float))
                or isinstance(completed, bool)
                or not math.isfinite(float(completed))
            ):
                raise ValueError(f"terminal durable outcome {request_id} lacks completion proof")
            terminal.append(record)
        elif completed is not None:
            raise ValueError(f"completed durable outcome {request_id} has a nonterminal status")
        else:
            unfinished += 1
    if any(not isinstance(request_id, str) or request_id not in record_ids for request_id in missing):
        raise ValueError("soak durable outcome proof has invalid missing request ids")
    return {
        "durably_terminal": len(terminal),
        "correct_successes": sum(record.get("outcome") == "correct" for record in terminal),
        "unfinished": unfinished,
    }


def per_thousand(delta: dict[str, int | float | None], terminal: int) -> dict[str, float | None]:
    """Normalize physical-growth deltas by every durable terminal record."""
    names = ("db_bytes", "wal_generated_bytes", "object_bytes", "object_count")
    if terminal <= 0:
        return {f"{name}_per_1000_terminal": None for name in names}
    return {
        f"{name}_per_1000_terminal": None if delta[name] is None else float(delta[name]) * 1000 / terminal
        for name in names
    }


def _ols_slope(points: list[tuple[float, int]]) -> float | None:
    if len(points) < 2:
        return None
    mean_x = math.fsum(point[0] for point in points) / len(points)
    mean_y = math.fsum(point[1] for point in points) / len(points)
    denominator = math.fsum((point[0] - mean_x) ** 2 for point in points)
    if denominator == 0:
        return None
    return math.fsum((x - mean_x) * (y - mean_y) for x, y in points) / denominator


def _slope_evidence(points: list[tuple[float, int]], appearances: list[float], seen_before: bool) -> dict:
    slope = _ols_slope(points)
    gaps = [right - left for left, right in zip(appearances, appearances[1:])]
    return {
        "sample_count": len(points),
        "first_sample_s": points[0][0] if points else None,
        "last_sample_s": points[-1][0] if points else None,
        "slope_bytes_per_hour": None if slope is None else slope * 3600,
        "coverage": "observed" if slope is not None else "insufficient_samples",
        "seen_before_post_window": seen_before,
        "first_seen_s": appearances[0],
        "last_seen_s": appearances[-1],
        "observed_sample_gaps_s": gaps,
        "max_observed_sample_gap_s": max(gaps) if gaps else None,
    }


def memory_slopes(
    samples: list[dict], started_at: float, post_start_s: float = 30 * 60,
    include: Callable[[str], bool] | None = None,
) -> dict[str, dict]:
    """Descriptive per-pod current-memory OLS slopes after the first 30 minutes."""
    points: dict[str, list[tuple[float, int]]] = {}
    seen_before: set[str] = set()
    seen: dict[str, list[float]] = {}
    for sample in samples:
        at = sample.get("at")
        pods = sample.get("pods")
        if not isinstance(at, (int, float)) or not isinstance(pods, dict):
            continue
        for name, counters in pods.items():
            if not isinstance(name, str) or (include is not None and not include(name)):
                continue
            memory = counters.get("memory_bytes") if isinstance(counters, dict) else None
            current = memory.get("current") if isinstance(memory, dict) else None
            if not isinstance(current, int):
                continue
            seen.setdefault(name, []).append(float(at) - started_at)
            if at < started_at + post_start_s:
                seen_before.add(name)
                continue
            points.setdefault(name, []).append((float(at) - started_at, current))
    output: dict[str, dict] = {}
    for name in sorted(points | {name: [] for name in seen}):
        entries = points.get(name, [])
        appearances = seen[name]
        output[name] = _slope_evidence(entries, appearances, name in seen_before)
    return output


def role_memory_slopes(
    samples: list[dict], started_at: float, role_for_name: Callable[[str], str],
    post_start_s: float = 30 * 60, include: Callable[[str], bool] | None = None,
) -> dict[str, dict]:
    """Describe post-window current-memory sums for each selected pod role."""
    points: dict[str, list[tuple[float, int]]] = {}
    appearances: dict[str, list[float]] = {}
    seen_before: set[str] = set()
    pod_counts: dict[str, list[int]] = {}
    for sample in samples:
        at, pods = sample.get("at"), sample.get("pods")
        if not isinstance(at, (int, float)) or not isinstance(pods, dict):
            continue
        grouped: dict[str, list[int]] = {}
        for name, counters in pods.items():
            if not isinstance(name, str) or (include is not None and not include(name)):
                continue
            memory = counters.get("memory_bytes") if isinstance(counters, dict) else None
            current = memory.get("current") if isinstance(memory, dict) else None
            if not isinstance(current, int):
                continue
            grouped.setdefault(role_for_name(name.rsplit("/", 1)[-1]), []).append(current)
        for role, values in grouped.items():
            elapsed = float(at) - started_at
            appearances.setdefault(role, []).append(elapsed)
            pod_counts.setdefault(role, []).append(len(values))
            if at < started_at + post_start_s:
                seen_before.add(role)
            else:
                points.setdefault(role, []).append((elapsed, sum(values)))
    output: dict[str, dict] = {}
    for role in sorted(points | appearances):
        evidence = _slope_evidence(points.get(role, []), appearances[role], role in seen_before)
        evidence.update({"aggregate": "sum_current_memory_bytes", "pods_per_sample": pod_counts[role]})
        output[role] = evidence
    return output


async def object_totals() -> dict[str, int]:
    """List visible current S3 payload objects through the configured transport."""
    from src.config import get_settings
    from src.core.s3_transport import S3Transport

    settings = get_settings()
    if not settings.s3_bucket:
        raise RuntimeError("configured API S3 bucket is missing")
    count = size = 0
    continuation: str | None = None
    async with S3Transport(
        endpoint_url=settings.s3_endpoint_url,
        access_key_id=settings.s3_access_key,
        secret_access_key=settings.s3_secret_key,
        region=settings.s3_region,
    ) as client:
        while True:
            arguments: dict[str, str] = {"Bucket": settings.s3_bucket}
            if continuation is not None:
                arguments["ContinuationToken"] = continuation
            page = await client.list_objects_v2(**arguments)
            for item in page.get("Contents", []):
                count += 1
                size += int(item["Size"])
            if not page.get("IsTruncated"):
                break
            continuation = page.get("NextContinuationToken")
            if not isinstance(continuation, str) or not continuation:
                raise RuntimeError("truncated S3 listing did not provide a continuation token")
    return {"object_count": count, "object_bytes": size}


def main() -> None:
    mode = sys.argv[1]
    # Cluster.exec_python writes the JSON argument to a temporary file inside
    # the pod; reading it avoids command-line size limits.
    _arguments = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    if mode != "objects":
        raise ValueError(f"unsupported soak metric mode {mode!r}")
    print(json.dumps(asyncio.run(object_totals()), allow_nan=False))


if __name__ == "__main__":
    main()
