#!/usr/bin/env python3
"""Prove on-demand workers scale with the unified executors in local Kind.

Usage: ./test.sh kubernetes up
       ./test.sh kubernetes pool-experiment [--out DIR]

Phases (recorded in report.json), against the Settings -> Kubernetes
defaults (Warm workers 1, Max workers 6), with Idle time shortened to 45 s
for the run and restored afterwards:

- floor: the static executors (scheduler and worker) are ready and exactly
  the warm minimum of worker Jobs is ready, with no waiting work.
- burst: a backlog larger than the static executors' memory budgets plus
  the Job ceiling's scales the worker Jobs up to the ceiling, never past it;
  every run succeeds.
- race: a run accepted while the extra Jobs drain still completes.
- drain: with no work, the extra Jobs drain themselves and the pool returns
  to the warm minimum.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time

from spike import kubectl

API = "app.kubernetes.io/name=bifrost-api"
POOL = "app.kubernetes.io/name=bifrost-pool-worker"
FLOOR = 1
CEILING = 6
RUN_SECONDS = 30
ENQUEUE = """import asyncio, base64, json, sys, uuid
from types import SimpleNamespace
from sqlalchemy import text
from src.core.database import get_db_context, init_db
from src.services.execution.async_executor import enqueue_code_execution

async def main(count, seconds):
    await init_db()
    async with get_db_context() as db:
        user_id = (await db.execute(text("select id from users order by created_at limit 1"))).scalar_one()
    ctx = SimpleNamespace(org_id=None, user_id=str(user_id), name="pool-spike", email="", startup=None,
                          form_inputs={}, embed={}, is_platform_admin=True, artifact_workspace_id=None)
    code = base64.b64encode(f"import time\\ntime.sleep({seconds})\\nresult = {seconds}".encode()).decode()
    ids = []
    for _ in range(count):
        ids.append(await enqueue_code_execution(context=ctx, script_name="pool_spike.py", code_base64=code,
                                                parameters={}, sync=False, lineage=None))
    print(json.dumps(ids))
asyncio.run(main(int(sys.argv[1]), float(sys.argv[2])))
"""


#: The run's Idle time (Settings -> Kubernetes), so pools scale down in minutes.
DRILL_IDLE_SECONDS = 45
POLICY = """import asyncio, json, sys
from src.core.database import get_db_context, init_db
from src.services.kubernetes_execution import KubernetesExecutionService, load_kubernetes_policy

async def main(changes):
    await init_db()
    async with get_db_context() as db:
        before = await load_kubernetes_policy(db)
        await KubernetesExecutionService(db).update_policy(changes, updated_by="kind-drill")
        await db.commit()
    print(json.dumps({name: getattr(before, name) for name in changes}))
asyncio.run(main(json.loads(sys.argv[1])))
"""


def sql(query: str) -> list[list[str]]:
    output = kubectl(
        "exec", "statefulset/postgres", "--",
        "psql", "-U", "bifrost", "-d", "bifrost", "-At", "-F", "\t", "-c", query,
    )
    return [line.split("\t") for line in output.splitlines() if line]


def pool_pods() -> list[dict]:
    items = json.loads(kubectl("get", "pods", "-l", POOL, "-o", "json"))["items"]
    return [pod for pod in items if pod.get("status", {}).get("phase") in ("Pending", "Running")]


def live_workers(form: str = "job") -> list[dict]:
    rows = sql(
        "SELECT id, state, coalesce(kubernetes_pod_uid,''), runs_handled, "
        "coalesce(memory_budget_bytes, 0) FROM execution_workers "
        f"WHERE form = '{form}' AND state IN ('reserved','starting','ready','draining') "
        "AND supported_handlers ? 'workflow.execute' "
        "ORDER BY created_at"
    )
    return [
        {"id": r[0], "state": r[1], "pod_uid": r[2], "runs": int(r[3]), "budget": int(r[4])}
        for r in rows
    ]


def ready_api_pod(timeout: float = 300) -> str:
    """A Ready API pod that is not terminating; a rollout leaves old pods behind."""
    deadline = time.monotonic() + timeout
    while True:
        for pod in json.loads(kubectl("get", "pods", "-l", API, "-o", "json"))["items"]:
            conditions = pod.get("status", {}).get("conditions", [])
            ready = any(c["type"] == "Ready" and c["status"] == "True" for c in conditions)
            if ready and not pod["metadata"].get("deletionTimestamp"):
                return pod["metadata"]["name"]
        if time.monotonic() >= deadline:
            raise RuntimeError("no Ready API pod")
        time.sleep(2)


def set_policy(**changes: int | bool) -> dict:
    """Change Settings -> Kubernetes and return the values replaced, for
    ``set_policy(**previous)`` to restore. The drills have no API client, so
    this runs the service ``PUT /api/admin/kubernetes/policy`` calls (same
    validation, same merge) in an API pod; controllers and workers read it
    on their next pass, without a restart."""
    api = ready_api_pod()
    return json.loads(kubectl("exec", api, "--", "python", "-c", POLICY, json.dumps(changes)))


def enqueue(count: int, seconds: float) -> list[str]:
    api = ready_api_pod()
    return json.loads(kubectl("exec", api, "--", "python", "-c", ENQUEUE, str(count), str(seconds)))


def statuses(ids: list[str]) -> dict[str, str]:
    quoted = ",".join(f"'{i}'" for i in ids)
    return {r[0]: r[1] for r in sql(f"SELECT id, status FROM executions WHERE id IN ({quoted})")}


def wait_done(ids: list[str], timeout: float, sample=None) -> float:  # type: ignore[no-untyped-def]
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        current = statuses(ids)
        if sample is not None:
            sample()
        if len(current) == len(ids) and all(s == "Success" for s in current.values()):
            return round(time.monotonic() - started, 1)
        failed = {k: v for k, v in current.items() if v in ("Failed", "Cancelled", "Timeout")}
        if failed:
            raise AssertionError(f"runs failed: {failed}")
        time.sleep(1)
    raise TimeoutError(f"runs not done: {statuses(ids)}")


def wait_floor(timeout: float) -> float:
    """Wait until only the warm minimum of Job workers is left, all ready."""
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        workers = live_workers()
        if len(workers) == FLOOR and all(w["state"] == "ready" for w in workers) and len(
            pool_pods()
        ) == FLOOR:
            return round(time.monotonic() - started, 1)
        time.sleep(2)
    raise TimeoutError(f"pool did not return to the warm minimum: {live_workers()}")


def run(out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=False)
    previous = set_policy(idle_seconds=DRILL_IDLE_SECONDS)
    try:
        return phases(out)
    finally:
        set_policy(**previous)


def phases(out: Path) -> dict:
    report: dict = {"started_at": datetime.now(timezone.utc).isoformat()}

    static = [w for w in live_workers("static") if w["state"] == "ready"]
    assert len(static) >= 2, f"expected the scheduler and worker executors: {static}"
    # Inline runs request the default; that many fit an executor's budget.
    per_run = 64 * 2**20
    report["floor"] = {
        "seconds": wait_floor(300),
        "static_runs": sum(w["budget"] // per_run for w in static),
    }
    assert int(sql("SELECT count(*) FROM work_dispatches")[0][0]) == 0

    [floor_worker] = live_workers()
    burst_runs = report["floor"]["static_runs"] + CEILING * (floor_worker["budget"] // per_run) + 8
    peak = {"workers": 0, "pods": 0}

    def sample() -> None:
        peak["workers"] = max(peak["workers"], len(live_workers()))
        peak["pods"] = max(peak["pods"], len(pool_pods()))

    burst = enqueue(burst_runs, RUN_SECONDS)
    report["burst"] = {
        "runs": burst_runs,
        "seconds": wait_done(burst, 900, sample),
        "peak": dict(peak),
    }
    assert peak["workers"] <= CEILING and peak["pods"] <= CEILING, peak
    assert peak["workers"] == CEILING, f"burst never reached the ceiling: {peak}"

    # A run accepted while the extra Jobs drain still completes.
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline and not any(
        w["state"] == "draining" for w in live_workers()
    ):
        time.sleep(0.5)
    race = enqueue(1, 0)
    report["race"] = {"seconds": wait_done(race, 300)}

    report["drain"] = {"seconds_to_floor": wait_floor(600)}
    report["drain"]["executors"] = sql(
        "SELECT form, state, coalesce(end_reason,''), count(*) FROM execution_workers "
        "GROUP BY 1, 2, 3 ORDER BY 1, 2"
    )
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    (out / "report.json").write_text(json.dumps(report, indent=2))
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path(f"/tmp/bifrost-pool-experiment-{int(time.time())}"))
    args = parser.parse_args()
    print(json.dumps(run(args.out), indent=2))


if __name__ == "__main__":
    main()
