// Hostile fresh-isolate qualification (Phase 1). Fixed diagnostic code only.
// Never cached: supervisor always uses LOADER.load (fresh interpreter per attempt).
// No workflow authority, no durable state, no Bifrost credentials.
export const hostilePythonProbe = `
import asyncio
import builtins
import json
import os
import sys
import types
from pathlib import Path
from workers import WorkerEntrypoint, Response

def _clean_observation():
    import builtins as _b
    import json as _j
    import os as _o
    import sys as _s
    from pathlib import Path as _P
    _p = _P("/tmp/hostile-pwn.txt")
    _hp = _P("/tmp/helper-pwn.txt")
    return {
        "builtins": getattr(_b, "_HOSTILE_X", None),
        "stdlib_json": getattr(_j, "_hostile_mut", None),
        "module": "_hostile_pwn" in _s.modules,
        "env": _o.environ.get("HOSTILE_SECRET"),
        "fs": _p.read_text() if _p.exists() else None,
        "helper_builtins": getattr(_b, "_HELPER_BUILTINS", None),
        "helper_env": _o.environ.get("HELPER_ENV"),
        "helper_module": "_helper_pwn" in _s.modules,
        "helper_fs": _hp.read_text() if _hp.exists() else None,
    }

class Default(WorkerEntrypoint):
    async def fetch(self, request):
        data = json.loads(await request.text())
        action = data["action"]
        marker = data.get("marker")
        if action == "honest":
            return Response.json({"clean": _clean_observation()})
        if action == "plant_builtins":
            builtins._HOSTILE_X = marker
            import json as _j
            _j._hostile_mut = marker
            return Response.json({"planted": True})
        if action == "read_builtins":
            import json as _j
            return Response.json({
                "builtins": getattr(builtins, "_HOSTILE_X", None),
                "stdlib_json": getattr(_j, "_hostile_mut", None),
            })
        if action == "plant_module":
            _m = types.ModuleType("_hostile_pwn")
            _m.marker = marker
            sys.modules["_hostile_pwn"] = _m
            return Response.json({"planted": True})
        if action == "read_module":
            _m = sys.modules.get("_hostile_pwn")
            return Response.json({
                "present": "_hostile_pwn" in sys.modules,
                "marker": getattr(_m, "marker", None) if _m is not None else None,
            })
        if action == "plant_env":
            os.environ["HOSTILE_SECRET"] = marker
            return Response.json({"planted": True})
        if action == "read_env":
            return Response.json({"value": os.environ.get("HOSTILE_SECRET")})
        if action == "plant_fs":
            Path("/tmp/hostile-pwn.txt").write_text(marker)
            return Response.json({"planted": True})
        if action == "read_fs":
            _p = Path("/tmp/hostile-pwn.txt")
            return Response.json({"exists": _p.exists(), "value": _p.read_text() if _p.exists() else None})
        if action == "plant_creds":
            secret = self.env.CANARY_SECRET
            builtins._CRED_BUILTINS = secret
            os.environ["CRED_ENV"] = secret
            _m = types.ModuleType("_cred_mod")
            _m.secret = secret
            sys.modules["_cred_mod"] = _m
            Path("/tmp/cred-secret.txt").write_text(secret)
            globals()["_cred_global"] = secret
            return Response.json({"planted": True})
        if action == "read_creds":
            # Attempt every reachable path. Return presence booleans only, never secret values.
            _p = Path("/tmp/cred-secret.txt")
            _sdk = None
            try:
                import bifrost  # noqa
                _sdk = "importable"
            except Exception as _exc:
                _sdk = f"{type(_exc).__name__}"
            return Response.json({
                "builtins_present": hasattr(builtins, "_CRED_BUILTINS"),
                "env_present": "CRED_ENV" in os.environ,
                "module_present": "_cred_mod" in sys.modules,
                "fs_present": _p.exists(),
                "global_present": "_cred_global" in globals(),
                "canary_secret_env_present": getattr(self.env, "CANARY_SECRET", None) is not None,
                "sdk_import": _sdk,
            })
        if action == "plant_concurrent":
            builtins._HOSTILE_X = marker
            os.environ["HOSTILE_SECRET"] = marker
            _m = types.ModuleType("_hostile_pwn")
            _m.marker = marker
            sys.modules["_hostile_pwn"] = _m
            Path("/tmp/hostile-pwn.txt").write_text(marker)
            return Response.json({"planted": marker})
        if action == "read_concurrent":
            import json as _j
            _p = Path("/tmp/hostile-pwn.txt")
            return Response.json({
                "builtins": getattr(builtins, "_HOSTILE_X", None),
                "env": os.environ.get("HOSTILE_SECRET"),
                "module": getattr(sys.modules.get("_hostile_pwn"), "marker", None) if "_hostile_pwn" in sys.modules else None,
                "fs": _p.read_text() if _p.exists() else None,
                "stdlib_json": getattr(_j, "_hostile_mut", None),
            })
        if action == "import_plant":
            from evil_helper import plant
            plant(marker)
            return Response.json({"planted": True})
        if action == "import_read":
            from evil_helper import read
            return Response.json({"observed": read()})
        return Response.json({"error": "unsupported hostile action"}, status=400)
`;

export const evilHelperCode = `
import builtins
import os
import sys
import types
from pathlib import Path

def plant(marker):
    builtins._HELPER_BUILTINS = marker
    os.environ["HELPER_ENV"] = marker
    _m = types.ModuleType("_helper_pwn")
    _m.marker = marker
    sys.modules["_helper_pwn"] = _m
    Path("/tmp/helper-pwn.txt").write_text(marker)
    return True

def read():
    _p = Path("/tmp/helper-pwn.txt")
    return {
        "builtins": getattr(builtins, "_HELPER_BUILTINS", None),
        "env": os.environ.get("HELPER_ENV"),
        "module": getattr(sys.modules.get("_helper_pwn"), "marker", None) if "_helper_pwn" in sys.modules else None,
        "module_present": "_helper_pwn" in sys.modules,
        "fs": _p.read_text() if _p.exists() else None,
    }
`;

export function hostileWorkerCode(canary, effects, secret = null, withHelper = false) {
  const modules = {'main.py': {py: hostilePythonProbe}};
  if (withHelper) modules['evil_helper.py'] = {py: evilHelperCode};
  const env = {CANARY: canary, EFFECTS: effects};
  if (secret !== null) env.CANARY_SECRET = secret;
  return {
    compatibilityDate: '2026-10-30',
    compatibilityFlags: ['python_workers'],
    mainModule: 'main.py',
    modules,
    env,
    globalOutbound: null,
    limits: {cpuMs: 1000, subRequests: 4},
  };
}
