"""Runtime checklist for a live Bifrost stack (#769 rollout gates).

Run inside an API container of the stack under test:

    docker cp scripts/runtime_checklist.py <api>:/tmp/checklist.py
    docker exec -w /app <api> python /tmp/checklist.py

It proves, on the running images:

1. every system schedule callback runs to completion in this image, and the
   elected leader has been running the startup schedules;
2. a durable platform job is queued and completed by the platform-job host;
3. workflow runs complete: inline code (sync and async), a registered
   workflow executed through the HTTP API (sync and async), and a deferred
   (scheduled) run promoted by the leader.

The registered workflow is created with the stack's seeded user
(``BIFROST_DEFAULT_USER_EMAIL`` / ``..._PASSWORD``).

Prints one JSON report; exits non-zero if any check fails. Per-feature
paths (forms, endpoints, data providers, agents, chat, events) are covered
by their e2e suites, as is cancellation.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import select, text

from src.core.database import get_db_context, init_db


async def _caller() -> SimpleNamespace:
    async with get_db_context() as db:
        user_id = (
            await db.execute(text("select id from users order by created_at limit 1"))
        ).scalar_one()
    return SimpleNamespace(
        org_id=None, user_id=str(user_id), name="runtime-checklist", email="",
        startup=None, form_inputs={}, embed={}, is_platform_admin=True,
        artifact_workspace_id=None,
    )


def _code(source: str) -> str:
    return base64.b64encode(source.encode()).decode()


async def _status(execution_id: str) -> str | None:
    async with get_db_context() as db:
        return (
            await db.execute(
                text("select status from executions where id = :id"), {"id": execution_id}
            )
        ).scalar_one_or_none()


async def _wait_status(execution_id: str, wanted: set[str], timeout: float) -> str:
    deadline = time.monotonic() + timeout
    status = None
    while time.monotonic() < deadline:
        status = await _status(execution_id)
        if status in wanted:
            return status
        await asyncio.sleep(0.5)
    raise TimeoutError(f"{execution_id} stayed {status}")


async def check_schedules() -> dict:
    from src.scheduler.control import resolve_callback
    from src.scheduler.registry import SCHEDULED_TASKS

    results = {}
    for task in SCHEDULED_TASKS:
        started = time.monotonic()
        try:
            await resolve_callback(task.callback)()
            results[task.task_id] = {"ok": True, "seconds": round(time.monotonic() - started, 2)}
        except Exception as exc:  # report every callback, not just the first failure
            results[task.task_id] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:300]}
    async with get_db_context() as db:
        leader_runs = dict(
            (
                await db.execute(
                    text(
                        "select task_id, count(*) filter (where status in ('succeeded','enqueued')) "
                        "from scheduler_task_runs group by task_id"
                    )
                )
            ).all()
        )
        failed = (
            await db.execute(
                text(
                    "select task_id, error_message from scheduler_task_runs "
                    "where status = 'failed' order by started_at desc limit 5"
                )
            )
        ).all()
    startup = [t.task_id for t in SCHEDULED_TASKS if t.run_at_startup]
    return {
        "callbacks": results,
        "callbacks_ok": all(r["ok"] for r in results.values()),
        "startup_schedules_run_by_leader": all(leader_runs.get(t, 0) > 0 for t in startup),
        "missing_leader_runs": [t for t in startup if leader_runs.get(t, 0) == 0],
        "recent_failed_runs": [list(row) for row in failed],
    }


async def check_platform_job() -> dict:
    from src.jobs.platform.system_maintenance import (
        enqueue_automatic_file_index_reconciliation,
    )
    from src.models.orm.platform_jobs import PlatformJob

    outcome = await enqueue_automatic_file_index_reconciliation()
    job_id = outcome.platform_job_id
    deadline = time.monotonic() + 300
    status = None
    while time.monotonic() < deadline:
        async with get_db_context() as db:
            status = await db.scalar(select(PlatformJob.status).where(PlatformJob.id == job_id))
        if status in ("succeeded", "failed", "cancelled"):
            break
        await asyncio.sleep(1)
    return {"job_id": str(job_id), "status": status, "ok": status == "succeeded"}


WORKFLOW_PATH = "runtime_checklist/echo.py"
WORKFLOW_SOURCE = """from bifrost import workflow


@workflow(name="runtime_checklist_echo", description="Runtime checklist probe")
async def runtime_checklist_echo(value: int = 1):
    return {"value": value}
"""


async def _api_client():  # type: ignore[no-untyped-def]
    import httpx

    from src.config import get_settings

    settings = get_settings()
    client = httpx.AsyncClient(base_url="http://localhost:8000", timeout=60)
    login = await client.post(
        "/auth/login",
        data={
            "username": settings.default_user_email,
            "password": settings.default_user_password,
        },
    )
    login.raise_for_status()
    token = login.json().get("access_token")
    if not token:
        # A production-mode stack: the seeded user has MFA (TOTP_SECRET).
        import pyotp

        verified = await client.post(
            "/auth/mfa/login",
            json={
                "mfa_token": login.json()["mfa_token"],
                "code": pyotp.TOTP(os.environ["TOTP_SECRET"]).now(),
            },
        )
        verified.raise_for_status()
        token = verified.json()["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"
    return client


async def check_http_workflows() -> tuple[dict, str]:
    client = await _api_client()
    try:
        write = await client.put(
            "/api/files/editor/content",
            json={"path": WORKFLOW_PATH, "content": WORKFLOW_SOURCE, "encoding": "utf-8"},
        )
        write.raise_for_status()
        registered = await client.post(
            "/api/workflows/register",
            json={"path": WORKFLOW_PATH, "function_name": "runtime_checklist_echo"},
        )
        if registered.status_code == 409:
            listed = await client.get("/api/workflows")
            workflow_id = next(
                w["id"] for w in listed.json() if w["name"] == "runtime_checklist_echo"
            )
        else:
            registered.raise_for_status()
            workflow_id = registered.json()["id"]

        started = time.perf_counter()
        sync = await client.post(
            "/api/workflows/execute",
            json={"workflow_id": workflow_id, "input_data": {"value": 3}, "sync": True},
        )
        sync_ms = round((time.perf_counter() - started) * 1000, 1)
        body = sync.json()
        async_run = await client.post(
            "/api/workflows/execute",
            json={"workflow_id": workflow_id, "input_data": {"value": 4}},
        )
        async_id = async_run.json().get("execution_id")
        # A deferred run: accepted as SCHEDULED, promoted by the leader.
        deferred = await client.post(
            "/api/workflows/execute",
            json={
                "workflow_id": workflow_id,
                "input_data": {"value": 5},
                "scheduled_at": (datetime.now(timezone.utc) + timedelta(seconds=5)).isoformat(),
            },
        )
        deferred_body = deferred.json()
    finally:
        await client.aclose()
    report = {
        "http_sync": {"ok": body.get("status") == "Success", "ms": sync_ms, "status": body.get("status")},
        "http_async": {
            "ok": await _wait_status(async_id, {"Success", "Failed"}, 120) == "Success"
        },
    }
    # The promoter runs every 60 s; allow two ticks plus the run itself.
    deferred_status = (
        await _wait_status(deferred_body["execution_id"], {"Success", "Failed"}, 200)
        if deferred_body.get("status") == "Scheduled"
        else deferred_body.get("status")
    )
    report["deferred"] = {"ok": deferred_status == "Success", "status": deferred_status}
    return report, workflow_id


async def check_workflows() -> dict:
    from src.core.redis_client import get_redis_client
    from src.services.execution.async_executor import enqueue_code_execution

    ctx = await _caller()
    report: dict = {}

    sync_id = str(uuid.uuid4())
    started = time.perf_counter()
    await enqueue_code_execution(
        context=ctx, script_name="checklist_sync.py", code_base64=_code("result = 7"),
        parameters={}, execution_id=sync_id, sync=True, lineage=None,
    )
    result = await get_redis_client().wait_for_result(sync_id, timeout_seconds=60)
    report["sync"] = {
        "ok": bool(result) and result.get("status") == "Success",
        "ms": round((time.perf_counter() - started) * 1000, 1),
    }

    async_id = await enqueue_code_execution(
        context=ctx, script_name="checklist_async.py", code_base64=_code("result = 8"),
        parameters={}, sync=False, lineage=None,
    )
    report["async"] = {"ok": await _wait_status(async_id, {"Success", "Failed"}, 120) == "Success"}

    http_report, _workflow_id = await check_http_workflows()
    report.update(http_report)

    return report


async def main() -> int:
    await init_db()
    report = {
        "schedules": await check_schedules(),
        "platform_job": await check_platform_job(),
        "workflows": await check_workflows(),
    }
    ok = (
        report["schedules"]["callbacks_ok"]
        and report["schedules"]["startup_schedules_run_by_leader"]
        and report["platform_job"]["ok"]
        and all(v.get("ok") is not False for v in report["workflows"].values())
    )
    report["ok"] = ok
    print(json.dumps(report, indent=2, default=str))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
