"""Synthetic, bounded retention fixture run inside a local benchmark pod.

The workflow calls use the normal authenticated HTTP API.  The only direct
database mutation is ``backdate``: it runs in the API pod, accepts explicit
fresh execution UUIDs, and refuses every other row predicate.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import httpx


VERSION = "retention-fixture-v1"
PAYLOAD_BYTES = 5_000_000
TERMINAL = {"Success", "Failed", "CompletedWithErrors", "Timeout", "Cancelled", "Stuck"}
WORKFLOW_SOURCE = '''import logging
import random

from bifrost import workflow

LOGGER = logging.getLogger("bench.retention")


def _payload(seed: int, repetitive: bool) -> str:
    if repetitive:
        return "R" * 5_000_000
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    randomizer = random.Random(seed)
    return "".join(randomizer.choice(alphabet) for _ in range(5_000_000))


async def _run(seed: int, repetitive: bool, log_lines: int) -> dict:
    for line in range(log_lines):
        LOGGER.info("retention fixture line=%s seed=%s %s", line, seed, "L" * 1024)
    return {"fixture": "retention-fixture-v1", "seed": seed, "payload": _payload(seed, repetitive)}


@workflow(name="bench_retention_small_success", description="Small successful retention fixture.")
async def bench_retention_small_success(seed: int = 0) -> dict:
    return {"fixture": "retention-fixture-v1", "seed": seed, "ok": True}


@workflow(name="bench_retention_small_failed", description="Small failed retention fixture.")
async def bench_retention_small_failed(seed: int = 0) -> dict:
    raise RuntimeError(f"intentional retention fixture failure {seed}")


@workflow(name="bench_retention_repetitive_5mb", description="Deterministic repetitive 5 MB retention fixture.")
async def bench_retention_repetitive_5mb(seed: int = 0, log_lines: int = 0) -> dict:
    return await _run(seed, True, log_lines)


@workflow(name="bench_retention_entropy_5mb", description="Deterministic high-entropy 5 MB retention fixture.")
async def bench_retention_entropy_5mb(seed: int = 0, log_lines: int = 0) -> dict:
    return await _run(seed, False, log_lines)
'''
FUNCTIONS = (
    "bench_retention_small_success", "bench_retention_small_failed",
    "bench_retention_repetitive_5mb", "bench_retention_entropy_5mb",
)
CASES = {
    "small_success": {"workflow": "bench_retention_small_success", "count": 1, "expected_status": "Success", "large": False},
    "small_failed": {"workflow": "bench_retention_small_failed", "count": 1, "expected_status": "Failed", "large": False},
    "repetitive_5mb": {"workflow": "bench_retention_repetitive_5mb", "count": 20, "expected_status": "Success", "large": True},
    "entropy_5mb": {"workflow": "bench_retention_entropy_5mb", "count": 20, "expected_status": "Success", "large": True},
}


def declaration() -> dict[str, object]:
    return {
        "version": VERSION,
        "payload_bytes": PAYLOAD_BYTES,
        "cases": {name: spec["count"] for name, spec in CASES.items()},
        "source_sha256": hashlib.sha256(WORKFLOW_SOURCE.encode()).hexdigest(),
        "log_lines_per_large_execution": 8,
    }


def _check(response: httpx.Response, label: str) -> httpx.Response:
    if response.status_code >= 300:
        raise RuntimeError(f"{label} failed: HTTP {response.status_code} {response.text[:400]}")
    return response


async def _client() -> httpx.AsyncClient:
    http = httpx.AsyncClient(base_url=os.environ["BENCH_API_URL"], timeout=300)
    login = await http.post("/auth/login", data={
        "username": os.environ["BIFROST_DEFAULT_USER_EMAIL"],
        "password": os.environ["BIFROST_DEFAULT_USER_PASSWORD"],
    })
    _check(login, "login")
    http.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
    return http


def _rows(value: object) -> list[dict]:
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    if isinstance(value, dict):
        for key in ("items", "workflows", "executions"):
            if isinstance(value.get(key), list):
                return [row for row in value[key] if isinstance(row, dict)]
    raise RuntimeError("API list response did not contain rows")


async def prepare() -> dict:
    http = await _client()
    try:
        _check(await http.put("/api/files/editor/content", json={
            "path": "workflows/bench_retention_fixture.py", "content": WORKFLOW_SOURCE, "encoding": "utf-8",
        }), "write retention fixture")
        existing = {row.get("name"): row.get("id") for row in _rows(_check(
            await http.get("/api/workflows"), "list workflows").json())}
        for name in FUNCTIONS:
            if name not in existing:
                response = await http.post("/api/workflows/register", json={
                    "path": "workflows/bench_retention_fixture.py", "function_name": name,
                })
                if response.status_code >= 300 and response.status_code != 409:
                    _check(response, f"register {name}")
        listed = {row.get("name"): row.get("id") for row in _rows(_check(
            await http.get("/api/workflows"), "list retention workflows").json())}
        ids = {name: listed.get(name) for name in FUNCTIONS}
        if any(not isinstance(value, str) or not value for value in ids.values()):
            raise RuntimeError("registered retention fixture workflow is missing")
        return {"fixture": declaration(), "workflow_ids": ids}
    finally:
        await http.aclose()


def _result_payload(detail: dict, case: str) -> str:
    result = detail.get("result")
    if not isinstance(result, dict) or not isinstance(result.get("payload"), str):
        raise RuntimeError(f"{case} terminal record omitted its payload")
    return result["payload"]


def _validate_terminal(case: str, item: dict, detail: dict) -> dict:
    spec = CASES[case]
    if detail.get("status") != spec["expected_status"]:
        raise RuntimeError(f"{case} execution {item['execution_id']} ended {detail.get('status')!r}")
    result: dict[str, object] = {"expected_status": spec["expected_status"]}
    if spec["large"]:
        payload = _result_payload(detail, case)
        encoded = payload.encode("utf-8")
        if len(encoded) != PAYLOAD_BYTES:
            raise RuntimeError(f"{case} payload is {len(encoded)} bytes, expected {PAYLOAD_BYTES}")
        result["payload_utf8_bytes"] = len(encoded)
        result["payload_sha256"] = hashlib.sha256(encoded).hexdigest()
    return result


async def execute(workflow_ids: dict[str, str], case: str) -> dict:
    """Run one bounded fixture class to completion before another class starts."""
    if case not in CASES:
        raise ValueError(f"unknown retention fixture case {case!r}")
    spec = CASES[case]
    http = await _client()
    try:
        submitted: list[dict] = []
        for seed in range(spec["count"]):
            workflow_id = workflow_ids.get(spec["workflow"])
            if not isinstance(workflow_id, str):
                raise RuntimeError(f"fixture workflow ID missing for {spec['workflow']}")
            input_data = {"seed": seed}
            if spec["large"]:
                input_data["log_lines"] = 8
            response = _check(await http.post("/api/workflows/execute", json={
                "workflow_id": workflow_id, "input_data": input_data, "sync": False,
            }), f"submit {case}").json()
            execution_id = response.get("execution_id")
            if not isinstance(execution_id, str):
                raise RuntimeError(f"submit {case} omitted execution_id")
            submitted.append({"case": case, "workflow": spec["workflow"], "seed": seed, "execution_id": execution_id})
        deadline = time.monotonic() + 900
        details: list[dict] = []
        pending = {item["execution_id"]: item for item in submitted}
        while pending and time.monotonic() < deadline:
            for execution_id in list(pending):
                detail = _check(await http.get(f"/api/executions/{execution_id}"), "read retention execution").json()
                if detail.get("status") in TERMINAL:
                    submitted_item = pending.pop(execution_id)
                    details.append({**submitted_item, "validation": _validate_terminal(case, submitted_item, detail), "raw": detail})
            if pending:
                await asyncio.sleep(1)
        if pending:
            raise TimeoutError(f"retention fixture executions did not finish: {sorted(pending)}")
        if len({item["execution_id"] for item in details}) != spec["count"]:
            raise RuntimeError("retention fixture returned duplicate or missing execution IDs")
        return {"case": case, "submitted": submitted, "terminal_records": details}
    finally:
        await http.aclose()


async def maintenance(dry_run: bool) -> dict:
    http = await _client()
    try:
        settings = _check(await http.get("/api/maintenance/run-retention/settings"), "read run-retention settings").json()
        accepted = _check(await http.post("/api/maintenance/run-retention/run", json={"dry_run": dry_run}), "start run retention").json()
        job_id = accepted.get("job_id")
        if not isinstance(job_id, str) or not job_id:
            raise RuntimeError("run-retention acceptance omitted PlatformJob ID")
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            job = _check(await http.get(f"/api/platform-jobs/{job_id}"), "read run-retention PlatformJob").json()
            if job.get("status") in {"succeeded", "failed", "cancelled", "requires_action"}:
                if job["status"] != "succeeded":
                    raise RuntimeError(f"run-retention PlatformJob ended {job['status']}")
                return {"settings": settings, "accepted": accepted, "job": job}
            await asyncio.sleep(1)
        raise TimeoutError(f"run-retention PlatformJob {job_id} did not finish within 120s")
    finally:
        await http.aclose()


async def settings() -> dict:
    http = await _client()
    try:
        return _check(await http.get("/api/maintenance/run-retention/settings"), "read run-retention settings").json()
    finally:
        await http.aclose()


async def backdate(ids: list[str], receipt_sha256: str, age_days: int) -> dict:
    """Age only passed fresh UUIDs, after the caller stored its immutable receipt."""
    if not ids or len(ids) != len(set(ids)) or not all(_valid_uuid(value) for value in ids):
        raise ValueError("backdate requires unique execution UUIDs")
    if len(receipt_sha256) != 64 or any(char not in "0123456789abcdef" for char in receipt_sha256):
        raise ValueError("backdate requires immutable evidence receipt SHA-256")
    if type(age_days) is not int or age_days < 31:
        raise ValueError("backdate requires canonical retention days plus at least one day")
    from sqlalchemy import text
    from src.core.database import get_db_context

    cutoff = datetime.now(timezone.utc) - timedelta(days=age_days)
    async with get_db_context() as db:
        rows = (await db.execute(text("""
            UPDATE executions SET created_at = :cutoff, started_at = :cutoff, completed_at = :cutoff
             WHERE id = ANY(CAST(:ids AS uuid[]))
               AND status IN ('Success', 'Failed', 'Timeout', 'Cancelled', 'CompletedWithErrors', 'Stuck')
         RETURNING id::text
        """), {"ids": ids, "cutoff": cutoff})).scalars().all()
        await db.commit()
    if set(rows) != set(ids):
        raise RuntimeError("backdate updated a different terminal execution set")
    return {"ids": sorted(rows), "receipt_sha256": receipt_sha256, "age_days": age_days, "backdated_to": cutoff.isoformat()}


def _valid_uuid(value: object) -> bool:
    try:
        UUID(str(value))
    except ValueError:
        return False
    return True


async def inspect(ids: list[str], case_by_id: dict[str, str] | None = None) -> dict:
    """Read exactly the fixture IDs and their cascaded execution-log count."""
    if not ids or not all(_valid_uuid(value) for value in ids):
        raise ValueError("inspect requires explicit execution UUIDs")
    from sqlalchemy import text
    from src.core.database import get_db_context

    async with get_db_context() as db:
        executions = (await db.execute(text("""
            SELECT id::text, status, completed_at, pg_column_size(result) AS result_column_bytes,
                   octet_length(result->>'payload') AS payload_utf8_bytes
              FROM executions WHERE id = ANY(CAST(:ids AS uuid[])) ORDER BY id
        """), {"ids": ids})).mappings().all()
        logs = await db.scalar(text("SELECT count(*) FROM execution_logs WHERE execution_id = ANY(CAST(:ids AS uuid[]))"), {"ids": ids})
    grouped: dict[str, dict[str, int]] = {}
    for row in executions:
        case = (case_by_id or {}).get(row["id"], "unclassified")
        result = grouped.setdefault(case, {"executions": 0, "result_column_bytes": 0, "payload_utf8_bytes": 0})
        result["executions"] += 1
        result["result_column_bytes"] += int(row["result_column_bytes"] or 0)
        result["payload_utf8_bytes"] += int(row["payload_utf8_bytes"] or 0)
    return {"execution_ids": [row["id"] for row in executions], "execution_count": len(executions),
            "execution_log_count": int(logs or 0), "result_storage_by_case": grouped}


def main() -> None:
    mode = sys.argv[1]
    arguments = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    handlers = {"prepare": prepare, "execute": execute, "maintenance": maintenance, "settings": settings,
                "backdate": backdate, "inspect": inspect}
    if mode not in handlers:
        raise ValueError(f"unsupported retention fixture mode {mode!r}")
    print(json.dumps(asyncio.run(handlers[mode](**arguments)), allow_nan=False))


if __name__ == "__main__":
    main()
