"""Short representative correctness evidence before a long confirmation window."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import search
from cluster import Cluster

DURATION_S = 30.0
FAULT_COUNTERS = ("restarts", "oom_killed", "evicted", "quota_denials")


def _fault_counters_valid(counters: object) -> bool:
    return (
        isinstance(counters, dict)
        and all(isinstance(counters.get(name), int) and not isinstance(counters[name], bool) and counters[name] >= 0
                for name in FAULT_COUNTERS)
    )


def _capture_logs(cluster: Cluster, log_dir: Path | None, label: str) -> dict[str, str]:
    """Bounded API/worker log tails, captured at the requested evidence boundary."""
    if log_dir is None:
        return {}
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        pods = cluster.kubectl("get", "pods", "-o", "name", check=False).split()
        captured = {}
        for pod in pods:
            if not pod.startswith(("pod/bifrost-api-", "pod/bifrost-worker-")):
                continue
            name = pod.removeprefix("pod/")
            path = log_dir / f"{label}-{name}.log"
            path.write_text(cluster.kubectl("logs", name, "--all-containers", "--tail=200", check=False), encoding="utf-8")
            captured[name] = str(path)
        return captured
    except Exception as error:  # evidence collection must never hide the original failure
        return {"collection_error": f"{type(error).__name__}: {error}"}


def run(cluster: Cluster, ids: dict, rate: float, plan: dict, log_dir: Path | None = None,
        on_progress: Callable[[dict], None] | None = None) -> dict:
    """Exercise every submitted request once; this is a stop/go correctness preflight, never a capacity gate."""
    arrival_seed = search.confirmation_seed(plan)
    definition = {
        "arrival": "seeded open-loop Poisson arrivals through loadgen step",
        "duration_s": DURATION_S,
        "profile_seed": plan["seed"],
        "arrival_seed": arrival_seed,
        "reconciliation": "one outcomes query after the latest submitted request's class deadline",
        "first_phase": "30-second representative prefix",
        "steady_phase": "the later unchanged confirmation window after a fresh reset",
    }
    result = {
        "kind": "representative-correctness-preflight", "timing_definition": definition,
        "rate": rate, "profile_seed": plan["seed"], "arrival_seed": arrival_seed,
    }
    try:
        faults_before = cluster.tenant_faults()
        result["fault_counters"] = {"before": faults_before}
        if not _fault_counters_valid(faults_before):
            raise ValueError("incomplete or invalid tenant fault counters before preflight")
        command = cluster.start_exec_python("bench-driver", search.LOADGEN, ["step", json.dumps({
            "rate": rate, "duration_s": DURATION_S, "mix": plan["mix"], "seed": arrival_seed,
            "ids": ids, "io": plan.get("io"), "event_frames": True,
        })], cancel_driver=True)
        sent = command.result(int(DURATION_S + 600), lambda _frame: None)
        result.update({
            "sent": sent["requests"],
            "started": sent["started"],
            "offered_until": sent["started"] + DURATION_S,
            "immediate_log_tails": _capture_logs(cluster, log_dir, "after-step"),
        })
        if on_progress is not None:
            on_progress(result)
        reconciliation = search.await_reconciliation_deadline(sent["requests"], plan["deadlines_s"])
        outcomes = cluster.exec_python("bench-driver", search.LOADGEN, ["outcomes", json.dumps({
            "requests": [{"id": request["id"], "class": request["class"]} for request in sent["requests"]],
            "io": plan.get("io"),
        })], timeout_s=600)
        result["durable_outcomes"] = outcomes
        if on_progress is not None:
            on_progress(result)
        faults_after = cluster.tenant_faults()
        result["fault_counters"]["after"] = faults_after
        if not _fault_counters_valid(faults_after):
            raise ValueError("incomplete or invalid tenant fault counters after preflight")
        faults = {name: faults_after[name] - faults_before[name] for name in FAULT_COUNTERS}
        if any(value < 0 for value in faults.values()):
            raise ValueError("tenant fault counter regressed during preflight")
        faults["duplicate_executions"] = len(outcomes["duplicates"])
        records = search.to_records(sent["requests"], outcomes, sent["started"], plan["deadlines_s"])
        scheduled_classes = {request["class"] for request in sent["requests"] if request.get("scheduled") is not None}
        sent_classes = {request["class"] for request in sent["requests"] if request.get("sent") is not None}
        required_classes = set(plan["mix"])
        checks = {
            "every_mix_class_scheduled_and_sent": required_classes <= scheduled_classes and required_classes <= sent_classes,
            "all_submitted_correct_by_deadline": bool(records) and all(
                request.get("sent") is not None and record["outcome"] == "correct"
                for request, record in zip(sent["requests"], records, strict=True)
            ),
            "no_duplicate_durable_executions": not outcomes["duplicates"],
            "no_tenant_faults": not any(value != 0 for value in faults.values()),
        }
        result.update({
            "latest_deadline": reconciliation["latest_deadline_epoch_s"], "required_classes": sorted(required_classes),
            "scheduled_classes": sorted(scheduled_classes), "submitted_classes": sorted(sent_classes),
            "records": records, "faults": faults, "checks": checks,
        })
        known_durable_failure = any(
            request.get("sent") is not None and record["outcome"] != "correct"
            for request, record in zip(sent["requests"], records, strict=True)
        )
        known_failure = known_durable_failure or not checks["no_duplicate_durable_executions"] or not checks["no_tenant_faults"]
        if known_failure:
            result["overall"] = "fail"
        elif not checks["every_mix_class_scheduled_and_sent"]:
            result["overall"] = "inconclusive"
        else:
            result["overall"] = "pass" if checks["all_submitted_correct_by_deadline"] else "fail"
    except BaseException as error:
        result.update({
            "overall": "invalid", "error": f"{type(error).__name__}: {error}",
            "requires_namespace_teardown": bool(getattr(error, "requires_namespace_teardown", False)),
        })
    if result["overall"] != "pass":
        result["failure_log_tails"] = _capture_logs(cluster, log_dir, "failure")
    return result
