#!/usr/bin/env python3
"""Prove API-hosted platform control in local Kind.

Usage: ./test.sh kubernetes up
       ./test.sh kubernetes control-experiment [--out DIR]

Phases, each recorded under the output directory:

- election: exactly one API replica owns the trigger lease; the scheduler
  never does.
- steady: every scheduled run in the window was started by the lease owner,
  and no ``schedule_processor`` minute ran twice.
- latency: /health/live p50/p95/p99 on the leader pod versus a follower pod
  while the leader runs its startup burst after failover.
- crash: SIGKILL the leader's API process from the Kind node, so it cannot
  release its lease. A replica takes over only after database expiry; the dead
  owner starts nothing after the successor.
- rollout: ``kubectl rollout restart`` the API. Leadership moves without
  interleaved owners and every schedule minute still runs at most once.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import subprocess
import time

from spike import NS, kubectl

API = "app.kubernetes.io/name=bifrost-api"
SCHEDULER = "app.kubernetes.io/name=bifrost-scheduler"
LEASE_SECONDS = 30
PROBE = """import json, sys, time, urllib.request
samples = []
deadline = time.monotonic() + float(sys.argv[2])
while time.monotonic() < deadline:
    started = time.perf_counter()
    urllib.request.urlopen(sys.argv[1], timeout=5).read()
    samples.append((time.perf_counter() - started) * 1000)
    time.sleep(0.05)
print(json.dumps(samples))
"""


def sql(query: str) -> list[list[str]]:
    output = kubectl(
        "exec", "statefulset/postgres", "--",
        "psql", "-U", "bifrost", "-d", "bifrost", "-At", "-F", "\t", "-c", query,
    )
    return [line.split("\t") for line in output.splitlines() if line]


def pods(selector: str) -> list[dict]:
    items = json.loads(kubectl("get", "pods", "-l", selector, "-o", "json"))["items"]
    return [
        pod for pod in items
        if not pod["metadata"].get("deletionTimestamp")
        and pod.get("status", {}).get("phase") == "Running"
    ]


def lease() -> tuple[str | None, bool]:
    rows = sql(
        "SELECT coalesce(owner_id, ''), coalesce(lease_expires_at > now(), false) "
        "FROM scheduler_leases WHERE name = 'scheduler-triggers'"
    )
    if not rows or not rows[0][0]:
        return None, False
    return rows[0][0], rows[0][1] == "t"


def owner_pod(owner: str | None) -> str | None:
    return owner.split(":", 1)[0] if owner else None


def wait_for(predicate, timeout: float, what: str):  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.5)
    raise TimeoutError(what)


def runs_since(since: str) -> list[dict]:
    rows = sql(
        "SELECT task_id, leader_owner_id, status, started_at, coalesce(completed_at::text, '') "
        f"FROM scheduler_task_runs WHERE started_at >= '{since}' ORDER BY started_at"
    )
    return [
        {"task_id": r[0], "owner": r[1], "status": r[2], "started_at": r[3], "completed_at": r[4]}
        for r in rows
    ]


def duplicate_processor_minutes(runs: list[dict]) -> list[str]:
    minutes = Counter(
        run["started_at"][:16] for run in runs if run["task_id"] == "schedule_processor"
    )
    return [minute for minute, count in minutes.items() if count > 1]


def interleaved(runs: list[dict]) -> list[dict]:
    """Runs started by an owner after a different owner had already taken over."""
    retired: set[str] = set()
    current = None
    violations = []
    for run in runs:
        if run["owner"] in retired:
            violations.append(run)
        elif run["owner"] != current:
            if current is not None:
                retired.add(current)
            current = run["owner"]
    return violations


def now_sql() -> str:
    return sql("SELECT now()")[0][0]


def sample_pair(leader: dict, follower: dict, seconds: float) -> dict:
    """Leader and follower sampled concurrently so both see the same load."""
    sampler = pods(SCHEDULER)[0]["metadata"]["name"]
    procs = {}
    for role, pod in (("leader", leader), ("follower", follower)):
        url = f"http://{pod['status']['podIP']}:8000/health/live"
        procs[role] = subprocess.Popen(
            [str(Path(__file__).with_name("local-kind.sh")), "kubectl", "--", "-n", NS,
             "exec", sampler, "--", "python", "-c", PROBE, url, str(seconds)],
            stdout=subprocess.PIPE, text=True,
        )
    result = {}
    for role, proc in procs.items():
        out, _ = proc.communicate(timeout=seconds + 60)
        samples = sorted(json.loads(out))
        result[role] = {
            "pod": (leader if role == "leader" else follower)["metadata"]["name"],
            "samples": len(samples),
            "p50_ms": round(statistics.median(samples), 2),
            "p95_ms": round(samples[int(0.95 * (len(samples) - 1))], 2),
            "p99_ms": round(samples[int(0.99 * (len(samples) - 1))], 2),
            "max_ms": round(samples[-1], 2),
        }
    return result


def hard_kill(pod: dict) -> None:
    """SIGKILL the pod's API process on its node: no shutdown hooks run."""
    container_id = pod["status"]["containerStatuses"][0]["containerID"].split("://", 1)[1]
    node = f"{pod['spec']['nodeName']}"
    inspect = json.loads(subprocess.run(
        ["docker", "exec", node, "crictl", "inspect", container_id],
        check=True, capture_output=True, text=True,
    ).stdout)
    subprocess.run(
        ["docker", "exec", node, "kill", "-9", str(inspect["info"]["pid"])],
        check=True,
    )


def run(out: Path, steady_seconds: int) -> dict:
    out.mkdir(parents=True, exist_ok=False)
    report: dict = {"started_at": datetime.now(timezone.utc).isoformat()}

    api_pods = wait_for(lambda: (p := pods(API)) and len(p) >= 2 and p, 300, "two API pods")
    scheduler_pods = pods(SCHEDULER)

    # election
    owner = wait_for(lambda: (o := lease())[1] and o[0], 120, "a trigger leader")
    api_names = {pod["metadata"]["name"] for pod in api_pods}
    assert owner_pod(owner) in api_names, f"leader {owner} is not an API pod"
    assert owner_pod(owner) not in {pod["metadata"]["name"] for pod in scheduler_pods}
    report["election"] = {"leader": owner, "api_pods": sorted(api_names)}

    # steady
    since = now_sql()
    time.sleep(steady_seconds)
    steady = runs_since(since)
    report["steady"] = {
        "runs": len(steady),
        "owners": sorted({run["owner"] for run in steady}),
        "duplicate_processor_minutes": duplicate_processor_minutes(steady),
    }
    assert report["steady"]["owners"] == [owner], report["steady"]
    assert not report["steady"]["duplicate_processor_minutes"]

    # crash
    leader_pod = next(pod for pod in api_pods if pod["metadata"]["name"] == owner_pod(owner))
    crash_since = now_sql()
    # The restarted container reuses the pod hostname and PID 1, so a new
    # generation is identified by the lease token, not the owner string.
    old_token = sql("SELECT lease_token FROM scheduler_leases WHERE name = 'scheduler-triggers'")[0][0]
    crashed_at = time.monotonic()
    hard_kill(leader_pod)
    successor = wait_for(
        lambda: (o := lease())[1] and sql(
            "SELECT lease_token FROM scheduler_leases WHERE name = 'scheduler-triggers'"
        )[0][0] != old_token and o[0],
        LEASE_SECONDS + 60,
        "a successor leader",
    )
    handoff = round(time.monotonic() - crashed_at, 1)
    assert handoff >= LEASE_SECONDS - 12, f"crashed leader released early ({handoff}s)"
    successor_pod = wait_for(
        lambda: [pod for pod in pods(API) if pod["metadata"]["name"] == owner_pod(successor)],
        120,
        "the successor pod",
    )[0]

    def ready_follower() -> list[dict]:
        return [
            pod for pod in pods(API)
            if pod["metadata"]["name"] != owner_pod(successor)
            and any(c["type"] == "Ready" and c["status"] == "True"
                    for c in pod["status"].get("conditions", []))
        ]

    follower_pod = wait_for(ready_follower, 180, "a ready follower")[0]
    # The successor runs its startup burst now; sample both pods during it.
    report["latency_during_leader_startup"] = sample_pair(successor_pod, follower_pod, 30)
    time.sleep(max(0, 75 - (time.monotonic() - crashed_at)))
    crash_runs = runs_since(crash_since)
    report["crash"] = {
        "old_leader": owner,
        "successor": successor,
        "handoff_seconds": handoff,
        "runs": len(crash_runs),
        "interleaved_runs": interleaved(crash_runs),
        "duplicate_processor_minutes": duplicate_processor_minutes(crash_runs),
    }
    assert not report["crash"]["interleaved_runs"], report["crash"]
    assert not report["crash"]["duplicate_processor_minutes"], report["crash"]

    # rollout
    rollout_since = now_sql()
    started = time.monotonic()
    owners_seen: list[tuple[float, str | None]] = []
    kubectl("rollout", "restart", "deployment/bifrost-api")
    proc = subprocess.Popen(
        [str(Path(__file__).with_name("local-kind.sh")), "kubectl", "--", "-n", NS,
         "rollout", "status", "deployment/bifrost-api", "--timeout=600s"],
    )
    while proc.poll() is None:
        current, live = lease()
        owners_seen.append((round(time.monotonic() - started, 1), current if live else None))
        time.sleep(1)
    assert proc.returncode == 0, "API rollout did not complete"
    final = wait_for(lambda: (o := lease())[1] and o[0], LEASE_SECONDS + 60, "a leader after rollout")
    time.sleep(70)
    rollout_runs = runs_since(rollout_since)
    gaps = []
    gap_start = None
    for at, current in owners_seen:
        if current is None and gap_start is None:
            gap_start = at
        elif current is not None and gap_start is not None:
            gaps.append(round(at - gap_start, 1))
            gap_start = None
    report["rollout"] = {
        "owner_transitions": list(dict.fromkeys(o for _, o in owners_seen if o)),
        "final_leader": final,
        "leaderless_gaps_seconds": gaps,
        "runs": len(rollout_runs),
        "interleaved_runs": interleaved(rollout_runs),
        "duplicate_processor_minutes": duplicate_processor_minutes(rollout_runs),
        "scheduler_is_never_leader": all(
            owner_pod(o) not in {p["metadata"]["name"] for p in pods(SCHEDULER)}
            for _, o in owners_seen if o
        ),
    }
    assert not report["rollout"]["interleaved_runs"], report["rollout"]
    assert not report["rollout"]["duplicate_processor_minutes"], report["rollout"]
    assert report["rollout"]["scheduler_is_never_leader"]
    assert owner_pod(final) in {p["metadata"]["name"] for p in pods(API)}

    tasks = sql("SELECT task_id, coalesce(next_run_at::text, '') FROM scheduler_task_states ORDER BY task_id")
    report["catalog"] = {task: next_run for task, next_run in tasks}
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    (out / "report.json").write_text(json.dumps(report, indent=2))
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path(f"/tmp/bifrost-control-experiment-{int(time.time())}"))
    parser.add_argument("--steady-seconds", type=int, default=75)
    args = parser.parse_args()
    report = run(args.out, args.steady_seconds)
    print(json.dumps(report, indent=2))
    print(f"Report: {args.out / 'report.json'}")


if __name__ == "__main__":
    main()
