"""Open-loop load generator and outcome reconciler; runs in the bench-infra driver pod.

    python - <mode> '<json args>'    (stdin = this file; prints one JSON line)

Modes:
- ``prepare``: register the load workflows (``bench_wf_io``) and return the
  workflow and agent ids the other modes need, plus every AI connection's
  endpoint for the destination guard. Idempotent.
- ``step``: send seeded Poisson arrivals at ``rate`` per second for
  ``duration_s``, choosing each request's class by ``mix`` weight; every request
  carries a ``bench_request_id``. Samples unfinished work every
  ``sample_interval_s`` over the database (``application_name = bench-sampler``).
  Returns every scheduled request (scheduled/sent/response times, HTTP status,
  returned id, error) and the backlog series.
- ``sql``: one read-only query (database counters for the class runs).
- ``outcomes``: reconcile request ids to durable executions and agent runs by
  ``bench_request_id`` (so a submission that timed out but committed is still
  found) and check each class's expected output.

All times are epoch seconds from the pod clock; Kind pods share the host clock
with Postgres, so they compare directly with ``created_at``/``completed_at``.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import sys
from pathlib import Path
import time
import uuid

import asyncpg
import httpx

BASE = os.environ.get("BENCH_API_URL", "http://api.bench-tenant:8000")
FAKE_API = "http://bench-fake-api.bench-infra:8080"
AGENT_PROMPT = "Use the available tools to check the bench state, then reply briefly."
AGENT_CHAT_PROMPT = "Reply with one short sentence about the bench."

LOAD_SOURCE = '''import httpx

from bifrost import workflow


@workflow(name="bench_wf_io", description="Benchmark class wf-io: calls to a fake external API.")
async def bench_wf_io(bench_request_id: str = "", calls: int = 3, latency_ms: int = 200, response_bytes: int = 2000, gate: str = "") -> dict:
    received = 0
    async with httpx.AsyncClient(timeout=600) as http:
        for _ in range(calls):
            params = {"tag": bench_request_id, "ms": latency_ms, "bytes": response_bytes}
            if gate:
                params["gate"] = gate
            response = await http.get("''' + FAKE_API + '''/io", params=params)
            response.raise_for_status()
            received += len(response.content)
    return {"calls": calls, "bytes": received}
'''

WORKFLOW_CLASSES = {
    "wf-noop": "bench_wf_noop",
    "wf-sdk": "bench_wf_sdk",
    "wf-io": "bench_wf_io",
}
AGENT_CLASSES = {
    "agent-chat": ("bench_agent_chat", AGENT_CHAT_PROMPT),
    "agent-tools": ("bench_agent_tools", AGENT_PROMPT),
}
IO_DEFAULTS = {"calls": 3, "latency_ms": 200, "response_bytes": 2000}


class Session:
    """An authenticated API client that logs in again when its access token expires."""

    def __init__(self) -> None:
        limits = httpx.Limits(max_connections=400, max_keepalive_connections=400)
        self.http = httpx.AsyncClient(base_url=BASE, timeout=60, limits=limits)
        self._lock = asyncio.Lock()

    async def login(self) -> None:
        async with self._lock:
            response = await self.http.post("/auth/login", data={
                "username": os.environ["BIFROST_DEFAULT_USER_EMAIL"],
                "password": os.environ["BIFROST_DEFAULT_USER_PASSWORD"],
            })
            response.raise_for_status()
            self.http.headers["Authorization"] = f"Bearer {response.json()['access_token']}"

    async def request(self, method: str, path: str, **kwargs: object) -> httpx.Response:
        response = await self.http.request(method, path, **kwargs)  # type: ignore[arg-type]
        if response.status_code == 401:
            await self.login()
            response = await self.http.request(method, path, **kwargs)  # type: ignore[arg-type]
        return response


def _check(response: httpx.Response, what: str) -> httpx.Response:
    if response.status_code >= 300:
        raise SystemExit(f"{what} failed: HTTP {response.status_code} {response.text[:400]}")
    return response


def _rows(listed: object) -> list[dict]:
    if isinstance(listed, list):
        return listed
    if isinstance(listed, dict):
        return listed.get("items") or listed.get("workflows") or []
    return []


async def prepare() -> dict:
    session = Session()
    try:
        await session.login()
        workflows = {row["name"]: row["id"] for row in _rows(_check(await session.request("GET", "/api/workflows"), "list workflows").json())}
        if "bench_wf_io" not in workflows:
            _check(await session.request("PUT", "/api/files/editor/content", json={
                "path": "workflows/bench_load.py", "content": LOAD_SOURCE, "encoding": "utf-8",
            }), "write bench_load.py")
            response = await session.request("POST", "/api/workflows/register", json={"path": "workflows/bench_load.py", "function_name": "bench_wf_io"})
            if response.status_code >= 300 and response.status_code != 409:
                raise SystemExit(f"register bench_wf_io failed: HTTP {response.status_code} {response.text[:300]}")
            workflows = {row["name"]: row["id"] for row in _rows(_check(await session.request("GET", "/api/workflows"), "list workflows").json())}
        agents = {row["name"]: row["id"] for row in _rows(_check(await session.request("GET", "/api/agents"), "list agents").json())}
        missing = [name for name in WORKFLOW_CLASSES.values() if name not in workflows]
        missing += [name for name, _ in AGENT_CLASSES.values() if name not in agents]
        if missing:
            raise SystemExit(f"bench entities missing (run the class setup first): {missing}")
        connections = _check(await session.request("GET", "/api/admin/ai/connections"), "list connections").json()
        return {
            "workflows": {cls: workflows[name] for cls, name in WORKFLOW_CLASSES.items()},
            "agents": {cls: agents[name] for cls, (name, _) in AGENT_CLASSES.items()},
            "connections": {connection["name"]: connection.get("endpoint") or "" for connection in connections},
        }
    finally:
        await session.http.aclose()


def schedule(rate: float, duration_s: float, mix: dict[str, float], seed: int) -> list[tuple[float, str]]:
    """Seeded Poisson arrival offsets and classes; the same inputs give the same trace."""
    rng = random.Random(seed)
    classes = sorted(mix)
    weights = [mix[name] for name in classes]
    arrivals: list[tuple[float, str]] = []
    offset = rng.expovariate(rate)
    while offset < duration_s:
        arrivals.append((offset, rng.choices(classes, weights)[0]))
        offset += rng.expovariate(rate)
    return arrivals


UNFINISHED_SQL = """
select
  (select count(*) from executions where created_at >= to_timestamp($1) and status in ('Pending', 'Running', 'Scheduled', 'Cancelling')),
  (select count(*) from agent_runs where created_at >= to_timestamp($1) and status in ('queued', 'pending', 'running')),
  (select count(*) from work_dispatches),
  (select count(*) from work_dispatches where assigned_worker_id is not null)
"""


async def _sample(started: float, interval_s: float, stop: asyncio.Event, series: list[dict]) -> None:
    connection = await asyncpg.connect(os.environ["BENCH_DB_URL"], server_settings={"application_name": "bench-sampler"})
    try:
        while not stop.is_set():
            at = time.time()
            executions, agents, dispatches, claimed = await connection.fetchrow(UNFINISHED_SQL, started)
            series.append({"t": at, "executions": executions, "agent_runs": agents, "dispatches": dispatches, "claimed": claimed,
                           "unfinished": executions + agents})
            try:
                await asyncio.wait_for(stop.wait(), timeout=max(0.0, interval_s - (time.time() - at)))
            except TimeoutError:
                pass
    finally:
        await connection.close()


async def step(rate: float, duration_s: float, mix: dict[str, float], seed: int, ids: dict,
               sample_interval_s: float = 2.0, request_timeout_s: float = 30.0, io: dict | None = None) -> dict:
    io = {**IO_DEFAULTS, **(io or {})}
    session = Session()
    await session.login()
    arrivals = schedule(rate, duration_s, mix, seed)
    records: list[dict] = []
    stop = asyncio.Event()
    series: list[dict] = []
    started = time.time()
    sampler = asyncio.create_task(_sample(started, sample_interval_s, stop, series))

    async def send(offset: float, cls: str) -> None:
        request_id = uuid.uuid4().hex
        record = {"id": request_id, "class": cls, "scheduled": started + offset, "sent": None, "responded": None,
                  "http_status": None, "durable_id": None, "error": None}
        records.append(record)
        delay = record["scheduled"] - time.time()
        if delay > 0:
            await asyncio.sleep(delay)
        if cls in WORKFLOW_CLASSES:
            params: dict = {"bench_request_id": request_id}
            if cls == "wf-io":
                params.update(io)
            method, path, body, key = "POST", "/api/workflows/execute", {
                "workflow_id": ids["workflows"][cls], "input_data": params, "sync": False}, "execution_id"
        else:
            agent_name, prompt = AGENT_CLASSES[cls]
            method, path, body, key = "POST", "/api/agent-runs/enqueue", {
                "agent_name": agent_name, "input": {"message": f"{prompt} [bench:{request_id}]", "bench_request_id": request_id}}, "run_id"
        record["sent"] = time.time()
        try:
            response = await asyncio.wait_for(session.request(method, path, json=body), timeout=request_timeout_s)
        except Exception as error:  # noqa: BLE001 - every submission failure is a recorded outcome
            record["responded"] = time.time()
            record["error"] = f"{type(error).__name__}: {error}"[:300]
            return
        record["responded"] = time.time()
        record["http_status"] = response.status_code
        if response.status_code < 300:
            record["durable_id"] = str(response.json().get(key))
        else:
            record["error"] = response.text[:300]

    try:
        await asyncio.gather(*(send(offset, cls) for offset, cls in arrivals))
    finally:
        stop.set()
        await sampler
        await session.http.aclose()
    lags = sorted(r["sent"] - r["scheduled"] for r in records if r["sent"] is not None)
    return {
        "started": started, "rate": rate, "duration_s": duration_s, "seed": seed, "mix": mix, "io": io,
        "requests": records, "backlog": series,
        "driver_lag_p99_s": lags[min(len(lags) - 1, int(0.99 * len(lags)))] if lags else None,
    }


EXECUTIONS_SQL = """
select parameters->>'bench_request_id', id::text, status, result,
       extract(epoch from created_at), extract(epoch from started_at), extract(epoch from completed_at), error_message
from executions where parameters->>'bench_request_id' = any($1::text[])
"""
AGENT_RUNS_SQL = """
select input->>'bench_request_id', id::text, status, output,
       extract(epoch from created_at), extract(epoch from started_at), extract(epoch from completed_at), error,
       iterations_used, tokens_used
from agent_runs where input->>'bench_request_id' = any($1::text[])
"""


def correct(cls: str, status: str, result: object, io: dict) -> bool:
    """Whether a terminal record is a successful run that produced its class's expected output."""
    if cls in WORKFLOW_CLASSES and status != "Success":
        return False
    if cls in AGENT_CLASSES and status != "completed":
        return False
    if cls == "wf-noop":
        return result == {"ok": True}
    if cls == "wf-sdk":
        return isinstance(result, dict) and result.get("configs") == 5
    if cls == "wf-io":
        return isinstance(result, dict) and result.get("calls") == io["calls"] and result.get("bytes") == io["calls"] * io["response_bytes"]
    return True


async def outcomes(requests: list[dict], io: dict | None = None) -> dict:
    io = {**IO_DEFAULTS, **(io or {})}
    by_id = {request["id"]: request["class"] for request in requests}
    connection = await asyncpg.connect(os.environ["BENCH_DB_URL"], server_settings={"application_name": "bench-sampler"})
    try:
        ids = list(by_id)
        rows = [("workflow", row) for row in await connection.fetch(EXECUTIONS_SQL, ids)]
        rows += [("agent", row) for row in await connection.fetch(AGENT_RUNS_SQL, ids)]
    finally:
        await connection.close()
    found: dict[str, dict] = {}
    duplicates: dict[str, int] = {}
    for kind, row in rows:
        request_id = row[0]
        if request_id in found:
            duplicates[request_id] = duplicates.get(request_id, 1) + 1
            continue
        result = row[3]
        if isinstance(result, str):
            result = json.loads(result)
        found[request_id] = {
            "kind": kind, "durable_id": row[1], "status": row[2], "created": row[4] and float(row[4]),
            "started": row[5] and float(row[5]), "completed": row[6] and float(row[6]), "error": (row[7] or "")[:300] or None,
            "correct": correct(by_id[request_id], row[2], result, io),
            **({"iterations": row[8], "tokens": row[9]} if kind == "agent" else {}),
        }
    return {"outcomes": found, "duplicates": duplicates, "missing": [request_id for request_id in by_id if request_id not in found]}


async def sql(sql: str) -> dict:
    """One read-only query in its own read-only transaction; the first column of the first row."""
    connection = await asyncpg.connect(os.environ["BENCH_DB_URL"], server_settings={"application_name": "bench-sampler"})
    try:
        async with connection.transaction(readonly=True):
            value = await connection.fetchval(sql)
    finally:
        await connection.close()
    return {"value": json.loads(value) if isinstance(value, str) else value}


def main() -> None:
    mode, args = sys.argv[1], json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    handler = {"prepare": prepare, "step": step, "outcomes": outcomes, "sql": sql}[mode]
    print(json.dumps(asyncio.run(handler(**args)), default=str))


if __name__ == "__main__":
    main()
