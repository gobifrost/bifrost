#!/usr/bin/env python3
"""Failure drills for on-demand worker Jobs in local Kind (#769).

Usage: ./test.sh kubernetes pool-drills [--out DIR] [--only NAME ...]

The drills exercise worker Jobs, so the static executors (the scheduler and
worker Deployments) are scaled to zero for their duration and restored
afterwards; the API keeps the warm minimum of Jobs. Idle time
(Settings -> Kubernetes) is shortened to 45 s for the run, and every drill
that changes Settings restores what it changed.

- kill: SIGKILL a worker's process mid-run on its node. The worker is marked
  lost after missing heartbeats and its Job deleted; work accepted while it
  is dead goes to a new worker; the interrupted run is not replayed.
- controller_restart: delete every API pod during a burst. The restarted
  controllers adopt existing Jobs (one Job per executor, none duplicated)
  and the burst completes.
- oom: a run that exceeds the pod's memory limit ends visibly; the pool
  keeps serving other runs.
- unschedulable: workers that cannot be scheduled (the warm minimum
  included) are marked lost at their start deadline and replaced within the
  ceiling; queued work waits visibly. Worker memory is set in Settings; the
  memory request and start timeout are deployment env (an API restart).
- upgrade: move the API image's tag to a new build (as a new ``:latest``
  does) while the warm worker runs a run longer than the shutdown grace
  (#1052). One replacement on the new build becomes ready before the old
  worker drains as ``rotated``; a new run is served at once, the long run
  succeeds, the old worker retires, and nothing is lost. Then roll back the
  same way.
- sized_isolated_build: with isolated jobs on, an App deploy whose learned
  memory requirement (2.5 GiB) exceeds a standard pod's budget gets a sized
  pod: memory request equal to its limit, above the 2 GiB standard, and the
  build succeeds.
- max_workers_setting: with Max workers 2 in Settings a backlog holds the
  pool at 2; raising it to 4 lets the pool grow past 2 with no API restart.
- isolation_off: turning isolated jobs off lets the running isolated build
  finish in its pod, and moves the queued one (never placed) to a worker.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import time

from pool_spike import (
    API,
    CEILING,
    DRILL_IDLE_SECONDS,
    POOL,
    enqueue,
    live_workers,
    pool_pods,
    ready_api_pod,
    set_policy,
    sql,
    statuses,
    wait_done,
    wait_floor,
)
from spike import ROOT, kubectl, wait_for_rollout

MIB = 2**20
STANDARD_POD_MIB = 2048
INLINE_RUN_RESERVATION_BYTES = 64 * MIB
CONTROLLER_RESTART_RUN_SECONDS = 120
BUILD_FIXTURE = ROOT / "api/scripts/kubernetes_build_spike.py"
PLATFORM_DONE = ("succeeded", "failed", "canceled")


def node_kill(pod: dict) -> None:
    container_id = pod["status"]["containerStatuses"][0]["containerID"].split("://", 1)[1]
    node = pod["spec"]["nodeName"]
    pid = json.loads(subprocess.run(
        ["docker", "exec", node, "crictl", "inspect", container_id],
        check=True, capture_output=True, text=True,
    ).stdout)["info"]["pid"]
    subprocess.run(["docker", "exec", node, "kill", "-9", str(pid)], check=True)


def wait_status(ids: list[str], wanted: set[str], timeout: float) -> dict[str, str]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = statuses(ids)
        if len(current) == len(ids) and all(s in wanted for s in current.values()):
            return current
        time.sleep(1)
    raise TimeoutError(statuses(ids))


def controller_restart_burst_count(warm_worker_budget: int) -> int:
    """Keep enough 64 MiB runs alive to require three Job workers.

    Demand exceeds two complete worker budgets, so the warm worker cannot
    absorb the burst before controller replacement.
    """
    assert warm_worker_budget >= INLINE_RUN_RESERVATION_BYTES, warm_worker_budget
    per_worker = warm_worker_budget // INLINE_RUN_RESERVATION_BYTES
    return 2 * per_worker + 1


def ready_api_pods() -> dict[str, str]:
    """Ready, non-terminating API pod names and their incarnation UIDs."""
    pods = json.loads(kubectl("get", "pods", "-l", API, "-o", "json"))["items"]
    return {
        pod["metadata"]["name"]: pod["metadata"]["uid"]
        for pod in pods
        if not pod["metadata"].get("deletionTimestamp")
        and any(
            condition["type"] == "Ready" and condition["status"] == "True"
            for condition in pod.get("status", {}).get("conditions", [])
        )
    }


def live_controller_ids() -> set[str]:
    """Controller instance IDs currently published in Redis' controller hash."""
    script = """import asyncio, json
from src.core.cache.redis_client import get_redis
from src.services.worker_pool.controller import CONTROLLERS_KEY
async def main():
    async with get_redis() as redis:
        ids = await redis.hkeys(CONTROLLERS_KEY)
    print(json.dumps(sorted(key.decode() if isinstance(key, bytes) else key for key in ids)))
asyncio.run(main())
"""
    return set(json.loads(kubectl("exec", ready_api_pod(), "--", "python", "-c", script)))


def active_pool_jobs() -> dict[str, str]:
    """Unfinished pool Jobs, keyed by their durable Kubernetes UID binding."""
    jobs = json.loads(kubectl("get", "jobs", "-l", POOL, "-o", "json"))["items"]
    return {
        job["metadata"]["name"]: job["metadata"]["uid"]
        for job in jobs
        if not job["status"].get("succeeded") and not job["status"].get("failed")
    }


def worker_job_bindings() -> dict[str, str | None]:
    """Worker rows by Job name, before or after the remote UID is persisted."""
    rows = sql(
        "SELECT kubernetes_job_name, kubernetes_job_uid FROM execution_workers "
        "WHERE form='job' AND state IN ('reserved','starting','ready','draining') "
        "AND kubernetes_job_name IS NOT NULL"
    )
    assert len(rows) == len({row[0] for row in rows}), f"duplicate worker Job bindings: {rows}"
    return {name: uid or None for name, uid in rows}


def assert_active_jobs_bound() -> dict[str, str]:
    """Every live Kubernetes Job must have a row, with no changed bound UID.

    A Job may briefly exist after Kubernetes accepts its create request and
    before its controller persists the returned UID. Its deterministic name
    already proves ownership; once the row has a UID, an incarnation change is
    always a failure.
    """
    jobs = active_pool_jobs()
    # The controller commits the reservation before creating its Job. Read
    # Jobs first so a concurrent scale-up cannot appear as an orphan merely
    # because its durable row was committed after our earlier DB snapshot.
    bindings = worker_job_bindings()
    orphaned = {name: uid for name, uid in jobs.items() if name not in bindings}
    mismatched = {
        name: {"job": uid, "worker": bindings[name]}
        for name, uid in jobs.items()
        if bindings.get(name) is not None and bindings[name] != uid
    }
    assert not orphaned and not mismatched, (
        f"orphaned active Jobs: {orphaned}; changed Job UIDs: {mismatched}; "
        f"worker bindings: {bindings}"
    )
    return jobs


def assert_retained_job_bindings(expected: dict[str, str]) -> None:
    """The active Job incarnations from before the API restart still exist."""
    bindings = worker_job_bindings()
    jobs = active_pool_jobs()
    changed = {
        name: {"expected": uid, "worker": bindings.get(name), "job": jobs.get(name)}
        for name, uid in expected.items()
        if bindings.get(name) != uid or jobs.get(name) != uid
    }
    added = jobs.keys() - expected.keys()
    assert not changed and not added, (
        f"pre-restart Job bindings changed: {changed}; unexpected Jobs: {sorted(added)}"
    )


def snapshot_active_job_bindings() -> dict[str, str] | None:
    """Wait for creation to finish before taking the incarnation snapshot."""
    jobs = assert_active_jobs_bound()
    bindings = worker_job_bindings()
    unbound = {name: uid for name, uid in jobs.items() if bindings.get(name) is None}
    return None if unbound else jobs


def running_worker_bindings(ids: list[str]) -> dict[str, str]:
    quoted = ",".join(f"'{id_}'" for id_ in ids)
    rows = sql(
        "SELECT e.id, w.kubernetes_job_name FROM executions e "
        "JOIN execution_worker_claims c ON c.subject_id=e.id "
        "JOIN execution_workers w ON w.id=c.worker_id "
        f"WHERE e.id IN ({quoted}) AND e.status='Running'"
    )
    return {execution_id: name for execution_id, name in rows}


def wait_for_scaled_burst(burst: list[str], timeout: float) -> tuple[dict[str, str], dict[str, str]]:
    """Wait until all runs are claimed so restart cannot legitimately add Jobs."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        active = snapshot_active_job_bindings()
        bindings = running_worker_bindings(burst)
        workers = live_workers()
        ready = [worker for worker in workers if worker["state"] == "ready"]
        if (
            active is not None
            and len(bindings) == len(burst)
            and len(ready) == len(workers) > 1
            and len(set(bindings.values())) > 1
        ):
            return active, bindings
        time.sleep(1)
    raise TimeoutError(
        f"burst did not scale past the warm floor: workers={live_workers()}, "
        f"running={running_worker_bindings(burst)}"
    )


def wait_for_restarted_api(
    previous_pods: dict[str, str],
    previous_controllers: set[str],
    active_jobs: dict[str, str],
    burst: list[str],
) -> tuple[dict[str, str], set[str]]:
    """Require replacement API pods/controllers while the original Jobs run."""
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        current = statuses(burst)
        assert not {id_: status for id_, status in current.items() if status in ("Failed", "Cancelled", "Timeout")}, current
        if any(status == "Running" for status in current.values()):
            assert_retained_job_bindings(active_jobs)
        assert_active_jobs_bound()
        pods = ready_api_pods()
        controllers = live_controller_ids()
        if (
            len(pods) >= len(previous_pods)
            and set(pods.values()).isdisjoint(previous_pods.values())
            and len(controllers) >= len(previous_controllers)
            and controllers.isdisjoint(previous_controllers)
        ):
            return pods, controllers
        time.sleep(1)
    raise TimeoutError(
        f"API controllers did not fully restart: pods={ready_api_pods()}, "
        f"controllers={live_controller_ids()}"
    )


def kill_drill() -> dict:
    victim_runs = enqueue(2, 90)
    wait_status(victim_runs, {"Running"}, 120)
    [pod] = [p for p in pool_pods() if p["status"].get("phase") == "Running"]
    killed_at = time.monotonic()
    node_kill(pod)
    # Accepted after the worker died but before it is marked lost. (Accepted
    # before the kill, the worker would claim them at once: it admits work by
    # memory, and two sleeping runs leave it room.)
    queued = enqueue(3, 1)
    deadline = time.monotonic() + 120
    lost = None
    while time.monotonic() < deadline and lost is None:
        rows = sql(
            "SELECT state, coalesce(end_reason,'') FROM execution_workers "
            f"WHERE kubernetes_pod_uid = '{pod['metadata']['uid']}'"
        )
        if rows and rows[0][0] == "lost":
            lost = round(time.monotonic() - killed_at, 1)
        time.sleep(1)
    assert lost is not None, "killed worker was never marked lost"
    queued_seconds = wait_done(queued, 300)
    interrupted = statuses(victim_runs)
    return {
        "lost_after_seconds": lost,
        "queued_runs_completed_seconds": queued_seconds,
        # Started work is never replayed; it ends through the stuck sweep.
        "interrupted_runs": interrupted,
        "replayed": any(v == "Success" for v in interrupted.values()),
    }


def controller_restart_drill() -> dict:
    wait_floor(300)
    [floor_worker] = live_workers()
    burst_count = controller_restart_burst_count(floor_worker["budget"])
    burst = enqueue(burst_count, CONTROLLER_RESTART_RUN_SECONDS)
    active_jobs, before_bindings = wait_for_scaled_burst(burst, 300)
    assert len(set(before_bindings.values())) > 1, before_bindings
    before_pods = ready_api_pods()
    before_controllers = live_controller_ids()
    assert before_pods and before_controllers, (before_pods, before_controllers)

    started = time.monotonic()
    kubectl("delete", "pod", "-l", API, "--wait=false")
    after_pods, after_controllers = wait_for_restarted_api(
        before_pods, before_controllers, active_jobs, burst
    )
    assert_retained_job_bindings(active_jobs)
    assert_active_jobs_bound()
    after_bindings = running_worker_bindings(burst)
    assert len(set(after_bindings.values())) > 1, after_bindings
    assert any(status == "Running" for status in statuses(burst).values()), "the burst ended during restart"

    deadline = time.monotonic() + 600
    while True:
        current = statuses(burst)
        failed = {id_: status for id_, status in current.items() if status in ("Failed", "Cancelled", "Timeout")}
        assert not failed, f"runs failed: {failed}"
        if len(current) == len(burst) and all(status == "Success" for status in current.values()):
            break
        assert_active_jobs_bound()
        assert time.monotonic() < deadline, current
        time.sleep(1)

    quoted = ",".join(f"'{id_}'" for id_ in burst)
    claims = sql(
        "SELECT subject_id, count(*) FROM execution_worker_claims "
        "WHERE family='workflow_execution' AND subject_id IN ("
        f"{quoted}) GROUP BY subject_id ORDER BY subject_id"
    )
    assert len(claims) == len(burst) and all(int(count) == 1 for _, count in claims), claims
    return {
        "burst_seconds": round(time.monotonic() - started, 1),
        "burst_runs": burst_count,
        "pre_restart_job_bindings": active_jobs,
        "pre_restart_api_pods": before_pods,
        "post_restart_api_pods": after_pods,
        "pre_restart_controllers": sorted(before_controllers),
        "post_restart_controllers": sorted(after_controllers),
        "running_workers_before_restart": sorted(set(before_bindings.values())),
        "running_workers_after_restart": sorted(set(after_bindings.values())),
        "execution_claims": claims,
    }



def oom_drill() -> dict:
    api = ready_api_pod()
    script = """import asyncio, base64, json
from types import SimpleNamespace
from sqlalchemy import text
from src.core.database import get_db_context, init_db
from src.services.execution.async_executor import enqueue_code_execution
async def main():
    await init_db()
    async with get_db_context() as db:
        user_id = (await db.execute(text("select id from users order by created_at limit 1"))).scalar_one()
    ctx = SimpleNamespace(org_id=None, user_id=str(user_id), name="pool-drill", email="", startup=None,
                          form_inputs={}, embed={}, is_platform_admin=True, artifact_workspace_id=None)
    code = base64.b64encode(b"blocks = [bytearray(64 * 1024 * 1024) for _ in range(64)]\\nresult = len(blocks)").decode()
    print(json.dumps([await enqueue_code_execution(context=ctx, script_name="oom.py", code_base64=code,
                                                     parameters={}, sync=False, lineage=None)]))
asyncio.run(main())
"""
    hog = json.loads(kubectl("exec", api, "--", "python", "-c", script))
    outcome = wait_status(hog, {"Failed", "Timeout", "Cancelled", "Success"}, 600)
    after = enqueue(2, 0)
    after_seconds = wait_done(after, 300)
    error = sql(f"SELECT coalesce(error_message,'') FROM executions WHERE id = '{hog[0]}'")[0][0]
    return {"hog": outcome, "hog_error": error[:200], "pool_still_serving_seconds": after_seconds}


def unschedulable_drill() -> dict:
    unschedulable_mib = 1048576
    # Settings first: Worker memory may not be below the memory request.
    previous = set_policy(worker_memory_mib=unschedulable_mib, largest_worker_mib=unschedulable_mib)
    try:
        kubectl(
            "set", "env", "deployment/bifrost-api",
            f"BIFROST_POOL_WORKER_MEMORY_REQUEST_MIB={unschedulable_mib}",
            "BIFROST_POOL_START_TIMEOUT_SECONDS=60",
        )
        wait_for_rollout("deployment/bifrost-api", 300)
        try:
            # Replace every worker, the warm minimum included, with ones that
            # cannot be scheduled; nothing else can serve the run.
            started = datetime.now(timezone.utc).isoformat()
            kubectl("delete", "jobs", "-l", POOL, "--wait=true")
            waiting = enqueue(1, 0)
            time.sleep(150)
            lost = int(sql(
                "SELECT count(*) FROM execution_workers "
                f"WHERE end_reason='start_timeout' AND created_at >= '{started}'"
            )[0][0])
            live = live_workers()
            run_status = statuses(waiting)
            assert lost >= 1, "no worker reached its start deadline"
            assert all(s == "Pending" for s in run_status.values()), run_status
            return {
                "run_status": run_status,
                "workers_lost_at_start_deadline": lost,
                "live_workers": len(live),
                "within_ceiling": len(live) <= CEILING,
            }
        finally:
            kubectl(
                "set", "env", "deployment/bifrost-api",
                "BIFROST_POOL_WORKER_MEMORY_REQUEST_MIB-",
                "BIFROST_POOL_START_TIMEOUT_SECONDS-",
            )
            wait_for_rollout("deployment/bifrost-api", 300)
    finally:
        set_policy(**previous)
        # Workers started between the two restores carry the drill's limit.
        kubectl("delete", "jobs", "-l", POOL, "--wait=true")


def api_image() -> str:
    return kubectl(
        "get", "deployment/bifrost-api", "-o",
        "jsonpath={.spec.template.spec.containers[?(@.name=='api')].image}",
    ).strip()


def nodes() -> list[str]:
    return kubectl("get", "nodes", "-o", "jsonpath={.items[*].metadata.name}").split()


def ctr(node: str, *args: str, data: bytes | None = None) -> None:
    subprocess.run(["docker", "exec", "-i", node, "ctr", "-n", "k8s.io", "images", *args],
                   check=True, capture_output=True, input=data)


def publish_same_tag(image: str, version: str) -> None:
    """Move ``image``'s tag to a new build (``BIFROST_VERSION=version``), as
    pushing a new ``:latest`` does."""
    build = subprocess.run(
        ["docker", "build", "-q", "-"], check=True, capture_output=True,
        input=f"FROM {image}\nENV BIFROST_VERSION={version}\n".encode(),
    ).stdout.decode().strip()
    subprocess.run(["docker", "tag", build, image], check=True)
    archive = subprocess.run(["docker", "save", image], check=True, capture_output=True).stdout
    for node in nodes():
        ctr(node, "import", "-", data=archive)


def restart_api() -> None:
    kubectl("rollout", "restart", "deployment/bifrost-api")
    wait_for_rollout("deployment/bifrost-api", 600)


def rotation(old_id: str, version: str, started_at: str, long_run: str | None) -> dict:
    """Wait for the replacement on ``version`` to be ready and the old worker
    to drain as rotated, check warm capacity during it, then its retirement."""
    deadline = time.monotonic() + 300
    while True:
        assert time.monotonic() < deadline, live_workers()
        state = sql(f"SELECT state, coalesce(end_reason,'') FROM execution_workers WHERE id='{old_id}'")[0]
        ready_current = int(sql(
            "SELECT count(*) FROM execution_workers WHERE form='job' AND state='ready' "
            f"AND memory_budget_bytes IS NOT NULL AND version='{version}'"
        )[0][0])
        if state[0] != "ready":
            assert state == ["draining", "rotated"], state
            assert ready_current >= 1, "drained before its replacement could claim"
            break
        time.sleep(1)
    # Interactive work is served at once by the replacement.
    quick = enqueue(1, 0.1)
    quick_seconds = wait_done(quick, 30)
    assert quick_seconds < 10, quick_seconds
    run_wait = wait_done([long_run], 900) if long_run else 0.0
    deadline = time.monotonic() + 600
    while sql(f"SELECT state FROM execution_workers WHERE id='{old_id}'")[0][0] != "retired":
        assert time.monotonic() < deadline, "the drained worker did not retire"
        time.sleep(2)
    launched = sql(
        "SELECT coalesce(version,''), state FROM execution_workers "
        f"WHERE form='job' AND created_at >= '{started_at}'"
    )
    lost = sql(f"SELECT id, end_reason FROM execution_workers WHERE state='lost' AND ended_at >= '{started_at}'")
    assert lost == [], lost
    assert [row[0] for row in launched] == [version], f"one replacement, no churn: {launched}"
    return {"quick_run_seconds": quick_seconds, "long_run_wait_seconds": run_wait, "launched": launched}


def running_version() -> str:
    return kubectl(
        "exec", ready_api_pod(), "--", "python", "-c",
        "from shared.version import get_version; print(get_version())",
    ).strip()


def upgrade_drill() -> dict:
    image = api_image()
    original = f"{image}-drill-original"
    local_original = subprocess.run(
        ["docker", "image", "inspect", "-f", "{{.Id}}", image], check=True, capture_output=True, text=True,
    ).stdout.strip()
    for node in nodes():
        ctr(node, "tag", "--force", f"docker.io/library/{image}", f"docker.io/library/{original}")
    report: dict = {}

    # Same-tag upgrade while the warm worker runs past the shutdown grace.
    wait_floor(300)
    [old] = live_workers()
    started_at = sql("SELECT now()")[0][0]
    [long_run] = enqueue(1, 400)
    wait_status([long_run], {"Running"}, 120)
    upgraded = f"drill-{int(time.time())}"
    publish_same_tag(image, upgraded)
    restart_api()
    report["upgrade"] = rotation(old["id"], upgraded, started_at, long_run)

    # Rollback to the original build, under the same tag again.
    wait_floor(300)
    [old] = live_workers()
    started_at = sql("SELECT now()")[0][0]
    for node in nodes():
        ctr(node, "tag", "--force", f"docker.io/library/{original}", f"docker.io/library/{image}")
    subprocess.run(["docker", "tag", local_original, image], check=True)
    restart_api()
    rolled_back = running_version()
    report["rollback"] = rotation(old["id"], rolled_back, started_at, None)
    report["versions"] = {"upgraded": upgraded, "rolled_back": rolled_back}
    return report


def enqueue_deploy(hold_seconds: int, required_mib: int | None = None) -> str:
    """Seed an App and enqueue its deploy in an API pod; the job id."""
    args = ["exec", "-i", ready_api_pod(), "--", "python", "-", "--mode", "enqueue-deploy",
            "--hold-seconds", str(hold_seconds)]
    if required_mib is not None:
        args += ["--required-mib", str(required_mib)]
    output = kubectl(*args, data=BUILD_FIXTURE.read_text())
    return json.loads(output.strip().splitlines()[-1])["job_id"]


def platform_job(job_id: str) -> dict:
    [row] = sql(
        "SELECT status, execution_backend, coalesce(kubernetes_job_name,''), "
        "coalesce(kubernetes_job_uid,''), coalesce(kubernetes_pod_uid,''), "
        "coalesce(memory_required_bytes,0), coalesce(memory_limit_bytes,0), "
        f"coalesce(error_code,'') FROM platform_jobs WHERE id = '{job_id}'"
    )
    return {
        "status": row[0], "backend": row[1], "job_name": row[2], "job_uid": row[3],
        "pod_uid": row[4], "required_bytes": int(row[5]), "limit_bytes": int(row[6]),
        "error_code": row[7],
    }


def wait_platform_job(job_id: str, done, timeout: float) -> dict:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout
    while True:
        job = platform_job(job_id)
        if done(job):
            return job
        assert job["status"] not in PLATFORM_DONE, job
        assert time.monotonic() < deadline, job
        time.sleep(2)


def wait_platform_succeeded(job_id: str, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    while (job := platform_job(job_id))["status"] not in PLATFORM_DONE:
        assert time.monotonic() < deadline, job
        time.sleep(2)
    assert job["status"] == "succeeded", job
    return job


def running_in_pod(job: dict) -> bool:
    return job["status"] == "running" and bool(job["pod_uid"])


def quantity_mib(quantity: str) -> int:
    for suffix, factor in (("Gi", 1024), ("Mi", 1), ("Ki", 1 / 1024)):
        if quantity.endswith(suffix):
            return int(float(quantity[: -len(suffix)]) * factor)
    return int(quantity) // MIB


def build_pod_memory(job_name: str) -> dict:
    [pod] = json.loads(kubectl("get", "pods", "-l", f"job-name={job_name}", "-o", "json"))["items"]
    resources = pod["spec"]["containers"][0]["resources"]
    return {
        "request_mib": quantity_mib(resources["requests"]["memory"]),
        "limit_mib": quantity_mib(resources["limits"]["memory"]),
    }


def sized_isolated_build_drill() -> dict:
    wait_floor(300)
    previous = set_policy(isolated_jobs_enabled=True)
    try:
        job_id = enqueue_deploy(30, required_mib=2560)
        job = wait_platform_job(job_id, running_in_pod, 600)
        memory = build_pod_memory(job["job_name"])
        assert job["backend"] == "kubernetes", job
        assert job["required_bytes"] >= 2560 * MIB, job
        # Sized: the pod requests its whole limit, larger than a standard pod.
        assert memory["request_mib"] == memory["limit_mib"], memory
        assert memory["limit_mib"] > STANDARD_POD_MIB, memory
        assert memory["limit_mib"] * MIB == job["limit_bytes"], (memory, job)
        done = wait_platform_succeeded(job_id, 900)
        return {"job": done, "pod_memory": memory}
    finally:
        set_policy(**previous)


def api_pod_restarts() -> dict[str, int]:
    pods = json.loads(kubectl("get", "pods", "-l", API, "-o", "json"))["items"]
    return {
        pod["metadata"]["name"]: sum(c["restartCount"] for c in pod["status"].get("containerStatuses", []))
        for pod in pods
        if not pod["metadata"].get("deletionTimestamp")
    }


def max_workers_setting_drill() -> dict:
    wait_floor(300)
    [floor_worker] = live_workers()
    per_worker = floor_worker["budget"] // (64 * MIB)
    previous = set_policy(max_workers=2)
    try:
        # More than four workers' worth of runs, long enough to outlast both limits.
        burst = enqueue(4 * per_worker + 4, 120)
        deadline = time.monotonic() + 180
        while len(live_workers()) < 2:
            assert time.monotonic() < deadline, live_workers()
            time.sleep(2)
        held_until = time.monotonic() + 30
        while time.monotonic() < held_until:
            assert len(live_workers()) <= 2, live_workers()
            time.sleep(2)
        api_before = api_pod_restarts()
        set_policy(max_workers=4)
        changed_at = time.monotonic()
        peak = 2
        while peak <= 2:
            assert time.monotonic() - changed_at < 180, "the pool did not follow Max workers 4"
            peak = max(peak, len(live_workers()))
            time.sleep(2)
        followed_seconds = round(time.monotonic() - changed_at, 1)

        def sample() -> None:
            nonlocal peak
            peak = max(peak, len(live_workers()))

        burst_seconds = wait_done(burst, 900, sample)
        assert peak <= 4, peak
        assert api_pod_restarts() == api_before, "the API restarted"
        return {"followed_seconds": followed_seconds, "peak_workers": peak, "burst_seconds": burst_seconds}
    finally:
        set_policy(**previous)


def isolation_off_drill() -> dict:
    wait_floor(300)
    previous = set_policy(isolated_jobs_enabled=True, max_isolated_pods=1)
    try:
        running_id = enqueue_deploy(90)
        wait_platform_job(running_id, running_in_pod, 600)
        queued_id = enqueue_deploy(1)
        time.sleep(10)
        queued = platform_job(queued_id)
        # Max isolated pods 1: it waits for the running build's pod.
        assert queued["status"] == "queued" and not queued["job_name"], queued
        set_policy(isolated_jobs_enabled=False)
        moved = wait_platform_job(queued_id, lambda job: job["backend"] == "local", 60)
        assert running_in_pod(platform_job(running_id)), "the running build stopped"
        finished = wait_platform_succeeded(running_id, 900)
        on_worker = wait_platform_succeeded(queued_id, 900)
        assert finished["backend"] == "kubernetes", finished
        assert on_worker["backend"] == "local" and not on_worker["job_uid"], on_worker
        return {"moved": moved, "isolated": finished, "on_worker": on_worker}
    finally:
        set_policy(**previous)


DRILLS = (
    ("kill", kill_drill),
    ("controller_restart", controller_restart_drill),
    ("oom", oom_drill),
    ("unschedulable", unschedulable_drill),
    ("upgrade", upgrade_drill),
    ("sized_isolated_build", sized_isolated_build_drill),
    ("max_workers_setting", max_workers_setting_drill),
    ("isolation_off", isolation_off_drill),
)


STATIC = ("deployment/bifrost-scheduler", "deployment/bifrost-worker")


def scale_static(replicas: int) -> None:
    for deployment in STATIC:
        kubectl("scale", deployment, f"--replicas={replicas}")
    for deployment in STATIC:
        wait_for_rollout(deployment, 600)


def run(out: Path, only: list[str]) -> dict:
    out.mkdir(parents=True, exist_ok=False)
    report: dict = {"started_at": datetime.now(timezone.utc).isoformat()}
    previous = set_policy(idle_seconds=DRILL_IDLE_SECONDS)
    scale_static(0)
    try:
        report["jobs_only_floor_seconds"] = wait_floor(300)
        for name, drill in DRILLS:
            if only and name not in only:
                continue
            report[name] = drill()
            (out / "report.json").write_text(json.dumps(report, indent=2))
    finally:
        scale_static(1)
        set_policy(**previous)
    report["finally_floor_seconds"] = wait_floor(900)
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    (out / "report.json").write_text(json.dumps(report, indent=2))
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path(f"/tmp/bifrost-pool-drills-{int(time.time())}"))
    parser.add_argument("--only", nargs="*", default=[], choices=[name for name, _ in DRILLS])
    args = parser.parse_args()
    print(json.dumps(run(args.out, args.only), indent=2))


if __name__ == "__main__":
    main()
