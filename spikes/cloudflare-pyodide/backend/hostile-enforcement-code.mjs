// Hostile enforcement qualification (Phase 2). Fixed diagnostic code only.
// Never cached: supervisor always uses LOADER.load (fresh interpreter per attempt).
// No workflow authority, no durable state, no Bifrost credentials.
// Probes: cpu_catch_effect, cpu_nocatch, mem_grow, subrequest_abuse,
// output_abuse, normal_accounting. EFFECTS.record is the authenticated-effect
// proxy: if hostile code can call it after its budget, that is catch-and-continue.
export const enforcementPythonProbe = `
import asyncio
import builtins
import json
import os
import sys
import time
import types
from pathlib import Path
from workers import WorkerEntrypoint, Response

class Default(WorkerEntrypoint):
    async def fetch(self, request):
        data = json.loads(await request.text())
        action = data["action"]
        marker = data.get("marker")
        if action == "honest":
            await self.env.EFFECTS.record("before")
            total = sum(range(1000))
            await self.env.EFFECTS.record("after")
            return Response.json({"total": total, "marker": marker})
        if action == "cpu_catch_effect":
            await self.env.EFFECTS.record("before")
            start = time.perf_counter()
            total = 0
            caught = None
            try:
                for value in range(200000000):
                    total += value
            except BaseException as exc:
                caught = type(exc).__name__
                caught_msg = str(exc)[:500]
                await self.env.EFFECTS.record("caught")
                # Hostile authenticated effect after budget: must not land
                # if the provider truly stops the isolate.
                await self.env.EFFECTS.record("after")
                elapsed = time.perf_counter() - start
                return Response.json({"caught": caught, "caught_msg": caught_msg,
                                       "elapsed_perf": elapsed, "total": total})
            elapsed = time.perf_counter() - start
            await self.env.EFFECTS.record("after")
            return Response.json({"caught": None, "elapsed_perf": elapsed, "total": total})
        if action == "cpu_nocatch":
            await self.env.EFFECTS.record("before")
            start = time.perf_counter()
            total = 0
            for value in range(200000000):
                total += value
            elapsed = time.perf_counter() - start
            await self.env.EFFECTS.record("after")
            return Response.json({"total": total, "elapsed_perf": elapsed})
        if action == "mem_grow":
            await self.env.EFFECTS.record("before")
            chunks = []
            chunk_bytes = 8 * 1024 * 1024
            peak = 0
            caught = None
            caught_msg = None
            try:
                for idx in range(64):
                    buf = bytearray(chunk_bytes)
                    # Touch pages so the allocator cannot lazily defer.
                    for off in range(0, len(buf), 4096):
                        buf[off] = 1
                    chunks.append(buf)
                    peak = (idx + 1) * chunk_bytes
                await self.env.EFFECTS.record("after")
                return Response.json({"completed": True, "peak_bytes": peak,
                                       "chunks": len(chunks), "caught": None})
            except BaseException as exc:
                caught = type(exc).__name__
                caught_msg = str(exc)[:500]
                return Response.json({"completed": False, "peak_bytes": peak,
                                       "chunks": len(chunks), "caught": caught,
                                       "caught_msg": caught_msg})
        if action == "subrequest_abuse":
            await self.env.EFFECTS.record("before")
            from workers import fetch as wfetch
            attempts = []
            for idx in range(10):
                try:
                    resp = await wfetch("https://example.com/")
                    status = resp.status
                    await resp.body.cancel() if hasattr(resp.body, "cancel") else None
                    attempts.append({"i": idx, "status": status})
                except Exception as exc:
                    attempts.append({"i": idx, "error_type": type(exc).__name__,
                                      "error": str(exc)[:300]})
            await self.env.EFFECTS.record("after")
            return Response.json({"attempts": attempts})
        if action == "output_abuse":
            await self.env.EFFECTS.record("before")
            big = "X" * 2000000
            await self.env.EFFECTS.record("after")
            return Response.json({"big": big, "len": len(big)})
        if action == "normal_accounting":
            await self.env.EFFECTS.record("before")
            start = time.perf_counter()
            total = sum(range(1000))
            elapsed = time.perf_counter() - start
            await self.env.EFFECTS.record("after")
            return Response.json({"total": total, "elapsed_perf": elapsed})
        return Response.json({"error": "unsupported enforcement action"}, status=400)
`;

export function enforcementWorkerCode(canary, effects) {
  return {
    compatibilityDate: '2026-10-30',
    compatibilityFlags: ['python_workers'],
    mainModule: 'main.py',
    modules: {'main.py': {py: enforcementPythonProbe}},
    env: {CANARY: canary, EFFECTS: effects},
    globalOutbound: null,
    limits: {cpuMs: 1000, subRequests: 4},
  };
}
