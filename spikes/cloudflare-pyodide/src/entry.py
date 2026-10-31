"""Local feasibility probe. No arbitrary-code HTTP endpoint or credentials."""
import asyncio
import importlib
import time
import sys
from pathlib import Path
from workers import WorkerEntrypoint, Response

sys.path.insert(0, str(Path(__file__).parent / "vendor"))

class Default(WorkerEntrypoint):
    async def fetch(self, request):
        if request.method == "POST":
            from probe import run
            body = await request.json()
            name = body["workload"]
            from fixtures import WORKLOADS
            if name not in WORKLOADS:
                return Response.json({"error": "unknown fixture"}, status=400)
            parameters = body.get("parameters", {})
            if len(str(parameters)) > 65536:
                return Response.json({"error": "parameters too large"}, status=413)
            result = await run(name, parameters, request.headers.get("X-Spike-Token"))
            return Response.json(result)
        started = time.perf_counter()
        await asyncio.sleep(0)
        imports = {}
        for name in ("httpx", "pydantic", "bifrost", "src.services.execution.engine"):
            try:
                importlib.import_module(name)
                imports[name] = "ok"
            except Exception as exc:
                imports[name] = f"{type(exc).__name__}: {exc}"
        loaded = sorted(name for name, module in sys.modules.items()
                        if "/vendor/" in str(getattr(module, "__file__", "")).replace("\\", "/"))
        return Response.json({"imports": imports, "loaded_upstream_modules": loaded,
                              "wall_ms": (time.perf_counter()-started)*1000})
