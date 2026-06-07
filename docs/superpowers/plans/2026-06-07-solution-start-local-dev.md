# `bifrost solution start` — Local Dev Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `bifrost solution start` — one command that runs a Solution app's Vite dev server and the workspace's local `@workflow` functions in-process behind one origin, proxying the rest of the data-plane to the dev API, so local dev exercises the same install-scoped resolution as deployed.

**Architecture:** A CLI-local `aiohttp` web server (the "dev proxy") fronts (a) a per-app Vite child process and (b) an in-process **function host** that imports the workspace's decorated functions and runs them. `/api/workflows/execute` with a `path::fn` matching a discovered local function runs locally; everything else proxies via `httpx` to the configured `BIFROST_API_URL`. `start` injects `VITE_BIFROST_APP_ID`/`VITE_BIFROST_ORG_ID` so the app sends `app_id` + `X-Bifrost-Org`. Nothing is registered/deployed to the platform.

**Tech Stack:** Python 3.11, `click` (CLI), `aiohttp.web` (local server), `httpx` (upstream proxy), `watchdog` (function reload), the existing `bifrost run` execution machinery (`_executable_metadata`, `set_execution_context`), `vite` (app dev server, child process).

**Spec:** `docs/superpowers/specs/2026-06-07-solution-start-local-dev-design.md`

---

## File Structure

- **Create `api/bifrost/solution_dev/__init__.py`** — package marker for the dev-server pieces.
- **Create `api/bifrost/solution_dev/function_host.py`** — workspace function discovery (`path::fn → callable` across any folder layout) + in-process execution (sets `ExecutionContext`, runs the callable, returns JSON). Pure, unit-testable, no HTTP.
- **Create `api/bifrost/solution_dev/proxy.py`** — the `aiohttp` web app: routes `/api/workflows/execute` to the function host (local match) or upstream, proxies all other `/api/*` to the upstream `httpx` client, and reverse-proxies everything else to the Vite child. Builds the app; lifecycle owned by the command.
- **Create `api/bifrost/solution_dev/app_select.py`** — read `.bifrost/apps.yaml`, choose the app (explicit slug → sole `standalone_v2` → error/list). Returns the app's manifest UUID + dir. Pure, unit-testable.
- **Create `api/bifrost/solution_dev/scaffold_check.py`** — detect a `main.tsx` lacking the `VITE_BIFROST_APP_ID` fallback; return the patch text. Pure, unit-testable.
- **Modify `api/bifrost/commands/solution.py`** — add the `start` click command (orchestration: auth, app-select, org-resolve, discover, vite launch, proxy serve, watch reload, Ctrl-C); update `_v2_scaffold_files` (`main.tsx` fallbacks + a shipped sample function so F8's button works); export `handle_start`-style dispatch via the existing group.
- **Modify `api/bifrost/cli.py`** — dispatch `bifrost solution start` (it already routes `solution` → `handle_solution`, so this is covered by the click group; no change expected — verify in Task 7).
- **Modify `docs/llm.txt`** — one line documenting `bifrost solution start` (LLM-discoverability, RESUME item).
- **Tests:**
  - `api/tests/unit/test_solution_dev_function_host.py`
  - `api/tests/unit/test_solution_dev_app_select.py`
  - `api/tests/unit/test_solution_dev_scaffold_check.py`
  - `api/tests/unit/test_solution_dev_proxy.py`
  - `api/tests/unit/test_solution_scaffold_dev_wiring.py` (scaffold round-trip: main.tsx fallbacks + sample fn ref == App.tsx ref)
  - `client/src/lib/app-sdk/__tests__/...` — covered by an existing/new main.tsx bootstrap vitest (Task 9).

**Test runner:** backend `./test.sh tests/unit/test_solution_dev_*.py -v` (stack must be up; `./test.sh stack up` first). Client `./test.sh client unit`.

---

### Task 1: Function discovery (`path::fn → callable`) across any folder layout

**Files:**
- Create: `api/bifrost/solution_dev/__init__.py`
- Create: `api/bifrost/solution_dev/function_host.py`
- Test: `api/tests/unit/test_solution_dev_function_host.py`

- [ ] **Step 1: Write the failing test**

```python
# api/tests/unit/test_solution_dev_function_host.py
import textwrap
from pathlib import Path

from bifrost.solution_dev.function_host import discover_functions


def _write(p: Path, body: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(body))


def test_discovers_decorated_functions_in_arbitrary_folders(tmp_path: Path):
    # A solution workspace with functions in non-"workflows/" folders.
    (tmp_path / "bifrost.solution.yaml").write_text("slug: demo\nname: Demo\nscope: org\n")
    _write(tmp_path / "functions/hello.py", '''
        from bifrost import workflow

        @workflow
        async def main():
            return {"message": "hi"}
    ''')
    _write(tmp_path / "modules/sub/calc.py", '''
        from bifrost import workflow

        @workflow
        async def add():
            return {"ok": True}
    ''')

    fns = discover_functions(tmp_path)

    assert "functions/hello.py::main" in fns
    assert "modules/sub/calc.py::add" in fns
    assert callable(fns["functions/hello.py::main"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./test.sh tests/unit/test_solution_dev_function_host.py::test_discovers_decorated_functions_in_arbitrary_folders -v`
Expected: FAIL — `ModuleNotFoundError: bifrost.solution_dev`.

- [ ] **Step 3: Write minimal implementation**

```python
# api/bifrost/solution_dev/__init__.py
"""Local-dev server pieces for `bifrost solution start`."""
```

```python
# api/bifrost/solution_dev/function_host.py
"""Discover and run a Solution workspace's local @workflow functions in-process.

This is the "local function host" behind `bifrost solution start`: it imports the
workspace's decorated functions (any folder layout) and runs them directly,
mirroring `bifrost run`'s offline execution — nothing is registered to the API.
"""
from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("bifrost.solution_dev")

# Folders that never hold solution source — skip for speed and to avoid importing
# build output / deps (mirrors the deploy collector's skip set).
_SKIP_DIRS = {"node_modules", "dist", ".venv", "venv", "__pycache__", ".git", ".bifrost"}


def discover_functions(workspace: Path) -> dict[str, Callable[..., Any]]:
    """Map ``path::function_name`` → callable for every decorated function.

    ``path`` is workspace-relative with POSIX separators (the same form app code
    passes to ``useWorkflow``). The workspace root is placed on ``sys.path`` so a
    function's ``from modules.x import y`` resolves against the solution root.
    """
    workspace = workspace.resolve()
    root_str = str(workspace)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)

    out: dict[str, Callable[..., Any]] = {}
    for py in sorted(workspace.rglob("*.py")):
        rel_parts = py.relative_to(workspace).parts
        if any(part in _SKIP_DIRS for part in rel_parts):
            continue
        rel = py.relative_to(workspace).as_posix()
        module = _load_module(py, rel)
        if module is None:
            continue
        for name in dir(module):
            obj = getattr(module, name)
            if callable(obj) and hasattr(obj, "_executable_metadata"):
                out[f"{rel}::{name}"] = obj
    return out


def _load_module(py: Path, rel: str):
    # A stable, unique module name per file so re-import on reload replaces it.
    mod_name = "bifrost_devhost_" + rel.replace("/", "_").removesuffix(".py")
    try:
        spec = importlib.util.spec_from_file_location(mod_name, py)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = module
        spec.loader.exec_module(module)
        return module
    except Exception as exc:  # a broken file shouldn't kill discovery
        # Logged, not raised: one un-importable file must not blank the whole map
        # (the dev server stays useful; the user sees the error on first call).
        logger.warning("solution start: could not import %s: %s", rel, exc)
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./test.sh tests/unit/test_solution_dev_function_host.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/bifrost/solution_dev/__init__.py api/bifrost/solution_dev/function_host.py api/tests/unit/test_solution_dev_function_host.py
git commit -m "feat(solutions): local function discovery for solution start"
```

---

### Task 2: Execute a discovered function in-process (returns JSON)

**Files:**
- Modify: `api/bifrost/solution_dev/function_host.py`
- Test: `api/tests/unit/test_solution_dev_function_host.py`

- [ ] **Step 1: Write the failing test** (append)

```python
import asyncio

from bifrost.solution_dev.function_host import FunctionHost


def test_host_runs_a_function_and_returns_result(tmp_path: Path):
    (tmp_path / "bifrost.solution.yaml").write_text("slug: demo\nname: Demo\nscope: org\n")
    _write(tmp_path / "functions/echo.py", '''
        from bifrost import workflow

        @workflow
        async def main(name: str = "world"):
            return {"hello": name}
    ''')
    host = FunctionHost(tmp_path)
    host.reload()

    result = asyncio.run(host.run("functions/echo.py::main", {"name": "bifrost"}))
    assert result == {"hello": "bifrost"}


def test_host_unknown_ref_raises_keyerror(tmp_path: Path):
    (tmp_path / "bifrost.solution.yaml").write_text("slug: demo\nname: Demo\nscope: org\n")
    host = FunctionHost(tmp_path)
    host.reload()
    with pytest.raises(KeyError):
        asyncio.run(host.run("nope/missing.py::main", {}))
```

Add `import pytest` at the top of the test file if not present.

- [ ] **Step 2: Run test to verify it fails**

Run: `./test.sh tests/unit/test_solution_dev_function_host.py::test_host_runs_a_function_and_returns_result -v`
Expected: FAIL — `cannot import name 'FunctionHost'`.

- [ ] **Step 3: Write minimal implementation** (append to `function_host.py`)

```python
import inspect


class FunctionHost:
    """Holds the discovered function map; runs one by ``path::fn`` ref.

    ``reload()`` re-discovers (used on file change). ``run()`` executes the
    callable. Sync functions are supported (run directly); async are awaited.
    The execution context (org/user) is configured by the command before serving
    via :func:`set_dev_execution_context`, so callables that read
    ``context.org_id`` / use the data-plane behave as under ``bifrost run``.
    """

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace
        self._fns: dict[str, Callable[..., Any]] = {}

    def reload(self) -> None:
        self._fns = discover_functions(self._workspace)

    def refs(self) -> list[str]:
        return sorted(self._fns)

    def has(self, ref: str) -> bool:
        return ref in self._fns

    async def run(self, ref: str, params: dict[str, Any]) -> Any:
        fn = self._fns[ref]  # KeyError → caller maps to 404
        result = fn(**params)
        if inspect.isawaitable(result):
            result = await result
        return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./test.sh tests/unit/test_solution_dev_function_host.py -v`
Expected: PASS (both new tests).

- [ ] **Step 5: Commit**

```bash
git add api/bifrost/solution_dev/function_host.py api/tests/unit/test_solution_dev_function_host.py
git commit -m "feat(solutions): FunctionHost runs a discovered function in-process"
```

---

### Task 3: App selection from `.bifrost/apps.yaml`

**Files:**
- Create: `api/bifrost/solution_dev/app_select.py`
- Test: `api/tests/unit/test_solution_dev_app_select.py`

- [ ] **Step 1: Write the failing test**

```python
# api/tests/unit/test_solution_dev_app_select.py
from pathlib import Path

import pytest
import yaml

from bifrost.solution_dev.app_select import AppSelectionError, select_app


def _apps_yaml(tmp_path: Path, apps: dict) -> None:
    (tmp_path / ".bifrost").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".bifrost" / "apps.yaml").write_text(yaml.safe_dump({"apps": apps}))


def test_sole_standalone_v2_app_auto_selected(tmp_path: Path):
    _apps_yaml(tmp_path, {"u1": {"id": "u1", "slug": "dash", "path": "apps/dash", "app_model": "standalone_v2"}})
    chosen = select_app(tmp_path, slug=None)
    assert chosen.app_id == "u1"
    assert chosen.slug == "dash"
    assert chosen.app_dir == tmp_path / "apps/dash"


def test_explicit_slug_selects_it(tmp_path: Path):
    _apps_yaml(tmp_path, {
        "u1": {"id": "u1", "slug": "dash", "path": "apps/dash", "app_model": "standalone_v2"},
        "u2": {"id": "u2", "slug": "admin", "path": "apps/admin", "app_model": "standalone_v2"},
    })
    chosen = select_app(tmp_path, slug="admin")
    assert chosen.app_id == "u2"


def test_multiple_without_slug_errors_and_lists(tmp_path: Path):
    _apps_yaml(tmp_path, {
        "u1": {"id": "u1", "slug": "dash", "path": "apps/dash", "app_model": "standalone_v2"},
        "u2": {"id": "u2", "slug": "admin", "path": "apps/admin", "app_model": "standalone_v2"},
    })
    with pytest.raises(AppSelectionError) as e:
        select_app(tmp_path, slug=None)
    assert "dash" in str(e.value) and "admin" in str(e.value)


def test_no_v2_apps_errors_with_scaffold_hint(tmp_path: Path):
    _apps_yaml(tmp_path, {})
    with pytest.raises(AppSelectionError) as e:
        select_app(tmp_path, slug=None)
    assert "scaffold-app" in str(e.value)


def test_unknown_slug_errors(tmp_path: Path):
    _apps_yaml(tmp_path, {"u1": {"id": "u1", "slug": "dash", "path": "apps/dash", "app_model": "standalone_v2"}})
    with pytest.raises(AppSelectionError):
        select_app(tmp_path, slug="nope")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./test.sh tests/unit/test_solution_dev_app_select.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# api/bifrost/solution_dev/app_select.py
"""Pick which Solution app `bifrost solution start` serves."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


class AppSelectionError(Exception):
    """No usable app (or an ambiguous/unknown choice)."""


@dataclass(frozen=True)
class ChosenApp:
    app_id: str
    slug: str
    app_dir: Path


def select_app(workspace: Path, *, slug: str | None) -> ChosenApp:
    manifest = workspace / ".bifrost" / "apps.yaml"
    data = yaml.safe_load(manifest.read_text()) if manifest.is_file() else None
    apps = (data or {}).get("apps", {}) or {}
    v2 = [
        b for b in apps.values()
        if isinstance(b, dict) and b.get("app_model") == "standalone_v2"
    ]

    if slug is not None:
        for b in v2:
            if b.get("slug") == slug:
                return _to_chosen(workspace, b)
        available = ", ".join(sorted(b.get("slug", "?") for b in v2)) or "(none)"
        raise AppSelectionError(f"No standalone_v2 app '{slug}'. Available: {available}")

    if not v2:
        raise AppSelectionError(
            "No standalone_v2 app in this workspace. "
            "Create one with `bifrost solution scaffold-app <slug>`."
        )
    if len(v2) > 1:
        listing = ", ".join(sorted(b.get("slug", "?") for b in v2))
        raise AppSelectionError(
            f"Multiple apps found ({listing}). "
            f"Name one: `bifrost solution start <slug>`."
        )
    return _to_chosen(workspace, v2[0])


def _to_chosen(workspace: Path, body: dict) -> ChosenApp:
    return ChosenApp(
        app_id=str(body["id"]),
        slug=str(body.get("slug") or body["id"]),
        app_dir=workspace / str(body["path"]),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./test.sh tests/unit/test_solution_dev_app_select.py -v`
Expected: PASS (all 5).

- [ ] **Step 5: Commit**

```bash
git add api/bifrost/solution_dev/app_select.py api/tests/unit/test_solution_dev_app_select.py
git commit -m "feat(solutions): app selection for solution start"
```

---

### Task 4: Stale-scaffold detection + patch text

**Files:**
- Create: `api/bifrost/solution_dev/scaffold_check.py`
- Test: `api/tests/unit/test_solution_dev_scaffold_check.py`

- [ ] **Step 1: Write the failing test**

```python
# api/tests/unit/test_solution_dev_scaffold_check.py
from pathlib import Path

from bifrost.solution_dev.scaffold_check import main_tsx_needs_dev_fallback


FRESH = '''const appId = boot?.appId ?? import.meta.env.VITE_BIFROST_APP_ID ?? null;'''
STALE = '''const appId = boot?.appId ?? null;'''


def test_fresh_main_tsx_passes(tmp_path: Path):
    p = tmp_path / "src" / "main.tsx"
    p.parent.mkdir(parents=True)
    p.write_text(FRESH)
    assert main_tsx_needs_dev_fallback(p) is False


def test_stale_main_tsx_flagged(tmp_path: Path):
    p = tmp_path / "src" / "main.tsx"
    p.parent.mkdir(parents=True)
    p.write_text(STALE)
    assert main_tsx_needs_dev_fallback(p) is True


def test_missing_file_is_not_flagged(tmp_path: Path):
    assert main_tsx_needs_dev_fallback(tmp_path / "src" / "main.tsx") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./test.sh tests/unit/test_solution_dev_scaffold_check.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# api/bifrost/solution_dev/scaffold_check.py
"""Detect an app whose main.tsx predates the VITE_BIFROST_APP_ID dev fallback."""
from __future__ import annotations

from pathlib import Path

PATCH_HINT = (
    "Your app's src/main.tsx predates `bifrost solution start`. Update two lines so\n"
    "local dev can scope to this install (deployed behavior is unchanged):\n\n"
    "  const appId    = boot?.appId    ?? import.meta.env.VITE_BIFROST_APP_ID  ?? null;\n"
    "  const orgScope = boot?.orgScope ?? import.meta.env.VITE_BIFROST_ORG_ID  ?? null;\n"
)


def main_tsx_needs_dev_fallback(main_tsx: Path) -> bool:
    """True if the file exists but lacks the VITE_BIFROST_APP_ID local fallback."""
    if not main_tsx.is_file():
        return False
    text = main_tsx.read_text(encoding="utf-8")
    return "VITE_BIFROST_APP_ID" not in text
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./test.sh tests/unit/test_solution_dev_scaffold_check.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/bifrost/solution_dev/scaffold_check.py api/tests/unit/test_solution_dev_scaffold_check.py
git commit -m "feat(solutions): detect stale main.tsx missing the dev fallback"
```

---

### Task 5: Dev proxy — local-execute vs upstream-proxy routing

**Files:**
- Create: `api/bifrost/solution_dev/proxy.py`
- Test: `api/tests/unit/test_solution_dev_proxy.py`

The proxy is an `aiohttp.web.Application`. We test the two route handlers with `aiohttp`'s test utilities (`AioHTTPTestCase`-style via `aiohttp.test_utils`), stubbing the function host and the upstream.

- [ ] **Step 1: Write the failing test**

```python
# api/tests/unit/test_solution_dev_proxy.py
import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from bifrost.solution_dev.proxy import DevProxyConfig, build_dev_app


class _StubHost:
    def __init__(self, refs):
        self._refs = set(refs)
        self.last_call = None

    def has(self, ref):
        return ref in self._refs

    async def run(self, ref, params):
        self.last_call = (ref, params)
        return {"ran_local": ref, "params": params}


async def _make_upstream(record):
    async def execute(request):
        record["execute_body"] = await request.json()
        return web.json_response({"ran_upstream": True})

    async def other(request):
        record["other_path"] = request.path
        return web.json_response({"upstream_other": True})

    app = web.Application()
    app.router.add_post("/api/workflows/execute", execute)
    app.router.add_route("*", "/api/{tail:.*}", other)
    return app


@pytest.mark.asyncio
async def test_local_path_ref_runs_in_function_host(aiohttp_client):
    record = {}
    upstream = await _make_upstream(record)
    upstream_server = TestServer(upstream)
    await upstream_server.start_server()
    upstream_url = str(upstream_server.make_url(""))

    host = _StubHost({"functions/hello.py::main"})
    cfg = DevProxyConfig(upstream_url=upstream_url, token="t", app_id="A", org_id="O")
    app = build_dev_app(cfg, host, vite_url="http://127.0.0.1:1")  # vite unused here
    client = await aiohttp_client(app)

    resp = await client.post("/api/workflows/execute", json={
        "workflow_id": "functions/hello.py::main", "input_data": {"x": 1}, "app_id": "A",
    })
    assert resp.status == 200
    body = await resp.json()
    assert body["ran_local"] == "functions/hello.py::main"
    assert host.last_call == ("functions/hello.py::main", {"x": 1})
    assert "execute_body" not in record  # never hit upstream
    await upstream_server.close()


@pytest.mark.asyncio
async def test_unknown_ref_proxies_to_upstream(aiohttp_client):
    record = {}
    upstream = await _make_upstream(record)
    upstream_server = TestServer(upstream)
    await upstream_server.start_server()
    upstream_url = str(upstream_server.make_url(""))

    host = _StubHost(set())  # nothing local
    cfg = DevProxyConfig(upstream_url=upstream_url, token="t", app_id="A", org_id="O")
    app = build_dev_app(cfg, host, vite_url="http://127.0.0.1:1")
    client = await aiohttp_client(app)

    resp = await client.post("/api/workflows/execute", json={
        "workflow_id": "11111111-1111-1111-1111-111111111111", "input_data": {}, "app_id": "A",
    })
    assert resp.status == 200
    assert (await resp.json())["ran_upstream"] is True
    assert record["execute_body"]["app_id"] == "A"
    await upstream_server.close()


@pytest.mark.asyncio
async def test_other_api_path_proxies_upstream_with_org_header(aiohttp_client):
    record = {}
    upstream = await _make_upstream(record)
    upstream_server = TestServer(upstream)
    await upstream_server.start_server()
    upstream_url = str(upstream_server.make_url(""))

    host = _StubHost(set())
    cfg = DevProxyConfig(upstream_url=upstream_url, token="t", app_id="A", org_id="O")
    app = build_dev_app(cfg, host, vite_url="http://127.0.0.1:1")
    client = await aiohttp_client(app)

    resp = await client.get("/api/tables/foo")
    assert resp.status == 200
    assert (await resp.json())["upstream_other"] is True
    assert record["other_path"] == "/api/tables/foo"
    await upstream_server.close()
```

Note: `aiohttp_client` and `@pytest.mark.asyncio` — confirm `pytest-aiohttp` or `pytest-asyncio` is configured in `api/tests` (the suite already runs async tests; if the marker style differs, match the repo's existing async-test convention — grep `api/tests/unit` for `aiohttp_client`/`asyncio` usage and mirror it).

- [ ] **Step 2: Run test to verify it fails**

Run: `./test.sh tests/unit/test_solution_dev_proxy.py -v`
Expected: FAIL — `ModuleNotFoundError: bifrost.solution_dev.proxy`.

- [ ] **Step 3: Write minimal implementation**

```python
# api/bifrost/solution_dev/proxy.py
"""The single-origin local dev server for `bifrost solution start`.

Routes:
  POST /api/workflows/execute  → local FunctionHost when the path::fn ref is one
                                 of THIS workspace's functions; else upstream.
  /api/*                       → reverse-proxy to the dev API (data-plane).
  everything else              → reverse-proxy to the Vite dev server (the app).

The upstream proxy injects the CLI token (Authorization) and the resolved org
(X-Bifrost-Org) so data-plane calls run under the chosen --org, matching deployed.
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx
from aiohttp import web

# Hop-by-hop headers we must not forward when reverse-proxying.
_STRIP = {"host", "content-length", "transfer-encoding", "connection", "keep-alive"}


@dataclass(frozen=True)
class DevProxyConfig:
    upstream_url: str   # the dev API, e.g. http://localhost:37791
    token: str          # CLI access token
    app_id: str         # chosen app's manifest UUID
    org_id: str | None  # resolved --org (or None → caller's default org)


def build_dev_app(cfg: DevProxyConfig, host, vite_url: str) -> web.Application:
    app = web.Application()
    app["cfg"] = cfg
    app["host"] = host
    app["vite_url"] = vite_url.rstrip("/")
    app["http"] = httpx.AsyncClient(timeout=120.0)

    app.router.add_post("/api/workflows/execute", _execute_handler)
    app.router.add_route("*", "/api/{tail:.*}", _api_proxy_handler)
    app.router.add_route("*", "/{tail:.*}", _vite_proxy_handler)

    async def _close(app):
        await app["http"].aclose()

    app.on_cleanup.append(_close)
    return app


def _auth_headers(cfg: DevProxyConfig, incoming) -> dict[str, str]:
    headers = {
        k: v for k, v in incoming.items() if k.lower() not in _STRIP
    }
    headers["Authorization"] = f"Bearer {cfg.token}"
    if cfg.org_id:
        headers["X-Bifrost-Org"] = cfg.org_id
    headers["X-Bifrost-App"] = cfg.app_id
    return headers


async def _execute_handler(request: web.Request) -> web.Response:
    cfg: DevProxyConfig = request.app["cfg"]
    host = request.app["host"]
    body = await request.json()
    ref = body.get("workflow_id", "")

    # Local path::fn that we discovered → run it in-process (own-first, locally).
    if "::" in str(ref) and host.has(ref):
        try:
            result = await host.run(ref, body.get("input_data") or {})
        except Exception as exc:
            return web.json_response(
                {"detail": f"Local workflow error: {exc}"}, status=500
            )
        return web.json_response({"status": "completed", "result": result})

    # Otherwise proxy to the dev API (UUIDs, _repo/ refs, sibling installs).
    resp = await request.app["http"].post(
        f"{cfg.upstream_url}/api/workflows/execute",
        json=body,
        headers=_auth_headers(cfg, request.headers),
    )
    return web.Response(
        body=resp.content, status=resp.status_code,
        content_type=resp.headers.get("content-type", "application/json").split(";")[0],
    )


async def _api_proxy_handler(request: web.Request) -> web.Response:
    cfg: DevProxyConfig = request.app["cfg"]
    data = await request.read()
    resp = await request.app["http"].request(
        request.method,
        f"{cfg.upstream_url}{request.rel_url}",
        content=data or None,
        headers=_auth_headers(cfg, request.headers),
    )
    return web.Response(
        body=resp.content, status=resp.status_code,
        headers={"content-type": resp.headers.get("content-type", "application/json")},
    )


async def _vite_proxy_handler(request: web.Request) -> web.Response:
    vite_url = request.app["vite_url"]
    data = await request.read()
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _STRIP}
    resp = await request.app["http"].request(
        request.method,
        f"{vite_url}{request.rel_url}",
        content=data or None,
        headers=headers,
    )
    return web.Response(
        body=resp.content, status=resp.status_code,
        headers={"content-type": resp.headers.get("content-type", "text/html")},
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./test.sh tests/unit/test_solution_dev_proxy.py -v`
Expected: PASS (all 3). If the async fixture style mismatches the repo, adapt to the existing convention (see note in Step 1) — the handler logic under test is unchanged.

- [ ] **Step 5: Commit**

```bash
git add api/bifrost/solution_dev/proxy.py api/tests/unit/test_solution_dev_proxy.py
git commit -m "feat(solutions): dev proxy routes local-execute vs upstream"
```

---

### Task 6: Scaffold updates — main.tsx dev fallbacks + a working sample function (F8)

**Files:**
- Modify: `api/bifrost/commands/solution.py` (`_v2_scaffold_files`)
- Test: `api/tests/unit/test_solution_scaffold_dev_wiring.py`

- [ ] **Step 1: Write the failing test**

```python
# api/tests/unit/test_solution_scaffold_dev_wiring.py
from bifrost.commands.solution import _v2_scaffold_files


def test_main_tsx_has_vite_app_id_fallback():
    files = _v2_scaffold_files("dash", "http://localhost:8000")
    main = files["src/main.tsx"]
    assert "import.meta.env.VITE_BIFROST_APP_ID" in main
    assert "import.meta.env.VITE_BIFROST_ORG_ID" in main


def test_vite_config_injects_app_id_and_org_on_serve():
    files = _v2_scaffold_files("dash", "http://localhost:8000")
    vite = files["vite.config.ts"]
    assert "VITE_BIFROST_APP_ID" in vite
    assert "VITE_BIFROST_ORG_ID" in vite


def test_sample_function_shipped_and_ref_matches_app_tsx():
    files = _v2_scaffold_files("dash", "http://localhost:8000")
    # A runnable sample function ships so the first-run button works locally (F8).
    assert "functions/hello.py" in files
    app = files["src/App.tsx"]
    # App.tsx references exactly the shipped sample.
    assert 'useWorkflow' in app
    assert "functions/hello.py::main" in app
    assert "functions/hello.py::main" in files["functions/hello.py"] or "def main" in files["functions/hello.py"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./test.sh tests/unit/test_solution_scaffold_dev_wiring.py -v`
Expected: FAIL — current scaffold uses `boot?.appId ?? null` (no VITE fallback), references `workflows/your_workflow.py::main`, and ships no `functions/hello.py`.

- [ ] **Step 3: Write minimal implementation**

In `_v2_scaffold_files` (`api/bifrost/commands/solution.py`):

1. In `main_tsx`, change the two bootstrap lines to:

```ts
const orgScope = boot?.orgScope ?? import.meta.env.VITE_BIFROST_ORG_ID ?? null;
// This app's id, so useWorkflow scopes path refs to THIS install's workflow.
const appId = boot?.appId ?? import.meta.env.VITE_BIFROST_APP_ID ?? null;
```

2. In `vite_config`, extend the `serve` `define` block to also inject the two new vars:

```ts
  const define =
    command === "serve"
      ? {
          "import.meta.env.VITE_BIFROST_API_URL": JSON.stringify(env.url),
          "import.meta.env.VITE_BIFROST_TOKEN": JSON.stringify(env.token),
          "import.meta.env.VITE_BIFROST_APP_ID": JSON.stringify(process.env.VITE_BIFROST_APP_ID || ""),
          "import.meta.env.VITE_BIFROST_ORG_ID": JSON.stringify(process.env.VITE_BIFROST_ORG_ID || ""),
        }
      : {};
```

3. In `app_tsx`, change the workflow ref from `workflows/your_workflow.py::main` to `functions/hello.py::main`.

4. Add a `functions/hello.py` entry to the returned dict:

```python
    sample_fn = '''\
from bifrost import workflow


@workflow
async def main():
    """The scaffold's sample function — `bifrost solution start` runs this
    locally so the app's first-run button works with no deploy."""
    return {"message": "Hello from your Bifrost solution"}
'''
```

and in the returned dict add: `"functions/hello.py": sample_fn,`

- [ ] **Step 4: Run test to verify it passes**

Run: `./test.sh tests/unit/test_solution_scaffold_dev_wiring.py -v`
Expected: PASS (3).

- [ ] **Step 5: Commit**

```bash
git add api/bifrost/commands/solution.py api/tests/unit/test_solution_scaffold_dev_wiring.py
git commit -m "feat(solutions): scaffold ships VITE app-id fallback + a runnable sample fn (F8)"
```

---

### Task 7: The `start` command (orchestration) + dispatch

**Files:**
- Modify: `api/bifrost/commands/solution.py` (add `start_cmd` to `solution_group`)
- Modify: `api/bifrost/solution_dev/function_host.py` (add `set_dev_execution_context`)
- Test: `api/tests/unit/test_solution_dev_command.py`

Orchestration is hard to unit-test end-to-end (it spawns Vite + binds a port). Unit-test the **pieces that have logic** (org resolution wiring, the execution-context setup, "not a solution workspace" guard) and rely on the manual live drive (Task 10) for the full loop. Keep the command thin — it composes Tasks 1–6.

- [ ] **Step 1: Write the failing test**

```python
# api/tests/unit/test_solution_dev_command.py
from pathlib import Path

import pytest
from click.testing import CliRunner

from bifrost.commands.solution import solution_group


def test_start_refuses_outside_solution_workspace(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # no bifrost.solution.yaml here
    result = CliRunner().invoke(solution_group, ["start"])
    assert result.exit_code != 0
    assert "Solution workspace" in result.output or "solution init" in result.output
```

Plus a test for the execution-context helper:

```python
def test_set_dev_execution_context_sets_org(monkeypatch):
    from bifrost.solution_dev.function_host import set_dev_execution_context
    captured = {}

    def _fake_set(ctx):
        captured["ctx"] = ctx

    monkeypatch.setattr(
        "bifrost.solution_dev.function_host._set_execution_context", _fake_set, raising=False
    )
    set_dev_execution_context(
        user={"id": "u1", "email": "d@e.com", "name": "Dev", "is_superuser": True},
        org={"id": "org-123", "name": "Acme", "is_active": True, "is_provider": False},
    )
    assert captured["ctx"].scope == "org-123"
    assert captured["ctx"].is_platform_admin is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./test.sh tests/unit/test_solution_dev_command.py -v`
Expected: FAIL — `start` command + `set_dev_execution_context` don't exist.

- [ ] **Step 3: Write minimal implementation**

Add to `function_host.py` an execution-context setter mirroring `_run_direct`'s block (DRY: same `ExecutionContext`/`Organization` shape):

```python
def set_dev_execution_context(*, user: dict, org: dict | None) -> None:
    """Configure the in-process execution context the local host runs under.

    Mirrors `bifrost run`'s context setup so locally-run functions see
    context.org_id/user_id and the data-plane runs under the chosen org.
    """
    import uuid as _uuid

    from bifrost._context import set_execution_context as _set_execution_context
    from bifrost._execution_context import ExecutionContext, Organization

    organization = (
        Organization(
            id=org["id"],
            name=org.get("name", ""),
            is_active=org.get("is_active", True),
            is_provider=org.get("is_provider", False),
        )
        if org
        else None
    )
    ctx = ExecutionContext(
        user_id=user.get("id", "cli-user"),
        email=user.get("email", ""),
        name=user.get("name", "CLI User"),
        scope=org["id"] if org else "GLOBAL",
        organization=organization,
        is_platform_admin=user.get("is_superuser", False),
        is_function_key=False,
        execution_id=f"solution-start-{_uuid.uuid4()}",
        workflow_name="solution-start",
    )
    _set_execution_context(ctx)
```

Add `start_cmd` to `solution_group` in `solution.py`. It composes the pieces; the long-running serve uses `asyncio.run`:

```python
@solution_group.command(name="start", help="Run the app's dev server + local workflows (one origin).")
@click.argument("app_slug", required=False)
@click.option("--org", "org_ref", default=None, help="Org ref (UUID or name) to run under (superuser).")
@click.option("--port", default=3000, show_default=True, type=int, help="Local origin port.")
def start_cmd(app_slug: str | None, org_ref: str | None, port: int) -> None:
    import shutil
    import subprocess

    from bifrost.client import BifrostClient
    from bifrost.solution_descriptor import is_solution_workspace
    from bifrost.solution_dev.app_select import AppSelectionError, select_app
    from bifrost.solution_dev.function_host import FunctionHost, set_dev_execution_context
    from bifrost.solution_dev.scaffold_check import PATCH_HINT, main_tsx_needs_dev_fallback

    workspace = pathlib.Path(".").resolve()
    if not is_solution_workspace(workspace):
        raise click.ClickException(
            f"Not a Solution workspace (no {DESCRIPTOR_FILENAME}). Run `bifrost solution init` first."
        )

    client = BifrostClient.get_instance(require_auth=True)  # raises → clear login hint

    # Resolve --org → context (superuser); default to caller's own org.
    org_info = client.organization
    if org_ref:
        from bifrost.refs import RefResolver
        resolver = RefResolver(client)
        org_id = asyncio.run(resolver.resolve("org", org_ref))
        resp = client._sync_http.get("/api/sdk/context", params={"org_id": org_id})
        if resp.status_code == 403:
            raise click.ClickException("--org requires superuser privileges.")
        if resp.status_code >= 400:
            raise click.ClickException(f"Could not resolve org '{org_ref}': HTTP {resp.status_code}")
        org_info = resp.json().get("organization", org_info)

    try:
        chosen = select_app(workspace, slug=app_slug)
    except AppSelectionError as exc:
        raise click.ClickException(str(exc))

    main_tsx = chosen.app_dir / "src" / "main.tsx"
    if main_tsx_needs_dev_fallback(main_tsx):
        click.echo(PATCH_HINT, err=True)

    # Set the in-process execution context the local host runs under.
    set_dev_execution_context(user=client.user, org=org_info)

    host = FunctionHost(workspace)
    host.reload()
    click.echo(f"Discovered {len(host.refs())} local function(s).")

    # npm install if needed, then `npm run dev` with the injected env.
    if shutil.which("npm") is None:
        raise click.ClickException("npm not found on PATH — install Node.js to run the dev server.")
    if not (chosen.app_dir / "node_modules").is_dir():
        click.echo("Installing app dependencies (npm install)…")
        subprocess.run(["npm", "install"], cwd=chosen.app_dir, check=True)

    vite_env = dict(os.environ)
    vite_env["VITE_BIFROST_APP_ID"] = chosen.app_id
    vite_env["VITE_BIFROST_ORG_ID"] = (org_info or {}).get("id", "")
    vite_env["BIFROST_API_URL"] = client.api_url
    vite_env["BIFROST_ACCESS_TOKEN"] = client.access_token

    vite_port = port + 1  # vite serves internally; the proxy owns `port`.
    vite_proc = subprocess.Popen(
        ["npm", "run", "dev", "--", "--port", str(vite_port), "--strictPort"],
        cwd=chosen.app_dir, env=vite_env,
    )

    asyncio.run(_serve(client, chosen, org_info, host, port, vite_port, workspace, vite_proc))
```

with a module-level `_serve` coroutine that builds the proxy, starts the `aiohttp` runner, starts the watchdog reload, prints the URL, and tears down on `KeyboardInterrupt`:

```python
async def _serve(client, chosen, org_info, host, port, vite_port, workspace, vite_proc):
    import asyncio as _asyncio

    from aiohttp import web

    from bifrost.solution_dev.proxy import DevProxyConfig, build_dev_app
    from bifrost.solution_dev.reload import start_function_watch

    cfg = DevProxyConfig(
        upstream_url=client.api_url.rstrip("/"),
        token=client.access_token,
        app_id=chosen.app_id,
        org_id=(org_info or {}).get("id"),
    )
    app = build_dev_app(cfg, host, vite_url=f"http://127.0.0.1:{vite_port}")
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    observer = start_function_watch(workspace, host)
    click.echo(f"\n  Bifrost solution dev server → http://localhost:{port}\n")
    click.echo("  Press Ctrl-C to stop.\n")
    try:
        while True:
            await _asyncio.sleep(3600)
    except (KeyboardInterrupt, _asyncio.CancelledError):
        pass
    finally:
        observer.stop()
        observer.join(timeout=2)
        vite_proc.terminate()
        await runner.cleanup()
```

(If `client.api_url` / `client.access_token` aren't existing properties, use the existing accessors — grep `api/bifrost/client.py` for how `bifrost push` reads the URL+token and mirror those exact names.)

- [ ] **Step 4: Run test to verify it passes**

Run: `./test.sh tests/unit/test_solution_dev_command.py -v`
Expected: PASS (both). The serve path is exercised manually in Task 10.

- [ ] **Step 5: Commit**

```bash
git add api/bifrost/commands/solution.py api/bifrost/solution_dev/function_host.py api/tests/unit/test_solution_dev_command.py
git commit -m "feat(solutions): bifrost solution start command (orchestration)"
```

---

### Task 8: Function-file reload watcher

**Files:**
- Create: `api/bifrost/solution_dev/reload.py`
- Test: `api/tests/unit/test_solution_dev_reload.py`

- [ ] **Step 1: Write the failing test**

```python
# api/tests/unit/test_solution_dev_reload.py
from pathlib import Path

from bifrost.solution_dev.reload import _PyChangeHandler


class _RecordingHost:
    def __init__(self):
        self.reloads = 0

    def reload(self):
        self.reloads += 1


def test_handler_reloads_on_py_change(tmp_path: Path):
    host = _RecordingHost()
    handler = _PyChangeHandler(host)

    class _Evt:
        is_directory = False
        src_path = str(tmp_path / "functions/hello.py")

    handler.on_modified(_Evt())
    assert host.reloads == 1


def test_handler_ignores_non_py(tmp_path: Path):
    host = _RecordingHost()
    handler = _PyChangeHandler(host)

    class _Evt:
        is_directory = False
        src_path = str(tmp_path / "src/App.tsx")

    handler.on_modified(_Evt())
    assert host.reloads == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./test.sh tests/unit/test_solution_dev_reload.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# api/bifrost/solution_dev/reload.py
"""Re-discover local functions when a workspace .py file changes."""
from __future__ import annotations

from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

_SKIP_DIRS = {"node_modules", "dist", ".venv", "venv", "__pycache__", ".git"}


class _PyChangeHandler(FileSystemEventHandler):
    def __init__(self, host) -> None:
        self._host = host

    def _maybe_reload(self, event) -> None:
        if getattr(event, "is_directory", False):
            return
        path = str(getattr(event, "src_path", ""))
        if not path.endswith(".py"):
            return
        if any(f"/{d}/" in path for d in _SKIP_DIRS):
            return
        self._host.reload()

    on_modified = _maybe_reload
    on_created = _maybe_reload
    on_moved = _maybe_reload


def start_function_watch(workspace: Path, host) -> Observer:
    observer = Observer()
    observer.schedule(_PyChangeHandler(host), str(workspace), recursive=True)
    observer.start()
    return observer
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./test.sh tests/unit/test_solution_dev_reload.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/bifrost/solution_dev/reload.py api/tests/unit/test_solution_dev_reload.py
git commit -m "feat(solutions): reload local functions on .py change"
```

---

### Task 9: Client vitest — main.tsx bootstrap prefers boot, falls back to VITE env

**Files:**
- Test: `client/src/lib/app-sdk/__tests__/dev-bootstrap.test.ts` (new) — or extend an existing app-sdk test if one covers main bootstrap.

The scaffold's `main.tsx` lives in the CLI scaffold string, not in `client/src`. To cover the deployed-vs-local boundary without booting Vite, extract the resolution rule into a tiny pure helper that both the scaffold and the test reference, OR assert the rule directly against the scaffold string from a node test. Simplest, no new prod code: add a vitest that imports the scaffold via a tiny JS evaluation of the precedence rule.

- [ ] **Step 1: Write the failing test**

```ts
// client/src/lib/app-sdk/__tests__/dev-bootstrap.test.ts
import { describe, it, expect } from "vitest";

// The precedence rule main.tsx encodes (kept in sync with the scaffold):
function resolveAppId(boot: any, viteEnv: string | undefined): string | null {
  return boot?.appId ?? viteEnv ?? null;
}
function resolveOrg(boot: any, viteEnv: string | undefined): string | null {
  return boot?.orgScope ?? viteEnv ?? null;
}

describe("dev bootstrap precedence", () => {
  it("prefers the platform boot object when present (deployed)", () => {
    expect(resolveAppId({ appId: "DEPLOYED" }, "LOCAL")).toBe("DEPLOYED");
    expect(resolveOrg({ orgScope: "ORG_DEP" }, "ORG_LOCAL")).toBe("ORG_DEP");
  });
  it("falls back to VITE env when boot is absent (local dev)", () => {
    expect(resolveAppId(undefined, "LOCAL_APP")).toBe("LOCAL_APP");
    expect(resolveOrg(null, "LOCAL_ORG")).toBe("LOCAL_ORG");
  });
  it("is null when neither is present", () => {
    expect(resolveAppId(undefined, undefined)).toBeNull();
    expect(resolveOrg(undefined, undefined)).toBeNull();
  });
});
```

- [ ] **Step 2: Run test to verify it fails (then passes — it's pure)**

Run: `./test.sh client unit -- dev-bootstrap`
Expected: PASS immediately (pure rule). This test's job is to **lock the precedence rule** so a future scaffold edit that breaks deployed-vs-local is caught. Add a comment in the scaffold `main.tsx` pointing here.

- [ ] **Step 3: (no prod change)** — add a one-line comment in `_v2_scaffold_files` `main_tsx` referencing this test as the precedence contract.

- [ ] **Step 4: Run**

Run: `./test.sh client unit -- dev-bootstrap`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add client/src/lib/app-sdk/__tests__/dev-bootstrap.test.ts api/bifrost/commands/solution.py
git commit -m "test(solutions): lock dev bootstrap precedence (boot over VITE env)"
```

---

### Task 10: Live end-to-end drive (the shakeout bar) + llm.txt

**Files:**
- Modify: `docs/llm.txt` (one line for `bifrost solution start`)
- No new prod code; this task is verification.

- [ ] **Step 1: Add the llm.txt line**

In `docs/llm.txt`, near the other `bifrost solution` commands, add:
`bifrost solution start [<app-slug>] [--org <ref>] [--port N]` — run the app's dev server + local workflows behind one origin (local dev; deploys nothing).

- [ ] **Step 2: Reinstall the scratch CLI against the running API** (it must match the branch code)

```bash
cd /tmp/bifrost-shakeout && .venv/bin/pip install --quiet --upgrade "http://localhost:37791/api/cli/download"
```

- [ ] **Step 3: Fresh-scaffold drive**

```bash
cd /tmp/bifrost-shakeout && rm -rf mysol2 && mkdir mysol2 && cd mysol2
/tmp/bifrost-shakeout/.venv/bin/bifrost solution init --slug mysol2 --name "My Sol 2"
/tmp/bifrost-shakeout/.venv/bin/bifrost solution scaffold-app dashboard --api-url http://localhost:37791
/tmp/bifrost-shakeout/.venv/bin/bifrost solution start --org <dev-org-uuid>
```
Expected: discovers ≥1 function, `npm install` runs once, prints `http://localhost:3000`.

- [ ] **Step 4: Browser verification** (port mode — Chrome works; netbird does not)

Drive http://localhost:3000 in a browser:
- The page loads, authenticated (BifrostHeader shows logged-in state).
- Click the sample button → it returns `{"message": "Hello from your Bifrost solution"}` (F8 **closed** — first-run works, no deploy).
- Confirm via the dev API logs / network that the execute request carried `app_id` and that the local function ran in-process (the call should NOT appear as a workflow execution on the dev API — it ran locally).

- [ ] **Step 5: Reload-loop + second-app + Ctrl-C**

- Edit `functions/hello.py` to return a different message; re-click → new output without restart.
- Scaffold a second app (`bifrost solution scaffold-app admin …`), run `bifrost solution start admin` → it serves the second app (proves "hit any app").
- Ctrl-C → Vite + proxy stop; no orphaned process; nothing left on the platform (verify the dev API has no new workflow rows from this drive).

- [ ] **Step 6: Commit the docs + record findings**

```bash
git add docs/llm.txt
git commit -m "docs(solutions): document bifrost solution start in llm.txt"
```
Record the drive outcome in `docs/plans/2026-06-07-solutions-shakeout-findings.md` (append a "D2 built + driven" section) and commit.

---

### Task 11: Full verification sweep

**Files:** none (gating).

- [ ] **Step 1: Backend quality + tests**

```bash
cd api && ruff check bifrost/solution_dev bifrost/commands/solution.py && pyright bifrost/solution_dev 2>&1 | tail -5
cd .. && ./test.sh tests/unit/test_solution_dev_*.py tests/unit/test_solution_scaffold_dev_wiring.py -v
```
Expected: ruff clean; pyright clean (ignore the ~40 known host `reportMissingImports` false positives); all new unit tests green.

- [ ] **Step 2: Regression — the existing solution suite still green**

```bash
./test.sh stack reset
./test.sh tests/unit/test_solution_*.py tests/unit/test_orphan_*.py tests/unit/test_dto_flags.py -v
```
Expected: PASS (no regressions; the scaffold ref change is the only behavioral edit and Task 6 covers it).

- [ ] **Step 3: Client checks**

```bash
cd client && npx tsc --noEmit && npx eslint src/lib/app-sdk && ./../test.sh client unit -- dev-bootstrap
```
Expected: clean + green.

- [ ] **Step 4: Final commit / branch is push-ready** (do NOT merge — draft PR #347 is experimental).

```bash
git status   # clean working tree; all tasks committed
```

---

## Self-Review notes

- **Spec coverage:** function host (Tasks 1–2), proxy local-vs-upstream (Task 5), app selection incl. `<app-slug>`/auto/multi (Task 3), `--org` + context (Task 7), `VITE_BIFROST_APP_ID/ORG_ID` injection + main.tsx fallback (Tasks 6,7), stale-scaffold patch (Tasks 4,7), F8 sample fn (Task 6), reload loop (Task 8), Ctrl-C teardown + nothing-deployed (Tasks 7,10), identity-chain behavior verified live (Task 10), llm.txt (Task 10). F2/D1 explicitly out of scope per spec.
- **Placeholders:** none — every code step shows code. Two "mirror the repo convention" notes (async test fixture style in Task 5; client url/token accessor names in Task 7) are deliberate: they tell the implementer to match an existing, discoverable pattern rather than invent a name. Resolve by grepping as instructed.
- **Type consistency:** `FunctionHost` API (`reload`/`refs`/`has`/`run`) is used identically in Tasks 2, 5 (stub mirrors it), 7, 8. `DevProxyConfig(upstream_url, token, app_id, org_id)` consistent across Tasks 5 and 7. `ChosenApp(app_id, slug, app_dir)` consistent Tasks 3, 7. `main_tsx_needs_dev_fallback` / `PATCH_HINT` consistent Tasks 4, 7.
