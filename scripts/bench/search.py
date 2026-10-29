"""Capacity search: open-loop steps, reconciliation, gates, and validity.

Implements docs/plans/2026-10-29-capacity-benchmark-methodology.md §5–§6 on top
of ``incluster/loadgen.py`` (traffic and outcomes), ``capacity.py`` (gates),
and ``hostmetrics.py`` (external contention).
"""

from __future__ import annotations

import json
import math
import threading
import time
from pathlib import Path

import capacity
import hostmetrics
from cluster import Cluster

HERE = Path(__file__).resolve().parent
LOADGEN = HERE / "incluster" / "loadgen.py"
HOST_INTERVAL_S = 5.0
# Expected arrivals per gated class: 20 % above the 400 the gates require, so a
# Poisson window rarely falls short of them.
OBSERVATION_TARGET = 480
# Ramp steps: at most this many seconds of offered work may accumulate as
# unfinished across the measured window. The strict per-second fraction is
# only resolvable over confirmation-length windows (methodology §6, R10).
RAMP_DRIFT_SECONDS = 2.0
EXTEND_CAP_S = 1800
CONFIRM_MIN_S = 900
CONFIRM_CAP_S = 3600
INFRA_LABELS = ("bench-driver", "bench-fake-llm", "bench-fake-api", "seaweedfs")


def window_seconds(rate: float, mix: dict[str, float], minimum_s: float, observations: int) -> float:
    """Long enough for ``observations`` arrivals of every class holding at least 10 % of the mix."""
    total = sum(mix.values())
    shares = [weight / total for weight in mix.values() if weight / total >= 0.10]
    return max(minimum_s, observations / (rate * min(shares)))


class HostSampler:
    """Host and cgroup counters every ``HOST_INTERVAL_S`` while a step runs."""

    def __init__(self, cluster: Cluster, disk: dict[str, str]) -> None:
        self.cluster = cluster
        self.disk = disk
        self.nodes = {"bench": hostmetrics.node_cgroup(cluster.identity.node_name)}
        for name in cluster.external_nodes():
            self.nodes[f"external:{name}"] = hostmetrics.node_cgroup(name)
        self.snapshots: list[dict] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def _pods(self) -> dict:
        paths = {}
        for namespace, name, uid in self.cluster.bench_pods():
            path = hostmetrics.pod_cgroup(self.nodes["bench"], uid)
            if path is not None:
                paths[f"{namespace}/{name}"] = path
        return paths

    def _loop(self) -> None:
        pods = self._pods()
        refreshed = time.monotonic()
        while not self._stop.is_set():
            if time.monotonic() - refreshed > 30:
                pods, refreshed = self._pods(), time.monotonic()
            self.snapshots.append(hostmetrics.snapshot(self.nodes, pods, self.disk))
            self._stop.wait(HOST_INTERVAL_S)

    def __enter__(self) -> HostSampler:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join()

    def intervals(self, start: float, end: float) -> list[dict]:
        inside = [snap for snap in self.snapshots if start <= snap["at"] <= end]
        return [hostmetrics.window(a, b) for a, b in zip(inside, inside[1:])]


def to_records(sent: list[dict], outcomes: dict, started: float, deadlines: dict[str, float]) -> list[dict]:
    """Join driver records and durable outcomes into capacity records (seconds from step start)."""
    records = []
    for request in sent:
        found = outcomes["outcomes"].get(request["id"])
        deadline = (request["sent"] or request["scheduled"]) + deadlines[request["class"]]
        if found and found["correct"] and found["completed"] is not None and found["completed"] <= deadline:
            outcome = "correct"
        elif found and found["completed"] is None and request["sent"] is not None:
            outcome = "unfinished"
        else:
            outcome = "failed"
        records.append({
            "id": request["id"],
            "class": request["class"],
            "scheduled_s": request["scheduled"] - started,
            "sent_s": None if request["sent"] is None else request["sent"] - started,
            "admission_s": None if request["responded"] is None or request["sent"] is None else request["responded"] - request["sent"],
            "completed_s": found["completed"] - started if outcome == "correct" else None,
            "queue_s": found["started"] - found["created"] if outcome == "correct" and found["started"] else None,
            "outcome": outcome,
            "status": found["status"] if found else None,
            "http_status": request["http_status"],
            "error": (found or {}).get("error") or request["error"],
            "iterations": (found or {}).get("iterations"),
        })
    return records


def run_step(cluster: Cluster, ids: dict, rate: float, plan: dict, seed: int, disk: dict[str, str],
             window_s: float | None = None, confirm: bool = False) -> dict:
    """One open-loop step: warm-up, measured window, drain; returns gates, validity and raw evidence."""
    mix = plan["mix"]
    window_s = window_s or window_seconds(rate, mix, plan["window_min_s"], OBSERVATION_TARGET)
    thresholds = plan["thresholds"]
    drift_limit = thresholds["drift_fraction"] * rate if confirm else rate * RAMP_DRIFT_SECONDS / window_s
    duration = plan["warmup_s"] + window_s
    faults_before = cluster.tenant_faults()
    with HostSampler(cluster, disk) as host:
        sent = cluster.exec_python("bench-driver", LOADGEN, ["step", json.dumps({
            "rate": rate, "duration_s": duration, "mix": mix, "seed": seed, "ids": ids, "io": plan.get("io"),
        })], timeout_s=int(duration + 600))
        time.sleep(plan["drain_s"])
    outcomes = cluster.exec_python("bench-driver", LOADGEN, ["outcomes", json.dumps({
        "requests": [{"id": r["id"], "class": r["class"]} for r in sent["requests"]], "io": plan.get("io"),
    })], timeout_s=600)
    started = sent["started"]
    window = (plan["warmup_s"], plan["warmup_s"] + window_s)
    faults = {key: after - faults_before.get(key, 0) for key, after in cluster.tenant_faults().items()}
    faults["duplicate_executions"] = len(outcomes["duplicates"])
    records = to_records(sent["requests"], outcomes, started, plan["deadlines_s"])
    backlog = [(sample["t"] - started, float(sample["unfinished"])) for sample in sent["backlog"]]
    verdict = capacity.step_verdict(records, backlog, window, rate,
                                    {**thresholds, "drift_limit_per_s": drift_limit, "faults": faults}, seed, mix)
    validity = hostmetrics.assess(host.intervals(started + window[0], started + window[1]),
                                  {name for name in host.snapshots[-1]["pods"] if name.split("/")[1].startswith(INFRA_LABELS)} if host.snapshots else set())
    if sent["driver_lag_p99_s"] is not None and sent["driver_lag_p99_s"] > 0.25:
        validity["invalid"].append(f"driver lag p99 {sent['driver_lag_p99_s']:.3f} s")
    overall = "invalid" if validity["invalid"] else verdict["overall"]
    return {
        "window": window,
        "rate": rate, "seed": seed, "window_s": window_s, "confirm": confirm, "drift_limit_per_s": drift_limit, "overall": overall, "verdict": verdict, "validity": validity,
        "faults": faults, "driver_lag_p99_s": sent["driver_lag_p99_s"], "missing": outcomes["missing"],
        "records": records, "backlog": backlog,
        "host": host.intervals(started, started + duration + plan["drain_s"]),
    }


def search(cluster: Cluster, ids: dict, plan: dict, disk: dict[str, str], on_step) -> dict:
    """Geometric ramp and two bisections.

    An inconclusive step is rerun at the same rate with its window doubled
    (cap ``EXTEND_CAP_S``); an invalid step is rerun once with the same seed;
    a second invalid result stops the search.
    """
    history: list[tuple[float, str]] = []
    steps: list[dict] = []
    rate = plan["start_rate"]
    window_s: float | None = None
    while rate is not None:
        step = run_step(cluster, ids, rate, plan, plan["seed"] + len(history), disk, window_s=window_s)
        steps.append(step)
        on_step(step)
        if step["overall"] == "invalid":
            if any(previous["rate"] == rate and previous["overall"] == "invalid" for previous in steps[:-1]):
                return {"steps": steps, "bracket": None, "stopped": f"step at {rate}/s invalid twice"}
            window_s = step["window_s"]
            continue
        if step["overall"] == "inconclusive" and step["window_s"] * 2 <= EXTEND_CAP_S:
            window_s = step["window_s"] * 2
            continue
        window_s = None
        history.append((rate, step["overall"]))
        rate = capacity.search_next(history, plan.get("factor", 1.25), plan.get("bisections", 2))
    passed = [r for r, status in history if status == "pass"]
    failed = [r for r, status in history if status != "pass" and r > max(passed, default=0)]
    return {"steps": steps, "bracket": [max(passed, default=None), min(failed, default=None)], "stopped": None}


CONFIRM_FAILURE_UPPER = 0.001
# Size confirmation windows for 25 % more arrivals than the bound needs, so a
# Poisson window rarely falls short.
CONFIRM_HEADROOM = 1.25


def confirmation_seconds(rate: float, mix: dict[str, float], observations: int) -> tuple[float, bool]:
    """Window giving every gated class ``observations`` (with headroom), capped; (seconds, whether capped)."""
    needed = window_seconds(rate, mix, CONFIRM_MIN_S, math.ceil(observations * CONFIRM_HEADROOM))
    return min(needed, CONFIRM_CAP_S), needed > CONFIRM_CAP_S


def confirm(cluster: Cluster, ids: dict, rate: float, plan: dict, disk: dict[str, str]) -> dict:
    """Confirmation at ``rate``: strict drift tolerance and a 0.1 % per-class failure bound.

    Runs until the evidence settles: any other gate failing, or a class whose
    observed failure rate exceeds the bound, fails; every class proving the
    bound passes; otherwise the window is lengthened to the observations the
    observed failures require and run again, until the 60 min cap, after which
    the result is inconclusive, never certified.
    """
    observations = capacity.observations_needed(CONFIRM_FAILURE_UPPER)
    segments: list[dict] = []
    while True:
        window_s, capped = confirmation_seconds(rate, plan["mix"], observations)
        loose = {**plan, "thresholds": {**plan["thresholds"], "failure_upper": 1.0}}
        step = run_step(cluster, ids, rate, loose, plan["seed"] + 1000 + len(segments), disk, window_s=window_s, confirm=True)
        segments.append(step)
        verdict = step["verdict"]
        if step["overall"] in ("fail", "invalid"):
            outcome = step["overall"]
            break
        evidence = {}
        for name, gate in verdict["reliability"].items():
            records = [r for r in step["records"] if r["class"] == name and step["window"][0] <= r["scheduled_s"] < step["window"][1]]
            failures = sum(1 for r in records if r["outcome"] != "correct")
            evidence[name] = capacity.reliability_evidence(failures, len(records), CONFIRM_FAILURE_UPPER)
        step["reliability_evidence"] = evidence
        if any(status == "fail" for status, _ in evidence.values()):
            outcome = "fail"
            break
        if step["overall"] == "pass" and all(status == "pass" for status, _ in evidence.values()):
            outcome = "pass"
            break
        needed = max((n for status, n in evidence.values() if status == "insufficient"), default=observations)
        if capped or confirmation_seconds(rate, plan["mix"], needed)[1] or needed <= observations:
            outcome = "inconclusive"
            break
        observations = needed
    final = dict(segments[-1])
    final.update({"overall": outcome, "segments": len(segments), "capped": capped})
    return final
