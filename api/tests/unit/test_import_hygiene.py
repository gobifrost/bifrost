"""Import-closure hygiene: forbidden heavyweights must not load at import time.

These tests are the regression lock for the memory-slimming work
(clean template entrypoint + lazy-import pass). Each case imports a
role's entry closure in a fresh interpreter and fails if a forbidden
top-level package appears in sys.modules. No DB, no network.

Issue #1379 extends the same technique to the dev-only closure: the
production image installs requirements.lock (runtime closure) while
dev/CI install requirements-dev.lock, so each entry closure must also
import cleanly with every dev-only module blocked.
"""
import json
import subprocess
import sys

import pytest

# Each test spawns a fresh Python interpreter via subprocess to inspect a
# module's import closure — that's ~1.5s per case. Marked `slow` so the
# every-PR unit lane skips them; they still run in `./test.sh all` and nightly.
pytestmark = pytest.mark.slow

HEAVY = {"fastapi", "starlette", "uvicorn", "anthropic", "openai", "mcp", "numpy", "pgvector"}
# The worker-entry and template modules must be stdlib-thin, not merely heavy-free:
THIN_EXTRA = {"sqlalchemy", "pydantic", "redis", "httpx", "apscheduler", "src.worker.pool_main"}


def closure_roots(module: str) -> set[str]:
    code = (
        f"import json, sys; import {module}; "
        "print(json.dumps(sorted({m.split('.')[0] for m in sys.modules} "
        "| {m for m in sys.modules if m == 'src.worker.pool_main'})))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=120, cwd="/app",
    )
    assert out.returncode == 0, f"import {module} failed:\n{out.stderr}"
    return set(json.loads(out.stdout))


def test_worker_entry_is_stdlib_thin():
    # Install the startup signal handler before loading the application.
    assert closure_roots("src.worker.main") & (HEAVY | THIN_EXTRA) == set()

def test_template_process_module_is_stdlib_thin():
    assert closure_roots("src.services.execution.template_process") & (HEAVY | THIN_EXTRA) == set()

def test_executor_closure_has_no_heavyweights():
    assert closure_roots("src.worker.pool_main") & HEAVY == set()

def test_scheduler_closure_has_no_heavyweights():
    assert closure_roots("src.scheduler.main") & HEAVY == set()


# Issue #1379: dev-only packages (test/debug) ship in
# requirements-dev.lock, never in the production image's requirements.lock.
# Block every dev-only module in a fresh interpreter: entry closures must
# import cleanly, proving the imports are unreachable and not merely
# unexercised. (Absence from sys.modules alone would not catch a lazy import
# that only fires on a worker code path.)
DEV_ONLY = {
    "pytest",
    "pytest_asyncio",
    "_pytest",
    "pytest_cov",
    "pytest_env",
    "pytest_timeout",
    "coverage",  # via pytest-cov
    "debugpy",
    "watchdog",  # dev-loop only in-image: `bifrost watch` / `solution dev`
    "pluggy",  # transitive-only (via pytest), never imported directly
    "iniconfig",  # transitive-only (via pytest), never imported directly
}


def import_without_dev(module: str) -> None:
    poison = "; ".join(f"sys.modules[{m!r}] = None" for m in sorted(DEV_ONLY))
    code = f"import sys; {poison}; import {module}"
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=120,
        cwd="/app",
    )
    assert out.returncode == 0, (
        f"import {module} needs dev-only packages:\n{out.stderr}"
    )


def test_worker_entry_imports_without_dev_packages():
    import_without_dev("src.worker.main")


def test_scheduler_entry_imports_without_dev_packages():
    import_without_dev("src.scheduler.main")


def test_executor_entry_imports_without_dev_packages():
    import_without_dev("src.worker.pool_main")


def test_template_process_imports_without_dev_packages():
    import_without_dev("src.services.execution.template_process")


def test_api_entry_imports_without_dev_packages():
    import_without_dev("src.main")


def test_cli_entry_imports_without_dev_packages():
    # The exact worker shell-out path: shim -> `python -m bifrost`.
    poison = "; ".join(f"sys.modules[{m!r}] = None" for m in sorted(DEV_ONLY))
    code = (
        f"import sys; {poison}; import runpy; "
        "sys.argv = ['bifrost', '--help']; "
        "runpy.run_module('bifrost', run_name='__main__')"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=120,
        cwd="/app",
    )
    assert out.returncode == 0, (
        f"python -m bifrost --help needs dev-only packages:\n{out.stderr}"
    )
