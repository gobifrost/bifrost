#!/usr/bin/env python3
"""#769 pressure test: memory-admitted executors under sustained mixed load.

Usage: ./test.sh kubernetes up
       ./test.sh kubernetes pressure-test [--out DIR] [--ramp-seconds 480]

Phases (recorded in report.json), against the base manifests' defaults:

- floor: warm-floor sync latency (p50/p95/p99) before any load.
- learning: a workflow's learned request follows its runs' peak growth.
- sized: work a standard worker cannot hold gets a Job sized for it.
- too_large: work no worker can hold fails visibly instead of waiting.
- understated: a burst of runs that use far more than their request. The
  gate may admit too much before it sees them (a never-measured burst can
  cost one learning kill); every run ends, any kill is visible, the pool
  keeps serving, the workflow learns its size, and the same burst again all
  succeeds.
- ramp: sustained mixed load (async, sync, scheduled, heavy, agent runs)
  with an API pod and a busy worker pod killed part-way; the Job count
  curve is sampled throughout.
- drain: the pool returns to the warm minimum.

Every accepted run must reach a terminal state. Failures are allowed only
where the phase expects them (the killed worker's runs, the first
understated burst's runs stopped or lost for memory, agent runs without a
configured model).
"""
from __future__ import annotations

import argparse
import json
import random
import subprocess
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from pool_spike import pool_pods, ready_api_pod, sql, wait_floor
from spike import LOCAL, NS, ROOT, kubectl

DRIVER = (ROOT / "scripts/kubernetes/pressure_driver.py").read_text()
MIB = 2**20
TERMINAL = {"Success", "Failed", "Cancelled", "Timeout", "CompletedWithErrors", "Stuck"}
EXECUTOR_LOST = "The worker running this stopped before it finished"
# How a run stopped for memory ends: by the executor's dangerous-memory stop,
# by the kernel, or lost with its executor.
MEMORY_STOPS = ("Stopped after using", "Stopped by the system", EXECUTOR_LOST)


def driver(mode: str, arg: str = "") -> dict:
    """Run one pressure_driver mode in a Ready API pod."""
    api = ready_api_pod()
    result = subprocess.run(
        [str(LOCAL), "kubectl", "--", "-n", NS, "exec", "-i", api, "--",
         "sh", "-c", f"cd /app && python - {mode} '{arg}'"],
        input=DRIVER, text=True, capture_output=True, check=True, timeout=900,
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


def statuses(ids: list[str]) -> dict[str, tuple[str, str]]:
    if not ids:
        return {}
    quoted = ",".join(f"'{i}'" for i in ids)
    rows = sql(
        "SELECT id, status, left(coalesce(error_message, ''), 120) FROM executions "
        f"WHERE id IN ({quoted})"
    )
    return {r[0]: (r[1], r[2] if len(r) > 2 else "") for r in rows}


def agent_statuses(ids: list[str]) -> dict[str, tuple[str, str]]:
    if not ids:
        return {}
    quoted = ",".join(f"'{i}'" for i in ids)
    rows = sql(
        f"SELECT id, status, left(coalesce(error, ''), 120) FROM agent_runs WHERE id IN ({quoted})"
    )
    return {r[0]: (r[1], r[2] if len(r) > 2 else "") for r in rows}


def wait_terminal(ids: list[str], timeout: float) -> dict[str, tuple[str, str]]:
    deadline = time.monotonic() + timeout
    while True:
        current = statuses(ids)
        if len(current) == len(ids) and all(s in TERMINAL for s, _ in current.values()):
            return current
        if time.monotonic() >= deadline:
            waiting = {k: v for k, v in current.items() if v[0] not in TERMINAL}
            missing = len(ids) - len(current)
            raise TimeoutError(f"{len(waiting)} runs not terminal, {missing} missing: {list(waiting.items())[:5]}")
        time.sleep(3)


def wait_agents_terminal(ids: list[str], timeout: float) -> dict[str, tuple[str, str]]:
    deadline = time.monotonic() + timeout
    while True:
        current = agent_statuses(ids)
        if len(current) == len(ids) and all(
            s in ("completed", "failed", "cancelled") for s, _ in current.values()
        ):
            return current
        if time.monotonic() >= deadline:
            raise TimeoutError(f"agent runs not terminal: {current}")
        time.sleep(3)


def learned_mib(workflow_id: str) -> float | None:
    value = sql(f"SELECT learned_memory_bytes FROM workflows WHERE id = '{workflow_id}'")[0][0]
    return None if value == "" else round(int(value) / MIB, 1)


def seed_learned(workflow_id: str, mib: int) -> None:
    sql(f"UPDATE workflows SET learned_memory_bytes = {mib * MIB} WHERE id = '{workflow_id}'")


def standard_budget_mib() -> int:
    rows = sql(
        "SELECT max(memory_budget_bytes) FROM execution_workers WHERE form = 'job' "
        "AND memory_limit_bytes IS NULL AND state = 'ready'"
    )
    return int(rows[0][0]) // MIB


def tally(results: dict[str, tuple[str, str]]) -> dict:
    return {
        "statuses": dict(Counter(status for status, _ in results.values())),
        "errors": dict(Counter(error for status, error in results.values() if status != "Success")),
    }


class Curve:
    """Samples the worker pool every few seconds in the background."""

    def __init__(self) -> None:
        self.samples: list[dict] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        started = time.monotonic()
        while not self._stop.is_set():
            try:
                rows = sql(
                    "SELECT form, resource_class, state, count(*), "
                    "coalesce(sum(memory_budget_bytes), 0), coalesce(sum(memory_reserved_bytes), 0), "
                    "count(*) FILTER (WHERE memory_pressured) "
                    "FROM execution_workers WHERE state IN ('reserved','starting','ready','draining') "
                    "GROUP BY 1, 2, 3"
                )
                waiting = int(sql("SELECT count(*) FROM work_dispatches WHERE assigned_worker_id IS NULL")[0][0])
                self.samples.append({
                    "t": round(time.monotonic() - started, 1),
                    "waiting": waiting,
                    "pods": len(pool_pods()),
                    "workers": [
                        {"form": r[0], "class": r[1], "state": r[2], "n": int(r[3]),
                         "budget_mib": int(r[4]) // MIB, "reserved_mib": int(r[5]) // MIB,
                         "pressured": int(r[6])}
                        for r in rows
                    ],
                })
            except Exception as exc:  # one missed sample is not a failure
                self.samples.append({"t": round(time.monotonic() - started, 1), "error": str(exc)[:200]})
            self._stop.wait(3)

    def __enter__(self) -> "Curve":
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()
        self._thread.join(timeout=30)

    def pressured_samples(self) -> int:
        return sum(
            1 for s in self.samples if any(w["pressured"] for w in s.get("workers", []))
        )

    def peak_jobs(self) -> int:
        return max(
            (sum(w["n"] for w in s.get("workers", []) if w["form"] == "job") for s in self.samples),
            default=0,
        )


def kill_pod(selector: str, *, busy: bool = False) -> str | None:
    pods = json.loads(kubectl("get", "pods", "-l", selector, "-o", "json"))["items"]
    pods = [p for p in pods if p["status"].get("phase") == "Running" and not p["metadata"].get("deletionTimestamp")]
    if busy:
        busy_uids = {
            r[0] for r in sql(
                "SELECT kubernetes_pod_uid FROM execution_workers WHERE form = 'job' AND state = 'ready' "
                "AND active_runs > 0 AND kubernetes_pod_uid IS NOT NULL"
            )
        }
        pods = [p for p in pods if p["metadata"]["uid"] in busy_uids]
    if not pods:
        return None
    name = pods[0]["metadata"]["name"]
    kubectl("delete", "pod", name, "--wait=false", "--grace-period=0", "--force")
    return name


def run(out: Path, ramp_seconds: int) -> dict:
    out.mkdir(parents=True, exist_ok=False)
    report: dict = {"started_at": datetime.now(timezone.utc).isoformat()}

    report["floor_seconds"] = wait_floor(300)
    setup = driver("setup")
    workflows = setup["workflows"]
    report["floor"] = driver("latency", "200")

    # learning: the learned request follows peak growth.
    light = driver("submit", json.dumps([
        {"workflow": workflows["pressure_light"], "params": {"seconds": 0.2, "mb": 5}, "count": 4, "mode": "async"},
    ]))
    heavy = driver("submit", json.dumps([
        {"workflow": workflows["pressure_heavy"], "params": {"seconds": 1, "mb": 300}, "count": 4, "mode": "async"},
    ]))
    learning_runs = wait_terminal(light["ids"] + heavy["ids"], 600)
    report["learning"] = {
        "runs": tally(learning_runs),
        "light_learned_mib": learned_mib(workflows["pressure_light"]),
        "heavy_learned_mib": learned_mib(workflows["pressure_heavy"]),
    }
    assert all(s == "Success" for s, _ in learning_runs.values()), learning_runs
    assert report["learning"]["light_learned_mib"] < 64, report["learning"]
    assert 300 <= report["learning"]["heavy_learned_mib"] < 400, report["learning"]

    # sized: a request larger than a standard worker's budget.
    budget = standard_budget_mib()
    huge_mib = budget + 300
    seed_learned(workflows["pressure_huge"], huge_mib)
    with Curve() as sized_curve:
        huge = driver("submit", json.dumps([
            {"workflow": workflows["pressure_huge"], "params": {"seconds": 5, "mb": huge_mib - 50}, "count": 1, "mode": "async"},
        ]))
        sized_runs = wait_terminal(huge["ids"], 900)
    sized_workers = sql("SELECT count(*), max(memory_limit_bytes) FROM execution_workers WHERE resource_class = 'sized'")
    report["sized"] = {
        "standard_budget_mib": budget,
        "request_mib": huge_mib + 16,
        "runs": tally(sized_runs),
        "sized_workers": int(sized_workers[0][0]),
        "sized_limit_mib": int(sized_workers[0][1] or 0) // MIB,
        "curve": sized_curve.samples,
    }
    assert all(s == "Success" for s, _ in sized_runs.values()), sized_runs
    assert report["sized"]["sized_workers"] >= 1, report["sized"]

    # too_large: beyond the largest worker the controller may launch.
    seed_learned(workflows["pressure_huge"], 64 * 1024)
    too_large = driver("submit", json.dumps([
        {"workflow": workflows["pressure_huge"], "params": {"seconds": 1, "mb": 1}, "count": 1, "mode": "async"},
    ]))
    too_large_runs = wait_terminal(too_large["ids"], 600)
    report["too_large"] = tally(too_large_runs)
    [(status, error)] = too_large_runs.values()
    assert status == "Failed" and "MiB of memory" in error, too_large_runs

    # understated: requests ten times too small; recovery, then the same burst.
    seed_learned(workflows["pressure_spike"], 16)
    burst = [
        {"workflow": workflows["pressure_spike"], "params": {"seconds": 10, "mb": 200}, "count": 12, "mode": "async"},
    ]
    with Curve() as spike_curve:
        spike_runs = wait_terminal(driver("submit", json.dumps(burst))["ids"], 900)
    after_spike = driver("submit", json.dumps([
        {"workflow": workflows["pressure_light"], "params": {"seconds": 0.1, "mb": 5}, "count": 10, "mode": "async"},
    ]))
    after_spike_runs = wait_terminal(after_spike["ids"], 600)
    learned_after_spike = learned_mib(workflows["pressure_spike"])
    with Curve() as again_curve:
        again_runs = wait_terminal(driver("submit", json.dumps(burst))["ids"], 900)
    report["understated"] = {
        "runs": tally(spike_runs),
        "after": tally(after_spike_runs),
        "learned_mib": learned_after_spike,
        "again": tally(again_runs),
        "pressured_samples": spike_curve.pressured_samples(),
        "curve": spike_curve.samples,
        "again_curve": again_curve.samples,
    }
    unexplained = {
        k: v for k, v in spike_runs.items()
        if v[0] != "Success" and not v[1].startswith(MEMORY_STOPS)
    }
    assert not unexplained, unexplained
    assert all(s == "Success" for s, _ in after_spike_runs.values()), after_spike_runs
    assert learned_after_spike is not None and learned_after_spike >= 190, report["understated"]["learned_mib"]
    assert all(s == "Success" for s, _ in again_runs.values()), again_runs

    # ramp: sustained mixed load with kills.
    ids: list[str] = []
    sync_ms: list[float] = []
    agent_ids: list[str] = []
    errors: list[str] = []
    kills: dict[str, str | None] = {}
    rng = random.Random(769)
    with Curve() as ramp_curve:
        started = time.monotonic()
        tick = 0
        while time.monotonic() - started < ramp_seconds:
            elapsed = time.monotonic() - started
            plan = [
                {"workflow": workflows["pressure_light"],
                 "params": {"seconds": round(rng.uniform(0.5, 5), 1), "mb": rng.choice([2, 5, 20])},
                 "count": 6, "mode": "async"},
                {"workflow": workflows["pressure_light"], "params": {"seconds": 0, "mb": 1}, "count": 3, "mode": "sync"},
            ]
            if tick % 3 == 0:
                plan.append({"workflow": workflows["pressure_heavy"], "params": {"seconds": 3, "mb": 300}, "count": 1, "mode": "async"})
            if tick % 4 == 0:
                plan.append({"workflow": workflows["pressure_light"], "params": {"seconds": 1, "mb": 5},
                             "count": 1, "mode": "scheduled", "delay": rng.randint(20, 60)})
            submitted = driver("submit", json.dumps(plan))
            ids += submitted["ids"]
            sync_ms += submitted["sync_ms"]
            errors += submitted["errors"]
            if tick % 6 == 0:
                agent_ids += driver("agents", "1")["ids"]
            if "api" not in kills and elapsed >= ramp_seconds * 0.3:
                kills["api"] = kill_pod("app.kubernetes.io/name=bifrost-api")
            if "worker" not in kills and elapsed >= ramp_seconds * 0.6:
                kills["worker"] = kill_pod("app.kubernetes.io/name=bifrost-pool-worker", busy=True)
            tick += 1
            time.sleep(max(0.0, started + tick * 5 - time.monotonic()))
        ramp_runs = wait_terminal(ids, 1200)
        ramp_agents = wait_agents_terminal(agent_ids, 900)
    sync_ms.sort()
    unexpected = {
        k: v for k, v in ramp_runs.items() if v[0] != "Success" and not v[1].startswith(EXECUTOR_LOST)
    }
    report["ramp"] = {
        "seconds": ramp_seconds,
        "runs": len(ramp_runs),
        "tally": tally(ramp_runs),
        "submit_errors": errors[:20],
        "submit_error_count": len(errors),
        "sync_under_load": {
            "n": len(sync_ms),
            "p50_ms": sync_ms[len(sync_ms) // 2] if sync_ms else None,
            "p95_ms": sync_ms[int(0.95 * (len(sync_ms) - 1))] if sync_ms else None,
            "max_ms": sync_ms[-1] if sync_ms else None,
        },
        "agents": tally(ramp_agents),
        "kills": kills,
        "peak_jobs": ramp_curve.peak_jobs(),
        "unexpected_failures": list(unexpected.items())[:20],
        "curve": ramp_curve.samples,
    }
    report["drain"] = {"seconds_to_floor": wait_floor(900)}
    report["drain"]["executors"] = sql(
        "SELECT form, resource_class, state, coalesce(end_reason,''), count(*) FROM execution_workers "
        "GROUP BY 1, 2, 3, 4 ORDER BY 1, 2, 3"
    )
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    (out / "report.json").write_text(json.dumps(report, indent=2))

    assert not errors, errors[:5]
    assert not unexpected, list(unexpected.items())[:5]
    assert report["ramp"]["peak_jobs"] <= 6, report["ramp"]["peak_jobs"]
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path(f"/tmp/bifrost-pressure-test-{int(time.time())}"))
    parser.add_argument("--ramp-seconds", type=int, default=480)
    args = parser.parse_args()
    report = run(args.out, args.ramp_seconds)
    print(json.dumps({k: v for k, v in report.items() if k not in ("ramp", "sized", "understated")}, indent=2))
    print(json.dumps(
        {k: v for k, v in report["understated"].items() if k not in ("curve", "again_curve")}, indent=2
    ))
    print(json.dumps({k: v for k, v in report["ramp"].items() if k != "curve"}, indent=2))


if __name__ == "__main__":
    main()
