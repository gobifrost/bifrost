"""Deterministic producer-to-result workload for the disposable Kubernetes spike.

Runs inside the API pod with its existing dependencies. This measures the real
RabbitMQ/worker/protected-child path, not HTTP request latency. No external
services, user code, or credentials are needed by the payload.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import math
import os
import time
from typing import Any
from uuid import uuid4


def percentile(values: list[float], quantile: float) -> float:
    """Nearest-rank percentile; small samples are descriptive, not an SLO proof."""
    if not values:
        raise ValueError("At least one observation is required")
    if not 0 < quantile <= 1:
        raise ValueError("Quantile must be in (0, 1]")
    return sorted(values)[max(0, math.ceil(quantile * len(values)) - 1)]


async def run(count: int, concurrency: int, delay: float, timeout: int, expect_error: str | None = None, sdk: bool = False) -> bool:
    from src.core.constants import SYSTEM_USER_EMAIL, SYSTEM_USER_ID
    from src.core.database import close_db, get_db_context, init_db
    from src.core.redis_client import close_redis_client, get_redis_client
    from src.jobs.rabbitmq import rabbitmq
    from src.models.orm.executions import Execution
    from src.sdk.context import ExecutionContext
    from src.services.execution.async_executor import enqueue_code_execution

    await init_db()
    semaphore = asyncio.Semaphore(concurrency)
    observations: list[dict[str, Any]] = []
    code = "import asyncio, socket\nawait asyncio.sleep(delay)\n"
    if sdk:
        code += "from bifrost import organizations\norgs = await organizations.list()\nassert isinstance(orgs, list)\n"
    code += "return {'marker': marker, 'worker': socket.gethostname()}\n"
    payload = base64.b64encode(code.encode()).decode()

    async def execute_one() -> None:
        async with semaphore:
            execution_id = str(uuid4())
            marker = str(uuid4())
            context = ExecutionContext(
                user_id=SYSTEM_USER_ID, email=SYSTEM_USER_EMAIL,
                name="Kubernetes spike", scope="GLOBAL", organization=None,
                is_platform_admin=True, is_function_key=False,
                execution_id=execution_id,
            )
            started = time.perf_counter()
            await enqueue_code_execution(
                context, "elastic-runtime-spike", payload,
                {"delay": delay, "marker": marker}, execution_id, sync=True,
            )
            print(json.dumps({"event": "submitted", "execution_id": execution_id}), flush=True)
            result = await get_redis_client().wait_for_result(execution_id, timeout)
            elapsed_ms = (time.perf_counter() - started) * 1000
            async with get_db_context() as db:
                row = await db.get(Execution, execution_id)
                durable_status = row.status.value if row else None
                durable_result = row.result if row else None
            ok = bool(
                result and result.get("status") == "Success"
                and isinstance(result.get("result"), dict)
                and result["result"].get("marker") == marker
                and isinstance(result["result"].get("worker"), str)
                and durable_status == "Success"
                and durable_result == result["result"]
            )
            if expect_error:
                ok = bool(result and result.get("status") == "Failed"
                          and result.get("error_type") == expect_error
                          and durable_status == "Failed")
            observation = {
                "event": "completed", "execution_id": execution_id,
                "elapsed_ms": round(elapsed_ms, 3), "ok": ok,
                "status": result.get("status") if result else "caller_timeout",
                "durable_status": durable_status,
                "error_type": result.get("error_type") if result else None,
                "worker": durable_result.get("worker") if isinstance(durable_result, dict) else None,
            }
            observations.append(observation)
            print(json.dumps(observation), flush=True)

    try:
        await asyncio.gather(*(execute_one() for _ in range(count)))
        latencies = [item["elapsed_ms"] for item in observations]
        print(json.dumps({
            "event": "summary", "measurement": "producer_to_result_ms",
            "count": count, "concurrency": concurrency, "payload_delay_seconds": delay, "sdk": sdk,
            "passed": sum(item["ok"] for item in observations),
            "p50_ms": percentile(latencies, .50), "p95_ms": percentile(latencies, .95),
            "p99_ms": percentile(latencies, .99),
        }), flush=True)
        return all(item["ok"] for item in observations)
    finally:
        await rabbitmq.close()
        await close_redis_client()
        await close_db()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--delay", type=float, default=0)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--expect-error", choices=["WorkerShutdown"])
    parser.add_argument("--sdk", action="store_true", help="Call the real organizations SDK/API inside each isolated execution")
    args = parser.parse_args()
    if args.count < 1 or args.concurrency < 1 or args.delay < 0 or args.timeout < 1:
        parser.error("count, concurrency and timeout must be positive; delay must be nonnegative")
    if os.environ.get("BIFROST_KUBERNETES_SPIKE") != "1" or os.environ.get("BIFROST_ENVIRONMENT") != "testing":
        parser.error("Only permitted in the explicitly marked disposable testing cluster")
    raise SystemExit(0 if asyncio.run(run(args.count, args.concurrency, args.delay, args.timeout, args.expect_error, args.sdk)) else 1)


if __name__ == "__main__":
    main()
