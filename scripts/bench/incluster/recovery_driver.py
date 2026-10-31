"""Fresh, append-only D3 workload declaration and effect-row contract.

The source is deliberately separate from capacity workloads.  Each invocation
appends an effect document; no unique key, upsert, or retry hides a duplicate.
The host collector queries only rows for generated benchmark request IDs.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import random
import sys
import time
import uuid
from pathlib import Path


WORKLOAD_VERSION = "recovery-effects-v1"
EFFECT_TABLE = "bench_recovery_effects_v1"
RECOVERY_CLASS = "wf-recovery-effects"

WORKFLOW_SOURCE = '''import asyncio

from bifrost import tables, workflow

@workflow(name="bench_wf_recovery_effects_v1", description="Append-only recovery effect probe.")
async def bench_wf_recovery_effects_v1(bench_request_id: str, effect_token: str, effect_index: int = 0, hold_s: float = 0) -> dict:
    await tables.insert("bench_recovery_effects_v1", {
        "bench_request_id": bench_request_id,
        "effect_token": effect_token,
        "effect_index": effect_index,
        "workload_version": "recovery-effects-v1",
        "class": "wf-recovery-effects",
    })
    if hold_s:
        await asyncio.sleep(hold_s)
    return {"bench_request_id": bench_request_id, "effect_token": effect_token, "effect_index": effect_index}
'''


def effect_token(request_id: str, effect_index: int = 0) -> str:
    if not request_id or effect_index < 0:
        raise ValueError("effect token needs a nonempty request ID and nonnegative index")
    return hashlib.sha256(f"{WORKLOAD_VERSION}:{request_id}:{effect_index}".encode()).hexdigest()


def declaration() -> dict:
    """The exact fresh workload/table identity a recovery runner must declare."""
    return {"workload_version": WORKLOAD_VERSION, "workflow_name": "bench_wf_recovery_effects_v1",
            "table": EFFECT_TABLE, "append_only": True, "source": WORKFLOW_SOURCE}


def request_plan(run_nonce: str, rate: float, duration_s: float, seed: int) -> list[dict]:
    """Fresh deterministic requests and tokens, generated before the driver receives load."""
    if not run_nonce or rate <= 0 or duration_s <= 0:
        raise ValueError("recovery request plan needs a fresh nonce and positive rate/duration")
    rng = random.Random(seed)
    offset = rng.expovariate(rate)
    planned: list[dict] = []
    while offset < duration_s:
        request_id = uuid.uuid5(uuid.NAMESPACE_URL, f"{WORKLOAD_VERSION}:{run_nonce}:{len(planned)}").hex
        planned.append({"id": request_id, "class": RECOVERY_CLASS, "offset": offset,
                        "effect_token": effect_token(request_id)})
        offset += rng.expovariate(rate)
    if not planned:
        raise ValueError("recovery request plan produced no workload requests")
    return planned


def _rows(value: object) -> list[dict]:
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    if isinstance(value, dict):
        return [row for row in (value.get("items") or value.get("workflows") or []) if isinstance(row, dict)]
    return []


class Session:
    """The normal in-cluster benchmark login path, without exposing its credentials."""

    def __init__(self) -> None:
        import httpx
        self.http = httpx.AsyncClient(base_url=os.environ["BENCH_API_URL"], timeout=60)

    async def login(self) -> None:
        response = await self.http.post("/auth/login", data={
            "username": os.environ["BIFROST_DEFAULT_USER_EMAIL"],
            "password": os.environ["BIFROST_DEFAULT_USER_PASSWORD"],
        })
        response.raise_for_status()
        self.http.headers["Authorization"] = f"Bearer {response.json()['access_token']}"

    async def request(self, method: str, path: str, **kwargs: object):
        response = await self.http.request(method, path, **kwargs)
        if response.status_code == 401:
            await self.login()
            response = await self.http.request(method, path, **kwargs)
        return response


def _check(response, what: str):
    if response.status_code >= 300:
        raise RuntimeError(f"{what} failed: HTTP {response.status_code}")
    return response


async def prepare() -> dict:
    """Create exactly one fresh append-only table and register its recovery workflow."""
    session = Session()
    try:
        await session.login()
        table = await session.request("POST", "/api/tables", json={"name": EFFECT_TABLE})
        if table.status_code >= 300:
            raise RuntimeError("recovery effects table already exists; benchmark namespaces must be reset")
        written = await session.request("PUT", "/api/files/editor/content", json={
            "path": "workflows/bench_recovery_effects.py", "content": WORKFLOW_SOURCE, "encoding": "utf-8",
        })
        _check(written, "write recovery workflow")
        registered = await session.request("POST", "/api/workflows/register", json={
            "path": "workflows/bench_recovery_effects.py", "function_name": "bench_wf_recovery_effects_v1",
        })
        if registered.status_code >= 300 and registered.status_code != 409:
            _check(registered, "register recovery workflow")
        workflows = {row.get("name"): row.get("id") for row in _rows(_check(
            await session.request("GET", "/api/workflows"), "list workflows").json())}
        workflow_id = workflows.get("bench_wf_recovery_effects_v1")
        if not isinstance(workflow_id, str) or not workflow_id:
            raise RuntimeError("registered recovery workflow is missing")
        return {"workflows": {RECOVERY_CLASS: workflow_id}, "fixture": declaration()}
    finally:
        await session.http.aclose()


def _recovery_requests(ids: dict, duration_s: float) -> list[dict]:
    recovery = ids.get("recovery") if isinstance(ids, dict) else None
    requests = recovery.get("requests") if isinstance(recovery, dict) else None
    if not isinstance(requests, list) or not requests:
        raise RuntimeError("recovery load requires a predeclared request/token mapping")
    seen: set[str] = set()
    parsed: list[dict] = []
    for request in requests:
        if not isinstance(request, dict):
            raise RuntimeError("recovery request mapping contains a non-object")
        request_id, token, offset = request.get("id"), request.get("effect_token"), request.get("offset")
        if not isinstance(request_id, str) or not isinstance(token, str) or not isinstance(offset, (int, float)):
            raise RuntimeError("recovery request mapping is incomplete")
        if request_id in seen or offset < 0 or offset >= duration_s or token != effect_token(request_id):
            raise RuntimeError("recovery request mapping is invalid")
        seen.add(request_id)
        parsed.append({"id": request_id, "effect_token": token, "offset": float(offset)})
    return parsed


async def step(rate: float, duration_s: float, mix: dict[str, float], seed: int, ids: dict,
               sample_interval_s: float = 2.0, request_timeout_s: float = 30.0, io: dict | None = None,
               event_frames: bool = False) -> dict:
    """Loadgen-compatible recovery traffic which sends the predeclared token with every request."""
    del rate, mix, seed, sample_interval_s, io
    workflow_id = ids.get("workflows", {}).get(RECOVERY_CLASS)
    if not isinstance(workflow_id, str) or not workflow_id:
        raise RuntimeError("recovery workflow ID is missing")
    planned = _recovery_requests(ids, duration_s)
    session = Session()
    await session.login()
    started = time.time()
    if event_frames:
        print(json.dumps({"event": "started", "started": started}), flush=True)
    records: list[dict] = []

    async def send(planned_request: dict) -> None:
        record = {"id": planned_request["id"], "class": RECOVERY_CLASS,
                  "scheduled": started + planned_request["offset"], "sent": None, "responded": None,
                  "http_status": None, "durable_id": None, "error": None}
        records.append(record)
        delay = record["scheduled"] - time.time()
        if delay > 0:
            await asyncio.sleep(delay)
        record["sent"] = time.time()
        if event_frames:
            print(json.dumps({"event": "request_sent", "request_id": record["id"], "sent": record["sent"]}), flush=True)
        try:
            response = await asyncio.wait_for(session.request("POST", "/api/workflows/execute", json={
                "workflow_id": workflow_id,
                "input_data": {"bench_request_id": record["id"], "effect_token": planned_request["effect_token"]},
                "sync": False,
            }), timeout=request_timeout_s)
            record["responded"] = time.time()
            record["http_status"] = response.status_code
            if response.status_code < 300:
                record["durable_id"] = str(response.json().get("execution_id"))
            else:
                record["error"] = response.text[:300]
        except Exception as error:  # noqa: BLE001 - submission errors are raw evidence
            record["responded"] = time.time()
            record["error"] = f"{type(error).__name__}: {error}"[:300]

    try:
        await asyncio.gather(*(send(item) for item in planned))
    finally:
        await session.http.aclose()
    lags = sorted(record["sent"] - record["scheduled"] for record in records if record["sent"] is not None)
    return {"started": started, "rate": None, "duration_s": duration_s, "seed": None, "mix": {RECOVERY_CLASS: 1},
            "io": {}, "requests": records, "backlog": [],
            "driver_lag_p99_s": lags[min(len(lags) - 1, int(0.99 * len(lags)))] if lags else None}


EXECUTIONS_SQL = """
select parameters->>'bench_request_id', id::text, status, result,
       extract(epoch from created_at), extract(epoch from started_at), extract(epoch from completed_at), error
from executions
where parameters->>'bench_request_id' = any($1::text[])
order by created_at, id
"""


async def outcomes(requests: list[dict], io: dict | None = None) -> dict:
    """Canonical durable execution rows; duplicate rows remain explicit."""
    del io
    import asyncpg
    requested = [request["id"] for request in requests]
    connection = await asyncpg.connect(os.environ["BENCH_DB_URL"], server_settings={"application_name": "bench-recovery-sampler"})
    try:
        found = await connection.fetch(EXECUTIONS_SQL, requested)
    finally:
        await connection.close()
    rows = [{"request_id": row[0], "kind": "workflow", "durable_id": row[1], "status": row[2],
             "created": row[4] and float(row[4]), "started": row[5] and float(row[5]),
             "completed": row[6] and float(row[6]), "error": (row[7] or "")[:300] or None, "correct": row[2] == "Success"}
            for row in found]
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(row["request_id"], []).append(row)
    return {"outcomes": {request_id: values[0] for request_id, values in grouped.items()},
            "duplicates": {request_id: len(values) for request_id, values in grouped.items() if len(values) > 1},
            "missing": [request_id for request_id in requested if request_id not in grouped], "rows": rows}


async def effects(request_ids: list[str]) -> dict:
    """Read only the declared effect fields for this fresh request set."""
    import asyncpg
    connection = await asyncpg.connect(os.environ["BENCH_DB_URL"], server_settings={"application_name": "bench-recovery-sampler"})
    try:
        found = await connection.fetch("""
            select d.data->>'bench_request_id', d.data->>'effect_token', d.data->>'effect_index'
            from documents d join tables t on t.id = d.table_id
            where t.name = $1 and d.data->>'bench_request_id' = any($2::text[])
            order by d.created_at, d.id
        """, EFFECT_TABLE, request_ids)
    finally:
        await connection.close()
    return {"rows": [{"bench_request_id": row[0], "effect_token": row[1], "effect_index": row[2]} for row in found]}


async def start_busy(workflow_id: str, request_id: str, token: str, hold_s: float) -> dict:
    """Start one durable long-running effect probe for D6 before exact worker selection."""
    if token != effect_token(request_id) or hold_s <= 0:
        raise RuntimeError("D6 busy recovery workload input is invalid")
    session = Session()
    try:
        await session.login()
        response = _check(await session.request("POST", "/api/workflows/execute", json={
            "workflow_id": workflow_id,
            "input_data": {"bench_request_id": request_id, "effect_token": token, "hold_s": hold_s}, "sync": False,
        }), "start D6 busy workflow")
        return {"request_id": request_id, "effect_token": token, "http_status": response.status_code,
                "durable_id": str(response.json().get("execution_id")), "submitted_at": time.time()}
    finally:
        await session.http.aclose()


async def pooler_db_ready() -> dict:
    """Exercise the API's configured Pooler-backed database route without reading its URL or credentials."""
    import httpx

    base = os.environ["BENCH_API_URL"].rstrip("/")
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(f"{base}/health/detailed")
    body = response.json()
    database = body.get("components", {}).get("database", {})
    return {"ok": response.status_code == 200 and body.get("status") == "healthy" and database.get("status") == "healthy",
            "http_status": response.status_code, "database_status": database.get("status")}


async def api_ready() -> dict:
    """Confirm the replacement API can serve its normal readiness endpoint."""
    import httpx

    base = os.environ["BENCH_API_URL"].rstrip("/")
    async with httpx.AsyncClient(timeout=10.0) as client:
        health = await client.get(f"{base}/health")
        response = await client.get(f"{base}/health/ready")
    body = response.json()
    return {"ok": health.status_code == 200 and response.status_code == 200 and body.get("status") == "healthy",
            "health_http_status": health.status_code, "ready_http_status": response.status_code}


def main() -> None:
    mode, args = sys.argv[1], json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    handler = {
        "prepare": prepare, "step": step, "outcomes": outcomes, "effects": effects, "start-busy": start_busy,
        "pooler-db-ready": pooler_db_ready, "api-ready": api_ready,
    }[mode]
    print(json.dumps(asyncio.run(handler(**args))))


if __name__ == "__main__":
    main()
