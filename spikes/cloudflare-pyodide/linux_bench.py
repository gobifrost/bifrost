"""Linux CPython engine-only comparison, NOT a dispatcher/process-pool benchmark."""
import asyncio
import json
from pathlib import Path
import sys
import time
sys.path.insert(0, str(Path(__file__).parent / "src"))
from probe import run


async def main():
    import httpx
    async with httpx.AsyncClient() as client:
        response = await client.post("http://host.docker.internal:39046/api/auth/login",
            data={"username": "dev@gobifrost.com", "password": "password"})
        response.raise_for_status()
        token = response.json()["access_token"]
    results = []
    for name in ("json_transform", "async_context", "workspace_import", "http_orchestration",
                 "integration_read", "table_update", "multi_step", "sync_json", "io_timeout", "cpu_timeout"):
        started = time.perf_counter()
        body = await run(name, token=token)
        results.append({"workload": name, "body": body, "client_wall_ms": (time.perf_counter()-started)*1000})
    Path("/spike/linux-benchmark.json").write_text(json.dumps({"runtime": "Linux CPython engine-only; no pool/dispatch", "results": results}, indent=2, default=str)+"\n")


asyncio.run(main())
