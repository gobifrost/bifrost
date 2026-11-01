#!/usr/bin/env python3
"""One bounded stock-runtime worker-elasticity arm; it never builds images.

Run an arm explicitly rather than composing a nine-arm campaign.  All runtime
probes are fail-closed and retained before a later observation can fail.
"""

from __future__ import annotations

import argparse
import copy
import fcntl
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import deploy  # noqa: E402
import hostmetrics  # noqa: E402
import resource_summary  # noqa: E402
import run  # noqa: E402
import search  # noqa: E402
import worker_timeline  # noqa: E402
from cluster import Cluster  # noqa: E402

DRIVER = HERE / "incluster" / "elasticity_driver.py"
LOADGEN = HERE / "incluster" / "loadgen.py"
LOCK_PATH = Path("/tmp/bifrost-capacity-host.lock")
POLICY_FIELDS = ("warm_workers", "max_workers", "worker_memory_mib", "largest_worker_mib", "idle_seconds")
FLOOR_TIMEOUT_S = 720.0
IDLE_OBSERVE_S = 30.0
LIVE_STATES = frozenset({"reserved", "starting", "ready", "draining"})


def build_profile(warm_workers: int, max_workers: int) -> dict:
    """Return the fixed max-four envelope while changing only the requested arm."""
    if type(warm_workers) is not int or type(max_workers) is not int or max_workers not in (1, 2, 4) or not 0 <= warm_workers <= max_workers:
        raise ValueError("arms require non-negative warm workers through max workers 1, 2, or 4")
    profile = copy.deepcopy(run.load_profile("4gb-capacity"))
    profile["quota"] = {"cpu": "5200m", "memory": "5664Mi", "storage": "50Gi", "pods": 20, "pvcs": 4}
    profile["components"] = {
        "api": {"replicas": 1, "cpu": "1250m", "memory": "640Mi"},
        "client": {"replicas": 1, "cpu": "100m", "memory": "32Mi"},
        "redis": {"cpu": "150m", "memory": "64Mi"},
        "postgres": {"cpu": "1", "memory": "768Mi", "storage": "20Gi"},
        "pooler": {"cpu": "100m", "memory": "64Mi"},
    }
    profile["worker_cpu"] = "650m"
    profile["worker_policy"] = {"warm_workers": warm_workers, "max_workers": max_workers,
                                "worker_memory_mib": 1024, "largest_worker_mib": 1024, "idle_seconds": 300}
    profile["settle_s"] = 0
    return profile


def policy_controls(response: object) -> dict[str, int]:
    """Project only the scalar policy controls evidence may retain."""
    policy = response.get("policy") if isinstance(response, dict) else None
    if not isinstance(policy, dict):
        raise RuntimeError("policy response omitted policy")
    result = {name: policy.get(name) for name in POLICY_FIELDS}
    if any(type(value) is not int for value in result.values()):
        missing = [name for name, value in result.items() if type(value) is not int]
        raise RuntimeError(f"policy response missing valid scalar fields: {', '.join(missing)}")
    return result  # type: ignore[return-value]


def observe(cluster: Cluster) -> dict:
    """One sequential, allowlisted checkpoint. Probe failures deliberately propagate."""
    return {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "worker_db": worker_timeline.worker_snapshot(cluster, stale_after_s=15.0),
            "work": worker_timeline.work_snapshot(cluster),
            "pods": worker_timeline.pod_snapshot(cluster),
            "quota": worker_timeline.quota_snapshot(cluster),
            "scope": "sequential DB/pod/quota observations; missing data is never zero"}


def floor_state(sample: dict, warm_workers: int) -> str:
    """Return ready only for the explicit target floor; unknown remains waiting."""
    work = sample.get("work", {})
    pods = sample.get("pods", {})
    worker_db = sample.get("worker_db")
    if not isinstance(work, dict) or not isinstance(pods, dict) or not isinstance(worker_db, dict):
        return "waiting"
    workers = worker_db.get("workers")
    if work.get("status") != "observed" or pods.get("status") != "observed" or not isinstance(workers, list):
        return "waiting"
    counts = work.get("counts", {})
    if any((counts.get(name) or {}).get("status") != "reported" or (counts.get(name) or {}).get("value") != 0
           for name in ("executions", "agent_runs", "dispatches", "claimed_dispatches")):
        return "waiting"
    live = []
    for worker in workers:
        if not isinstance(worker, dict) or not isinstance(worker.get("lifecycle"), dict):
            return "waiting"
        state, ended = worker.get("state"), worker["lifecycle"].get("ended_at_epoch_s")
        if state in LIVE_STATES:
            if ended is not None:
                return "waiting"
            live.append(worker)
        elif ended is None:
            return "waiting"
    pod_rows = pods.get("pods")
    if not isinstance(pod_rows, list):
        return "waiting"
    if warm_workers == 0:
        return "ready" if not live and not pod_rows else "waiting"
    if len(live) != warm_workers or len(pod_rows) != warm_workers:
        return "waiting"
    worker_uids = {worker.get("pod_uid") for worker in live}
    pod_uids = {pod.get("pod_uid") for pod in pod_rows if isinstance(pod, dict)}
    if len(worker_uids) != warm_workers or worker_uids != pod_uids or not all(isinstance(uid, str) and uid for uid in worker_uids):
        return "waiting"
    for worker, pod in zip(sorted(live, key=lambda item: item["pod_uid"]), sorted(pod_rows, key=lambda item: item.get("pod_uid", ""))):
        heartbeat = worker.get("heartbeat_freshness", {})
        reported = worker.get("reported_heartbeat_state", {})
        active = reported.get("active_runs", {}) if isinstance(reported, dict) else {}
        reserved = reported.get("memory_reserved_bytes", {}) if isinstance(reported, dict) else {}
        budget = reported.get("memory_budget_bytes", {}) if isinstance(reported, dict) else {}
        pressured = reported.get("memory_pressured", {}) if isinstance(reported, dict) else {}
        ready = pod.get("ready", {}) if isinstance(pod, dict) else {}
        if worker.get("state") != "ready" or heartbeat.get("state") != "fresh" or active != {"status": "reported", "value": 0} \
                or reserved != {"status": "reported", "value": 0} or budget.get("status") != "reported" \
                or type(budget.get("value")) not in (int, float) or budget["value"] <= 0 \
                or pressured != {"status": "reported", "value": False} or pod.get("phase") != "Running" \
                or pod.get("deletion_at") is not None or ready.get("value") is not True:
            return "waiting"
    return "ready"


def wait_for_floor(cluster: Cluster, warm_workers: int, *, timeout_s: float = FLOOR_TIMEOUT_S,
                   checkpoint: Callable[[dict], None] | None = None, sleep: Callable[[float], None] = time.sleep,
                   monotonic: Callable[[], float] = time.monotonic) -> dict:
    """Observe, never manipulate, until the target idle floor appears or expires."""
    deadline = monotonic() + timeout_s
    while True:
        sample = observe(cluster)
        sample["floor"] = {"warm_workers": warm_workers, "state": floor_state(sample, warm_workers)}
        if checkpoint:
            checkpoint(sample)
        if sample["floor"]["state"] == "ready":
            return sample
        if monotonic() >= deadline:
            raise TimeoutError(f"worker floor warm={warm_workers} was not observed within {timeout_s}s")
        sleep(min(5.0, max(0.0, deadline - monotonic())))


def observe_idle_floor(cluster: Cluster, warm_workers: int, checkpoint: Callable[[dict], None], *,
                       duration_s: float = IDLE_OBSERVE_S, sleep: Callable[[float], None] = time.sleep,
                       monotonic: Callable[[], float] = time.monotonic) -> None:
    """Retain at least 30 seconds of verified idle-floor observations without changing policy."""
    deadline = monotonic() + duration_s
    while True:
        sample = observe(cluster)
        state = floor_state(sample, warm_workers)
        checkpoint({**sample, "floor": {"warm_workers": warm_workers, "state": state}})
        if state != "ready":
            raise RuntimeError("worker floor changed during required idle observation")
        if monotonic() >= deadline:
            return
        sleep(min(5.0, max(0.0, deadline - monotonic())))


def _write_jsonl(path: Path, value: dict) -> None:
    line = json.dumps(value, sort_keys=True, allow_nan=False) + "\n"
    with path.open("a", encoding="utf-8") as output:
        output.write(line)
        output.flush()
        os.fsync(output.fileno())


def _driver(cluster: Cluster, mode: str, payload: dict, timeout_s: int = 120) -> dict:
    return cluster.exec_python("bench-driver", DRIVER, [mode, json.dumps({**payload, "loadgen_source": LOADGEN.read_text(encoding="utf-8")})], timeout_s=timeout_s)


def _assert_policy(cluster: Cluster, requested: dict[str, int]) -> dict[str, int]:
    observed = policy_controls(_driver(cluster, "policy", {}))
    if observed != requested:
        raise RuntimeError(f"effective policy differs from requested controls: {observed}")
    return observed


def _during(cluster: Cluster, phase: str, checkpoint: Callable[[dict, str], None], action: Callable[[], dict],
            on_result: Callable[[dict], None] | None = None) -> dict:
    """Sample a phase while its requests are in flight; a failed probe invalidates it."""
    stopped = threading.Event()
    probe_error: list[BaseException] = []

    def sample() -> None:
        try:
            while not stopped.is_set():
                checkpoint(observe(cluster), phase)
                stopped.wait(5.0)
        except BaseException as error:
            probe_error.append(error)
            checkpoint({"probe_failure": {"error_class": type(error).__name__}}, phase)
            stopped.set()

    thread = threading.Thread(target=sample, daemon=True)
    thread.start()
    try:
        result = action()
        if on_result:
            on_result(result)
        checkpoint({"action_result": result}, f"{phase}_result")
    finally:
        stopped.set()
        thread.join()
    if probe_error:
        raise RuntimeError("elasticity phase probe failed") from probe_error[0]
    return result


def _phase(bounds: dict[str, dict[str, float]], name: str, action: Callable[[], object]) -> object:
    """Time a phase in host epoch seconds so HostSampler intervals can be attributed conservatively."""
    started = time.time()
    try:
        return action()
    finally:
        bounds[name] = {"started_epoch_s": started, "finished_epoch_s": time.time()}


def _resource_evidence(host: search.HostSampler, bounds: dict[str, dict[str, float]]) -> dict:
    """Keep raw cgroup samples and per-phase lower-bound summaries without a capacity or billing claim."""
    latest = host.snapshots[-1]["pods"] if host.snapshots else {}
    infra = {name for name in latest if name.split("/", 1)[-1].split("@", 1)[0].startswith(search.INFRA_LABELS)}
    phases = {}
    for name, bound in bounds.items():
        started, finished = bound["started_epoch_s"], bound["finished_epoch_s"]
        if finished <= started:
            continue
        intervals = host.intervals(started, finished)
        phases[name] = {
            "bounds": bound,
            "intervals": intervals,
            "validity": (hostmetrics.assess(intervals, infra) if intervals else {
                "status": "unmeasured", "reason": "no host interval lies wholly inside this phase",
                "invalid": [], "warnings": [],
            }),
            "resource_usage": resource_summary.summarize(intervals, started, (0.0, finished - started)),
        }
    full_bounds = (min((bound["started_epoch_s"] for bound in bounds.values()), default=None),
                   max((bound["finished_epoch_s"] for bound in bounds.values()), default=None))
    full_intervals = host.intervals(*full_bounds) if all(value is not None for value in full_bounds) else []
    return {
        "snapshots": host.snapshots,
        "phases": phases,
        "validity": hostmetrics.assess(full_intervals, infra),
        "limits": "sampled Kind cgroups are lower-bound observations only; pod discovery refreshes every 30 seconds, so first short-lived pods can be absent. These samples do not establish complete cost or peak usage.",
    }


def run_arm(warm_workers: int, max_workers: int, api_image: str, out: Path, *, cluster: Cluster | None = None) -> Path:
    """Run exactly one requested arm and retain phase checkpoints before each next action."""
    if out.exists():
        raise ValueError(f"elasticity output must be fresh: {out}")
    profile = build_profile(warm_workers, max_workers)
    requested = {name: profile["worker_policy"][name] for name in POLICY_FIELDS}
    selected = cluster or Cluster()
    selected.verify()
    current = run.Run(selected, "worker-elasticity", f"warm{warm_workers}-max{max_workers}", profile, "fake", out, api_image)
    out.mkdir(parents=True)
    checkpoints = out / "timeline.jsonl"
    current.result["timeline"] = {
        "path": str(checkpoints),
        "sampling": "allowlisted sequential DB/pod/quota samples across floor waits and request phases",
    }

    def checkpoint(sample: dict, phase: str = "wait") -> None:
        _write_jsonl(checkpoints, {"phase": phase, **sample})

    def record_result(name: str) -> Callable[[dict], None]:
        def record(value: dict) -> None:
            current.result[name] = value
            current.save()
        return record

    failure: BaseException | None = None
    host: search.HostSampler | None = None
    phase_bounds: dict[str, dict[str, float]] = {}
    try:
        deploy.reset(selected)
        run._prepare(selected, current, profile, "fake", api_image)
        disk = profile.get("capacity", {}).get("disk")
        if not isinstance(disk, dict):
            raise RuntimeError("elasticity profile omitted HostSampler disk identity")
        host = search.HostSampler(selected, disk)
        with host:
            current.result["policy_before"] = _assert_policy(selected, requested)
            _phase(phase_bounds, "wait_before_first", lambda: checkpoint(
                wait_for_floor(selected, warm_workers, checkpoint=checkpoint), "floor_before_first",
            ))
            _phase(phase_bounds, "idle_before_first", lambda: observe_idle_floor(
                selected, warm_workers, lambda sample: checkpoint(sample, "idle_before_first"),
            ))
            first = _phase(phase_bounds, "first_arrival", lambda: _during(
                selected, "first_arrival", checkpoint, lambda: _driver(selected, "measure", {"count": 1}, timeout_s=120),
                record_result("first_arrival"),
            ))
            checkpoint(observe(selected), "after_first")
            _phase(phase_bounds, "cooldown_after_first", lambda: checkpoint(
                wait_for_floor(selected, warm_workers, checkpoint=checkpoint), "floor_before_burst",
            ))
            burst = _phase(phase_bounds, "burst", lambda: _during(
                selected, "burst", checkpoint, lambda: _driver(selected, "measure", {"count": 32}, timeout_s=180),
                record_result("burst"),
            ))
            checkpoint(observe(selected), "after_burst")
            _phase(phase_bounds, "cooldown_after_burst", lambda: checkpoint(
                wait_for_floor(selected, warm_workers, checkpoint=checkpoint), "idle_floor",
            ))
            _phase(phase_bounds, "idle_after_burst", lambda: observe_idle_floor(
                selected, warm_workers, lambda sample: checkpoint(sample, "idle_after_burst"),
            ))
            current.result["policy_after"] = _assert_policy(selected, requested)
            current.result["status"] = (
                "observed_failure" if first["verdict"] != "observed" or burst["verdict"] != "observed" else "observed"
            )
    except BaseException as error:
        failure = error
        current.result.update({"status": "invalid", "error_class": type(error).__name__})
    finally:
        if host is not None:
            try:
                current.result["host_sampling"] = _resource_evidence(host, phase_bounds)
                if current.result["host_sampling"]["validity"]["invalid"] and failure is None:
                    failure = RuntimeError("host validity gate failed")
            except BaseException as evidence_error:
                current.result["host_sampling"] = {
                    "snapshots": host.snapshots,
                    "resource_evidence_error_class": type(evidence_error).__name__,
                }
                if failure is None:
                    failure = evidence_error
        try:
            deploy.reset(selected)
            current.result["cleanup"] = {"verified": True}
        except BaseException as cleanup_error:
            current.result["cleanup"] = {"verified": False, "error_class": type(cleanup_error).__name__}
            if failure is None:
                failure = cleanup_error
        if failure is not None:
            current.result.update({"status": "invalid", "error_class": type(failure).__name__})
        current.result["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        current.save()
    if failure is not None:
        raise failure
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warm-workers", required=True, type=int, choices=(0, 1))
    parser.add_argument("--max-workers", required=True, type=int, choices=(1, 2, 4))
    parser.add_argument("--api-image", required=True)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        print(run_arm(args.warm_workers, args.max_workers, args.api_image, args.out))


if __name__ == "__main__":
    main()
