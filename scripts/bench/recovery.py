"""Bounded recovery-drill evidence built on the existing benchmark harness.

This module owns only the recovery-specific proof.  Arrival generation,
ordinary send-time SLOs, durable outcomes, and host validity stay in
``search.run_step``.  It deliberately retains raw evidence instead of
rewriting a failed SDK operation as a passed recovery result.
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass

import guard
import search


RECOVERY_EFFECTS_VERSION = "recovery-effects-v1"
API_RECOVERY_BOUND_S = 60.0
OWNER_LEASE_S = 20.0
ONE_SWEEP_S = 15.0  # api/src/worker/pool_main.py SWEEP_SECONDS
WORKER_RECOVERY_BOUND_S = OWNER_LEASE_S + ONE_SWEEP_S
PRIMARY_LABELS = {"cnpg.io/cluster": "bifrost-db", "cnpg.io/instanceRole": "primary"}
API_LABELS = {"app.kubernetes.io/name": "bifrost-api"}


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
    found = json.loads(cluster.kubectl("get", "pods", "-l", selector, "-o", "json"))["items"]
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
    terminal_and_loss = True
    strict_effects = True
    for request in requests:
        request_id = request["id"]
        rows = durable.get(request_id, [])
        accepted = bool(rows) or (isinstance(request.get("http_status"), int) and 200 <= request["http_status"] < 300)
        records = appended.get(request_id, [])
        expected = request.get("effect_token")
        matching = [row for row in records if row.get("effect_token") == expected]
        terminal = len(rows) == 1 and bool(rows[0].get("terminal"))
        effect_ok = not accepted or (len(matching) == 1 and len(records) == 1)
        if accepted and not terminal:
            terminal_and_loss = False
        if not effect_ok:
            strict_effects = False
        per_request[request_id] = {
            "accepted": accepted, "durable_entities": rows, "effects": records,
            "effect_token": expected, "effect_count": len(records), "matching_effect_count": len(matching),
            "terminal": terminal, "strict_effect": effect_ok,
        }
    return {"per_request": per_request, "terminal_and_loss": terminal_and_loss, "strict_effects": strict_effects}


def recovery_deadlines(requests: list[dict], rows: list[dict], db_ready_at: float, deadlines_s: dict[str, float]) -> dict:
    """Preserve normal send-time SLOs and D3's post-ready recovery clock."""
    by_id = {row["request_id"]: row for row in rows}
    evidence: dict[str, dict] = {}
    normal = recovery = True
    for request in requests:
        row = by_id.get(request["id"])
        sent = request.get("sent")
        if not isinstance(sent, (int, float)) or row is None:
            normal = recovery = False
            evidence[request["id"]] = {"normal_deadline_met": False, "recovery_deadline_met": False}
            continue
        deadline = sent + deadlines_s[request["class"]]
        completed = row.get("completed")
        terminal = bool(row.get("terminal")) and isinstance(completed, (int, float))
        normal_ok = terminal and completed <= deadline
        recovery_deadline = max(deadline, db_ready_at + deadlines_s[request["class"]]) if sent < db_ready_at else deadline
        recovery_ok = terminal and completed <= recovery_deadline
        normal &= normal_ok
        recovery &= recovery_ok
        evidence[request["id"]] = {"normal_deadline": deadline, "normal_deadline_met": normal_ok,
                                    "recovery_deadline": recovery_deadline, "recovery_deadline_met": recovery_ok}
    return {"per_request": evidence, "normal_send_time_slo": normal, "recovery_deadline": recovery}


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


def worker_recovery_assessment(kill: dict, worker_after: dict | None, replacement: dict | None, *, observed_at: float) -> dict:
    """D6's explicit lost-path/replacement-admission result, never inferred from a pod list alone."""
    lost = worker_after is not None and worker_after.get("state") == "lost"
    replacement_ready = replacement is not None and replacement.get("state") == "ready"
    elapsed = observed_at - kill["killed_at"]
    return {"lost_path_observed": lost, "worker_after": worker_after, "replacement": replacement,
            "replacement_admitted": replacement_ready, "observed_after_seconds": elapsed,
            "loss_within_lease_plus_one_sweep": lost and elapsed <= kill["loss_bound_s"],
            "bound": {"owner_lease_seconds": OWNER_LEASE_S, "one_sweep_seconds": ONE_SWEEP_S,
                      "total_seconds": kill["loss_bound_s"]}}


def d3(cluster, ids: dict, plan: dict, disk: dict[str, str], *, rate: float, effects: list[dict]) -> dict:
    """One D3 frame; caller supplies fresh recovery-workload IDs and effects after reconciliation."""
    cluster.verify()
    target = cnpg_primary(cluster)
    injection = delete_exact(cluster, target, cnpg_primary, kind="CNPG primary")
    step = search.run_step(cluster, ids, rate, plan, plan["seed"], disk)
    assessment = effect_assessment(step["sent"], step["outcomes"]["rows"], effects)
    return {"kind": "cnpg-primary-restart", "workload_version": RECOVERY_EFFECTS_VERSION,
            "target": injection, "step": step, "effects": assessment,
            "passed": False, "claim": "unresolved until replacement readiness and recovery deadlines are captured"}


def d5(cluster, ids: dict, plan: dict, disk: dict[str, str], *, rate: float) -> dict:
    """One D5 restart frame; readiness evidence is supplied by the caller's probe loop."""
    cluster.verify()
    target = api_target(cluster)
    preflight = api_restart_preflight(cluster, target)
    injection = delete_exact(cluster, target, api_target, kind="API")
    step = search.run_step(cluster, ids, rate, plan, plan["seed"], disk)
    return {"kind": "api-restart", "target": injection, "preflight": preflight, "step": step,
            "recovery_bound_seconds": API_RECOVERY_BOUND_S, "passed": False,
            "claim": "unresolved until replacement UID/readiness and terminating-pod quota evidence are captured"}
