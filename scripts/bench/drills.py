"""Failure drills against the benchmark tenant (methodology §8): pooler restarts.

D1 deletes the PgBouncer Pooler pod (graceful); D2 SIGKILLs the PgBouncer
process inside it from the Kind node (abrupt, like a crash). Load runs open
loop throughout; the fault lands ``FAULT_AT_S`` into the step. Every fault is
guard-checked against the tenant namespace and the pod's labels first.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path

import guard
import search
from cluster import Cluster

HERE = Path(__file__).resolve().parent
FAULT_AT_S = 60
POOLER_LABELS = {"cnpg.io/poolerName": "bifrost-db-pooler"}
RECOVERY_BOUND_S = 30
CLEAN_TAIL_S = 300
LOG_PATTERNS = {
    "owner_renewal_retry": "Remote owner renewal failed; retrying",
    "owner_lost": "Remote API owner lease could not be renewed",
    "leader_lost": "Control replica lost trigger leadership",
    "leader_renew_failed": "Failed to renew scheduler trigger lease",
    "platform_job_stopped": "Platform-job runner",
    "disconnect": "ConnectionDoesNotExistError",
    "interface_error": "InterfaceError",
}
WORKERS_SQL = """
select coalesce(json_agg(json_build_object('id', id::text, 'state', state, 'generation', remote_session_generation,
       'reason', end_reason) order by id), '[]'::json) from execution_workers
"""


def pooler_pod(cluster: Cluster) -> dict:
    pods = json.loads(cluster.kubectl("get", "pods", "-l", "cnpg.io/poolerName=bifrost-db-pooler", "-o", "json"))["items"]
    running = [pod for pod in pods if pod["metadata"].get("deletionTimestamp") is None]
    if len(running) != 1:
        raise RuntimeError(f"expected one Pooler pod, found {len(running)}")
    guard.check_fault_target(running[0], guard.TENANT_NS, POOLER_LABELS)
    return running[0]


def inject(cluster: Cluster, kind: str) -> dict:
    """Apply one fault and return when and to what it was applied."""
    cluster.verify()
    pod = pooler_pod(cluster)
    name = pod["metadata"]["name"]
    at = time.time()
    if kind == "pooler-delete":
        cluster.kubectl("delete", "pod", name, "--wait=false")
    elif kind == "pgbouncer-kill":
        container = next(c for c in pod["status"]["containerStatuses"] if c["name"] == "pgbouncer")["containerID"].split("//", 1)[1]
        script = (
            f'pid=$(crictl inspect --output go-template --template "{{{{.info.pid}}}}" {container}); '
            'target=$(pgrep -P "$pid" -x pgbouncer || true); '
            '[ -n "$target" ] || target=$(pgrep -x pgbouncer); '
            'kill -9 $target && echo $target'
        )
        killed = subprocess.run(["docker", "exec", cluster.identity.node_name, "sh", "-c", script],
                                capture_output=True, text=True, check=True).stdout.strip()
        return {"kind": kind, "pod": name, "at": at, "killed_pid": killed}
    else:
        raise ValueError(kind)
    return {"kind": kind, "pod": name, "at": at}


def wait_ready(cluster: Cluster, since: float) -> float:
    """Epoch seconds at which a Pooler pod is Ready again."""
    deadline = time.time() + 300
    while time.time() < deadline:
        pods = json.loads(cluster.kubectl("get", "pods", "-l", "cnpg.io/poolerName=bifrost-db-pooler", "-o", "json"))["items"]
        for pod in pods:
            ready = next((c for c in pod["status"].get("conditions", []) if c["type"] == "Ready"), {})
            if ready.get("status") == "True" and pod["metadata"].get("deletionTimestamp") is None and time.time() >= since + 1:
                return time.time()
        time.sleep(1)
    raise RuntimeError("Pooler did not become Ready within 300 s")


def log_counts(cluster: Cluster, since_s: int) -> dict[str, dict[str, int]]:
    """Occurrences of each pattern in API and worker logs since the drill started."""
    counts: dict[str, dict[str, int]] = {}
    for pod in cluster.kubectl("get", "pods", "-o", "name").split():
        name = pod.removeprefix("pod/")
        if not name.startswith(("bifrost-api-", "bifrost-worker-")):
            continue
        text = cluster.kubectl("logs", name, f"--since={since_s}s", "--all-containers", check=False)
        counts[name] = {key: text.count(pattern) for key, pattern in LOG_PATTERNS.items()}
    return counts


def classify(records: list[dict], fault_s: float, ready_s: float, workers_before: list[dict], workers_after: list[dict]) -> dict:
    """Outcomes before, during and after the fault, recovery time, and the pass checks."""
    before = [r for r in records if r["sent_s"] is not None and r["sent_s"] < fault_s]
    during = [r for r in records if r["sent_s"] is not None and fault_s <= r["sent_s"] < ready_s]
    after = [r for r in records if r["sent_s"] is not None and r["sent_s"] >= ready_s]
    first_success = min((r["sent_s"] for r in after if r["outcome"] == "correct"), default=None)
    tail = [r for r in after if r["sent_s"] >= ready_s + RECOVERY_BOUND_S]
    was_lost = {b["id"] for b in workers_before if b["state"] == "lost"}
    lost = [w for w in workers_after if w["state"] == "lost" and w["id"] not in was_lost]
    generations = {b["id"]: b["generation"] for b in workers_before}
    regenerated = [w["id"] for w in workers_after if w["id"] in generations and generations[w["id"]] != w["generation"]]
    checks = {
        "first_success_within_bound": first_success is not None and first_success - ready_s <= RECOVERY_BOUND_S,
        "clean_tail": bool(tail) and all(r["outcome"] == "correct" for r in tail),
        "no_executor_lost": not lost,
        "no_owner_regeneration": not regenerated,
        "no_unfinished": all(r["outcome"] != "unfinished" for r in records),
    }
    return {
        "fault_s": fault_s, "ready_s": ready_s,
        "failures": {name: sum(r["outcome"] != "correct" for r in group) for name, group in (("before", before), ("during", during), ("after", after))},
        "requests": {"before": len(before), "during": len(during), "after": len(after)},
        "first_success_after_ready_s": None if first_success is None else first_success - ready_s,
        "lost_executors": lost, "regenerated_owners": regenerated,
        "checks": checks, "passed": all(checks.values()),
    }


def drill(cluster: Cluster, ids: dict, plan: dict, kind: str, rate: float, disk: dict[str, str]) -> dict:
    """Steady load with one fault ``FAULT_AT_S`` in; classify outcomes around it."""
    workers_before = cluster.driver_sql(WORKERS_SQL)
    started_wall = time.time()
    fault: dict = {}

    def fire() -> None:
        time.sleep(plan["warmup_s"] + FAULT_AT_S)
        fault.update(inject(cluster, kind))
        fault["ready_at"] = wait_ready(cluster, fault["at"])

    window_s = FAULT_AT_S + RECOVERY_BOUND_S + CLEAN_TAIL_S + 60
    injector = threading.Thread(target=fire, daemon=True)
    injector.start()
    step = search.run_step(cluster, ids, rate, plan, plan["seed"] + 2000, disk, window_s=window_s)
    injector.join()
    workers_after = cluster.driver_sql(WORKERS_SQL)
    logs = log_counts(cluster, int(time.time() - started_wall) + 30)
    started = step["started"]
    result = classify(step["records"], fault["at"] - started, fault["ready_at"] - started, workers_before, workers_after)
    return {"kind": kind, "rate": rate, "fault": fault, "logs": logs, **result,
        "step": step,
    }
