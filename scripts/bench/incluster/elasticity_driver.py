"""Authenticated, one-shot elasticity probes run inside ``bench-driver``.

The host sends ``loadgen.py`` as data because Cluster.exec_python copies only
this script into the pod.  Loading that source keeps request authentication,
durable reconciliation, and correctness rules identical to the load generator.
"""

from __future__ import annotations

import asyncio
import json
import math
import sys
import time
import types
import uuid
from pathlib import Path


POLICY_FIELDS = ("warm_workers", "max_workers", "worker_memory_mib", "largest_worker_mib", "idle_seconds")


def _load(payload: dict):
    source = payload.get("loadgen_source")
    if not isinstance(source, str) or not source:
        raise ValueError("elasticity driver requires loadgen_source")
    module = types.ModuleType("_elasticity_loadgen")
    module.__file__ = "loadgen.py"
    exec(payload["loadgen_source"], module.__dict__)
    module.WORKFLOW_CLASSES["wf-wait"] = "bench_wf_wait"
    original_correct = module.correct

    def correct(cls: str, status: str, result: object, io: dict) -> bool:
        if cls == "wf-wait":
            return status == "Success" and result == {"waited_s": 2}
        return original_correct(cls, status, result, io)

    module.correct = correct
    return module


async def policy(payload: dict) -> dict:
    module = _load(payload)
    session = module.Session()
    try:
        await session.login()
        response = await session.request("GET", "/api/admin/kubernetes/execution")
        if response.status_code >= 300:
            raise RuntimeError(f"policy read failed: HTTP {response.status_code}")
        value = response.json()
        policy = value.get("policy") if isinstance(value, dict) else None
        if not isinstance(policy, dict):
            raise RuntimeError("policy read returned a non-object")
        projected = {name: policy.get(name) for name in POLICY_FIELDS}
        if any(type(value) is not int for value in projected.values()):
            raise RuntimeError("policy read omitted required scalar controls")
        return {"policy": projected}
    finally:
        await session.http.aclose()


async def measure(payload: dict) -> dict:
    """Send exactly one fixed wf-wait wave, then preserve durable reconciliation."""
    count = payload.get("count")
    if type(count) is not int or count not in (1, 32):
        raise ValueError("measure count must be the declared first request or 32-request burst")
    module = _load(payload)
    session = module.Session()
    try:
        await session.login()
        listed = await session.request("GET", "/api/workflows")
        if listed.status_code >= 300:
            raise RuntimeError(f"workflow list failed: HTTP {listed.status_code}")
        workflows = {
            row["name"]: row["id"]
            for row in module._rows(listed.json())
            if isinstance(row, dict) and isinstance(row.get("name"), str) and isinstance(row.get("id"), str)
        }
        workflow_id = workflows.get("bench_wf_wait")
        if workflow_id is None:
            raise RuntimeError("bench_wf_wait is not registered")
        started = time.time()

        async def submit(index: int) -> dict:
            request_id = uuid.uuid4().hex
            record = {"id": request_id, "class": "wf-wait", "index": index, "scheduled": started,
                      "sent": None, "sent_monotonic": None, "responded": None, "http_status": None,
                      "durable_id": None, "error": None}
            record["sent"] = time.time()
            record["sent_monotonic"] = time.monotonic()
            try:
                response = await asyncio.wait_for(session.request("POST", "/api/workflows/execute", json={
                    "workflow_id": workflow_id, "input_data": {"bench_request_id": request_id}, "sync": False,
                }), timeout=60.0)
                record["responded"] = time.time()
                record["http_status"] = response.status_code
                if response.status_code < 300:
                    durable_id = response.json().get("execution_id")
                    if isinstance(durable_id, str) and durable_id:
                        record["durable_id"] = durable_id
                    else:
                        record["error"] = "response omitted execution_id"
                else:
                    record["error"] = f"HTTP {response.status_code}"
            except Exception as error:  # every admission failure is evidence, never a retry
                record["responded"] = time.time()
                record["error"] = f"{type(error).__name__}: {error}"[:300]
            return record

        requests = list(await asyncio.gather(*(submit(index) for index in range(count))))
        deadline = max(record["sent_monotonic"] for record in requests if isinstance(record["sent_monotonic"], (int, float))) + 60.0
        reconciliation = None
        reconciled_at = None
        try:
            reconciliation = await module.outcomes(requests)
            reconciled_at = time.time()

            def complete(value: dict) -> bool:
                return not value["missing"] and all(item.get("completed") is not None for item in value["outcomes"].values())

            while not complete(reconciliation) and time.monotonic() < deadline:
                await asyncio.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
                reconciliation = await module.outcomes(requests)
                reconciled_at = time.time()
        except Exception as error:
            return {"started": started, "deadline_s": 60, "requests": requests,
                    "reconciliation": reconciliation, "reconciliation_observed_epoch_s": reconciled_at,
                    "late_count": None, "not_completed_by_deadline_count": None,
                    "verdict": "invalid", "error_class": type(error).__name__}
        result = reconciled_records(requests, reconciliation)
        return {"started": started, "deadline_s": 60, **result, "reconciliation": reconciliation,
                "reconciliation_observed_epoch_s": reconciled_at}
    finally:
        await session.http.aclose()


def reconciled_records(requests: list[dict], reconciliation: dict) -> dict:
    """Join loadgen reconciliation without replacing its class-specific correctness verdict."""
    records = []
    for request in requests:
        outcome = reconciliation["outcomes"].get(request["id"])
        sent = request.get("sent")
        completed = (outcome or {}).get("completed")
        started = (outcome or {}).get("started")
        created = (outcome or {}).get("created")
        e2e = completed - sent if all(isinstance(value, (int, float)) for value in (completed, sent)) else None
        queue = started - created if all(isinstance(value, (int, float)) for value in (started, created)) else None
        timing_valid = (all(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
                            for value in (sent, created, started, completed))
                        and sent <= created <= started <= completed)
        durable_identity_matches = (request.get("durable_id") is None
                                    or request["durable_id"] == (outcome or {}).get("durable_id"))
        records.append({**request, "created": created, "started": started, "completed": completed,
                        "queue_s": queue, "e2e_s": e2e, "correct": (outcome or {}).get("correct"),
                        "timing_valid": timing_valid, "durable_identity_matches": durable_identity_matches,
                        "deadline_s": 60, "deadline_met": timing_valid and e2e <= 60,
                        "duplicate_durable_rows": max(0, reconciliation["duplicates"].get(request["id"], 1) - 1)})
    late_count = sum(record["timing_valid"] and record["e2e_s"] > 60 for record in records)
    deadline_misses = sum(record["deadline_met"] is not True for record in records)
    observed_failure = bool(reconciliation["missing"] or reconciliation["duplicates"] or any(
        record["error"] is not None or record["correct"] is not True or record["deadline_met"] is not True
        or record["durable_identity_matches"] is not True for record in records
    ))
    return {"requests": records, "late_count": late_count, "not_completed_by_deadline_count": deadline_misses,
            "verdict": "observed_failure" if observed_failure else "observed"}


def main() -> None:
    mode, payload = sys.argv[1], json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    handlers = {"policy": policy, "measure": measure}
    print(json.dumps(asyncio.run(handlers[mode](payload)), allow_nan=False))


if __name__ == "__main__":
    main()
