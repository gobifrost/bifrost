#!/usr/bin/env python3
"""Failure drills for on-demand worker Jobs in local Kind (#769).

Usage: ./test.sh kubernetes pool-drills [--out DIR] [--only NAME ...]

The drills exercise worker Jobs, so the static executors (the scheduler and
worker Deployments) are scaled to zero for their duration and restored
afterwards; the API keeps the warm minimum of Jobs.

- kill: SIGKILL a worker's process mid-run on its node. The worker is marked
  lost after missing heartbeats and its Job deleted; queued work moves to a
  new worker; the interrupted run is not replayed.
- controller_restart: delete every API pod during a burst. The restarted
  controllers adopt existing Jobs (one Job per executor, none duplicated)
  and the burst completes.
- oom: a run that exceeds the pod's memory limit ends visibly; the pool
  keeps serving other runs.
- unschedulable: workers that cannot be scheduled (the warm minimum
  included) are marked lost at their start deadline and replaced within the
  ceiling; queued work waits visibly.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import time

from pool_spike import (
    CEILING,
    POOL,
    enqueue,
    live_workers,
    pool_pods,
    ready_api_pod,
    sql,
    statuses,
    wait_done,
    wait_floor,
)
from spike import kubectl


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


def job_count() -> int:
    """Unfinished worker Jobs (finished ones stay until their TTL as history)."""
    jobs = json.loads(kubectl("get", "jobs", "-l", POOL, "-o", "json"))["items"]
    return sum(1 for job in jobs if not job["status"].get("succeeded") and not job["status"].get("failed"))


def kill_drill() -> dict:
    victim_runs = enqueue(2, 90)
    wait_status(victim_runs, {"Running"}, 120)
    [pod] = [p for p in pool_pods() if p["status"].get("phase") == "Running"]
    queued = enqueue(3, 1)  # waits behind the busy slots (2 per worker)
    killed_at = time.monotonic()
    node_kill(pod)
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
    burst = enqueue(12, 8)
    time.sleep(5)
    kubectl("delete", "pod", "-l", "app.kubernetes.io/name=bifrost-api", "--wait=false")
    peak_jobs = 0
    started = time.monotonic()
    while True:
        current = statuses(burst)
        peak_jobs = max(peak_jobs, job_count())
        executors = int(sql("SELECT count(*) FROM execution_workers WHERE form='job'")[0][0])
        assert job_count() <= executors, "a Job exists without an executor row"
        if len(current) == len(burst) and all(s == "Success" for s in current.values()):
            break
        assert time.monotonic() - started < 600, current
        time.sleep(2)
    duplicates = sql(
        "SELECT kubernetes_job_name, count(*) FROM execution_workers "
        "WHERE form='job' GROUP BY 1 HAVING count(*) > 1"
    )
    return {
        "burst_seconds": round(time.monotonic() - started, 1),
        "peak_jobs": peak_jobs,
        "duplicate_jobs": duplicates,
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
    kubectl(
        "set", "env", "deployment/bifrost-api",
        "BIFROST_POOL_WORKER_MEMORY_REQUEST_MIB=1048576",
        "BIFROST_POOL_WORKER_MEMORY_LIMIT_MIB=1048576",
        "BIFROST_POOL_START_TIMEOUT_SECONDS=60",
    )
    kubectl("rollout", "status", "deployment/bifrost-api", "--timeout=300s")
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
            "BIFROST_POOL_WORKER_MEMORY_LIMIT_MIB-",
            "BIFROST_POOL_START_TIMEOUT_SECONDS-",
        )
        kubectl("rollout", "status", "deployment/bifrost-api", "--timeout=300s")


DRILLS = (
    ("kill", kill_drill),
    ("controller_restart", controller_restart_drill),
    ("oom", oom_drill),
    ("unschedulable", unschedulable_drill),
)


STATIC = ("deployment/bifrost-scheduler", "deployment/bifrost-worker")


def scale_static(replicas: int) -> None:
    for deployment in STATIC:
        kubectl("scale", deployment, f"--replicas={replicas}")
    for deployment in STATIC:
        kubectl("rollout", "status", deployment, "--timeout=600s")


def run(out: Path, only: list[str]) -> dict:
    out.mkdir(parents=True, exist_ok=False)
    report: dict = {"started_at": datetime.now(timezone.utc).isoformat()}
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
