#!/usr/bin/env python3
"""Prove on-demand reusable workers in local Kind (#769 stage 3 gate).

Usage: BIFROST_KIND_BUILD_JOBS=1 BIFROST_KIND_API_CONTROL=1 BIFROST_KIND_POOL=1 \\
           ./test.sh kubernetes up
       ./test.sh kubernetes pool-experiment [--out DIR]

Phases (recorded in report.json):

- zero: no worker pods and no live executors.
- first: one run starts the first worker; cold start is measured.
- reuse: more runs before idle expiry are handled by the same pod.
- burst: a backlog launches extra workers, never past the ceiling (3).
- idle: with no work, every worker drains itself and the pool returns to zero.
- race: a run accepted while its worker is draining still completes (a new
  worker is launched for it).
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
CEILING = 3
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


def sql(query: str) -> list[list[str]]:
    output = kubectl(
        "exec", "statefulset/postgres", "--",
        "psql", "-U", "bifrost", "-d", "bifrost", "-At", "-F", "\t", "-c", query,
    )
    return [line.split("\t") for line in output.splitlines() if line]


def pool_pods() -> list[dict]:
    items = json.loads(kubectl("get", "pods", "-l", POOL, "-o", "json"))["items"]
    return [pod for pod in items if pod.get("status", {}).get("phase") in ("Pending", "Running")]


def live_workers() -> list[dict]:
    rows = sql(
        "SELECT id, state, coalesce(kubernetes_pod_uid,''), runs_handled FROM execution_workers "
        "WHERE state IN ('reserved','starting','ready','draining') ORDER BY created_at"
    )
    return [{"id": r[0], "state": r[1], "pod_uid": r[2], "runs": int(r[3])} for r in rows]


def enqueue(count: int, seconds: float) -> list[str]:
    api = json.loads(kubectl("get", "pods", "-l", API, "-o", "json"))["items"][0]["metadata"]["name"]
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


def wait_zero(timeout: float) -> float:
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        if not live_workers() and not pool_pods():
            return round(time.monotonic() - started, 1)
        time.sleep(2)
    raise TimeoutError(f"pool did not return to zero: {live_workers()}")


def run(out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=False)
    report: dict = {"started_at": datetime.now(timezone.utc).isoformat()}

    assert not pool_pods() and not live_workers(), "pool is not at zero"
    assert int(sql("SELECT count(*) FROM work_dispatches WHERE placement='pool'")[0][0]) == 0
    report["zero"] = {"pool_pods": 0, "live_executors": 0}

    first = enqueue(1, 0)
    report["first"] = {"cold_start_seconds": wait_done(first, 300)}
    [worker] = live_workers()
    report["first"]["worker"] = worker

    reuse = enqueue(3, 1)
    reuse_seconds = wait_done(reuse, 120)
    workers = live_workers()
    assert [w["id"] for w in workers] == [worker["id"]], workers
    assert workers[0]["pod_uid"] == worker["pod_uid"]
    report["reuse"] = {"seconds": reuse_seconds, "same_pod": True, "runs_handled": workers[0]["runs"]}

    peak = {"workers": 0, "pods": 0}

    def sample() -> None:
        peak["workers"] = max(peak["workers"], len(live_workers()))
        peak["pods"] = max(peak["pods"], len(pool_pods()))

    burst = enqueue(16, 6)
    report["burst"] = {"runs": 16, "seconds": wait_done(burst, 600, sample), "peak": peak}
    assert peak["workers"] <= CEILING and peak["pods"] <= CEILING, peak
    assert peak["workers"] > 1, "burst never added a worker"

    report["idle"] = {"seconds_to_zero": wait_zero(300)}
    ended = sql(
        "SELECT state, coalesce(end_reason,''), count(*) FROM execution_workers GROUP BY 1, 2"
    )
    report["idle"]["executors"] = ended
    assert all(state == "retired" for state, *_ in ended), ended

    warm = enqueue(1, 0)
    wait_done(warm, 300)
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline and not any(
        w["state"] == "draining" for w in live_workers()
    ):
        time.sleep(0.5)
    race = enqueue(1, 0)
    report["race"] = {"seconds": wait_done(race, 300), "workers_after": live_workers()}
    report["idle_again"] = {"seconds_to_zero": wait_zero(300)}
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
