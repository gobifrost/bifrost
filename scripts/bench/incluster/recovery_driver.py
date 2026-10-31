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
import sys
from pathlib import Path


WORKLOAD_VERSION = "recovery-effects-v1"
EFFECT_TABLE = "bench_recovery_effects_v1"

WORKFLOW_SOURCE = '''from bifrost import tables, workflow

@workflow(name="bench_wf_recovery_effects_v1", description="Append-only recovery effect probe.")
async def bench_wf_recovery_effects_v1(bench_request_id: str, effect_token: str, effect_index: int = 0) -> dict:
    await tables.insert("bench_recovery_effects_v1", {
        "bench_request_id": bench_request_id,
        "effect_token": effect_token,
        "effect_index": effect_index,
        "workload_version": "recovery-effects-v1",
        "class": "wf-recovery-effects",
    })
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
    mode, _args = sys.argv[1], json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    handler = {"pooler-db-ready": pooler_db_ready, "api-ready": api_ready}[mode]
    print(json.dumps(asyncio.run(handler())))


if __name__ == "__main__":
    main()
