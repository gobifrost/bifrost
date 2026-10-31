"""Fresh gated wf-io fixture for recovery D7/D8 evidence.

The effect is appended only after the fake API gate opens.  The driver never
prints URLs, credentials, or request inputs beyond generated benchmark IDs.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import time
from pathlib import Path


VERSION = "recovery-extended-v1"
TABLE = "bench_recovery_extended_effects_v1"
CLASS = "wf-recovery-gated-io"
FAKE_API = "http://bench-fake-api.bench-infra:8080"
SOURCE = '''from bifrost import tables, workflow
import httpx

@workflow(name="bench_wf_recovery_gated_io_v1", description="Gated append-only stale-worker recovery probe.")
async def bench_wf_recovery_gated_io_v1(bench_request_id: str, effect_token: str, gate: str) -> dict:
    async with httpx.AsyncClient(timeout=None) as client:
        response = await client.get("http://bench-fake-api.bench-infra:8080/io", params={"gate": gate, "tag": bench_request_id})
        response.raise_for_status()
    await tables.insert("bench_recovery_extended_effects_v1", {"bench_request_id": bench_request_id, "effect_token": effect_token, "version": "recovery-extended-v1"})
    return {"bench_request_id": bench_request_id, "effect_token": effect_token}
'''


def token(request_id: str) -> str:
    if not request_id:
        raise ValueError("request ID is required")
    return hashlib.sha256(f"{VERSION}:{request_id}".encode()).hexdigest()


class Session:
    def __init__(self) -> None:
        import httpx
        self.http = httpx.AsyncClient(base_url=os.environ["BENCH_API_URL"], timeout=60)

    async def login(self) -> None:
        response = await self.http.post("/auth/login", data={"username": os.environ["BIFROST_DEFAULT_USER_EMAIL"], "password": os.environ["BIFROST_DEFAULT_USER_PASSWORD"]})
        response.raise_for_status()
        self.http.headers["Authorization"] = f"Bearer {response.json()['access_token']}"

    async def request(self, method: str, path: str, **kwargs: object):
        response = await self.http.request(method, path, **kwargs)
        if response.status_code == 401:
            await self.login()
            response = await self.http.request(method, path, **kwargs)
        return response


async def prepare() -> dict:
    session = Session()
    try:
        await session.login()
        table = await session.request("POST", "/api/tables", json={"name": TABLE})
        if table.status_code >= 300:
            raise RuntimeError("fresh extended recovery table was not created")
        written = await session.request("PUT", "/api/files/editor/content", json={"path": "workflows/bench_recovery_extended.py", "content": SOURCE, "encoding": "utf-8"})
        written.raise_for_status()
        registered = await session.request("POST", "/api/workflows/register", json={"path": "workflows/bench_recovery_extended.py", "function_name": "bench_wf_recovery_gated_io_v1"})
        if registered.status_code >= 300 and registered.status_code != 409:
            registered.raise_for_status()
        listed = await session.request("GET", "/api/workflows")
        listed.raise_for_status()
        rows = listed.json().get("items", listed.json()) if isinstance(listed.json(), dict) else listed.json()
        workflow_id = next((row.get("id") for row in rows if isinstance(row, dict) and row.get("name") == "bench_wf_recovery_gated_io_v1"), None)
        if not isinstance(workflow_id, str) or not workflow_id:
            raise RuntimeError("extended recovery workflow is missing")
        return {"version": VERSION, "table": TABLE, "workflow": {CLASS: workflow_id}, "append_only_after_gate": True}
    finally:
        await session.http.aclose()


async def start(workflow_id: str, request_id: str, gate: str) -> dict:
    if not workflow_id or not request_id or not gate:
        raise ValueError("gated recovery start requires workflow, request, and gate")
    session = Session()
    try:
        await session.login()
        sent = time.time()
        response = await session.request("POST", "/api/workflows/execute", json={"workflow_id": workflow_id, "input_data": {"bench_request_id": request_id, "effect_token": token(request_id), "gate": gate}, "sync": False})
        body = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
        return {"request_id": request_id, "gate": gate, "sent_at": sent, "http_status": response.status_code,
                "durable_id": str(body.get("execution_id")) if response.status_code < 300 else None}
    finally:
        await session.http.aclose()


async def release(gate: str) -> dict:
    import httpx
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(f"{FAKE_API}/gate/{gate}")
    return {"gate": gate, "http_status": response.status_code, "released_at": time.time(), "released": response.status_code == 200}


async def effects(request_id: str) -> dict:
    import asyncpg
    connection = await asyncpg.connect(os.environ["BENCH_DB_URL"], server_settings={"application_name": "bench-recovery-extended"})
    try:
        rows = await connection.fetch("""select d.data->>'bench_request_id', d.data->>'effect_token'
          from documents d join tables t on t.id=d.table_id where t.name=$1 and d.data->>'bench_request_id'=$2 order by d.created_at,d.id""", TABLE, request_id)
    finally:
        await connection.close()
    return {"request_id": request_id, "rows": [{"bench_request_id": row[0], "effect_token": row[1]} for row in rows]}


async def outcome(request_id: str) -> dict:
    import asyncpg
    connection = await asyncpg.connect(os.environ["BENCH_DB_URL"], server_settings={"application_name": "bench-recovery-extended"})
    try:
        rows = await connection.fetch("""select id::text,status,result,error_message,extract(epoch from completed_at)
          from executions where parameters->>'bench_request_id'=$1 order by created_at,id""", request_id)
    finally:
        await connection.close()
    return {"request_id": request_id, "rows": [{"durable_id": row[0], "status": row[1], "result": row[2],
             "error": (row[3] or "")[:300] or None, "completed_at": float(row[4]) if row[4] is not None else None} for row in rows]}


async def cancel(durable_id: str) -> dict:
    session = Session()
    try:
        await session.login()
        issued_at = time.time()
        response = await session.request("POST", f"/api/executions/{durable_id}/cancel")
        responded_at = time.time()
        return {"durable_id": durable_id, "issued_at": issued_at, "responded_at": responded_at, "http_status": response.status_code,
                "http_accepted": 200 <= response.status_code < 300, "body": response.text[:300]}
    finally:
        await session.http.aclose()


def main() -> None:
    mode, args = sys.argv[1], json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    handlers = {"prepare": prepare, "start": start, "release": release, "effects": effects, "outcome": outcome, "cancel": cancel}
    print(json.dumps(asyncio.run(handlers[mode](**args)), allow_nan=False))


if __name__ == "__main__":
    main()
