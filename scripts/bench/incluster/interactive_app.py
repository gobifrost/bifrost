"""Normal authenticated API operations for the standalone browser benchmark.

This runs only in ``bench-infra/bench-driver``.  It creates an independent V2
App, submits the normal source-zip deployment, reads the shared PlatformJob,
and later reads durable execution records.  It never handles a browser token
or calls an external service.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import time
from pathlib import Path

import httpx

BASE = os.environ["BENCH_API_URL"]
TERMINAL = {"Success", "Failed", "CompletedWithErrors", "Timeout", "Cancelled", "Stuck"}


def _check(response: httpx.Response, label: str) -> httpx.Response:
    if response.status_code >= 300:
        raise RuntimeError(f"{label} failed: HTTP {response.status_code} {response.text[:400]}")
    return response


async def _client() -> httpx.AsyncClient:
    http = httpx.AsyncClient(base_url=BASE, timeout=300)
    login = await http.post(
        "/auth/login",
        data={
            "username": os.environ["BIFROST_DEFAULT_USER_EMAIL"],
            "password": os.environ["BIFROST_DEFAULT_USER_PASSWORD"],
        },
    )
    _check(login, "login")
    http.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
    return http


async def deploy(source_b64: str, name: str, slug: str, timeout_s: int = 900) -> dict:
    """Create then deploy an independent V2 app through its public API contract."""
    source = base64.b64decode(source_b64.encode("ascii"), validate=True)
    http = await _client()
    try:
        app = _check(
            await http.post(
                "/api/applications",
                json={"name": name, "slug": slug, "app_model": "standalone_v2", "access_level": "authenticated"},
            ),
            "create standalone app",
        ).json()
        accepted = _check(
            await http.post(
                f"/api/applications/{app['id']}/deploy",
                files={"source": ("browser-fixture.zip", source, "application/zip")},
            ),
            "deploy standalone app",
        ).json()
        job_id = accepted.get("job_id")
        if not isinstance(job_id, str) or not job_id:
            raise RuntimeError("application.deploy did not return a PlatformJob ID")
        deadline = time.monotonic() + timeout_s
        while True:
            job = _check(await http.get(f"/api/platform-jobs/{job_id}"), "read application.deploy job").json()
            if job.get("status") in {"succeeded", "failed", "cancelled", "requires_action"}:
                break
            if time.monotonic() >= deadline:
                raise TimeoutError(f"application.deploy PlatformJob {job_id} did not finish within {timeout_s}s")
            await asyncio.sleep(1)
        application = _check(await http.get(f"/api/applications/{app['id']}"), "read deployed application").json()
        return {"accepted": accepted, "job": job, "application": application}
    finally:
        await http.aclose()


async def records(execution_ids: list[str]) -> dict:
    """Read durable execution state after the browser observer has finished."""
    if not execution_ids or any(not isinstance(value, str) or not value for value in execution_ids):
        raise ValueError("browser execution IDs must be nonempty strings")
    if len(execution_ids) != len(set(execution_ids)):
        raise ValueError("browser execution IDs must be unique")
    http = await _client()
    try:
        listed = _check(await http.get("/api/executions", params={"limit": max(100, len(execution_ids))}), "list executions").json()
        rows = listed if isinstance(listed, list) else listed.get("executions") or listed.get("items") or []
        by_id = {str(row.get("execution_id")): row for row in rows}
        result = []
        for execution_id in execution_ids:
            row = by_id.get(execution_id)
            if row is None or row.get("status") not in TERMINAL:
                raise RuntimeError(f"browser execution {execution_id} is absent or nonterminal in durable list")
            detail = _check(await http.get(f"/api/executions/{execution_id}"), "read browser execution").json()
            if detail.get("execution_id") != execution_id or detail.get("status") != row.get("status"):
                raise RuntimeError("browser execution detail differs from durable list identity or status")
            result.append({
                "id": execution_id,
                "status": row.get("status"),
                "created_at": row.get("created_at"),
                "started_at": row.get("started_at"),
                "completed_at": row.get("completed_at"),
                "duration_ms": row.get("duration_ms"),
                "error": row.get("error_message") or row.get("error"),
                "result": detail.get("result"),
            })
        return {"records": result}
    finally:
        await http.aclose()


def main() -> None:
    mode = sys.argv[1]
    args = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    handler = {"deploy": deploy, "records": records}[mode]
    print(json.dumps(asyncio.run(handler(**args))))


if __name__ == "__main__":
    main()
