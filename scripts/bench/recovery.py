"""Bounded recovery-drill evidence built on the existing benchmark harness.

This module owns only the recovery-specific proof.  Arrival generation,
ordinary send-time SLOs, durable outcomes, and host validity stay in
``search.run_step``.  It deliberately retains raw evidence instead of
rewriting a failed SDK operation as a passed recovery result.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Callable

import guard
import search


RECOVERY_EFFECTS_VERSION = "recovery-effects-v1"
FAULT_AT_S = 60.0
API_RECOVERY_BOUND_S = 60.0
OWNER_LEASE_S = 20.0
ONE_SWEEP_S = 15.0  # api/src/worker/pool_main.py SWEEP_SECONDS
WORKER_RECOVERY_BOUND_S = OWNER_LEASE_S + ONE_SWEEP_S
PRIMARY_LABELS = {"cnpg.io/cluster": "bifrost-db", "cnpg.io/instanceRole": "primary"}
API_LABELS = {"app.kubernetes.io/name": "bifrost-api"}
TERMINAL_STATUSES = {
    "workflow": {"Success", "Failed", "Timeout", "Stuck", "CompletedWithErrors", "Cancelled"},
    "agent": {"completed", "failed", "cancelled"},
}
HERE = Path(__file__).resolve().parent
RECOVERY_DRIVER = HERE / "incluster" / "recovery_driver.py"


def _ready(pod: dict, container: str | None = None) -> bool:
    if pod.get("metadata", {}).get("deletionTimestamp") is not None:
        return False
    conditions = pod.get("status", {}).get("conditions", [])
    if not any(item.get("type") == "Ready" and item.get("status") == "True" for item in conditions):
        return False
    if container is None:
        return True
    return any(item.get("name") == container and item.get("ready") for item in pod.get("status", {}).get("containerStatuses", []))


def _one_pod(cluster, selector: str, labels: dict[str, str], *, kind: str, container: str) -> dict:
    found = [pod for pod in json.loads(cluster.kubectl("get", "pods", "-l", selector, "-o", "json"))["items"]
             if pod.get("metadata", {}).get("deletionTimestamp") is None]
    if len(found) != 1:
        raise RuntimeError(f"expected exactly one {kind} target, found {len(found)}")
    pod = found[0]
    if pod.get("metadata", {}).get("namespace") != guard.TENANT_NS or not _ready(pod, container):
        raise RuntimeError(f"{kind} target is not one ready live tenant pod")
    if any(pod.get("metadata", {}).get("labels", {}).get(key) != value for key, value in labels.items()):
        raise RuntimeError(f"{kind} target lacks required benchmark labels")
    guard.check_fault_target(pod, guard.TENANT_NS, labels)
    return pod


def cnpg_primary(cluster) -> dict:
    """Only the current one-instance CNPG primary is eligible for D3."""
    resource = json.loads(cluster.kubectl("get", "cluster", "bifrost-db", "-o", "json"))
    status = resource.get("status", {})
    if status.get("readyInstances") != 1:
        raise RuntimeError("CloudNativePG Cluster/bifrost-db does not report exactly one ready instance")
    return _one_pod(cluster, "cnpg.io/cluster=bifrost-db,cnpg.io/instanceRole=primary", PRIMARY_LABELS,
                    kind="CNPG primary", container="postgres")


def api_target(cluster) -> dict:
    return _one_pod(cluster, "app.kubernetes.io/name=bifrost-api", API_LABELS, kind="API", container="api")


def api_restart_preflight(cluster, target: dict) -> dict:
    """D5 evidence captured before deletion, including terminating-pod quota pressure."""
    deployment = json.loads(cluster.kubectl("get", "deployment", "bifrost-api", "-o", "json"))
    quota = json.loads(cluster.kubectl("get", "resourcequota", "tenant-envelope", "-o", "json"))
    grace = deployment.get("spec", {}).get("template", {}).get("spec", {}).get("terminationGracePeriodSeconds")
    if not isinstance(grace, int) or grace < 0:
        raise RuntimeError("API deployment has no valid termination grace period")
    return {"old_pod": target["metadata"]["name"], "old_uid": target["metadata"]["uid"],
            "termination_grace_seconds": grace, "quota_before": quota.get("status", {})}


def replacement_proof(old_uid: str, replacement: dict, *, ready_at: float, bound_s: float, fault_at: float) -> dict:
    """UID and readiness proof shared by D3/D5 and later restart drills."""
    new_uid = replacement.get("metadata", {}).get("uid")
    changed = isinstance(new_uid, str) and new_uid != old_uid
    ready = _ready(replacement)
    return {"old_uid": old_uid, "replacement_uid": new_uid, "uid_changed": changed, "ready": ready,
            "ready_at": ready_at, "recovery_seconds": ready_at - fault_at,
            "within_bound": ready and changed and ready_at - fault_at <= bound_s}


def delete_exact(cluster, target: dict, selector, *, kind: str) -> dict:
    """Re-identify a selected target immediately before one nonblocking delete."""
    cluster.verify()
    current = selector(cluster)
    old = target.get("metadata", {})
    now = current.get("metadata", {})
    if (now.get("name"), now.get("uid")) != (old.get("name"), old.get("uid")):
        raise RuntimeError(f"{kind} target changed before destruction")
    cluster.verify()
    at = time.time()
    cluster.kubectl("delete", "pod", now["name"], "--wait=false")
    return {"pod": now["name"], "old_uid": now["uid"], "fault_at": at, "delete": "issued_once"}


def effect_assessment(requests: list[dict], durable_rows: list[dict], effects: list[dict]) -> dict:
    """Keep accepted/entity and append-only effect guarantees separate.

    An effect row is expected to carry only request_id, effect_token, and
    effect_index.  No unique key or idempotency behavior is assumed here.
    """
    durable: dict[str, list[dict]] = {}
    for row in durable_rows:
        durable.setdefault(row["request_id"], []).append(row)
    appended: dict[str, list[dict]] = {}
    for row in effects:
        appended.setdefault(row["bench_request_id"], []).append(row)
    per_request: dict[str, dict] = {}
    submitted_unaccepted: list[dict] = []
    terminal_and_loss = True
    strict_effects = True
    for request in requests:
        request_id = request["id"]
        rows = durable.get(request_id, [])
        http_accepted = isinstance(request.get("http_status"), int) and 200 <= request["http_status"] < 300
        accepted = http_accepted or len(rows) == 1
        records = appended.get(request_id, [])
        expected = request.get("effect_token")
        matching = [row for row in records if row.get("effect_token") == expected]
        terminal = _terminal(rows[0]) if len(rows) == 1 else False
        effect_ok = not accepted or (len(matching) == 1 and len(records) == 1)
        if accepted and not terminal:
            terminal_and_loss = False
        if not effect_ok:
            strict_effects = False
        per_request[request_id] = {
            "submitted": True, "accepted": accepted,
            "accepted_by": "http-2xx" if http_accepted else "one-durable-entity" if len(rows) == 1 else None,
            "durable_entities": rows, "effects": records,
            "effect_token": expected, "effect_count": len(records), "matching_effect_count": len(matching),
            "terminal": terminal, "strict_effect": effect_ok,
        }
        if not accepted:
            submitted_unaccepted.append({"request": request, "durable_entities": rows, "effects": records})
    return {"per_request": per_request, "submitted_unaccepted": submitted_unaccepted,
            "terminal_and_loss": terminal_and_loss, "strict_effects": strict_effects}


def _terminal(row: dict) -> bool:
    """Canonical loadgen outcomes have status/completed, never a synthetic terminal flag."""
    return (isinstance(row.get("completed"), (int, float))
            and row.get("status") in TERMINAL_STATUSES.get(row.get("kind"), set()))


def recovery_deadlines(requests: list[dict], rows: list[dict], db_ready_at: float, deadlines_s: dict[str, float]) -> dict:
    """Preserve normal send-time SLOs and D3's post-ready recovery clock."""
    by_id: dict[str, list[dict]] = {}
    for row in rows:
        by_id.setdefault(row["request_id"], []).append(row)
    evidence: dict[str, dict] = {}
    normal = recovery = True
    submitted_unaccepted: list[dict] = []
    for request in requests:
        matching = by_id.get(request["id"], [])
        row = matching[0] if len(matching) == 1 else None
        http_accepted = isinstance(request.get("http_status"), int) and 200 <= request["http_status"] < 300
        accepted = http_accepted or len(matching) == 1
        if not accepted:
            submitted_unaccepted.append({"request": request, "durable_entities": matching})
            evidence[request["id"]] = {"accepted": False, "durable_entities": matching,
                                        "normal_deadline_met": None, "recovery_deadline_met": None}
            continue
        sent = request.get("sent")
        if not isinstance(sent, (int, float)) or row is None:
            normal = recovery = False
            evidence[request["id"]] = {"accepted": True, "durable_entities": matching,
                                        "normal_deadline_met": False, "recovery_deadline_met": False}
            continue
        deadline = sent + deadlines_s[request["class"]]
        completed = row.get("completed")
        terminal = _terminal(row)
        normal_ok = terminal and completed <= deadline
        # A request already terminal before the database came back had no recovery
        # delay.  It keeps its ordinary deadline instead of receiving an extension.
        recovery_deadline = (max(deadline, db_ready_at + deadlines_s[request["class"]])
                             if sent < db_ready_at and not (terminal and completed <= db_ready_at) else deadline)
        recovery_ok = terminal and completed <= recovery_deadline
        normal &= normal_ok
        recovery &= recovery_ok
        evidence[request["id"]] = {"accepted": True, "durable_entities": matching,
                                    "normal_deadline": deadline, "normal_deadline_met": normal_ok,
                                    "recovery_deadline": recovery_deadline, "recovery_deadline_met": recovery_ok}
    return {"per_request": evidence, "submitted_unaccepted": submitted_unaccepted,
            "normal_send_time_slo": normal, "recovery_deadline": recovery}


BUSY_WORKER_SQL = """
select coalesce(json_agg(json_build_object(
  'worker_id', w.id::text, 'pod_uid', w.kubernetes_pod_uid,
  'job_name', w.kubernetes_job_name, 'execution_id', e.id::text, 'status', e.status
) order by e.created_at), '[]'::json)
from executions e
join execution_worker_claims c on c.family = 'workflow_execution' and c.subject_id = e.id
join execution_workers w on w.id = c.worker_id
where e.status = 'Running' and w.state = 'ready' and w.kubernetes_pod_uid is not null
"""


def busy_worker(cluster) -> tuple[dict, dict]:
    """Select one worker only when durable ownership proves it is running work."""
    rows = cluster.driver_sql(BUSY_WORKER_SQL)
    if not isinstance(rows, list) or len(rows) != 1:
        raise RuntimeError("expected exactly one durable busy worker target")
    owned = rows[0]
    uid = owned.get("pod_uid")
    if not isinstance(uid, str) or not uid:
        raise RuntimeError("durable busy worker target has no pod UID")
    pods = json.loads(cluster.kubectl("get", "pods", "-o", "json"))["items"]
    matching = [pod for pod in pods if pod.get("metadata", {}).get("uid") == uid and _ready(pod, "worker")]
    if len(matching) != 1:
        raise RuntimeError("durable busy worker UID does not identify one ready worker pod")
    return owned, matching[0]


def kill_worker_process(cluster, owned: dict, pod: dict) -> dict:
    """SIGKILL the exact ready worker container process on the guarded Kind node."""
    cluster.verify()
    if pod.get("metadata", {}).get("uid") != owned.get("pod_uid") or pod.get("spec", {}).get("nodeName") != cluster.identity.node_name:
        raise RuntimeError("worker target changed or is outside the guarded Kind node")
    status = [item for item in pod.get("status", {}).get("containerStatuses", []) if item.get("name") == "worker"]
    if len(status) != 1 or not isinstance(status[0].get("containerID"), str):
        raise RuntimeError("worker target lacks one container ID")
    container_id = status[0]["containerID"].split("://", 1)[-1]
    inspected = subprocess.run(["docker", "exec", cluster.identity.node_name, "crictl", "inspect", container_id],
                               capture_output=True, text=True, check=True)
    pid = json.loads(inspected.stdout).get("info", {}).get("pid")
    if not isinstance(pid, int) or pid <= 1:
        raise RuntimeError("crictl did not return a safe worker container PID")
    cluster.verify()
    subprocess.run(["docker", "exec", cluster.identity.node_name, "kill", "-9", str(pid)], check=True)
    return {"worker_id": owned["worker_id"], "execution_id": owned["execution_id"], "pod": pod["metadata"]["name"],
            "pod_uid": owned["pod_uid"], "container_id": container_id, "pid": pid, "killed_at": time.time(),
            "loss_bound_s": WORKER_RECOVERY_BOUND_S}


WORKER_STATE_SQL = """
select coalesce(json_agg(json_build_object('id', id::text, 'state', state,
  'generation', remote_session_generation, 'reason', end_reason) order by id), '[]'::json)
from execution_workers
"""


def worker_slot_release(cluster, worker_id: str) -> dict:
    """Prove the killed worker no longer holds a running execution claim."""
    try:
        canonical = str(uuid.UUID(worker_id))
    except (TypeError, ValueError, AttributeError) as error:
        raise RuntimeError("durable worker target has an invalid worker ID") from error
    rows = cluster.driver_sql(f"""
        select coalesce(json_agg(json_build_object('execution_id', e.id::text, 'status', e.status) order by e.created_at), '[]'::json)
        from executions e
        join execution_worker_claims c on c.subject_id = e.id and c.family = 'workflow_execution'
        where c.worker_id = '{canonical}'::uuid and e.status = 'Running'
    """)
    if not isinstance(rows, list):
        raise RuntimeError("worker slot-release query did not return a list")
    return {"worker_id": canonical, "running_claims": rows, "released": not rows}


class WorkerRecoverySampler:
    """Capture first observed loss and replacement admission while D6 is running."""

    def __init__(self, cluster, killed_worker_id: str, *, interval_s: float = 1.0) -> None:
        self.cluster = cluster
        self.killed_worker_id = killed_worker_id
        self.interval_s = interval_s
        self.samples: list[dict] = []
        self.first_lost_at: float | None = None
        self.first_replacement_ready_at: float | None = None
        self.first_lost: dict | None = None
        self.first_replacement: dict | None = None
        self.pre_kill_ready_worker_ids: set[str] = set()
        self.killed_at: float | None = None
        self.error: str | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="bench-d6-recovery-sampler")

    def start(self) -> None:
        self._thread.start()

    def snapshot_before_kill(self) -> dict:
        """Freeze ready workers before destruction so an existing slot cannot count as replacement."""
        workers = self.cluster.driver_sql(WORKER_STATE_SQL)
        if not isinstance(workers, list):
            raise RuntimeError("worker-state query did not return a list")
        self.pre_kill_ready_worker_ids = {worker["id"] for worker in workers
                                          if isinstance(worker.get("id"), str) and worker.get("state") == "ready"}
        snapshot = {"observed_at": time.time(), "phase": "pre-kill", "workers": workers}
        self.samples.append(snapshot)
        return snapshot

    def mark_killed(self, killed_at: float) -> None:
        if not isinstance(killed_at, (int, float)):
            raise RuntimeError("D6 kill has no timestamp")
        self.killed_at = float(killed_at)

    def stop(self) -> dict:
        self._stop.set()
        self._thread.join(10)
        if self._thread.is_alive():
            raise RuntimeError("D6 recovery sampler did not stop")
        if self.error is not None:
            raise RuntimeError(f"D6 recovery sampler failed: {self.error}")
        return self.evidence()

    def observe(self, workers: list[dict], *, observed_at: float) -> None:
        snapshot = {"observed_at": observed_at, "phase": "post-kill" if self.killed_at is not None else "pre-kill", "workers": workers}
        self.samples.append(snapshot)
        if self.killed_at is None or observed_at < self.killed_at:
            return
        killed = next((worker for worker in workers if worker.get("id") == self.killed_worker_id), None)
        if self.first_lost_at is None and killed is not None and killed.get("state") == "lost":
            self.first_lost_at, self.first_lost = observed_at, killed
        replacement = next((worker for worker in workers if worker.get("id") not in self.pre_kill_ready_worker_ids
                            and worker.get("state") == "ready"), None)
        if self.first_replacement_ready_at is None and replacement is not None:
            self.first_replacement_ready_at, self.first_replacement = observed_at, replacement

    def evidence(self) -> dict:
        return {"samples": self.samples, "killed_at": self.killed_at,
                "pre_kill_ready_worker_ids": sorted(self.pre_kill_ready_worker_ids),
                "first_lost_at": self.first_lost_at, "first_lost": self.first_lost,
                "first_replacement_ready_at": self.first_replacement_ready_at,
                "first_replacement": self.first_replacement}

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                workers = self.cluster.driver_sql(WORKER_STATE_SQL)
                if not isinstance(workers, list):
                    raise RuntimeError("worker-state query did not return a list")
                self.observe(workers, observed_at=time.time())
                self._stop.wait(self.interval_s)
        except Exception as error:  # noqa: BLE001 - sampler failures invalidate D6 evidence
            self.error = f"{type(error).__name__}: {error}"


def worker_recovery_assessment(kill: dict, sampled: dict) -> dict:
    """D6's loss clock uses sampling time, never a later reconciliation timestamp."""
    lost_at = sampled.get("first_lost_at")
    replacement_at = sampled.get("first_replacement_ready_at")
    lost = sampled.get("first_lost") if isinstance(sampled.get("first_lost"), dict) else {}
    replacement = sampled.get("first_replacement") if isinstance(sampled.get("first_replacement"), dict) else {}
    return {
        "lost_path_observed": lost_at is not None and lost.get("state") == "lost",
        "lost_terminal_status": lost.get("state"),
        "first_lost": lost or None, "first_lost_at": lost_at,
        "first_replacement": replacement or None, "first_replacement_ready_at": replacement_at,
        "replacement_admitted": replacement_at is not None and replacement.get("id") not in set(sampled.get("pre_kill_ready_worker_ids", [])),
        "loss_after_kill_seconds": None if lost_at is None else lost_at - kill["killed_at"],
        "replacement_after_kill_seconds": None if replacement_at is None else replacement_at - kill["killed_at"],
        "loss_within_lease_plus_one_sweep": lost_at is not None and lost_at - kill["killed_at"] <= kill["loss_bound_s"],
        "bound": {"owner_lease_seconds": OWNER_LEASE_S, "one_sweep_seconds": ONE_SWEEP_S,
                  "total_seconds": kill["loss_bound_s"]},
    }


class RestartInjector:
    """One restart fault scheduled from real loadgen frames, with persisted injector failure."""

    def __init__(self, cluster, *, selector: Callable, kind: str, delay_s: float,
                 ready: Callable[[dict, threading.Event], dict], preflight: Callable[[dict], dict] | None = None) -> None:
        self.cluster, self.selector, self.kind = cluster, selector, kind
        self.delay_s, self.ready, self.preflight = delay_s, ready, preflight
        self.state: dict = {"kind": kind, "status": "waiting"}
        self._wake, self._cancel = threading.Event(), threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"bench-{kind}-injector")

    def start(self) -> None:
        self._thread.start()

    def observe(self, frame: dict) -> None:
        if self.state.get("status") in {"error", "timeout"}:
            raise RuntimeError(self.state.get("error", f"{self.kind} injector failed"))
        if frame.get("event") == "started" and "driver_started_at" not in self.state:
            self.state["driver_started_at"] = float(frame["started"])
            self.state["scheduled_fault_at"] = self.state["driver_started_at"] + self.delay_s
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
            self._wait_for(lambda: "driver_started_at" in self.state, time.time() + 60, "driver did not emit started")
            scheduled = self.state["scheduled_fault_at"]
            self._wait_for(lambda: time.time() >= scheduled, scheduled + 60, "fault wait exceeded its schedule")
            self._wait_for(lambda: "first_request_sent_at" in self.state, time.time() + 60, "no request was sent before fault")
            self.cluster.verify()
            target = self.selector(self.cluster)
            if self.preflight is not None:
                self.state["preflight"] = self.preflight(target)
            injected = delete_exact(self.cluster, target, self.selector, kind=self.kind)
            self.state.update(injected)
            self.state["delete_uid_precondition"] = target["metadata"]["uid"]
            self.state["status"] = "deleted"
            self.state.update(self.ready(injected, self._cancel))
            self.state["status"] = "ready"
        except Exception as error:  # noqa: BLE001 - observable injection error invalidates measurement
            if self.state.get("status") != "timeout":
                self.state["status"] = "error"
            self.state["error"] = f"{type(error).__name__}: {error}"


def _probe(cluster, mode: str) -> dict:
    result = cluster.exec_python("bench-driver", RECOVERY_DRIVER, [mode, "{}"], timeout_s=30)
    if not result.get("ok"):
        raise RuntimeError(f"{mode} probe did not report ok")
    return result


def wait_d3_ready(cluster, injected: dict, cancelled: threading.Event) -> dict:
    deadline = time.time() + 300
    while time.time() < deadline:
        if cancelled.is_set():
            raise RuntimeError("injector cancelled while CNPG recovered")
        try:
            replacement = cnpg_primary(cluster)
            proof = replacement_proof(injected["old_uid"], replacement, ready_at=time.time(),
                                      fault_at=injected["fault_at"], bound_s=API_RECOVERY_BOUND_S)
            if proof["uid_changed"] and proof["ready"]:
                proof["pooler_db_probe"] = _probe(cluster, "pooler-db-ready")
                return proof
        except RuntimeError:
            pass
        time.sleep(1)
    raise TimeoutError("CNPG replacement did not become ready through the Pooler route")


def wait_d5_ready(cluster, injected: dict, cancelled: threading.Event) -> dict:
    deadline = time.time() + API_RECOVERY_BOUND_S
    old_deleting = False
    old_uid_observation: dict | None = None
    quota_while_old_terminating: dict | None = None
    while time.time() < deadline:
        if cancelled.is_set():
            raise RuntimeError("injector cancelled while API recovered")
        raw = cluster.kubectl("get", "pod", injected["pod"], "-o", "json", check=False)
        if raw.strip():
            old = json.loads(raw)
            old_deleting = old.get("metadata", {}).get("uid") == injected["old_uid"] and bool(old.get("metadata", {}).get("deletionTimestamp"))
            old_uid_observation = {"uid": old.get("metadata", {}).get("uid"),
                                   "deletion_timestamp": old.get("metadata", {}).get("deletionTimestamp")}
            if old_deleting and quota_while_old_terminating is None:
                quota = json.loads(cluster.kubectl("get", "resourcequota", "tenant-envelope", "-o", "json"))
                quota_while_old_terminating = quota.get("status", {})
        try:
            replacement = api_target(cluster)
            proof = replacement_proof(injected["old_uid"], replacement, ready_at=time.time(),
                                      fault_at=injected["fault_at"], bound_s=API_RECOVERY_BOUND_S)
            if proof["uid_changed"] and proof["ready"]:
                proof["api_health_probe"] = _probe(cluster, "api-ready")
                quota = json.loads(cluster.kubectl("get", "resourcequota", "tenant-envelope", "-o", "json"))
                proof["old_uid_observed_terminating"] = old_deleting
                proof["old_uid_observation"] = old_uid_observation
                proof["quota_while_old_terminating"] = quota_while_old_terminating
                proof["quota_during_replacement"] = quota.get("status", {})
                return proof
        except RuntimeError:
            pass
        time.sleep(1)
    raise TimeoutError("API replacement did not become ready within its recovery bound")


def _timing_checks(step: dict, plan: dict, injected: dict) -> dict[str, bool]:
    fault_at = injected.get("fault_at")
    started = step.get("started")
    end = None if started is None else started + plan["warmup_s"] + step["window_s"]
    return {"fault_inside_offered_window": fault_at is not None and started is not None and started <= fault_at <= end,
            "pre_fault_request_sent": injected.get("first_request_sent_at") is not None and fault_at is not None
            and injected["first_request_sent_at"] < fault_at}


def _restart_step(cluster, ids: dict, plan: dict, disk: dict[str, str], *, rate: float, selector: Callable,
                  kind: str, ready: Callable[[dict, threading.Event], dict], preflight: Callable[[dict], dict] | None = None) -> tuple[dict | None, dict, str | None]:
    injector = RestartInjector(cluster, selector=selector, kind=kind, delay_s=plan["warmup_s"] + FAULT_AT_S,
                               ready=ready, preflight=preflight)
    injector.start()
    try:
        step = search.run_step(cluster, ids, rate, plan, plan["seed"], disk, on_event=injector.observe, cancel_driver=True)
    except Exception as error:  # noqa: BLE001 - preserve the reason; do not turn a partial run into a pass
        injector.cancel("step failed")
        return None, injector.join(10), f"{type(error).__name__}: {error}"
    if "fault_at" not in injector.state:
        injector.cancel("step ended before fault injection")
    return step, injector.join(310), None


def d3(cluster, ids: dict, plan: dict, disk: dict[str, str], *, rate: float, effects: list[dict] | Callable[[list[str]], list[dict]],
       effect_tokens: dict[str, str]) -> dict:
    """D3 primary restart during fresh append-only workload traffic."""
    cluster.verify()
    step, injected, error = _restart_step(cluster, ids, plan, disk, rate=rate, selector=cnpg_primary,
                                           kind="CNPG primary", ready=lambda injected, cancelled: wait_d3_ready(cluster, injected, cancelled))
    result = {"kind": "cnpg-primary-restart", "workload_version": RECOVERY_EFFECTS_VERSION,
              "injector": injected, "step_error": error}
    if step is None:
        return {**result, "overall": "invalid", "passed": False}
    if injected.get("status") != "ready":
        return {**result, "step": step, "overall": "invalid", "passed": False}
    missing = [request["id"] for request in step["sent"] if request["id"] not in effect_tokens]
    if missing:
        return {**result, "step": step, "overall": "invalid", "passed": False,
                "effect_token_error": f"missing predeclared recovery effect tokens for {len(missing)} requests"}
    requests = [{**request, "effect_token": effect_tokens[request["id"]]} for request in step["sent"]]
    effect_rows = effects([request["id"] for request in requests]) if callable(effects) else effects
    effects_result = effect_assessment(requests, step["outcomes"]["rows"], effect_rows)
    ready_at = injected.get("ready_at")
    deadlines = (recovery_deadlines(requests, step["outcomes"]["rows"], ready_at, plan["deadlines_s"])
                 if isinstance(ready_at, (int, float)) else None)
    timing = _timing_checks(step, plan, injected)
    passed = (injected.get("status") == "ready" and all(timing.values())
              and bool(injected.get("within_bound")) and effects_result["terminal_and_loss"]
              and effects_result["strict_effects"] and deadlines is not None and deadlines["recovery_deadline"])
    return {**result, "step": step, "effects": effects_result, "deadlines": deadlines, "timing_checks": timing,
            "passed": passed, "overall": "passed" if passed else "failed"}


def d5(cluster, ids: dict, plan: dict, disk: dict[str, str], *, rate: float) -> dict:
    """D5 API restart during open-loop load, with UID, health, and quota evidence."""
    cluster.verify()
    step, injected, error = _restart_step(cluster, ids, plan, disk, rate=rate, selector=api_target,
                                           kind="API", preflight=lambda target: api_restart_preflight(cluster, target),
                                           ready=lambda injected, cancelled: wait_d5_ready(cluster, injected, cancelled))
    result = {"kind": "api-restart", "injector": injected, "step_error": error,
              "recovery_bound_seconds": API_RECOVERY_BOUND_S}
    if step is None:
        return {**result, "overall": "invalid", "passed": False}
    if injected.get("status") != "ready":
        return {**result, "step": step, "overall": "invalid", "passed": False}
    timing = _timing_checks(step, plan, injected)
    passed = injected.get("status") == "ready" and bool(injected.get("within_bound")) and all(timing.values())
    return {**result, "step": step, "timing_checks": timing, "passed": passed,
            "overall": "passed" if passed else "failed"}
