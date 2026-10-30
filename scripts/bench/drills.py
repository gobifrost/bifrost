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
from typing import Callable

import guard
import search
from cluster import Cluster, RemoteCleanupError

HERE = Path(__file__).resolve().parent
FAULT_AT_S = 60
POOLER_LABELS = {"cnpg.io/poolerName": "bifrost-db-pooler"}
RECOVERY_BOUND_S = 30
CLEAN_TAIL_S = 300
D4_RECOVERY_BOUND_S = 30
REDIS_LABELS = {"app.kubernetes.io/name": "redis"}
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
D4_TERMINAL_STATUSES = {
    "workflow": {"Success", "Failed", "Timeout", "Stuck", "CompletedWithErrors", "Cancelled"},
    "agent": {"completed", "failed", "cancelled"},
}


def pooler_pod(cluster: Cluster) -> dict:
    pods = json.loads(cluster.kubectl("get", "pods", "-l", "cnpg.io/poolerName=bifrost-db-pooler", "-o", "json"))["items"]
    running = [pod for pod in pods if pod["metadata"].get("deletionTimestamp") is None]
    if len(running) != 1:
        raise RuntimeError(f"expected one Pooler pod, found {len(running)}")
    guard.check_fault_target(running[0], guard.TENANT_NS, POOLER_LABELS)
    return running[0]


def redis_pod(cluster: Cluster) -> dict:
    """The one tenant Redis StatefulSet pod, selected without a name fallback."""
    pods = json.loads(cluster.kubectl("get", "pods", "-l", "app.kubernetes.io/name=redis", "-o", "json"))["items"]
    if len(pods) != 1:
        raise RuntimeError(f"expected exactly one Redis selector pod, found {len(pods)}")
    pod = pods[0]
    if pod["metadata"].get("deletionTimestamp") is not None:
        raise RuntimeError("Redis fault target is terminating")
    guard.check_fault_target(pod, guard.TENANT_NS, REDIS_LABELS)
    owners = pod["metadata"].get("ownerReferences", [])
    if pod["metadata"].get("name") != "redis-0" or not any(
        owner.get("kind") == "StatefulSet" and owner.get("name") == "redis" and owner.get("controller")
        for owner in owners
    ):
        raise RuntimeError("Redis fault target must be StatefulSet/redis pod redis-0")
    if not _pod_ready(pod):
        raise RuntimeError("Redis fault target has no ready redis container")
    return pod


def _pod_ready(pod: dict) -> bool:
    return pod["metadata"].get("deletionTimestamp") is None and any(
        condition.get("type") == "Ready" and condition.get("status") == "True"
        for condition in pod.get("status", {}).get("conditions", [])
    ) and any(status.get("name") == "redis" and status.get("ready") for status in pod.get("status", {}).get("containerStatuses", []))


def _empty_dir_spec(cluster: Cluster) -> dict:
    statefulset = json.loads(cluster.kubectl("get", "statefulset", "redis", "-o", "json"))
    volumes = statefulset.get("spec", {}).get("template", {}).get("spec", {}).get("volumes", [])
    data = [volume for volume in volumes if volume.get("name") == "data"]
    if len(data) != 1 or not isinstance(data[0].get("emptyDir"), dict):
        raise RuntimeError("StatefulSet/redis data volume is not emptyDir")
    return data[0]


def _redis_exec(cluster: Cluster, pod: str, *args: str) -> str:
    return cluster.kubectl("exec", pod, "-c", "redis", "--", "redis-cli", *args).strip()


def _redis_run_id(cluster: Cluster, pod: str) -> str:
    for line in _redis_exec(cluster, pod, "INFO", "server").splitlines():
        if line.startswith("run_id:"):
            return line.partition(":")[2]
    raise RuntimeError("Redis INFO server did not report run_id")


def _redis_snapshot(cluster: Cluster, pod: str, sentinel: str) -> dict:
    return {
        "pong": _redis_exec(cluster, pod, "PING") == "PONG",
        "run_id": _redis_run_id(cluster, pod),
        "sentinel_exists": _redis_exec(cluster, pod, "EXISTS", sentinel) == "1",
        "dbsize": _redis_exec(cluster, pod, "DBSIZE"),
    }


def preseed_d4(cluster: Cluster, run_id: str) -> dict:
    """Create a single known Redis key whose absence proves the emptyDir reset."""
    cluster.verify()
    pod = redis_pod(cluster)
    sentinel = f"d4-sentinel:{run_id}"
    if _redis_exec(cluster, pod["metadata"]["name"], "SET", sentinel, run_id) != "OK":
        raise RuntimeError("D4 sentinel write was not acknowledged")
    snapshot = _redis_snapshot(cluster, pod["metadata"]["name"], sentinel)
    if not snapshot["pong"] or not snapshot["sentinel_exists"]:
        raise RuntimeError("D4 initial Redis proof requires PONG and the preseeded sentinel")
    return {
        "pod": pod["metadata"]["name"],
        "uid": pod["metadata"]["uid"],
        "sentinel": sentinel,
        "before": snapshot,
        "empty_dir_before": _empty_dir_spec(cluster),
    }


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


def reconcile_d4(requests: list[dict], rows: list[dict], ready_at: float | None, deadlines: dict[str, float]) -> dict:
    """Keep every durable row and apply D4's accepted-request contract.

    A 2xx submission can have no durable row after Redis loss; a timed-out
    submission can have one.  Both cases must remain visible in the saved
    result instead of being collapsed into the normal one-row outcome map.
    """
    by_id: dict[str, list[dict]] = {}
    for row in rows:
        by_id.setdefault(row["request_id"], []).append(row)
    per_request: dict[str, dict] = {}
    first_correct: float | None = None
    one_row = True
    terminal = True
    for request in requests:
        request_id = request["id"]
        durable_rows = by_id.get(request_id, [])
        http_status = request.get("http_status")
        http_accepted = isinstance(http_status, int) and 200 <= http_status < 300
        accepted = http_accepted or bool(durable_rows)
        entry = {
            "submitted": True,
            "accepted": accepted,
            "accepted_by": "http-2xx" if http_accepted else "durable-row" if durable_rows else None,
            "rows": durable_rows,
            "failure": None,
        }
        if accepted and len(durable_rows) != 1:
            one_row = False
            entry["failure"] = "accepted_zero_durable_rows" if not durable_rows else "accepted_multiple_durable_rows"
        elif accepted:
            row = durable_rows[0]
            completed = row.get("completed")
            terminal_status = row.get("status") in D4_TERMINAL_STATUSES.get(row.get("kind"), set())
            entry["terminal_status"] = row.get("status")
            entry["terminal_kind"] = row.get("kind")
            deadline = (request.get("sent") or request.get("scheduled")) + deadlines[request["class"]]
            if completed is None or not terminal_status:
                terminal = False
                entry["failure"] = "accepted_unfinished" if completed is None else "accepted_nonterminal_status"
            elif completed > deadline:
                terminal = False
                entry["failure"] = "accepted_terminal_after_deadline"
            if row.get("correct") and terminal_status and ready_at is not None and request.get("sent") is not None and request["sent"] >= ready_at and completed is not None:
                delay = completed - ready_at
                first_correct = delay if first_correct is None else min(first_correct, delay)
        per_request[request_id] = entry
    checks = {
        "accepted_one_durable_row": one_row,
        "accepted_terminal_by_deadline": terminal,
        "first_correct_after_ready": first_correct is not None and first_correct <= D4_RECOVERY_BOUND_S,
    }
    return {
        "requests": per_request,
        "raw_rows": rows,
        "first_correct_after_ready_s": first_correct,
        "checks": checks,
        "passed": all(checks.values()),
    }


class RedisInjector:
    """One cancellable D4 injector driven by loadgen's actual start/sent frames."""

    def __init__(self, cluster: Cluster, preseed: dict, fault_delay_s: float) -> None:
        self.cluster = cluster
        self.preseed = preseed
        self.fault_delay_s = fault_delay_s
        self.state: dict = {"kind": "redis-empty-restart", "status": "waiting"}
        self._wake = threading.Event()
        self._cancel = threading.Event()
        self._thread = threading.Thread(target=self._run, name="bench-d4-injector")

    def start(self) -> None:
        self._thread.start()

    def observe(self, frame: dict) -> None:
        if self.state.get("status") in {"error", "timeout"}:
            raise RuntimeError(self.state.get("error", "D4 injector failed"))
        if frame.get("event") == "started" and "driver_started_at" not in self.state:
            self.state["driver_started_at"] = float(frame["started"])
            self.state["scheduled_fault_at"] = self.state["driver_started_at"] + self.fault_delay_s
        elif frame.get("event") == "request_sent" and "first_request_sent_at" not in self.state:
            self.state["first_request_sent_at"] = float(frame["sent"])
            self.state["first_request_id"] = frame["request_id"]
        self._wake.set()

    def cancel(self, reason: str) -> None:
        self.state.setdefault("cancel_reason", reason)
        self._cancel.set()
        self._wake.set()

    def join(self, timeout_s: float) -> dict:
        self._thread.join(timeout_s)
        if self._thread.is_alive():
            self.cancel("injector timeout")
            self._thread.join(5)
            self.state["status"] = "timeout"
        return self.state

    def _wait_for(self, predicate: Callable[[], bool], deadline: float, message: str) -> None:
        while not predicate():
            if self._cancel.is_set():
                raise RuntimeError(self.state.get("cancel_reason", "injector cancelled"))
            remaining = deadline - time.time()
            if remaining <= 0:
                raise TimeoutError(message)
            self._wake.wait(min(remaining, 0.2))
            self._wake.clear()

    def _run(self) -> None:
        try:
            self._wait_for(lambda: "driver_started_at" in self.state, time.time() + 60, "driver did not emit a start frame")
            scheduled = self.state["scheduled_fault_at"]
            self._wait_for(lambda: time.time() >= scheduled, scheduled + 60, "fault wait exceeded its schedule")
            self._wait_for(lambda: "first_request_sent_at" in self.state, time.time() + 60, "no pre-fault request was sent")
            self.cluster.verify()
            pod = redis_pod(self.cluster)
            if pod["metadata"]["uid"] != self.preseed["uid"]:
                raise RuntimeError("Redis pod changed after sentinel preseed")
            self.cluster.verify()
            deleted = self.cluster.delete_pod_uid(pod["metadata"]["name"], pod["metadata"]["uid"])
            self.state.update({
                "status": "deleted",
                "fault_at": time.time(),
                "old_uid": pod["metadata"]["uid"],
                "delete_uid_precondition": pod["metadata"]["uid"],
                "delete_response": deleted,
                "old_uid_observed_deleting": bool(deleted.get("metadata", {}).get("deletionTimestamp")),
            })
            self.state.update(wait_d4_ready(self.cluster, self.preseed, self.state, self._cancel))
            self.state["status"] = "ready"
        except Exception as error:  # noqa: BLE001 - persisted structured injector error, never a pass
            if self.state.get("status") != "timeout":
                self.state["status"] = "error"
            self.state["error"] = f"{type(error).__name__}: {error}"


def d4_ready_checks(preseed: dict, old_uid_gone: bool, replacement_uid: str, empty_dir_after: dict, snapshot: dict) -> dict[str, bool]:
    """Every proof required before D4 calls the replacement Redis ready."""
    return {
        "old_uid_gone": old_uid_gone,
        "replacement_uid_changed": replacement_uid != preseed["uid"],
        "empty_dir_unchanged": empty_dir_after == preseed["empty_dir_before"],
        "redis_pong": snapshot["pong"],
        "redis_run_id_changed": snapshot["run_id"] != preseed["before"]["run_id"],
        "preseeded_sentinel_missing": not snapshot["sentinel_exists"],
    }


def wait_d4_ready(cluster: Cluster, preseed: dict, injected: dict, cancelled: threading.Event) -> dict:
    """Return replacement evidence only after every D4 recovery proof succeeds."""
    deadline = time.time() + 300
    old_uid = preseed["uid"]
    old_gone = False
    while time.time() < deadline:
        if cancelled.is_set():
            raise RuntimeError("injector cancelled while Redis recovered")
        raw = cluster.kubectl("get", "pod", preseed["pod"], "-o", "json", check=False)
        if raw.strip():
            pod = json.loads(raw)
            uid = pod.get("metadata", {}).get("uid")
            if uid == old_uid:
                injected["old_uid_observed_deleting"] = injected["old_uid_observed_deleting"] or bool(pod["metadata"].get("deletionTimestamp"))
            elif uid:
                old_gone = True
        else:
            old_gone = True
        try:
            replacement = redis_pod(cluster)
        except RuntimeError:
            time.sleep(1)
            continue
        if replacement["metadata"]["uid"] == old_uid or not _pod_ready(replacement):
            time.sleep(1)
            continue
        after_empty_dir = _empty_dir_spec(cluster)
        snapshot = _redis_snapshot(cluster, replacement["metadata"]["name"], preseed["sentinel"])
        checks = d4_ready_checks(preseed, old_gone, replacement["metadata"]["uid"], after_empty_dir, snapshot)
        if not all(checks.values()):
            time.sleep(1)
            continue
        return {
            "ready_at": time.time(),
            "old_uid_gone": old_gone,
            "replacement_uid": replacement["metadata"]["uid"],
            "empty_dir_after": after_empty_dir,
            "empty_dir_unchanged": checks["empty_dir_unchanged"],
            "redis": snapshot,
        }
    raise TimeoutError("Redis did not satisfy D4 replacement readiness within 300s")


def d4_worker_evidence(before: list[dict], after: list[dict]) -> dict:
    """Raw worker state plus losses and session-generation changes around D4."""
    previous = {worker["id"]: worker for worker in before}
    return {
        "before": before,
        "after": after,
        "lost": [worker for worker in after if worker.get("state") == "lost" and previous.get(worker["id"], {}).get("state") != "lost"],
        "remote_session_generation_changes": [
            {"id": worker["id"], "before": previous[worker["id"]].get("generation"), "after": worker.get("generation")}
            for worker in after
            if worker.get("id") in previous and worker.get("generation") != previous[worker["id"]].get("generation")
        ],
    }


def _d4_workers_after(cluster: Cluster, before: list[dict]) -> dict:
    try:
        return d4_worker_evidence(before, cluster.driver_sql(WORKERS_SQL))
    except Exception as error:  # noqa: BLE001 - preserve unavailable evidence without converting it into a capacity gate
        return {"before": before, "after": None, "error": f"{type(error).__name__}: {error}"}


def d4_timing_checks(step: dict, plan: dict, injected: dict) -> dict[str, bool]:
    """Fault timing must describe actual offered traffic, never a delayed post-window fault."""
    fault_at = injected.get("fault_at")
    first_sent = injected.get("first_request_sent_at")
    offered_start = step.get("started")
    offered_end = None if offered_start is None else offered_start + plan["warmup_s"] + step["window_s"]
    return {
        "fault_inside_offered_window": fault_at is not None and offered_start is not None and offered_start <= fault_at <= offered_end,
        "pre_fault_request_sent": first_sent is not None and fault_at is not None and first_sent < fault_at,
    }


def _d4_final_outcomes(cluster: Cluster, step: dict, plan: dict, ready_at: float | None) -> tuple[dict | None, dict | None, str | None]:
    """Reconcile all sent requests only after their latest class deadline."""
    latest_deadline = max((request.get("sent") or request["scheduled"]) + plan["deadlines_s"][request["class"]]
                          for request in step["sent"])
    if latest_deadline > time.time():
        time.sleep(latest_deadline - time.time())
    try:
        outcomes = cluster.exec_python("bench-driver", search.LOADGEN, ["outcomes", json.dumps({
            "requests": [{"id": request["id"], "class": request["class"]} for request in step["sent"]],
            "io": plan.get("io"),
        })], timeout_s=600)
    except Exception as error:  # noqa: BLE001 - no durable evidence can be inferred after a driver loss
        return None, None, f"{type(error).__name__}: {error}"
    return outcomes, reconcile_d4(step["sent"], outcomes["rows"], ready_at, plan["deadlines_s"]), None


def d4(cluster: Cluster, ids: dict, plan: dict, disk: dict[str, str], run_id: str,
       persist_result: Callable[[dict], None]) -> dict:
    """Correctness-only Redis-empty restart at one offered request per second."""
    workers_before = cluster.driver_sql(WORKERS_SQL)
    preseed = preseed_d4(cluster, run_id)
    injector = RedisInjector(cluster, preseed, plan["warmup_s"] + FAULT_AT_S)
    injector.start()
    try:
        step = search.run_step(
            cluster, ids, 1.0, plan, plan["seed"] + 4000, disk,
            window_s=FAULT_AT_S + D4_RECOVERY_BOUND_S + 60,
            on_event=injector.observe,
            cancel_driver=True,
        )
    except Exception as error:  # noqa: BLE001 - record a harness failure as invalid evidence
        injector.cancel("step failed")
        injected = injector.join(10)
        error_text = f"{type(error).__name__}: {error}"
        result = {"kind": "redis-empty-restart", "mode": "correctness-only", "rate": 1.0,
                  "preseed": preseed, "injector": injected, "workers": _d4_workers_after(cluster, workers_before),
                  "step_error": error_text,
                  "final_reconciliation": {"available": False, "reason": "step did not return sent requests"},
                  "requires_namespace_teardown": isinstance(error, RemoteCleanupError),
                  "passed": False, "overall": "invalid"}
        persist_result(result)
        return result
    if "fault_at" not in injector.state:
        injector.cancel("step ended before D4 fault injection")
    injected = injector.join(310)
    ready_at = injected.get("ready_at") if injected.get("status") == "ready" else None
    initial = {
        "step": step,
        "reconciliation": reconcile_d4(step["sent"], step["outcomes"]["rows"], ready_at, plan["deadlines_s"]),
    }
    timing_checks = d4_timing_checks(step, plan, injected)
    persisted = {"kind": "redis-empty-restart", "mode": "correctness-only", "rate": 1.0,
                 "preseed": preseed, "injector": injected, "timing_checks": timing_checks, "initial": initial}
    persist_result(persisted)
    final_outcomes, final, final_error = _d4_final_outcomes(cluster, step, plan, ready_at)
    workers = _d4_workers_after(cluster, workers_before)
    if final_error is not None:
        result = {**persisted, "workers": workers,
                  "final_reconciliation": {"available": False, "reason": final_error},
                  "requires_namespace_teardown": True,
                  "passed": False, "overall": "invalid"}
        persist_result(result)
        return result
    assert final_outcomes is not None and final is not None
    if injected.get("status") != "ready":
        result = {**persisted, "workers": workers, "final": {"outcomes": final_outcomes, "reconciliation": final},
                  "final_reconciliation": {"available": True},
                  "passed": False, "overall": "invalid", "capacity_evidence": step["overall"]}
        persist_result(result)
        return result
    recovery_checks = {
        "uid_precondition_accepted": injected["delete_uid_precondition"] == preseed["uid"],
        **d4_ready_checks(preseed, injected["old_uid_gone"], injected["replacement_uid"], injected["empty_dir_after"], injected["redis"]),
    }
    passed = final["passed"] and all(recovery_checks.values())
    timing_valid = all(timing_checks.values())
    result = {**persisted, "workers": workers, "final": {"outcomes": final_outcomes, "reconciliation": final},
              "final_reconciliation": {"available": True}, "recovery_checks": recovery_checks,
              "passed": passed and timing_valid,
              "overall": "pass" if passed and timing_valid else "invalid" if not timing_valid else "fail",
              "capacity_evidence": step["overall"]}
    persist_result(result)
    return result


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
