"""Import-closure hygiene: forbidden heavyweights must not load at import time.

These tests are the regression lock for the memory-slimming work
(template spawn re-import + lazy-import pass). Each case imports a
role's entry closure in a fresh interpreter and fails if a forbidden
top-level package appears in sys.modules. No DB, no network.
"""
import json
import subprocess
import sys

HEAVY = {"fastapi", "starlette", "uvicorn", "anthropic", "openai", "mcp", "numpy", "pgvector"}
# The spawn-entry and template modules must be stdlib-thin, not merely heavy-free:
THIN_EXTRA = {"sqlalchemy", "pydantic", "redis", "httpx", "aio_pika", "apscheduler", "src.worker.app"}


def closure_roots(module: str) -> set[str]:
    code = (
        f"import json, sys; import {module}; "
        "print(json.dumps(sorted({m.split('.')[0] for m in sys.modules} "
        "| {m for m in sys.modules if m == 'src.worker.app'})))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=120, cwd="/app",
    )
    assert out.returncode == 0, f"import {module} failed:\n{out.stderr}"
    return set(json.loads(out.stdout))


def test_worker_spawn_entry_is_stdlib_thin():
    # multiprocessing spawn re-imports this module into the template process.
    assert closure_roots("src.worker.main") & (HEAVY | THIN_EXTRA) == set()

def test_template_process_module_is_stdlib_thin():
    assert closure_roots("src.services.execution.template_process") & (HEAVY | THIN_EXTRA) == set()

def test_worker_app_closure_has_no_heavyweights():
    assert closure_roots("src.worker.app") & HEAVY == set()

def test_scheduler_closure_has_no_heavyweights():
    assert closure_roots("src.scheduler.main") & HEAVY == set()
