#!/usr/bin/env python3
"""Read-only worker lifecycle and admission sampler for elastic-worker experiments."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Protocol

import guard
from cluster import Cluster


DEFAULT_INTERVAL_S = 5.0
DEFAULT_PROBE_TIMEOUT_S = 5.0
SOURCE_CONTROLS = {
    "policy_default_reference_source": "e611d31bb",
    "scope": "policy-default reference only; not observed runtime identity",
}
_QUOTA_FIELDS = frozenset({
    "limits.cpu", "limits.memory", "persistentvolumeclaims", "pods",
    "requests.cpu", "requests.memory", "requests.storage",
})
_REQUIRED_QUOTA_FIELDS = frozenset({
    "limits.cpu", "limits.memory", "pods", "requests.cpu", "requests.memory",
})

# This intentionally lists every selected column. Do not broaden this allowlist:
# execution_workers has data that is neither needed nor safe for benchmark evidence.
WORKERS_SQL = """
select json_build_object(
  'sample_time_epoch_s', extract(epoch from statement_timestamp()),
  'workers', coalesce((
    select json_agg(json_build_object(
      'worker_id', id::text,
      'state', state,
      'resource_class', resource_class,
      'version', version,
      'job_name', kubernetes_job_name,
      'job_uid', kubernetes_job_uid,
      'pod_uid', kubernetes_pod_uid,
      'created_at_epoch_s', extract(epoch from created_at),
      'ready_at_epoch_s', extract(epoch from ready_at),
      'heartbeat_at_epoch_s', extract(epoch from heartbeat_at),
      'ended_at_epoch_s', extract(epoch from ended_at),
      'active_runs', active_runs,
      'memory_pressured', memory_pressured,
      'memory_budget_bytes', memory_budget_bytes,
      'memory_reserved_bytes', memory_reserved_bytes,
      'memory_limit_bytes', memory_limit_bytes,
      'runs_handled', runs_handled
    ) order by created_at, id)
    from execution_workers
    where form = 'job' and kubernetes_namespace = 'bench-tenant'
  ), '[]'::json)
)
"""

# Matches the existing loadgen unfinished-work semantics. It intentionally
# reports only aggregate counts, not request IDs, payloads, or user data.
WORK_SQL = """
select json_build_object(
  'executions', (select count(*) from executions
                 where status in ('Pending', 'Running', 'Scheduled', 'Cancelling')),
  'agent_runs', (select count(*) from agent_runs
                 where status in ('queued', 'pending', 'running')),
  'dispatches', (select count(*) from work_dispatches),
  'claimed_dispatches', (select count(*) from work_dispatches where assigned_worker_id is not null)
)
"""


class Clock(Protocol):
    def time(self) -> float: ...
    def monotonic(self) -> float: ...
    def sleep(self, seconds: float) -> None: ...


def _finite_number(value: object) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return value


def _reported_number(value: object) -> dict:
    number = _finite_number(value)
    return {"status": "reported", "value": number} if number is not None and number >= 0 else {
        "status": "unknown", "value": None,
    }


def _safe_text(value: object, *, limit: int = 255) -> str | None:
    return value if isinstance(value, str) and 0 < len(value) <= limit else None


def _timestamp(value: object) -> float | int | None:
    return _finite_number(value)


def _heartbeat_freshness(sample_time: float | int, heartbeat: object, stale_after_s: float) -> dict:
    observed = _timestamp(heartbeat)
    if observed is None:
        return {"state": "unknown", "age_s": None}
    age = sample_time - observed
    if age < 0:
        return {"state": "clock_skew", "age_s": age}
    return {"state": "stale" if age > stale_after_s else "fresh", "age_s": age}


def _worker(row: object, sample_time: float | int, stale_after_s: float) -> dict:
    if not isinstance(row, dict):
        raise ValueError("worker query returned a non-object row")
    heartbeat = _timestamp(row.get("heartbeat_at_epoch_s"))
    return {
        "worker_id": _safe_text(row.get("worker_id"), limit=128),
        "state": _safe_text(row.get("state"), limit=64),
        "resource_class": _safe_text(row.get("resource_class"), limit=128),
        "version": _safe_text(row.get("version"), limit=128),
        "job_name": _safe_text(row.get("job_name"), limit=128),
        "job_uid": _safe_text(row.get("job_uid"), limit=128),
        "pod_uid": _safe_text(row.get("pod_uid"), limit=128),
        "lifecycle": {
            "created_at_epoch_s": _timestamp(row.get("created_at_epoch_s")),
            "ready_at_epoch_s": _timestamp(row.get("ready_at_epoch_s")),
            "heartbeat_at_epoch_s": heartbeat,
            "ended_at_epoch_s": _timestamp(row.get("ended_at_epoch_s")),
        },
        "heartbeat_freshness": _heartbeat_freshness(sample_time, heartbeat, stale_after_s),
        "reported_heartbeat_state": {
            "active_runs": _reported_number(row.get("active_runs")),
            "memory_pressured": {
                "status": "reported" if isinstance(row.get("memory_pressured"), bool) else "unknown",
                "value": row.get("memory_pressured") if isinstance(row.get("memory_pressured"), bool) else None,
            },
            "memory_budget_bytes": _reported_number(row.get("memory_budget_bytes")),
            "memory_reserved_bytes": _reported_number(row.get("memory_reserved_bytes")),
            "memory_limit_bytes": _reported_number(row.get("memory_limit_bytes")),
            "runs_handled": _reported_number(row.get("runs_handled")),
            "scope": "database-reported heartbeat state; active_runs is not exact child concurrency",
        },
    }


def worker_snapshot(cluster: Cluster, *, stale_after_s: float,
                    timeout_s: float = DEFAULT_PROBE_TIMEOUT_S) -> dict:
    """Read and sanitize only the explicitly allowed worker lifecycle fields."""
    raw = cluster.driver_sql(WORKERS_SQL, timeout_s=timeout_s)
    if not isinstance(raw, dict):
        raise ValueError("worker query did not return an object")
    sample_time = _timestamp(raw.get("sample_time_epoch_s"))
    rows = raw.get("workers")
    if sample_time is None or not isinstance(rows, list):
        raise ValueError("worker query omitted its sample time or worker list")
    return {
        "sample_time_epoch_s": sample_time,
        "workers": [_worker(row, sample_time, stale_after_s) for row in rows],
        "scope": "read-only execution_workers allowlist",
    }


def work_snapshot(cluster: Cluster, *, timeout_s: float = DEFAULT_PROBE_TIMEOUT_S) -> dict:
    """Return canonical aggregate unfinished work, or explicitly retain it as unmeasured."""
    raw = cluster.driver_sql(WORK_SQL, timeout_s=timeout_s)
    if not isinstance(raw, dict):
        return {"status": "unmeasured", "reason": "invalid_result"}
    counts = {name: _reported_number(raw.get(name)) for name in (
        "executions", "agent_runs", "dispatches", "claimed_dispatches",
    )}
    return {
        "status": "observed" if all(item["status"] == "reported" for item in counts.values()) else "unmeasured",
        "counts": counts,
        "scope": "current database counts; sequential snapshot and not an admission guarantee",
    }


def _resources(container: object) -> dict:
    if not isinstance(container, dict):
        return {"requests": {}, "limits": {}}
    resource = container.get("resources")
    resource = resource if isinstance(resource, dict) else {}

    def selected(name: str) -> dict:
        values = resource.get(name)
        values = values if isinstance(values, dict) else {}
        return {key: values[key] for key in ("cpu", "memory") if isinstance(values.get(key), str)}

    return {"requests": selected("requests"), "limits": selected("limits")}


def pod_snapshot(cluster: Cluster, *, timeout_s: float = DEFAULT_PROBE_TIMEOUT_S) -> dict:
    """Read worker-pod lifecycle and resource fields without emitting raw Pod documents."""
    listed = json.loads(cluster.kubectl("get", "pods", "-o", "json", timeout_s=timeout_s))
    items = listed.get("items")
    if not isinstance(items, list):
        raise ValueError("pod list omitted items")
    pods = []
    for pod in items:
        if not isinstance(pod, dict):
            raise ValueError("pod list contained a non-object")
        metadata = pod.get("metadata") if isinstance(pod.get("metadata"), dict) else {}
        name = metadata.get("name")
        if (metadata.get("namespace") != guard.TENANT_NS or not isinstance(name, str)
                or not name.startswith("bifrost-worker-")):
            continue
        status = pod.get("status") if isinstance(pod.get("status"), dict) else {}
        spec = pod.get("spec") if isinstance(pod.get("spec"), dict) else {}
        ready_condition = next(
            (condition for condition in status.get("conditions", [])
             if isinstance(condition, dict) and condition.get("type") == "Ready"),
            None,
        )
        spec_containers = spec.get("containers") if isinstance(spec.get("containers"), list) else []
        status_containers = status.get("containerStatuses") if isinstance(status.get("containerStatuses"), list) else []
        spec_by_name = {
            container["name"]: container
            for container in spec_containers
            if isinstance(container, dict) and isinstance(container.get("name"), str)
        }
        status_by_name = {
            container["name"]: container
            for container in status_containers
            if isinstance(container, dict) and isinstance(container.get("name"), str)
        }
        containers = []
        for container_name in sorted(set(spec_by_name) | set(status_by_name)):
            spec_container = spec_by_name.get(container_name, {})
            status_container = status_by_name.get(container_name, {})
            state = status_container.get("state") if isinstance(status_container.get("state"), dict) else {}
            running = state.get("running") if isinstance(state.get("running"), dict) else {}
            containers.append({
                "name": container_name,
                "ready": status_container.get("ready") if isinstance(status_container.get("ready"), bool) else None,
                "restart_count": _finite_number(status_container.get("restartCount")),
                "started_at": _safe_text(running.get("startedAt"), limit=64),
                "image": _safe_text(spec_container.get("image"), limit=512),
                "image_id": _safe_text(status_container.get("imageID"), limit=512),
                "resources": _resources(spec_container),
            })
        ready_status = ready_condition.get("status") if isinstance(ready_condition, dict) else None
        pods.append({
            "name": name,
            "pod_uid": _safe_text(metadata.get("uid"), limit=128),
            "phase": _safe_text(status.get("phase"), limit=64),
            "created_at": _safe_text(metadata.get("creationTimestamp"), limit=64),
            "deletion_at": _safe_text(metadata.get("deletionTimestamp"), limit=64),
            "started_at": _safe_text(status.get("startTime"), limit=64),
            "ready": {
                "value": {"True": True, "False": False}.get(ready_status),
                "transitioned_at": (
                    _safe_text(ready_condition.get("lastTransitionTime"), limit=64)
                    if isinstance(ready_condition, dict) else None
                ),
            },
            "containers": containers,
        })
    return {
        "status": "observed",
        "pods": sorted(pods, key=lambda item: item["name"]),
        "scope": "safe worker-pod lifecycle and resources",
    }


def quota_snapshot(cluster: Cluster, *, timeout_s: float = DEFAULT_PROBE_TIMEOUT_S) -> dict:
    """Read only named ResourceQuota hard/used fields from the guarded tenant."""
    quota = json.loads(cluster.kubectl(
        "get", "resourcequota", "tenant-envelope", "-o", "json", timeout_s=timeout_s,
    ))
    status = quota.get("status") if isinstance(quota.get("status"), dict) else {}

    def selected(name: str) -> dict:
        values = status.get(name) if isinstance(status.get(name), dict) else {}
        return {key: values[key] for key in sorted(_QUOTA_FIELDS) if isinstance(values.get(key), str)}

    hard, used = selected("hard"), selected("used")
    missing = {
        view: sorted(_REQUIRED_QUOTA_FIELDS - set(values))
        for view, values in (("hard", hard), ("used", used))
    }
    if any(missing.values()):
        raise ValueError("quota status is missing required fields")
    return {
        "status": "observed",
        "hard": hard,
        "used": used,
        "scope": "whitelisted ResourceQuota fields",
    }


def _timed(clock: Clock, started_wall: float, started_monotonic: float, **payload: object) -> dict:
    return {
        "timing": {
            "started_wall_epoch_s": started_wall,
            "finished_wall_epoch_s": clock.time(),
            "started_monotonic_s": started_monotonic,
            "finished_monotonic_s": clock.monotonic(),
        },
        **payload,
    }


def _append(output, frame: dict) -> None:
    output.write(json.dumps(frame, allow_nan=False, sort_keys=True) + "\n")
    output.flush()
    os.fsync(output.fileno())


def _validate(duration_s: float, interval_s: float, max_workers: int, warm_workers: int) -> None:
    if not all(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0
               for value in (duration_s, interval_s)):
        raise ValueError("duration and interval must be finite positive seconds")
    if isinstance(max_workers, bool) or not isinstance(max_workers, int) or max_workers not in {1, 2, 4}:
        raise ValueError("max workers must be one of 1, 2, or 4")
    if isinstance(warm_workers, bool) or not isinstance(warm_workers, int) or not 0 <= warm_workers <= max_workers:
        raise ValueError("warm workers must be an integer from zero through max workers")


def run(out: Path, *, duration_s: float, interval_s: float = DEFAULT_INTERVAL_S, max_workers: int,
        warm_workers: int, stale_after_s: float = 15.0, probe_timeout_s: float = DEFAULT_PROBE_TIMEOUT_S,
        cluster: Cluster | None = None, clock: Clock = time) -> None:
    """Append bounded raw observation frames to a newly-created JSONL file."""
    _validate(duration_s, interval_s, max_workers, warm_workers)
    if (not isinstance(stale_after_s, (int, float)) or isinstance(stale_after_s, bool)
            or not math.isfinite(stale_after_s) or stale_after_s <= 0):
        raise ValueError("stale heartbeat threshold must be finite and positive")
    if (not isinstance(probe_timeout_s, (int, float)) or isinstance(probe_timeout_s, bool)
            or not math.isfinite(probe_timeout_s) or probe_timeout_s <= 0):
        raise ValueError("probe timeout must be finite and positive")
    if out.exists():
        raise FileExistsError(f"refusing to overwrite fresh output path: {out}")

    observed = cluster or Cluster()
    if observed.namespace != guard.TENANT_NS:
        raise guard.GuardError("worker timeline may observe only bench-tenant")
    observed.verify()
    descriptor = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    started_monotonic = clock.monotonic()
    deadline = started_monotonic + duration_s
    scheduled_sample = started_monotonic
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        session_started_wall = clock.time()
        _append(output, _timed(
            clock, session_started_wall, clock.monotonic(), event="session_started", schema_version=1,
            source_controls=SOURCE_CONTROLS, experiment={"max_workers": max_workers, "warm_workers": warm_workers},
            interval_s=interval_s, duration_s=duration_s, stale_heartbeat_after_s=stale_after_s,
            probe_timeout_s=probe_timeout_s,
            snapshots="database, pods, and quota are sequential and not atomic",
        ))
        while clock.monotonic() < deadline:
            frame_started_wall, frame_started_monotonic = clock.time(), clock.monotonic()
            stage = "worker_db"
            collected: dict[str, object] = {}
            try:
                workers = worker_snapshot(observed, stale_after_s=stale_after_s, timeout_s=probe_timeout_s)
                collected["worker_db"] = workers
                stage = "work"
                work = work_snapshot(observed, timeout_s=probe_timeout_s)
                collected["work"] = work
                stage = "pods"
                pods = pod_snapshot(observed, timeout_s=probe_timeout_s)
                collected["pods"] = pods
                stage = "quota"
                quota = quota_snapshot(observed, timeout_s=probe_timeout_s)
            except BaseException as error:
                _append(output, _timed(
                    clock, frame_started_wall, frame_started_monotonic, event="failure",
                    failure={"stage": stage, "error_class": type(error).__name__}, **collected,
                ))
                raise
            _append(output, _timed(
                clock, frame_started_wall, frame_started_monotonic, event="sample",
                experiment={"max_workers": max_workers, "warm_workers": warm_workers},
                worker_db=workers, work=work, pods=pods, quota=quota,
                snapshots="database, pods, and quota are sequential and not atomic",
                cadence={"scheduled_monotonic_s": scheduled_sample},
            ))
            now = clock.monotonic()
            next_sample, missed = scheduled_sample + interval_s, 0
            if next_sample < now:
                missed = math.ceil((now - next_sample) / interval_s)
                next_sample += missed * interval_s
            if missed:
                gap_started_wall, gap_started_monotonic = clock.time(), clock.monotonic()
                _append(output, _timed(
                    clock, gap_started_wall, gap_started_monotonic, event="cadence_gap",
                    cadence={
                        "previous_scheduled_monotonic_s": scheduled_sample,
                        "next_scheduled_monotonic_s": next_sample,
                        "missed_intervals": missed,
                    },
                ))
            scheduled_sample = next_sample
            clock.sleep(max(0.0, min(scheduled_sample - clock.monotonic(), deadline - clock.monotonic())))
        finished_wall, finished_monotonic = clock.time(), clock.monotonic()
        _append(output, _timed(clock, finished_wall, finished_monotonic, event="completed"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="new JSONL destination; existing paths are refused")
    parser.add_argument("--duration-s", type=float, required=True, help="finite positive observation duration")
    parser.add_argument("--interval-s", type=float, default=DEFAULT_INTERVAL_S,
                        help="sample interval in seconds (default: 5)")
    parser.add_argument("--max-workers", type=int, required=True, choices=(1, 2, 4),
                        help="declared maximum worker count; sampler does not enforce it")
    parser.add_argument("--warm-workers", type=int, required=True,
                        help="declared warm worker count from zero through max workers")
    parser.add_argument("--stale-heartbeat-s", type=float, default=15.0,
                        help="heartbeat age above which a DB-reported heartbeat is labelled stale")
    parser.add_argument("--probe-timeout-s", type=float, default=DEFAULT_PROBE_TIMEOUT_S,
                        help="deadline for each read-only database or Kubernetes probe (default: 5)")
    args = parser.parse_args()
    try:
        run(args.out, duration_s=args.duration_s, interval_s=args.interval_s, max_workers=args.max_workers,
            warm_workers=args.warm_workers, stale_after_s=args.stale_heartbeat_s,
            probe_timeout_s=args.probe_timeout_s)
    except Exception as error:
        print(f"worker timeline failed: {type(error).__name__}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
