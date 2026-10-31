#!/usr/bin/env python3
"""Guarded correctness evidence for methodology §8 drills D7--D9.

These are deliberately separate from capacity runs.  Every helper returns
retained evidence; a missing platform hook is ``not_exercised`` or invalid,
never a synthetic passing result.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "incluster"))

import guard  # noqa: E402
import recovery  # noqa: E402
import run as bench_run  # noqa: E402
import deploy  # noqa: E402
import browser_preparation  # noqa: E402
from cluster import Cluster  # noqa: E402


LOCK_PATH = Path("/tmp/bifrost-capacity-host.lock")
D7_HEARTBEAT_SECONDS = 30.0
D7_CONTROLLER_SWEEP_SECONDS = 5.0
D8_CANCEL_BOUND_SECONDS = 30.0
D9_CONVERGENCE_SECONDS = 15.0
CONTROLLER_SOURCE = "api/src/services/worker_pool/controller.py"
LIVENESS_SOURCE = "api/src/services/worker_pool/liveness.py"
EXTENDED_DRIVER = HERE / "incluster" / "recovery_extended_driver.py"


@contextmanager
def host_lock() -> Iterator[None]:
    with open(LOCK_PATH, "a+", encoding="utf-8") as file:
        try:
            fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("another capacity benchmark holds the host lock") from error
        try:
            yield
        finally:
            fcntl.flock(file.fileno(), fcntl.LOCK_UN)


def d7_assessment(evidence: dict) -> dict:
    """Gate D7 only when the stale attempt ran after a verified isolation."""
    process_alive = evidence.get("process_alive_before_release") is True
    isolated = evidence.get("netns_connect_failed") is True and evidence.get("drop_installed") is True
    lost_db_at, heartbeat_at = evidence.get("lost_db_at"), evidence.get("last_db_heartbeat_at")
    held = isinstance(lost_db_at, (int, float)) and isinstance(heartbeat_at, (int, float)) and (
        lost_db_at - heartbeat_at >= D7_HEARTBEAT_SECONDS
    )
    stale = evidence.get("stale") if isinstance(evidence.get("stale"), dict) else {}
    stored_unchanged = stale.get("stored_failed_result_unchanged") is True
    rejected = stale.get("renewal_rejected_log") is True and stale.get("completion_rejected_log") is True
    effects = evidence.get("effects") if isinstance(evidence.get("effects"), dict) else {}
    strict_effect = (effects.get("count") == 1 and effects.get("append_after_gate") is True
                     and effects.get("no_replay_started") is True)
    slot_released = evidence.get("slot_released") is True
    if not process_alive:
        return {"overall": "not_exercised", "reason": "stale attempt not exercised: target process exited before drop removal and gate release",
                "checks": {"process_alive_before_release": False}, "evidence": evidence}
    checks = {"isolation_verified": isolated, "held_past_db_heartbeat": held,
              "stale_renewal_and_completion_rejected": rejected, "stored_failed_result_unchanged": stored_unchanged,
              "one_effect_after_gate": strict_effect, "slot_released": slot_released}
    return {"overall": "passed" if all(checks.values()) else "failed", "checks": checks,
            "bound": {"heartbeat_seconds": D7_HEARTBEAT_SECONDS, "controller_sweep_seconds": D7_CONTROLLER_SWEEP_SECONDS,
                      "liveness_source": LIVENESS_SOURCE, "controller_source": CONTROLLER_SOURCE}}


def d8_assessment(before_workers: list[dict], after_workers: list[dict], cancellation: dict) -> dict:
    """D8 preserves the HTTP/durable acceptance boundary during DB unavailability."""
    before_ids = {worker.get("id") for worker in before_workers if worker.get("state") == "ready"}
    newly_lost = [worker for worker in after_workers if worker.get("id") in before_ids and worker.get("state") == "lost"]
    accepted = cancellation.get("http_accepted") is True or cancellation.get("durable_accepted") is True
    accepted_at = cancellation.get("accepted_at")
    ready_at = cancellation.get("db_ready_at")
    terminal_at = cancellation.get("terminal_at")
    clock = accepted_at if isinstance(accepted_at, (int, float)) else ready_at
    clock_label = "acceptance" if isinstance(accepted_at, (int, float)) else "db-recovery"
    terminal_in_bound = (accepted and isinstance(clock, (int, float)) and isinstance(terminal_at, (int, float))
                         and 0 <= terminal_at - clock <= D8_CANCEL_BOUND_SECONDS)
    retry = cancellation.get("retry")
    retry_ok = retry is None or (not accepted and isinstance(retry, dict) and retry.get("new_request_receipt") is True)
    checks = {"no_healthy_worker_lost_after_shared_outage": not newly_lost,
              "cancellation_terminal_within_bound": terminal_in_bound, "retry_boundary_honest": retry_ok}
    return {"overall": "passed" if all(checks.values()) else "failed", "checks": checks,
            "newly_lost_healthy_workers": newly_lost, "cancellation_clock": clock_label,
            "bound_seconds": D8_CANCEL_BOUND_SECONDS,
            "submitted_unaccepted": not accepted}


def d9_assessment(bindings: dict[str, str], jobs: dict[str, str], policy: dict, controller_ack: object) -> dict:
    """Evaluate controller convergence without inventing an elected pool leader."""
    orphan_jobs = sorted(name for name in jobs if name not in bindings)
    uid_mismatch = sorted(name for name, uid in jobs.items() if bindings.get(name) not in (None, uid))
    convergence = policy.get("converged_at")
    changed_at = policy.get("changed_at")
    policy_ok = isinstance(convergence, (int, float)) and isinstance(changed_at, (int, float)) and convergence - changed_at <= D9_CONVERGENCE_SECONDS
    duplicate_bindings = policy.get("duplicate_job_bindings")
    no_duplicates = isinstance(duplicate_bindings, list) and not duplicate_bindings
    checks = {"no_orphan_jobs": not orphan_jobs, "no_changed_job_uid_bindings": not uid_mismatch,
              "no_duplicate_job_bindings": no_duplicates,
              "policy_converged": policy_ok, "per_replica_ack": controller_ack if isinstance(controller_ack, bool) else None}
    required = [checks["no_orphan_jobs"], checks["no_changed_job_uid_bindings"], checks["no_duplicate_job_bindings"], checks["policy_converged"]]
    overall = "passed" if all(required) else "failed"
    return {"overall": overall, "checks": checks, "orphan_jobs": orphan_jobs, "uid_mismatch": uid_mismatch,
            "duplicate_job_bindings": duplicate_bindings,
            "controller_model": "all live worker-pool controllers reconcile; no elected pool leader exists",
            "per_replica_ack": "unmeasured" if not isinstance(controller_ack, bool) else controller_ack,
            "bound_seconds": D9_CONVERGENCE_SECONDS}


def _worker_container_pid(cluster: Cluster, owned: dict, pod: dict) -> tuple[str, int]:
    if pod.get("metadata", {}).get("uid") != owned.get("pod_uid") or pod.get("spec", {}).get("nodeName") != cluster.identity.node_name:
        raise RuntimeError("D7 worker target changed or is outside the guarded Kind node")
    status = [item for item in pod.get("status", {}).get("containerStatuses", []) if item.get("name") == "worker"]
    if len(status) != 1 or not isinstance(status[0].get("containerID"), str):
        raise RuntimeError("D7 worker target has no exact worker container")
    container_id = status[0]["containerID"].split("://", 1)[-1]
    inspected = subprocess.run(["docker", "exec", cluster.identity.node_name, "crictl", "inspect", container_id], check=True, capture_output=True, text=True)
    pid = json.loads(inspected.stdout).get("info", {}).get("pid")
    if not isinstance(pid, int) or pid <= 1:
        raise RuntimeError("D7 crictl inspect has no safe worker process PID")
    return container_id, pid


def d7_drop_commands(node: str, pid: int, chain: str) -> dict:
    """Exact namespace-local drop/install cleanup commands; no SIGSTOP fallback."""
    if not chain.startswith("BIFROST_D7_") or len(chain) > 29:
        raise ValueError("D7 chain must be a bounded harness-owned name")
    prefix = ["docker", "exec", node, "nsenter", "-t", str(pid), "-n", "iptables"]
    return {"install": [prefix + ["-N", chain], prefix + ["-A", "OUTPUT", "!", "-o", "lo", "-j", chain],
                        prefix + ["-A", "INPUT", "!", "-i", "lo", "-j", chain], prefix + ["-A", chain, "-j", "DROP"]],
            "cleanup": [prefix + ["-D", "INPUT", "!", "-i", "lo", "-j", chain], prefix + ["-D", "OUTPUT", "!", "-o", "lo", "-j", chain],
                        prefix + ["-F", chain], prefix + ["-X", chain]]}


def d7_chain(request_id: str) -> str:
    return "BIFROST_D7_" + hashlib.sha256(request_id.encode()).hexdigest()[:12]


def _fake_api_ip(cluster: Cluster) -> str:
    service = json.loads(cluster.kubectl("get", "service", "bench-fake-api", "-o", "json", namespace=guard.INFRA_NS))
    value = service.get("spec", {}).get("clusterIP")
    if not isinstance(value, str) or value in {"", "None"} or any(part == "" for part in value.split(".")):
        raise RuntimeError("D7 fake API Service has no numeric cluster IP")
    try:
        import ipaddress
        ipaddress.ip_address(value)
    except ValueError as error:
        raise RuntimeError("D7 fake API Service IP is invalid") from error
    return value


def _worker_socket_probe(cluster: Cluster, pod: dict, numeric_ip: str) -> dict:
    """Structured socket proof from the target worker container, never the node image."""
    name = pod.get("metadata", {}).get("name")
    if not isinstance(name, str) or not name:
        raise RuntimeError("D7 worker pod lacks a name")
    script = f'''import json
import socket
ip = {numeric_ip!r}
result = {{"connected": False, "timeout": False, "errno": None}}
try:
    sock = socket.create_connection((ip, 8080), timeout=3)
    sock.close()
    result["connected"] = True
except socket.timeout:
    result["timeout"] = True
except OSError as error:
    result["errno"] = error.errno
print(json.dumps(result, sort_keys=True))
'''
    raw = cluster.kubectl("exec", name, "-c", "worker", "--", "python", "-c", script, namespace=guard.TENANT_NS)
    try:
        proof = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as error:
        raise RuntimeError("D7 worker socket probe did not return structured JSON") from error
    if not isinstance(proof, dict) or type(proof.get("connected")) is not bool or type(proof.get("timeout")) is not bool:
        raise RuntimeError("D7 worker socket probe returned an invalid shape")
    if proof.get("errno") is not None and type(proof["errno"]) is not int:
        raise RuntimeError("D7 worker socket probe returned a nonnumeric errno")
    return proof


def _cleanup_drop_commands(commands: dict) -> list[dict]:
    cleanup = commands.get("cleanup")
    if not isinstance(cleanup, list):
        raise RuntimeError("D7 cleanup commands are malformed")
    outcomes = []
    for command in cleanup:
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        outcomes.append({"command": command[-3:], "returncode": completed.returncode})
    return outcomes


def install_d7_worker_drop(cluster: Cluster, owned: dict, pod: dict, request_id: str) -> dict:
    """Install one worker-netns-only rule and prove non-loopback connect fails.

    The caller must invoke :func:`remove_d7_worker_drop` in ``finally``.  No
    broad node rule, traffic pause, or process signal is accepted as D7.
    """
    cluster.verify()
    fresh_owned, fresh_pod = recovery.busy_worker(cluster)
    if (fresh_owned.get("worker_id"), fresh_owned.get("pod_uid")) != (owned.get("worker_id"), owned.get("pod_uid")):
        raise RuntimeError("D7 durable worker target changed before network isolation")
    pod = fresh_pod
    numeric_ip = _fake_api_ip(cluster)
    pre_probe = _worker_socket_probe(cluster, pod, numeric_ip)
    if pre_probe != {"connected": True, "timeout": False, "errno": None}:
        raise RuntimeError("D7 pre-isolation worker socket probe did not connect to fake API")
    container_id, pid = _worker_container_pid(cluster, owned, pod)
    chain = d7_chain(request_id)
    commands = d7_drop_commands(cluster.identity.node_name, pid, chain)
    installed = []
    try:
        for command in commands["install"]:
            subprocess.run(command, check=True)
            installed.append(command)
        post_probe = _worker_socket_probe(cluster, pod, numeric_ip)
        failed_connect = post_probe.get("timeout") is True or isinstance(post_probe.get("errno"), int)
        if post_probe.get("connected") is True or not failed_connect:
            raise RuntimeError("D7 worker socket probe did not prove packet drop")
    except BaseException:
        cleanup = _cleanup_drop_commands(commands) if installed else []
        if cleanup and any(item["returncode"] != 0 for item in cleanup):
            raise RuntimeError(f"D7 partial rule cleanup failed: {cleanup}")
        raise
    return {"worker_id": owned.get("worker_id"), "pod_uid": owned.get("pod_uid"), "container_id": container_id,
            "pid": pid, "chain": chain, "commands": commands, "fake_api_ip": numeric_ip,
            "pre_probe": pre_probe, "post_probe": post_probe, "drop_installed": True, "netns_connect_failed": True}


def remove_d7_worker_drop(cluster: Cluster, pid: int, commands: dict) -> dict:
    """Remove only the chain D7 created, retaining each cleanup command's outcome."""
    cluster.verify()
    outcomes = _cleanup_drop_commands(commands)
    alive = subprocess.run(["docker", "exec", cluster.identity.node_name, "kill", "-0", str(pid)], check=False).returncode == 0
    namespace_alive = subprocess.run(["docker", "exec", cluster.identity.node_name, "nsenter", "-t", str(pid), "-n", "true"], check=False).returncode == 0
    if any(item["returncode"] != 0 for item in outcomes):
        if not alive and not namespace_alive:
            return {"removed": False, "cleanup": outcomes, "process_alive_before_release": False,
                    "namespace_gone": True, "reason": "worker exited before scoped DROP cleanup"}
        raise RuntimeError(f"D7 drop cleanup failed while worker namespace remained: {outcomes}")
    if not alive or not namespace_alive:
        return {"removed": True, "cleanup": outcomes, "process_alive_before_release": False,
                "namespace_gone": True, "reason": "worker exited before gate release"}
    return {"removed": True, "cleanup": outcomes, "process_alive_before_release": alive}


def _driver(cluster: Cluster, mode: str, args: dict, timeout_s: int = 300) -> dict:
    return cluster.exec_python("bench-driver", EXTENDED_DRIVER, [mode, json.dumps(args)], timeout_s=timeout_s)


def _wait_d7_owner(cluster: Cluster, durable_id: str, timeout_s: float = 60.0) -> tuple[dict, dict]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            owned, pod = recovery.busy_worker(cluster)
            if owned.get("execution_id") == durable_id:
                return owned, pod
        except RuntimeError:
            pass
        time.sleep(1)
    raise RuntimeError("D7 did not observe the gated request on one durable worker")


def _wait_d7_lost(cluster: Cluster, worker_id: str, timeout_s: float) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        rows = cluster.driver_sql(recovery.WORKER_STATE_SQL)
        if not isinstance(rows, list):
            raise RuntimeError("D7 worker state query did not return a list")
        lost = next((row for row in rows if row.get("id") == worker_id and row.get("state") == "lost"), None)
        if isinstance(lost, dict):
            return lost
        time.sleep(1)
    raise RuntimeError("D7 worker was not marked lost after heartbeat timeout plus sweep")


def _target_logs(cluster: Cluster, pod: dict, since_s: int) -> str:
    name = pod.get("metadata", {}).get("name")
    if not isinstance(name, str):
        raise RuntimeError("D7 target pod has no name for stale-attempt logs")
    return cluster.kubectl("logs", name, "-c", "worker", f"--since={since_s}s")


def run_d7(cluster: Cluster) -> dict:
    """One fresh D7 gated request; cleanup is retained even when evidence fails."""
    fixture = _driver(cluster, "prepare", {}, 600)
    workflow_id = fixture.get("workflow", {}).get("wf-recovery-gated-io")
    if not isinstance(workflow_id, str):
        raise RuntimeError("D7 fixture did not return its gated workflow ID")
    request_id, gate = uuid.uuid4().hex, f"d7-{uuid.uuid4().hex}"
    submitted = _driver(cluster, "start", {"workflow_id": workflow_id, "request_id": request_id, "gate": gate})
    durable_id = submitted.get("durable_id")
    if not isinstance(durable_id, str) or not durable_id:
        return {"overall": "not_exercised", "reason": "gated D7 submission was not HTTP-accepted with one durable ID", "submitted": submitted}
    owned, pod = _wait_d7_owner(cluster, durable_id)
    installed: dict | None = None
    removal: dict | None = None
    try:
        installed = install_d7_worker_drop(cluster, owned, pod, request_id)
        lost = _wait_d7_lost(cluster, owned["worker_id"], D7_HEARTBEAT_SECONDS + D7_CONTROLLER_SWEEP_SECONDS + 10)
    finally:
        if installed is not None:
            removal = remove_d7_worker_drop(cluster, installed["pid"], installed["commands"])
    if removal is None or removal.get("process_alive_before_release") is not True:
        return d7_assessment({"process_alive_before_release": False, "submitted": submitted, "target": owned,
                              "isolation": installed, "removal": removal})
    before_release = _driver(cluster, "outcome", {"request_id": request_id})
    released = _driver(cluster, "release", {"gate": gate})
    deadline = time.monotonic() + D8_CANCEL_BOUND_SECONDS + 10
    effects = _driver(cluster, "effects", {"request_id": request_id})
    outcome = _driver(cluster, "outcome", {"request_id": request_id})
    while time.monotonic() < deadline:
        rows = outcome.get("rows") if isinstance(outcome.get("rows"), list) else []
        effect_rows = effects.get("rows") if isinstance(effects.get("rows"), list) else []
        if len(effect_rows) == 1 and len(rows) == 1 and rows[0].get("completed_at") is not None:
            break
        time.sleep(1)
        effects = _driver(cluster, "effects", {"request_id": request_id})
        outcome = _driver(cluster, "outcome", {"request_id": request_id})
    rows = outcome.get("rows") if isinstance(outcome.get("rows"), list) else []
    logs = _target_logs(cluster, pod, int(D7_HEARTBEAT_SECONDS + D7_CONTROLLER_SWEEP_SECONDS + 30))
    before_rows = before_release.get("rows") if isinstance(before_release.get("rows"), list) else []
    stale = {"stored_failed_result_unchanged": len(before_rows) == 1 and before_rows == rows and before_rows[0].get("completed_at") is not None,
             "before_release_outcome": before_release,
             "renewal_rejected_log": "stale" in logs.lower() and "renew" in logs.lower(),
             "completion_rejected_log": "stale" in logs.lower() and "completion" in logs.lower(), "raw_log": logs[-4000:]}
    evidence = {**installed, **removal, "last_db_heartbeat_at": lost.get("heartbeat_at_epoch"),
                "lost_db_at": lost.get("observed_db_epoch"), "slot_released": recovery.worker_slot_release(cluster, owned["worker_id"])["released"],
                "stale": stale, "effects": {"count": len(effects.get("rows", [])), "append_after_gate": released.get("released") is True,
                                                  "no_replay_started": len(rows) == 1}, "submitted": submitted, "release": released,
                "outcome": outcome, "target": {"owned": owned, "pod": pod}}
    return {**d7_assessment(evidence), "evidence": evidence}


def run_d8(cluster: Cluster) -> dict:
    """Restart CNPG while one gated request runs, then preserve cancel acceptance evidence."""
    fixture = _driver(cluster, "prepare", {}, 600)
    workflow_id = fixture.get("workflow", {}).get("wf-recovery-gated-io")
    if not isinstance(workflow_id, str):
        raise RuntimeError("D8 fixture did not return its gated workflow ID")
    request_id, gate = uuid.uuid4().hex, f"d8-{uuid.uuid4().hex}"
    submitted = _driver(cluster, "start", {"workflow_id": workflow_id, "request_id": request_id, "gate": gate})
    durable_id = submitted.get("durable_id")
    if not isinstance(durable_id, str) or not durable_id:
        return {"overall": "not_exercised", "reason": "D8 long request did not create a durable execution", "submitted": submitted}
    owned, _pod = _wait_d7_owner(cluster, durable_id)
    before = cluster.driver_sql(recovery.WORKER_STATE_SQL)
    if not isinstance(before, list):
        raise RuntimeError("D8 worker pre-outage state was not a list")
    target = recovery.cnpg_primary(cluster)
    injected = recovery.delete_exact(cluster, target, recovery.cnpg_primary, kind="D8 CNPG primary")
    first_attempt = _driver(cluster, "cancel", {"durable_id": durable_id}, 60)
    import threading
    ready = recovery.wait_d3_ready(cluster, injected, threading.Event())
    attempts = [first_attempt]
    if not first_attempt["http_accepted"]:
        attempts.append(_driver(cluster, "cancel", {"durable_id": durable_id}, 60))
    accepted_attempt = next((attempt for attempt in attempts if attempt.get("http_accepted") is True), None)
    cancellation = {"attempts": attempts, "db_ready_at": ready.get("ready_at"),
                    "http_accepted": accepted_attempt is not None, "durable_accepted": accepted_attempt is not None,
                    "accepted_at": accepted_attempt.get("responded_at") if accepted_attempt else None}
    if len(attempts) == 2:
        cancellation["retry"] = {"new_request_receipt": False, "receipt": attempts[1],
                                 "reason": "second cancel targets the same durable execution; no new request was submitted"}
    _driver(cluster, "release", {"gate": gate})
    deadline = time.monotonic() + D8_CANCEL_BOUND_SECONDS + 10
    outcome = _driver(cluster, "outcome", {"request_id": request_id})
    while time.monotonic() < deadline and not any(row.get("completed_at") for row in outcome.get("rows", [])):
        time.sleep(1)
        outcome = _driver(cluster, "outcome", {"request_id": request_id})
    rows = outcome.get("rows", [])
    cancellation["terminal_at"] = rows[0].get("completed_at") if len(rows) == 1 else None
    after = cluster.driver_sql(recovery.WORKER_STATE_SQL)
    if not isinstance(after, list):
        raise RuntimeError("D8 worker post-outage state was not a list")
    return {**d8_assessment(before, after, cancellation), "submitted": submitted, "fault": injected,
            "db_ready": ready, "cancellation": cancellation, "outcome": outcome}


def _ready_api_pods(cluster: Cluster) -> list[dict]:
    pods = json.loads(cluster.kubectl("get", "pods", "-l", "app.kubernetes.io/name=bifrost-api", "-o", "json"))["items"]
    ready = [pod for pod in pods if pod.get("metadata", {}).get("deletionTimestamp") is None and recovery._ready(pod, "api")]
    return ready


def _api_pods(cluster: Cluster) -> list[dict]:
    ready = _ready_api_pods(cluster)
    if len(ready) != 2:
        raise RuntimeError(f"D9 requires exactly two ready API replicas, found {len(ready)}")
    return ready


def _controller_registry(cluster: Cluster, pod: dict) -> list[dict]:
    name = pod["metadata"]["name"]
    script = """import asyncio,json
from src.core.cache.redis_client import get_redis
from src.services.worker_pool.controller import CONTROLLERS_KEY
async def main():
 async with get_redis() as redis:
  rows=await redis.hgetall(CONTROLLERS_KEY)
 print(json.dumps(sorted([{'id':(k.decode() if isinstance(k,bytes) else k),'value':(v.decode() if isinstance(v,bytes) else v)} for k,v in rows.items()],key=lambda x:x['id'])))
asyncio.run(main())"""
    raw = cluster.kubectl("exec", name, "-c", "api", "--", "python", "-c", script)
    rows = json.loads(raw)
    if not isinstance(rows, list):
        raise RuntimeError("D9 controller registry did not return a list")
    return rows


def _job_bindings(cluster: Cluster) -> tuple[dict[str, str], dict[str, str | None], list[str]]:
    jobs = json.loads(cluster.kubectl("get", "jobs", "-o", "json"))["items"]
    active = {job["metadata"]["name"]: job["metadata"]["uid"] for job in jobs if not job.get("status", {}).get("succeeded") and not job.get("status", {}).get("failed")}
    rows = cluster.driver_sql("select coalesce(json_agg(json_build_object('job_name', kubernetes_job_name, 'job_uid', kubernetes_job_uid) order by id), '[]'::json) from execution_workers where form='job' and kubernetes_job_name is not null")
    if not isinstance(rows, list):
        raise RuntimeError("D9 worker-job binding query did not return a list")
    bindings, duplicates = {}, []
    for row in rows:
        name = row.get("job_name")
        if not isinstance(name, str):
            continue
        if name in bindings:
            duplicates.append(name)
        else:
            bindings[name] = row.get("job_uid")
    return active, bindings, duplicates


def _policy(cluster: Cluster, action: str, values: dict | None = None) -> dict:
    response = cluster.exec_python("bench-driver", HERE / "incluster" / "interactive_app.py", ["policy", json.dumps({"action": action, "values": values})])
    policy = response.get("policy")
    if not isinstance(policy, dict):
        raise RuntimeError("D9 policy helper did not return a policy")
    return policy


def run_d9(cluster: Cluster) -> dict:
    """D9 uses two simultaneous controllers; it never assumes one elected pool leader."""
    pods = _api_pods(cluster)
    target = pods[0]
    registry_before = _controller_registry(cluster, target)
    jobs_before, bindings_before, duplicates_before = _job_bindings(cluster)
    original = _policy(cluster, "read")
    try:
        changed_at = time.time()
        changed = _policy(cluster, "update", {"max_workers": 1})
        converged_at = None  # policy API receipt is not controller convergence
        def selected_ready_api(_cluster: Cluster) -> dict:
            current = [pod for pod in _ready_api_pods(_cluster) if pod.get("metadata", {}).get("uid") == target["metadata"]["uid"]]
            if len(current) != 1:
                raise RuntimeError("D9 selected ready API pod changed before deletion")
            return current[0]
        fault = recovery.delete_exact(cluster, target, selected_ready_api, kind="D9 selected ready API pod")
        deadline = time.monotonic() + D9_CONVERGENCE_SECONDS
        replacement = None
        original_uids = {pod["metadata"]["uid"] for pod in pods}
        while time.monotonic() < deadline:
            current = _ready_api_pods(cluster)
            replacement = next((pod for pod in current if pod.get("metadata", {}).get("uid") not in original_uids), None)
            if len(current) != 2:
                replacement = None
            if replacement is not None:
                break
            time.sleep(1)
        if replacement is None:
            raise TimeoutError("D9 selected ready API pod did not receive a replacement within 15 seconds")
        registry_after = _controller_registry(cluster, replacement)
        jobs_after, bindings_after, duplicates_after = _job_bindings(cluster)
    finally:
        restored = _policy(cluster, "update", browser_preparation.restore_policy(original))
    observed = d9_assessment(bindings_after, jobs_after, {"changed_at": changed_at, "converged_at": converged_at,
        "duplicate_job_bindings": sorted(set(duplicates_before + duplicates_after)), "before_jobs": jobs_before,
        "before_bindings": bindings_before, "changed_policy": changed, "restored_policy": restored}, None)
    return {**observed, "overall": "not_exercised", "reason": "targeted active-controller proof is unmeasured: controller registry has no API-pod identity",
            "selected_ready_api_pod": {"name": target["metadata"]["name"], "uid": target["metadata"]["uid"]},
            "replacement_api_pod": {"name": replacement["metadata"]["name"], "uid": replacement["metadata"]["uid"]},
            "registry_before": registry_before, "registry_after": registry_after,
            "controller_model": "all live worker-pool controllers reconcile; no elected pool leader exists",
            "per_replica_ack": "unmeasured", "policy_convergence": "unmeasured: policy API receipt is not per-controller application evidence",
            "fault": fault}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=("d7", "d8", "d9"), required=True)
    parser.add_argument("--profile", help="D7/D8 profile (defaults to m); D9 always uses m-ha")
    parser.add_argument("--api-image", required=True, help="immutable local candidate API image selected before deployment")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise ValueError(f"extended recovery output already exists: {args.out}")
    with host_lock():
        cluster = Cluster()
        cluster.verify()
        profile_name = "m-ha" if args.kind == "d9" else args.profile or "m"
        profile = bench_run.load_profile(profile_name)
        current = bench_run.Run(cluster, "recovery-drill", profile_name, profile, "fake", args.out, args.api_image)
        result = current.result
        result.update({"drill": args.kind, "api_pod_changes": None, "status": "running", "stage": "created",
                       "api_image_selected": args.api_image})
        current.save()  # immutable environment/image checkpoint before reset or deploy
        failure: BaseException | None = None
        try:
            result["stage"] = "reset-before-setup"
            current.save()
            deploy.reset(cluster)
            bench_run._prepare(cluster, current, profile, "fake", args.api_image)
            result["stage"] = "fault"
            current.save()
            if args.kind == "d7":
                result["result"] = run_d7(cluster)
            elif args.kind == "d8":
                result["result"] = run_d8(cluster)
            else:
                result["result"] = run_d9(cluster)
            result["status"] = result["result"]["overall"]
        except BaseException as error:
            failure = error
            result.update({"status": "invalid", "error": f"{type(error).__name__}: {error}"})
        finally:
            try:
                deploy.reset(cluster)
                result["cleanup"] = {"verified": True}
            except BaseException as error:
                result["cleanup"] = {"verified": False, "error": f"{type(error).__name__}: {error}"}
                result["status"] = "invalid"
                failure = failure or error
            result["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            current.save()
        if failure is not None:
            raise failure


if __name__ == "__main__":
    main()
