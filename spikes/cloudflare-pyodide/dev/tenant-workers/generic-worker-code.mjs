// CONSTANT generic per-tenant Python worker script.
//
// This is the whole point of the pivot: one immutable script per tenant,
// created once via env.LOADER.get(tenantWorkerId, () => genericWorkerCode()).
// Workflow source NEVER appears here; it arrives per attempt as PATH-keyed
// DATA (body.modules: storage path -> source, body.entry: entry storage path)
// and is loaded through a path-keyed virtual import hook that records REAL
// storage paths (e.g. 'workflows/wf.py') for the existing import-audit path.
// Module bytes reach the child only via the in-supervisor TenantGateway
// (GET /api/sdk/modules/<path>, GET /api/sdk/modules-resolve), which verifies
// manifest pins before serving; the child never sees the raw attempt store.
// The child env is {}: the child holds NO credentials. Where the real
// executor needs a credential to satisfy run_authorized_attempt, the parent
// supplies the existing DUMMY_CREDENTIAL placeholder and the real gateway
// injects the run token (unchanged posture, see tenant-entry.mjs).
//
// Any change to GENERIC_PYTHON below is a redeploy of the same constant
// script, not a per-tenant bake: existing tenantWorkerIds keep working.
import {runtimeCode} from '../runtime/runtime-code.mjs';

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
_VIRTUAL_NAMES = set()
_JWT_RE = re.compile(r'[A-Za-z0-9_-]{10,}\\.[A-Za-z0-9_-]{10,}\\.[A-Za-z0-9_-]{10,}')

class _PathImporter(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    # PATH-keyed virtual importer. Keys are storage paths
    # ('workflows/helper.py'); import names map to candidate paths the same
    # way production resolves logical names (dots/slashes -> base path).
    # Every concrete workspace load records its STORAGE PATH, so the parent
    # import audit reconciles paths, not module names. Namespace parents
    # (e.g. 'workflows' for 'workflows.helper') resolve transparently and are
    # not recorded: only concrete module/package loads count.
    def __init__(self, modules):
        self._modules = dict(modules)
    def _concrete(self, name):
        base = name.replace('.', '/')
        for cand in (base + '.py', base + '/__init__.py'):
            if cand in self._modules:
                return cand
        return None
    def find_spec(self, name, path=None, target=None):
        if self._concrete(name) is not None:
            return importlib.machinery.ModuleSpec(name, self)
        base = name.replace('.', '/')
        if any(p.startswith(base + '/') for p in self._modules):
            spec = importlib.machinery.ModuleSpec(name, self, is_package=True)
            spec.submodule_search_locations = []
            return spec
        return None
    def create_module(self, spec):
        return None
    def exec_module(self, module):
        cand = self._concrete(module.__name__)
        if cand is None:
            module.__path__ = []
            _VIRTUAL_NAMES.add(module.__name__)
            return
        RECORDED.append(cand)
        _VIRTUAL_NAMES.add(module.__name__)
        exec(compile(self._modules[cand], '<tenant-path>/' + cand, 'exec'), module.__dict__)

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
            entry_path = data.get("entry_path", data.get("entry"))
            # Entry is a gateway-served storage path, loaded directly (like the
            # real executor loads file_path): no parent import, no name guess.
            if not isinstance(entry_path, str) or entry_path not in modules:
                return Response.json({"error": "unknown entry path"}, status=400)
            # Fresh DATA per attempt: evict only the names this worker loaded
            # through the path importer, so sub-imports below re-execute the
            # supplied source. This is module refresh, not residue hiding:
            # builtins/env/fs/sys residue outside the importer (e.g.
            # _tenant_pwn) is left untouched and stays observable as the
            # documented within-tenant limitation.
            for _name in list(_VIRTUAL_NAMES):
                sys.modules.pop(_name, None)
            _VIRTUAL_NAMES.clear()
            sys.meta_path.insert(0, _PathImporter(modules))
            try:
                entry_mod = types.ModuleType("__tenant_entry__")
                RECORDED.append(entry_path)
                exec(compile(modules[entry_path], '<tenant-path>/' + entry_path, 'exec'), entry_mod.__dict__)
                fn = getattr(entry_mod, "main", None)
                result = fn() if callable(fn) else {"imported": True}
            finally:
                sys.meta_path = [f for f in sys.meta_path if not isinstance(f, _PathImporter)]
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

// Engine mode: the constant child runs the real Bifrost engine instead of the
// minimal path importer, so SDK workflows work. `api` is the supervisor's
// outbound stub (ctx.exports.TenantApi), which rewrites the virtual API host and
// constrains egress. Unlike the minimal child, this one holds the attempt-scoped
// run token, because the engine authenticates SDK calls itself; see
// dev/staging/README.md for the posture note.
export const ENGINE_PYTHON = `
import json
from workers import WorkerEntrypoint, Response
from runtime_runner import run_authorized_attempt

class Default(WorkerEntrypoint):
    async def fetch(self, request):
        body = json.loads(await request.text())
        result = await run_authorized_attempt(
            body["execution_id"], body["context"], body["api_url"],
            public_url=body.get("public_url", "https://staging.invalid"))
        return Response.json({"upstream_result": result})
`;

export function tenantEngineCode(api) {
  const code = runtimeCode(api, ENGINE_PYTHON);
  return {...code, limits: {...code.limits, subRequests: 12}};
}
