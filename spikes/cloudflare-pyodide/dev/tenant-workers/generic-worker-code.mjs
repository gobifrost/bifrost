// CONSTANT generic per-tenant Python worker script.
//
// This is the whole point of the pivot: one immutable script per tenant,
// created once via env.LOADER.get(tenantWorkerId, () => genericWorkerCode()).
// Workflow source NEVER appears here; it arrives per attempt as DATA
// (body.modules / body.entry) and is loaded through a virtual import hook
// that records every workspace load for the existing import-audit path.
// The child env is {}: the child holds NO credentials. Where the real
// executor needs a credential to satisfy run_authorized_attempt, the parent
// supplies the existing DUMMY_CREDENTIAL placeholder and the real gateway
// injects the run token (unchanged posture, see tenant-entry.mjs).
//
// Any change to GENERIC_PYTHON below is a redeploy of the same constant
// script, not a per-tenant bake: existing tenantWorkerIds keep working.

export const GENERIC_PYTHON = `
import asyncio
import builtins
import importlib
import importlib.abc
import importlib.machinery
import json
import os
import re
import sys
import time
import types
from pathlib import Path
from workers import WorkerEntrypoint, Response

RECORDED = []
_JWT_RE = re.compile(r'[A-Za-z0-9_-]{10,}\\.[A-Za-z0-9_-]{10,}\\.[A-Za-z0-9_-]{10,}')

class _DataImporter(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def __init__(self, modules):
        self._modules = dict(modules)
    def find_spec(self, name, path=None, target=None):
        if name.split('.')[0] in self._modules:
            return importlib.machinery.ModuleSpec(name, self)
        return None
    def create_module(self, spec):
        return None
    def exec_module(self, module):
        RECORDED.append(module.__name__)
        top = module.__name__.split('.')[0]
        exec(compile(self._modules[top], '<tenant-data>/' + top, 'exec'), module.__dict__)

def _clean():
    _p = Path("/tmp/tenant-hostile-pwn.txt")
    return {
        "builtins": getattr(builtins, "_TENANT_X", None),
        "module": "_tenant_pwn" in sys.modules,
        "module_marker": getattr(sys.modules.get("_tenant_pwn"), "marker", None),
        "env": os.environ.get("TENANT_HOSTILE_SECRET"),
        "fs": _p.read_text() if _p.exists() else None,
    }

class Default(WorkerEntrypoint):
    async def fetch(self, request):
        data = json.loads(await request.text())
        action = data.get("action")
        marker = data.get("marker")
        if action == "honest":
            return Response.json({"clean": _clean()})
        if action == "plant_builtins":
            builtins._TENANT_X = marker
            return Response.json({"planted": True})
        if action == "read_builtins":
            return Response.json({"builtins": getattr(builtins, "_TENANT_X", None)})
        if action == "plant_module":
            _m = types.ModuleType("_tenant_pwn")
            _m.marker = marker
            sys.modules["_tenant_pwn"] = _m
            return Response.json({"planted": True})
        if action == "read_module":
            _m = sys.modules.get("_tenant_pwn")
            return Response.json({
                "present": "_tenant_pwn" in sys.modules,
                "marker": getattr(_m, "marker", None) if _m is not None else None,
            })
        if action == "plant_env":
            os.environ["TENANT_HOSTILE_SECRET"] = marker
            return Response.json({"planted": True})
        if action == "read_env":
            return Response.json({"value": os.environ.get("TENANT_HOSTILE_SECRET")})
        if action == "plant_fs":
            Path("/tmp/tenant-hostile-pwn.txt").write_text(marker)
            return Response.json({"planted": True})
        if action == "read_fs":
            _p = Path("/tmp/tenant-hostile-pwn.txt")
            return Response.json({"exists": _p.exists(), "value": _p.read_text() if _p.exists() else None})
        if action == "slow":
            ms = int(data.get("ms", 500))
            start = time.monotonic()
            await asyncio.sleep(ms / 1000.0)
            end = time.monotonic()
            return Response.json({"marker": marker, "started_at": start, "ended_at": end})
        if action == "run":
            RECORDED.clear()
            modules = data.get("modules") or {}
            entry = data.get("entry")
            # Fresh DATA per attempt: evict prior attempts' virtual modules so
            # the import below re-executes the supplied source. This is module
            # refresh, not residue hiding: builtins/env/fs/sys residue outside
            # the supplied module set (e.g. _tenant_pwn) is left untouched and
            # stays observable as the documented within-tenant limitation.
            for _name in [n for n in sys.modules if n.split('.')[0] in modules]:
                del sys.modules[_name]
            sys.meta_path.insert(0, _DataImporter(modules))
            try:
                mod = importlib.import_module(entry)
                fn = getattr(mod, "main", None)
                result = fn() if callable(fn) else {"imported": True}
            finally:
                sys.meta_path = [f for f in sys.meta_path if not isinstance(f, _DataImporter)]
            return Response.json({"result": result, "recorded": sorted(set(RECORDED))})
        if action == "env_check":
            values = list(os.environ.values())
            shaped = [v for v in values if isinstance(v, str) and _JWT_RE.search(v)]
            return Response.json({"token_shaped_present": len(shaped) > 0,
                                  "dummy_credential_present": "gateway-injected" in values})
        return Response.json({"error": "unsupported action"}, status=400)
`;

export function genericWorkerCode() {
  return {
    compatibilityDate: '2026-10-30',
    compatibilityFlags: ['python_workers'],
    mainModule: 'main.py',
    modules: {'main.py': {py: GENERIC_PYTHON}},
    env: {},
    globalOutbound: null,
    limits: {cpuMs: 1000, subRequests: 4},
  };
}

export async function genericCodeDigest() {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(GENERIC_PYTHON));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, '0')).join('');
}
