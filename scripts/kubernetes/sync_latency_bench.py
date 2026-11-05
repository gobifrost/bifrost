"""Measure end-to-end sync run latency from inside an API container.

Usage: docker cp scripts/kubernetes/sync_latency_bench.py <api>:/tmp/bench.py
       docker exec -w /app <api> python /tmp/bench.py 200
"""
import asyncio, base64, json, statistics, sys, time, uuid
from types import SimpleNamespace
from sqlalchemy import text
from src.core.database import get_db_context, init_db
from src.core.redis_client import get_redis_client
from src.services.execution.async_executor import enqueue_code_execution

async def main():
    await init_db()
    async with get_db_context() as db:
        user_id = (await db.execute(text("select id from users order by created_at limit 1"))).scalar_one()
    ctx = SimpleNamespace(org_id=None, user_id=str(user_id), name="bench", email="", startup=None,
                          form_inputs={}, embed={}, is_platform_admin=True, artifact_workspace_id=None)
    code = base64.b64encode(b"result = 1").decode()
    samples = []
    for i in range(int(sys.argv[1]) + 5):
        eid = str(uuid.uuid4())
        t = time.perf_counter()
        await enqueue_code_execution(context=ctx, script_name="bench.py", code_base64=code, parameters={}, execution_id=eid, sync=True, lineage=None)
        r = await get_redis_client().wait_for_result(eid, timeout_seconds=60)
        if i >= 5:
            samples.append((time.perf_counter() - t) * 1000)
        assert r is not None, "no result"
    samples.sort()
    print(json.dumps({"n": len(samples), "status": r.get("status"), "p50_ms": round(statistics.median(samples), 1), "p95_ms": round(samples[int(0.95*(len(samples)-1))], 1), "p99_ms": round(samples[int(0.99*(len(samples)-1))], 1), "max_ms": round(samples[-1], 1)}))
asyncio.run(main())
