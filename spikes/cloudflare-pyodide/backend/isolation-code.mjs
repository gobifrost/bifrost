// Diagnostic primitive only: fixed code, no workflow authority or durable state.
export const pythonProbe = `
import asyncio
import builtins
import json
import os
import sys
import types
from pathlib import Path
from workers import WorkerEntrypoint, Response

class Default(WorkerEntrypoint):
    async def fetch(self, request):
        data = json.loads(await request.text())
        action = data["action"]
        if action == "state":
            previous = getattr(builtins, "_attempt_marker", None)
            contamination = {"env": os.environ.get("ATTEMPT_POISON"),
                             "module": "_attempt_poison" in sys.modules,
                             "filesystem": Path("/tmp/attempt-poison.txt").exists()}
            builtins._attempt_marker = data["marker"]
            os.environ["ATTEMPT_POISON"] = data["marker"]
            sys.modules["_attempt_poison"] = types.ModuleType("_attempt_poison")
            Path("/tmp/attempt-poison.txt").write_text(data["marker"])
            return Response.json({"previous": previous, "current": builtins._attempt_marker,
                                  "own_canary": self.env.CANARY,"contamination":contamination,
                                  "parent_canary_visible": getattr(self.env,"PARENT_CANARY",None) is not None})
        if action == "io":
            await self.env.EFFECTS.record("before")
            await asyncio.sleep(0.25)
            await self.env.EFFECTS.record("after")
            return Response.json({"completed": True})
        if action in ("cpu", "cpu_limit"):
            await self.env.EFFECTS.record("before")
            total = 0
            for value in range(200000000 if action == "cpu_limit" else 20000000):
                total += value
            await self.env.EFFECTS.record("after")
            return Response.json({"total": total})
        if action == "cpu_catch":
            await self.env.EFFECTS.record("before")
            total = 0
            try:
                for value in range(200000000):
                    total += value
            except BaseException as exc:
                await self.env.EFFECTS.record("caught")
                await self.env.EFFECTS.record("after")
                return Response.json({"caught": type(exc).__name__})
            return Response.json({"total": total})
        if action == "egress":
            from workers import fetch
            try:
                await fetch("https://example.com")
                return Response.json({"blocked": False})
            except Exception as exc:
                return Response.json({"blocked": True, "error_type": type(exc).__name__})
        return Response.json({"error": "unsupported probe"}, status=400)
`;

export function workerCode(canary, effects) {
  return {
    compatibilityDate: '2026-10-30',
    compatibilityFlags: ['python_workers'],
    mainModule: 'main.py',
    modules: {'main.py': {py: pythonProbe}},
    env: {CANARY: canary, EFFECTS: effects},
    globalOutbound: null,
    limits: {cpuMs: 1000, subRequests: 4},
  };
}
