"""In-cluster driver for the #769 pressure test (runs inside an API pod).

Usage (from pressure_test.py):
    kubectl exec -i <api> -- sh -c 'cd /app && python - <mode> <json>' < pressure_driver.py

Modes print one JSON line:
- setup: register the pressure workflows and an agent; {"workflows": {name: id}, "agent": name}
- submit <plan>: run a plan of submissions over HTTP; {"ids": [...], "sync_ms": [...], "errors": [...]}
- latency <n>: n sync inline runs at the warm floor; {"p50_ms", "p95_ms", "p99_ms", "max_ms"}
- agents <n>: queue n agent runs; {"ids": [...]}
"""
from __future__ import annotations

import asyncio
import base64
import json
import statistics
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

WORKFLOWS = ("pressure_light", "pressure_heavy", "pressure_huge", "pressure_spike")
SOURCE = "from bifrost import workflow\nimport asyncio\n\n" + "\n\n".join(
    f'''@workflow(name="{name}", description="#769 pressure test")
async def {name}(seconds: float = 1.0, mb: int = 5):
    block = bytearray(mb * 1024 * 1024)
    for offset in range(0, len(block), 4096):
        block[offset] = 1
    await asyncio.sleep(seconds)
    return {{"mb": mb}}
'''
    for name in WORKFLOWS
)
AGENT = "pressure_agent"


async def client():  # type: ignore[no-untyped-def]
    import httpx

    from src.config import get_settings

    settings = get_settings()
    http = httpx.AsyncClient(base_url="http://localhost:8000", timeout=300)
    login = await http.post(
        "/auth/login",
        data={"username": settings.default_user_email, "password": settings.default_user_password},
    )
    login.raise_for_status()
    http.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
    return http


async def setup() -> dict:
    http = await client()
    try:
        (await http.put(
            "/api/files/editor/content",
            json={"path": "workflows/pressure.py", "content": SOURCE, "encoding": "utf-8"},
        )).raise_for_status()
        ids = {}
        listed = {w["name"]: w["id"] for w in (await http.get("/api/workflows")).json()}
        for name in WORKFLOWS:
            if name in listed:
                ids[name] = listed[name]
                continue
            registered = await http.post(
                "/api/workflows/register",
                json={"path": "workflows/pressure.py", "function_name": name},
            )
            registered.raise_for_status()
            ids[name] = registered.json()["id"]
        # Agent names are not unique: create it only once across runs.
        agents = (await http.get("/api/agents")).json()
        if not any(agent["name"] == AGENT for agent in agents):
            (await http.post(
                "/api/agents",
                json={"name": AGENT, "system_prompt": "Reply OK.", "access_level": "authenticated"},
            )).raise_for_status()
    finally:
        await http.aclose()
    return {"workflows": ids, "agent": AGENT}


async def submit(plan: list[dict]) -> dict:
    """Each item: {"workflow", "params", "count", "mode": "async"|"sync"|"scheduled", "delay"}."""
    http = await client()
    ids: list[str] = []
    sync_ms: list[float] = []
    errors: list[str] = []
    sem = asyncio.Semaphore(32)

    async def one(item: dict) -> None:
        body: dict = {"workflow_id": item["workflow"], "input_data": item.get("params", {})}
        if item["mode"] == "sync":
            body["sync"] = True
        elif item["mode"] == "scheduled":
            body["scheduled_at"] = (
                datetime.now(timezone.utc) + timedelta(seconds=item.get("delay", 30))
            ).isoformat()
        else:
            body["sync"] = False
        async with sem:
            started = time.perf_counter()
            try:
                response = await http.post("/api/workflows/execute", json=body)
                data = response.json()
                if response.status_code >= 300 or "execution_id" not in data:
                    errors.append(f"{response.status_code}: {str(data)[:200]}")
                    return
                ids.append(data["execution_id"])
                if item["mode"] == "sync":
                    sync_ms.append(round((time.perf_counter() - started) * 1000, 1))
            except Exception as exc:  # recorded, the orchestrator fails on it
                errors.append(f"{type(exc).__name__}: {exc}")

    try:
        await asyncio.gather(*(one(item) for item in plan for _ in range(item.get("count", 1))))
    finally:
        await http.aclose()
    return {"ids": ids, "sync_ms": sync_ms, "errors": errors}


async def latency(count: int) -> dict:
    from sqlalchemy import text

    from src.core.database import get_db_context, init_db
    from src.core.redis_client import get_redis_client
    from src.services.execution.async_executor import enqueue_code_execution

    await init_db()
    async with get_db_context() as db:
        user_id = (await db.execute(text("select id from users order by created_at limit 1"))).scalar_one()
    ctx = SimpleNamespace(org_id=None, user_id=str(user_id), name="pressure", email="", startup=None,
                          form_inputs={}, embed={}, is_platform_admin=True, artifact_workspace_id=None)
    code = base64.b64encode(b"result = 1").decode()
    samples = []
    for index in range(count + 5):
        execution_id = str(uuid.uuid4())
        started = time.perf_counter()
        await enqueue_code_execution(context=ctx, script_name="pressure_latency.py", code_base64=code,
                                     parameters={}, execution_id=execution_id, sync=True, lineage=None)
        result = await get_redis_client().wait_for_result(execution_id, timeout_seconds=60)
        assert result is not None and result.get("status") == "Success", result
        if index >= 5:
            samples.append((time.perf_counter() - started) * 1000)
    samples.sort()
    pick = lambda q: round(samples[int(q * (len(samples) - 1))], 1)  # noqa: E731
    return {"n": len(samples), "p50_ms": round(statistics.median(samples), 1),
            "p95_ms": pick(0.95), "p99_ms": pick(0.99), "max_ms": round(samples[-1], 1)}


async def agents(count: int) -> dict:
    http = await client()
    try:
        ids = []
        for index in range(count):
            response = await http.post(
                "/api/agent-runs/enqueue", json={"agent_name": AGENT, "input": {"n": index}}
            )
            response.raise_for_status()
            ids.append(response.json()["run_id"])
    finally:
        await http.aclose()
    return {"ids": ids}


def main() -> None:
    mode = sys.argv[1]
    arg = sys.argv[2] if len(sys.argv) > 2 else None
    if mode == "setup":
        result = asyncio.run(setup())
    elif mode == "submit":
        result = asyncio.run(submit(json.loads(arg or "[]")))
    elif mode == "latency":
        result = asyncio.run(latency(int(arg or "200")))
    elif mode == "agents":
        result = asyncio.run(agents(int(arg or "1")))
    else:
        raise SystemExit(f"unknown mode {mode}")
    print(json.dumps(result))


main()
