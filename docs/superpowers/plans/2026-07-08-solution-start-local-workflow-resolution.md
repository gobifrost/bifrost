# Solution Start Local Workflow Resolution Implementation Plan (rev 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `bifrost solution start` resolve the Solution-under-development's workflow refs (path::function, name, manifest UUID) against the local filesystem before ever asking the platform, make every local-dev failure loud and actionable, and stop the upstream fallback from resolving cloud Solution copies.

**Architecture:** Extend the local `FunctionHost` into a local workflow resolver: it indexes decorated functions from disk plus aliases from `.bifrost/workflows.yaml`, and it tracks files that *failed to import* so a broken file is a loud local error instead of a silent fall-through to the deployed copy. The proxy resolves locally first; misses proxy upstream only when the descriptor sets `global_repo_access: true`, and that fallback request is stripped of **every** signal the server derives install scope from (body `solution_id`/`form_id`/`app_id` and the `X-Bifrost-App` header) — not just body `solution_id`. All local-dev errors return HTTP 200 with `{"error": ...}` because that is the only shape `useWorkflow` surfaces in the app.

**Tech Stack:** Python 3.11+ (CLI package), aiohttp local proxy, Click, PyYAML, watchdog, pytest via `./test.sh`.

## Global Constraints

- **CLI-only change.** Everything lives under `api/bifrost/` + `api/tests/unit/`. No server code, no DTO changes, no CONTRACT_VERSION concerns, no type generation.
- **`api/bifrost/` must never import `src.*`** (the packaged CLI has no `src` on its path).
- **Error contract:** anything the browser app must display returns HTTP **200** with `{"error": "<message>"}`. `useWorkflow` reads `body.error` only on 200; on non-200 it shows bare `statusText` (documented in `proxy.py` at the run-error block). Only "dev API unreachable" keeps its 502.
- **No unrequested fallbacks** (house rule). The manifest reader honors the `path` key only — exactly what deploy's `_collect_workflows` honors.
- Tests run with `./test.sh tests/unit/<file>.py -v` from the worktree root (stack must be booted once: `./test.sh stack up`). JUnit XML lands at `/tmp/bifrost-<project>/test-results.xml`.
- Execute in a **fresh worktree branched from main**. The `solution-start-workflow-debug` worktree has unrelated uncommitted changes (`api/src/core/module_cache_sync.py`) that must NOT be committed with this work.

---

## Why rev 2 (review findings this revision fixes)

1. **The rev-1 boundary was insufficient.** The server derives install scope with precedence ctx (`X-Bifrost-App` header → `Application.solution_id`, resolved at auth) > body `solution_id` > body `form_id` > body `app_id` (`api/src/services/solution_scope.py::derive_execution_solution_scope`). The proxy sends `X-Bifrost-App` on every request and the SDK puts `app_id` in the body. For **capture-born** installs (the v1→v2 migration path) entity ids are preserved server-side, so those signals resolve the install and the fallback would still run the cloud Solution copy. Fix: strip all four signals from the workflow-execute fallback. Consequence (accepted, documented): a non-admin dev's fallback execution of a `_repo` workflow can no longer authorize via "app uses this workflow" — it needs platform admin or form access. That failure is a loud 403; the alternative was silent wrong-copy execution.
2. **Manifest-UUID refs are works-locally-breaks-deployed** for deploy-born installs (deploy remaps every id to `uuid5(install, manifest_id)` and never rewrites app source). We still resolve them locally (for capture-born installs they're real), but print a one-time console warning teaching the portable shapes, and the scaffold comment advertises only name and path::function.
3. **Error contract:** rev 1 returned 404/409 JSON that the app renders as bare "Not Found"/"Conflict". Rev 2 returns 200 + `{"error": ...}`.
4. **Import failures no longer fall through.** A broken `.py` previously dropped out of discovery, so its refs proxied upstream and ran the deployed copy. Now the host tracks failures and resolution raises a local error carrying the import exception.
5. **Test reality:** `_StubHost` gains `resolve()`/`refs()`; `test_unknown_ref_proxies_to_upstream` (which asserts the removed solution_id-forwarding behavior) is deleted and replaced by the new gate tests.
6. **Stale alias index:** the watcher now also reloads on `.bifrost/workflows.yaml` changes and echoes a reload summary.

## Resolution Rules (the contract Tasks 1–3 implement)

Given `ref = body["workflow_id"]` on `POST /api/workflows/execute` at the local proxy:

1. **Ambiguous name** (two manifest entries share `name == ref` pointing at different local functions) → HTTP 200 `{"error": "workflow name '<ref>' is ambiguous..."}`. Never guess, never proxy.
2. **Ref resolves locally** (discovered `path::fn`, or manifest UUID/name whose entry points at a discovered function) → run in-process. If the ref is a bare UUID, print a one-time stderr warning that UUID refs don't survive deploy remapping.
3. **Ref's target file failed to import** (a `path::fn` whose file is in the failure map, or a manifest UUID/name whose entry's path is) → HTTP 200 `{"error": "<file> failed to import: <exception>"}`. Never proxy.
4. **Miss, `global_repo_access: false`** (default) → HTTP 200 `{"error": "Workflow '<ref>' not found... Local refs: <list>..."}`. Never proxy.
5. **Miss, `global_repo_access: true`** → proxy upstream with body fields `solution_id`, `form_id`, `app_id` removed and the `X-Bifrost-App` header removed (case-insensitively). `Authorization` and `X-Bifrost-Org` are kept. Upstream status/body pass through unchanged.
6. **Data-plane `/api/*` proxying is untouched** — it still appends `?solution=` and sends `X-Bifrost-App` (that's F2, correct for tables/configs/files).

## Current Code Map

- `api/bifrost/solution_dev/function_host.py` — discovery (`discover_functions`), `FunctionHost` (`reload`/`refs`/`has`/`run`). `_load_module` swallows import errors into a logger nobody configures.
- `api/bifrost/solution_dev/proxy.py` — `DevProxyConfig`, `_execute_handler` (local only when `"::" in ref and host.has(ref)`; otherwise stamps `body["solution_id"]` and proxies), `_auth_headers` (always adds `X-Bifrost-App`).
- `api/bifrost/solution_dev/reload.py` — watchdog handler; reloads on `.py` only, skips `.bifrost/` entirely, no feedback.
- `api/bifrost/commands/solution.py` — `start_cmd` (line ~2107, prints only a function count), `_serve` (line ~2642, builds `DevProxyConfig` without `global_repo_access`), `deploy_cmd` (line ~1711), `_collect_workflows` (line ~918), scaffold `App.tsx` comment (line ~570).
- `api/bifrost/solution_descriptor.py` — `SolutionDescriptor.global_repo_access` already exists.
- Tests: `api/tests/unit/test_solution_dev_function_host.py` (`_write` helper dedents), `test_solution_dev_proxy.py` (`_StubHost`, `_make_upstream`, `_serve`, `_free_port` helpers), `test_solution_dev_reload.py` (`_RecordingHost`), `test_solution_dev_command.py`.

---

## Task 1: Track Import Failures in Discovery

**Files:**
- Modify: `api/bifrost/solution_dev/function_host.py`
- Test: `api/tests/unit/test_solution_dev_function_host.py`

**Interfaces:**
- Produces: `discover_functions(workspace) -> tuple[dict[str, Callable], dict[str, str]]` (fns by `path::fn` ref, failures by workspace-relative POSIX path → one-line error string). `FunctionHost.failures() -> dict[str, str]`. Existing `reload()`, `refs()`, `run()` unchanged in behavior.

- [ ] **Step 1: Write the failing tests**

Add to `api/tests/unit/test_solution_dev_function_host.py`:

```python
def test_discovery_records_import_failures(tmp_path: Path):
    (tmp_path / "bifrost.solution.yaml").write_text("slug: demo\nname: Demo\n")
    _write(tmp_path / "functions/good.py", '''
        from bifrost import workflow

        @workflow
        async def main():
            return {"ok": True}
    ''')
    _write(tmp_path / "functions/broken.py", '''
        import does_not_exist_anywhere

        from bifrost import workflow

        @workflow
        async def main():
            return {"ok": True}
    ''')

    fns, failures = discover_functions(tmp_path)

    assert "functions/good.py::main" in fns
    assert not any(ref.startswith("functions/broken.py") for ref in fns)
    assert "functions/broken.py" in failures
    assert "does_not_exist_anywhere" in failures["functions/broken.py"]


def test_host_exposes_failures(tmp_path: Path):
    (tmp_path / "bifrost.solution.yaml").write_text("slug: demo\nname: Demo\n")
    _write(tmp_path / "functions/broken.py", '''
        raise RuntimeError("boom at import time")
    ''')

    host = FunctionHost(tmp_path)
    host.reload()

    assert host.refs() == []
    assert "boom at import time" in host.failures()["functions/broken.py"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./test.sh tests/unit/test_solution_dev_function_host.py -v`
Expected: the two new tests FAIL (`discover_functions` returns a dict, not a tuple; `FunctionHost.failures` doesn't exist). Pre-existing tests in the file also fail on the unpack once you get to Step 3 — that's the signature change; Step 3 updates them.

- [ ] **Step 3: Implement**

In `api/bifrost/solution_dev/function_host.py`:

Change `discover_functions` (and delete the `logging` import + `logger` — this was its only consumer; the failures map replaces it, and Tasks 4–5 echo failures to the console):

```python
def discover_functions(
    workspace: Path,
) -> tuple[dict[str, Callable[..., Any]], dict[str, str]]:
    """Map ``path::function_name`` → callable for every decorated function.

    Also returns a map of workspace-relative paths that FAILED to import →
    one-line error. A broken file must be a loud local error at resolution
    time, not a silent drop that lets the ref fall through to the platform
    (which would run the stale deployed copy — the exact confusion a local
    debug loop exists to prevent).

    ``path`` is workspace-relative with POSIX separators (the same form app
    code passes to ``useWorkflow``). The workspace root is placed on
    ``sys.path`` so a function's ``from modules.x import y`` resolves against
    the solution root.
    """
    workspace = workspace.resolve()
    root_str = str(workspace)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)

    out: dict[str, Callable[..., Any]] = {}
    failures: dict[str, str] = {}
    for py in sorted(workspace.rglob("*.py")):
        rel_parts = py.relative_to(workspace).parts
        if any(part in _SKIP_DIRS for part in rel_parts):
            continue
        rel = py.relative_to(workspace).as_posix()
        module, err = _load_module(py, rel)
        if err is not None:
            failures[rel] = err
            continue
        if module is None:
            continue
        for name in dir(module):
            obj = getattr(module, name)
            if callable(obj) and hasattr(obj, "_executable_metadata"):
                out[f"{rel}::{name}"] = obj
    return out, failures


def _load_module(py: Path, rel: str) -> tuple[ModuleType | None, str | None]:
    # A stable, unique module name per file so re-import on reload replaces it.
    mod_name = "bifrost_devhost_" + rel.replace("/", "_").removesuffix(".py")
    try:
        spec = importlib.util.spec_from_file_location(mod_name, py)
        if spec is None or spec.loader is None:
            return None, "could not build an import spec for this file"
        module = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = module
        spec.loader.exec_module(module)
        return module, None
    except Exception as exc:  # one broken file must not blank the whole map
        return None, f"{type(exc).__name__}: {exc}"
```

Update `FunctionHost`:

```python
    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace
        self._fns: dict[str, Callable[..., Any]] = {}
        self._failures: dict[str, str] = {}

    def reload(self) -> None:
        self._fns, self._failures = discover_functions(self._workspace)

    def failures(self) -> dict[str, str]:
        return dict(self._failures)
```

Update the one pre-existing direct caller in the test file (`fns = discover_functions(tmp_path)` at line ~32) to `fns, _ = discover_functions(tmp_path)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `./test.sh tests/unit/test_solution_dev_function_host.py -v`
Expected: ALL pass (new + pre-existing).

- [ ] **Step 5: Commit**

```bash
git add api/bifrost/solution_dev/function_host.py api/tests/unit/test_solution_dev_function_host.py
git commit -m "feat(solution-start): track import failures in local function discovery"
```

## Task 2: Local Workflow Index — UUID/Name/Path Resolution

**Files:**
- Modify: `api/bifrost/solution_dev/function_host.py`
- Test: `api/tests/unit/test_solution_dev_function_host.py`

**Interfaces:**
- Consumes: Task 1's `(fns, failures)` shape.
- Produces (Task 3 imports these from `bifrost.solution_dev.function_host`):
  - `class LocalWorkflowError(Exception)` — base
  - `class LocalWorkflowResolutionError(LocalWorkflowError)` — ambiguous name
  - `class LocalWorkflowImportError(LocalWorkflowError)` — target file failed to import
  - `FunctionHost.resolve(ref: str) -> str | None` — returns the local `path::fn` ref, `None` on a clean miss, raises the two errors above. **`FunctionHost.has()` is deleted** (its only caller was the proxy branch this plan replaces; a predicate that raises is a landmine).

- [ ] **Step 1: Write the failing tests**

Add to `api/tests/unit/test_solution_dev_function_host.py` (note: `pytest` is already imported):

```python
from bifrost.solution_dev.function_host import (
    LocalWorkflowImportError,
    LocalWorkflowResolutionError,
)


def test_host_resolves_manifest_uuid_and_name_to_local_ref(tmp_path: Path):
    (tmp_path / "bifrost.solution.yaml").write_text("slug: demo\nname: Demo\n")
    _write(tmp_path / "functions/preview.py", '''
        from bifrost import workflow

        @workflow
        async def recipients():
            return {"ok": True}
    ''')
    _write(tmp_path / ".bifrost/workflows.yaml", '''
        workflows:
          11111111-1111-1111-1111-111111111111:
            id: 11111111-1111-1111-1111-111111111111
            name: Preview Recipients
            path: functions/preview.py
            function_name: recipients
    ''')

    host = FunctionHost(tmp_path)
    host.reload()

    assert host.resolve("functions/preview.py::recipients") == "functions/preview.py::recipients"
    assert host.resolve("11111111-1111-1111-1111-111111111111") == "functions/preview.py::recipients"
    assert host.resolve("Preview Recipients") == "functions/preview.py::recipients"
    assert host.resolve("No Such Workflow") is None


def test_host_rejects_ambiguous_local_workflow_name(tmp_path: Path):
    (tmp_path / "bifrost.solution.yaml").write_text("slug: demo\nname: Demo\n")
    _write(tmp_path / "functions/a.py", '''
        from bifrost import workflow

        @workflow
        async def main():
            return {"a": True}
    ''')
    _write(tmp_path / "functions/b.py", '''
        from bifrost import workflow

        @workflow
        async def main():
            return {"b": True}
    ''')
    _write(tmp_path / ".bifrost/workflows.yaml", '''
        workflows:
          11111111-1111-1111-1111-111111111111:
            name: Duplicate
            path: functions/a.py
            function_name: main
          22222222-2222-2222-2222-222222222222:
            name: Duplicate
            path: functions/b.py
            function_name: main
    ''')

    host = FunctionHost(tmp_path)
    host.reload()

    with pytest.raises(LocalWorkflowResolutionError, match="ambiguous"):
        host.resolve("Duplicate")


def test_host_raises_import_error_for_broken_target(tmp_path: Path):
    (tmp_path / "bifrost.solution.yaml").write_text("slug: demo\nname: Demo\n")
    _write(tmp_path / "functions/broken.py", '''
        import does_not_exist_anywhere
    ''')
    _write(tmp_path / ".bifrost/workflows.yaml", '''
        workflows:
          11111111-1111-1111-1111-111111111111:
            name: Broken One
            path: functions/broken.py
            function_name: main
    ''')

    host = FunctionHost(tmp_path)
    host.reload()

    # All three ref shapes surface the import error instead of a clean miss.
    with pytest.raises(LocalWorkflowImportError, match="does_not_exist_anywhere"):
        host.resolve("functions/broken.py::main")
    with pytest.raises(LocalWorkflowImportError, match="does_not_exist_anywhere"):
        host.resolve("11111111-1111-1111-1111-111111111111")
    with pytest.raises(LocalWorkflowImportError, match="does_not_exist_anywhere"):
        host.resolve("Broken One")


def test_host_manifest_entry_without_local_file_is_a_clean_miss(tmp_path: Path):
    (tmp_path / "bifrost.solution.yaml").write_text("slug: demo\nname: Demo\n")
    _write(tmp_path / ".bifrost/workflows.yaml", '''
        workflows:
          11111111-1111-1111-1111-111111111111:
            name: Ghost
            path: functions/ghost.py
            function_name: main
    ''')

    host = FunctionHost(tmp_path)
    host.reload()

    assert host.resolve("Ghost") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./test.sh tests/unit/test_solution_dev_function_host.py -v`
Expected: the four new tests FAIL (`LocalWorkflowResolutionError` / `resolve` don't exist).

- [ ] **Step 3: Implement**

In `api/bifrost/solution_dev/function_host.py`, add near the top:

```python
import yaml
from dataclasses import dataclass, field
```

Add below `_SKIP_DIRS`:

```python
class LocalWorkflowError(Exception):
    """Base for local resolution errors the dev proxy surfaces to the app."""


class LocalWorkflowResolutionError(LocalWorkflowError):
    """A workflow name matched multiple local manifest entries."""


class LocalWorkflowImportError(LocalWorkflowError):
    """The ref's target file exists locally but failed to import."""


@dataclass(frozen=True)
class LocalWorkflowIndex:
    by_ref: dict[str, str] = field(default_factory=dict)       # ref → local path::fn
    ambiguous: dict[str, list[str]] = field(default_factory=dict)  # name → candidate refs
    failed: dict[str, str] = field(default_factory=dict)       # ref → import error


def _load_workflow_manifest_entries(workspace: Path) -> list[dict[str, Any]]:
    manifest = workspace / ".bifrost" / "workflows.yaml"
    if not manifest.is_file():
        return []
    data = yaml.safe_load(manifest.read_text()) or {}
    workflows = data.get("workflows") or {}
    if not isinstance(workflows, dict):
        return []
    entries: list[dict[str, Any]] = []
    for workflow_id, body in workflows.items():
        if not isinstance(body, dict):
            continue
        entry = dict(body)
        entry.setdefault("id", str(workflow_id))
        entries.append(entry)
    return entries


def build_local_workflow_index(
    workspace: Path,
    local_refs: set[str],
    failures: dict[str, str],
) -> LocalWorkflowIndex:
    """Index every ref shape onto the discovered local functions.

    Honors the manifest ``path`` key ONLY — the same key deploy's
    ``_collect_workflows`` honors — so ``solution start`` and ``deploy``
    agree about which entries exist.
    """
    by_ref = {ref: ref for ref in local_refs}
    failed: dict[str, str] = {}
    name_targets: dict[str, set[str]] = {}

    for entry in _load_workflow_manifest_entries(workspace):
        path = entry.get("path")
        function_name = entry.get("function_name")
        if not path or not function_name:
            continue
        local_ref = f"{path}::{function_name}"
        uuid_alias = str(entry["id"])
        name = entry.get("name")
        if name:
            name_targets.setdefault(str(name), set()).add(local_ref)

        if local_ref in local_refs:
            by_ref[uuid_alias] = local_ref
        elif path in failures:
            failed[uuid_alias] = failures[path]
        # else: entry points at a file that doesn't exist locally → clean miss.

    ambiguous: dict[str, list[str]] = {}
    for name, targets in name_targets.items():
        live = sorted(t for t in targets if t in local_refs)
        broken = sorted(t for t in targets if t.split("::", 1)[0] in failures)
        if len(live) > 1:
            ambiguous[name] = live
        elif len(live) == 1:
            by_ref[name] = live[0]
        elif broken:
            failed[name] = failures[broken[0].split("::", 1)[0]]
    return LocalWorkflowIndex(by_ref=by_ref, ambiguous=ambiguous, failed=failed)
```

Update `FunctionHost` (`reload` replaces Task 1's version; `has()` is DELETED):

```python
    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace
        self._fns: dict[str, Callable[..., Any]] = {}
        self._failures: dict[str, str] = {}
        self._index = LocalWorkflowIndex()

    def reload(self) -> None:
        self._fns, self._failures = discover_functions(self._workspace)
        self._index = build_local_workflow_index(
            self._workspace, set(self._fns), self._failures
        )

    def resolve(self, ref: str) -> str | None:
        """Local ``path::fn`` for any ref shape; None on a clean miss.

        Raises LocalWorkflowResolutionError (ambiguous name) or
        LocalWorkflowImportError (target file failed to import) — both must
        surface to the developer, never fall through to the platform.
        """
        if ref in self._index.ambiguous:
            candidates = ", ".join(self._index.ambiguous[ref])
            raise LocalWorkflowResolutionError(
                f"workflow name '{ref}' is ambiguous in .bifrost/workflows.yaml "
                f"(matches: {candidates}); use a path::function ref"
            )
        err = self._index.failed.get(ref)
        if err is None and "::" in ref:
            err = self._failures.get(ref.split("::", 1)[0])
        if err is not None:
            raise LocalWorkflowImportError(
                f"local workflow '{ref}' failed to import: {err} "
                f"(fix the file — it hot-reloads on save)"
            )
        return self._index.by_ref.get(ref)
```

(Keep `refs()`, `failures()`, `run()` as-is. `run()` is only ever called with a value `resolve()` returned, which is always a key of `_fns`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `./test.sh tests/unit/test_solution_dev_function_host.py -v`
Expected: ALL pass. (Note: `test_host_unknown_ref_raises_keyerror` exercises `run()` directly and still passes — `run()` is unchanged.)

- [ ] **Step 5: Commit**

```bash
git add api/bifrost/solution_dev/function_host.py api/tests/unit/test_solution_dev_function_host.py
git commit -m "feat(solution-start): resolve UUID/name/path workflow refs against the local workspace"
```

## Task 3: Proxy — Resolve Locally First, Gate and Sanitize the Fallback

**Files:**
- Modify: `api/bifrost/solution_dev/proxy.py`
- Test: `api/tests/unit/test_solution_dev_proxy.py`

**Interfaces:**
- Consumes: `FunctionHost.resolve/refs/run`, `LocalWorkflowError` (Task 2), `DevProxyConfig.global_repo_access` (added here, threaded from the command in Task 5).
- Produces: `DevProxyConfig(..., global_repo_access: bool = False)`. Execute-handler behavior per the Resolution Rules section.

- [ ] **Step 1: Update the test scaffolding**

In `api/tests/unit/test_solution_dev_proxy.py`:

Replace `_StubHost` with:

```python
class _StubHost:
    """Stub of FunctionHost's resolver surface (resolve/refs/run)."""

    def __init__(self, refs, aliases=None, error=None):
        self._refs = set(refs)
        self._aliases = dict(aliases or {})
        self._error = error  # raised by resolve() when set
        self.last_call = None

    def refs(self):
        return sorted(self._refs)

    def resolve(self, ref):
        if self._error is not None:
            raise self._error
        if ref in self._refs:
            return ref
        return self._aliases.get(ref)

    async def run(self, ref, params):
        self.last_call = (ref, params)
        return {"ran_local": ref, "params": params}
```

In `_make_upstream`, extend the `execute` handler to record headers:

```python
    async def execute(request):
        record["execute_body"] = await request.json()
        record["execute_headers"] = dict(request.headers)
        return web.json_response({"ran_upstream": True})
```

**Delete `test_unknown_ref_proxies_to_upstream`** (lines ~206–230). It asserts the removed behavior: unknown ref proxied with `solution_id` stamped into the body and no gate. Its replacements are the two gate tests below.

- [ ] **Step 2: Write the failing tests**

Add to `api/tests/unit/test_solution_dev_proxy.py`:

```python
from bifrost.solution_dev.function_host import (
    LocalWorkflowImportError,
    LocalWorkflowResolutionError,
)


async def test_local_name_ref_resolves_before_execute():
    record = {}
    up_port, dev_port = _free_port(), _free_port()
    up_runner = await _serve(_make_upstream(record), up_port)
    host = _StubHost(
        {"functions/preview.py::recipients"},
        aliases={"Preview Recipients": "functions/preview.py::recipients"},
    )
    cfg = DevProxyConfig(
        upstream_url=f"http://127.0.0.1:{up_port}",
        token="t", app_id="A", org_id="O", solution_id="S",
    )
    dev_runner = await _serve(build_dev_app(cfg, host, vite_url="http://127.0.0.1:1"), dev_port)
    try:
        async with httpx.AsyncClient() as c:
            r = await c.post(
                f"http://127.0.0.1:{dev_port}/api/workflows/execute",
                json={"workflow_id": "Preview Recipients", "input_data": {"x": 1}, "app_id": "A"},
            )
        assert r.status_code == 200
        assert r.json()["result"] == {
            "ran_local": "functions/preview.py::recipients",
            "params": {"x": 1},
        }
        assert "execute_body" not in record
    finally:
        await dev_runner.cleanup()
        await up_runner.cleanup()


async def test_resolution_errors_surface_as_200_error_body():
    # Ambiguity and import failures must be VISIBLE in the app: useWorkflow
    # renders body.error only on HTTP 200 (non-200 shows bare statusText).
    for error, needle in [
        (LocalWorkflowResolutionError("workflow name 'Dup' is ambiguous"), "ambiguous"),
        (LocalWorkflowImportError("local workflow 'x' failed to import: boom"), "boom"),
    ]:
        record = {}
        up_port, dev_port = _free_port(), _free_port()
        up_runner = await _serve(_make_upstream(record), up_port)
        host = _StubHost(set(), error=error)
        cfg = DevProxyConfig(
            upstream_url=f"http://127.0.0.1:{up_port}",
            token="t", app_id="A", org_id="O", solution_id="S",
            global_repo_access=True,
        )
        dev_runner = await _serve(build_dev_app(cfg, host, vite_url="http://127.0.0.1:1"), dev_port)
        try:
            async with httpx.AsyncClient() as c:
                r = await c.post(
                    f"http://127.0.0.1:{dev_port}/api/workflows/execute",
                    json={"workflow_id": "Dup", "input_data": {}},
                )
            assert r.status_code == 200
            assert needle in r.json()["error"]
            assert "execute_body" not in record  # even with global access: no proxy
        finally:
            await dev_runner.cleanup()
            await up_runner.cleanup()


async def test_unknown_ref_without_global_access_errors_locally():
    record = {}
    up_port, dev_port = _free_port(), _free_port()
    up_runner = await _serve(_make_upstream(record), up_port)
    host = _StubHost({"functions/hello.py::main"})
    cfg = DevProxyConfig(
        upstream_url=f"http://127.0.0.1:{up_port}",
        token="t", app_id="A", org_id="O", solution_id="S",
        global_repo_access=False,
    )
    dev_runner = await _serve(build_dev_app(cfg, host, vite_url="http://127.0.0.1:1"), dev_port)
    try:
        async with httpx.AsyncClient() as c:
            r = await c.post(
                f"http://127.0.0.1:{dev_port}/api/workflows/execute",
                json={"workflow_id": "Shared Global Workflow", "input_data": {}, "app_id": "A"},
            )
        assert r.status_code == 200
        body = r.json()
        assert "not found" in body["error"]
        assert "functions/hello.py::main" in body["error"]  # lists known refs
        assert "global_repo_access" in body["error"]
        assert "execute_body" not in record
    finally:
        await dev_runner.cleanup()
        await up_runner.cleanup()


async def test_unknown_ref_with_global_access_proxies_without_scope_signals():
    record = {}
    up_port, dev_port = _free_port(), _free_port()
    up_runner = await _serve(_make_upstream(record), up_port)
    host = _StubHost(set())
    cfg = DevProxyConfig(
        upstream_url=f"http://127.0.0.1:{up_port}",
        token="t", app_id="A", org_id="O", solution_id="S",
        global_repo_access=True,
    )
    dev_runner = await _serve(build_dev_app(cfg, host, vite_url="http://127.0.0.1:1"), dev_port)
    try:
        async with httpx.AsyncClient() as c:
            r = await c.post(
                f"http://127.0.0.1:{dev_port}/api/workflows/execute",
                json={
                    "workflow_id": "Shared Global Workflow",
                    "input_data": {},
                    "app_id": "A",
                    "form_id": "F",
                    "solution_id": "client-sent",
                },
            )
        assert r.status_code == 200
        assert r.json()["ran_upstream"] is True
        body = record["execute_body"]
        assert body["workflow_id"] == "Shared Global Workflow"
        # EVERY signal the server derives install scope from must be gone —
        # for capture-born installs these ids exist server-side and would
        # resolve the CLOUD copy of this Solution's workflows.
        assert "solution_id" not in body
        assert "app_id" not in body
        assert "form_id" not in body
        headers = {k.lower(): v for k, v in record["execute_headers"].items()}
        assert "x-bifrost-app" not in headers
        assert headers["x-bifrost-org"] == "O"          # org scope stays
        assert headers["authorization"] == "Bearer t"   # auth stays
    finally:
        await dev_runner.cleanup()
        await up_runner.cleanup()


async def test_local_uuid_ref_warns_once(capsys):
    uuid_ref = "11111111-1111-1111-1111-111111111111"
    record = {}
    up_port, dev_port = _free_port(), _free_port()
    up_runner = await _serve(_make_upstream(record), up_port)
    host = _StubHost({"functions/a.py::main"}, aliases={uuid_ref: "functions/a.py::main"})
    cfg = DevProxyConfig(
        upstream_url=f"http://127.0.0.1:{up_port}",
        token="t", app_id="A", org_id="O",
    )
    dev_runner = await _serve(build_dev_app(cfg, host, vite_url="http://127.0.0.1:1"), dev_port)
    try:
        async with httpx.AsyncClient() as c:
            r1 = await c.post(
                f"http://127.0.0.1:{dev_port}/api/workflows/execute",
                json={"workflow_id": uuid_ref, "input_data": {}},
            )
            r2 = await c.post(
                f"http://127.0.0.1:{dev_port}/api/workflows/execute",
                json={"workflow_id": uuid_ref, "input_data": {}},
            )
        assert r1.status_code == 200 and r2.status_code == 200
        err = capsys.readouterr().err
        assert err.count("manifest UUID") == 1  # warned exactly once
    finally:
        await dev_runner.cleanup()
        await up_runner.cleanup()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `./test.sh tests/unit/test_solution_dev_proxy.py -v`
Expected: new tests FAIL (`global_repo_access` unknown field; handler still uses `host.has`). `test_local_path_ref_runs_in_function_host` and `test_local_error_returns_200_with_error_field` also fail until Step 4 (handler calls `has` which the new `_StubHost` doesn't define) — that's expected at this step.

- [ ] **Step 4: Implement**

In `api/bifrost/solution_dev/proxy.py`:

Add imports:

```python
import uuid as _uuid

import click

from bifrost.solution_dev.function_host import LocalWorkflowError
```

Add `global_repo_access` to the config:

```python
@dataclass(frozen=True)
class DevProxyConfig:
    upstream_url: str   # the dev API, e.g. http://localhost:37791
    token: str          # CLI access token
    app_id: str         # chosen app's manifest UUID
    org_id: str | None  # bound install org id (or None for a global install)
    solution_id: str | None = None  # bound Solution install id
    # Whether bifrost.solution.yaml sets global_repo_access. Gates whether a
    # workflow ref that misses locally may fall back to the platform's shared
    # _repo/ content at all. Mirrors the module-loader semantics (§3.5).
    global_repo_access: bool = False
```

Add near `_CFG`/`_HOST` app keys (per-app so tests don't share state):

```python
_WARNED_UUID_REFS = web.AppKey("warned_uuid_refs", set)
```

and in `build_dev_app`, after `app[_HTTP] = ...`:

```python
    app[_WARNED_UUID_REFS] = set()
```

Replace `_execute_handler` in full:

```python
def _is_uuid(ref: str) -> bool:
    try:
        _uuid.UUID(ref)
        return True
    except ValueError:
        return False


# Body fields the server derives install scope from (see
# derive_execution_solution_scope: ctx/X-Bifrost-App > solution_id > form_id
# > app_id). The global fallback must carry NONE of them: for capture-born
# installs these ids exist server-side and would resolve the CLOUD copy of
# this Solution's own workflows — the bug local-first resolution prevents.
_SCOPE_BODY_FIELDS = ("solution_id", "form_id", "app_id")


async def _execute_handler(request: web.Request) -> web.Response:
    cfg: DevProxyConfig = request.app[_CFG]
    host = request.app[_HOST]
    body = await request.json()
    ref = str(body.get("workflow_id", ""))

    # Surface resolution problems in the app: useWorkflow reads `body.error`
    # on a 200 (the deployed error contract) and shows it; on a non-200 it
    # only shows `statusText`, hiding the cause. Same contract as run errors.
    try:
        local_ref = host.resolve(ref)
    except LocalWorkflowError as exc:
        return web.json_response({"error": str(exc)})

    if local_ref is not None:
        if _is_uuid(ref) and ref not in request.app[_WARNED_UUID_REFS]:
            request.app[_WARNED_UUID_REFS].add(ref)
            click.echo(
                f"  warning: workflow ref '{ref}' is a manifest UUID — it runs "
                "locally, but deploy remaps entity ids, so this ref will NOT "
                "resolve on a deployed install. Use the workflow name or "
                f"'{local_ref}' instead.",
                err=True,
            )
        try:
            result = await host.run(local_ref, body.get("input_data") or {})
        except Exception as exc:
            # Returning {"error": ...} at 200 gives the dev the actual
            # traceback — the whole point of a local debug loop.
            import traceback

            tb = traceback.format_exc()
            return web.json_response({"error": f"{type(exc).__name__}: {exc}\n\n{tb}"})
        return web.json_response({"status": "completed", "result": result})

    if not cfg.global_repo_access:
        known = ", ".join(host.refs()) or "(none discovered)"
        return web.json_response({
            "error": (
                f"Workflow '{ref}' not found in this Solution workspace. "
                f"Local refs: {known}. This Solution does not set "
                "global_repo_access: true in bifrost.solution.yaml, so "
                "`bifrost solution start` will not ask the platform to "
                "resolve it."
            )
        })

    # Shared _repo/ fallback, stripped of every install-scope signal.
    fallback_body = {k: v for k, v in body.items() if k not in _SCOPE_BODY_FIELDS}
    headers = {
        k: v
        for k, v in _auth_headers(cfg, request.headers).items()
        if k.lower() != "x-bifrost-app"
    }
    try:
        resp = await request.app[_HTTP].post(
            f"{cfg.upstream_url}/api/workflows/execute",
            json=fallback_body,
            headers=headers,
        )
    except httpx.HTTPError:
        return web.json_response(
            {"detail": f"Dev API unreachable at {cfg.upstream_url}"}, status=502
        )
    return web.Response(
        body=resp.content, status=resp.status_code,
        headers=_passthrough_headers(resp, "application/json"),
    )
```

Also update the module docstring's first route line to:

```
  POST /api/workflows/execute  → local FunctionHost when the ref (path::fn,
                                 manifest UUID, or name) resolves to THIS
                                 workspace; misses fall back to the platform's
                                 shared _repo/ content only when the descriptor
                                 sets global_repo_access, with all install-scope
                                 signals stripped from the fallback request.
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `./test.sh tests/unit/test_solution_dev_proxy.py -v`
Expected: ALL pass, including the pre-existing `test_local_path_ref_runs_in_function_host`, `test_local_error_returns_200_with_error_field`, `test_other_api_path_proxies_with_org_header` (data-plane untouched), and the websocket tests.

- [ ] **Step 6: Commit**

```bash
git add api/bifrost/solution_dev/proxy.py api/tests/unit/test_solution_dev_proxy.py
git commit -m "feat(solution-start): local-first workflow resolution with gated, scope-stripped fallback"
```

## Task 4: Watcher — Reload on workflows.yaml, Echo Reload Feedback

**Files:**
- Modify: `api/bifrost/solution_dev/reload.py`
- Test: `api/tests/unit/test_solution_dev_reload.py`

**Interfaces:**
- Consumes: `FunctionHost.reload/refs/failures` (Tasks 1–2).
- Produces: no new API; behavior only.

- [ ] **Step 1: Write the failing tests**

In `api/tests/unit/test_solution_dev_reload.py`, extend `_RecordingHost` (the echo needs `refs()`/`failures()`):

```python
class _RecordingHost:
    def __init__(self):
        self.reloads = 0

    def reload(self):
        self.reloads += 1

    def refs(self):
        return ["functions/hello.py::main"]

    def failures(self):
        return {}
```

Add tests:

```python
def test_handler_reloads_on_workflow_manifest_change():
    host = _RecordingHost()
    handler = _PyChangeHandler(host)

    class _Evt:
        is_directory = False
        src_path = "/ws/.bifrost/workflows.yaml"

    handler.on_modified(_Evt())
    assert host.reloads == 1


def test_handler_ignores_other_manifests():
    host = _RecordingHost()
    handler = _PyChangeHandler(host)

    class _Evt:
        is_directory = False
        src_path = "/ws/.bifrost/apps.yaml"

    handler.on_modified(_Evt())
    assert host.reloads == 0


def test_handler_echoes_reload_summary(capsys):
    host = _RecordingHost()
    handler = _PyChangeHandler(host)

    class _Evt:
        is_directory = False
        src_path = "/ws/functions/hello.py"

    handler.on_modified(_Evt())
    out = capsys.readouterr().out
    assert "1 local function(s)" in out


def test_handler_echoes_import_failures(capsys):
    class _FailingHost(_RecordingHost):
        def refs(self):
            return []

        def failures(self):
            return {"functions/broken.py": "ImportError: boom"}

    host = _FailingHost()
    handler = _PyChangeHandler(host)

    class _Evt:
        is_directory = False
        src_path = "/ws/functions/broken.py"

    handler.on_modified(_Evt())
    err = capsys.readouterr().err
    assert "functions/broken.py" in err
    assert "boom" in err
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./test.sh tests/unit/test_solution_dev_reload.py -v`
Expected: the four new tests FAIL (manifest changes ignored; nothing echoed).

- [ ] **Step 3: Implement**

Replace `api/bifrost/solution_dev/reload.py`'s handler:

```python
"""Re-discover local functions when a workspace .py file (or the workflow
manifest) changes."""
from __future__ import annotations

import pathlib
from pathlib import Path

import click
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver

_SKIP_DIRS = {"node_modules", "dist", ".venv", "venv", "__pycache__", ".git", ".bifrost"}


class _PyChangeHandler(FileSystemEventHandler):
    def __init__(self, host) -> None:
        self._host = host

    def _maybe_reload(self, event) -> None:
        if getattr(event, "is_directory", False):
            return
        path = str(getattr(event, "src_path", ""))
        # watchdog emits native paths; PureWindowsPath parses BOTH separators,
        # so these checks work for / and \ regardless of host platform.
        parts = pathlib.PureWindowsPath(path).parts
        # .bifrost/workflows.yaml feeds the name/UUID alias index — editing it
        # must re-resolve, or the dev loop serves stale aliases with no signal.
        is_workflow_manifest = (
            len(parts) >= 2 and parts[-2] == ".bifrost" and parts[-1] == "workflows.yaml"
        )
        if not is_workflow_manifest:
            if not path.endswith(".py"):
                return
            if any(part in _SKIP_DIRS for part in parts):
                return
        self._host.reload()
        click.echo(f"  reloaded — {len(self._host.refs())} local function(s)")
        for rel, err in sorted(self._host.failures().items()):
            click.echo(f"  ⚠ import error in {rel}: {err}", err=True)

    on_modified = _maybe_reload
    on_created = _maybe_reload
    on_moved = _maybe_reload


def start_function_watch(workspace: Path, host) -> BaseObserver:
    observer = Observer()
    observer.schedule(_PyChangeHandler(host), str(workspace), recursive=True)
    observer.start()
    return observer
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./test.sh tests/unit/test_solution_dev_reload.py -v`
Expected: ALL pass, including the pre-existing skip-dir tests (`/ws/.bifrost/state.py` still skipped — it's a `.py` inside a skip dir, not the manifest).

- [ ] **Step 5: Commit**

```bash
git add api/bifrost/solution_dev/reload.py api/tests/unit/test_solution_dev_reload.py
git commit -m "feat(solution-start): reload on workflows.yaml edits and echo reload feedback"
```

## Task 5: Command Plumbing — global_repo_access + Startup Visibility

**Files:**
- Modify: `api/bifrost/commands/solution.py`
- Test: `api/tests/unit/test_solution_dev_command.py`

**Interfaces:**
- Consumes: `DevProxyConfig.global_repo_access` (Task 3), `FunctionHost.refs/failures` (Tasks 1–2), `SolutionDescriptor.global_repo_access` (exists).
- Produces: `_dev_proxy_config(client, chosen, org_info, solution_id, global_repo_access) -> DevProxyConfig` — a pure helper `_serve` uses, testable without sockets.

- [ ] **Step 1: Write the failing test**

Add to `api/tests/unit/test_solution_dev_command.py`:

```python
def test_dev_proxy_config_threads_descriptor_global_repo_access():
    from bifrost.commands.solution import _dev_proxy_config

    class _Client:
        api_url = "http://127.0.0.1:8000/"
        _access_token = "tok"

    class _Chosen:
        app_id = "app-uuid"

    cfg = _dev_proxy_config(
        _Client(), _Chosen(), {"id": "org-1"}, "install-1", True
    )
    assert cfg.global_repo_access is True
    assert cfg.upstream_url == "http://127.0.0.1:8000"
    assert cfg.solution_id == "install-1"
    assert cfg.org_id == "org-1"

    cfg = _dev_proxy_config(_Client(), _Chosen(), None, "install-1", False)
    assert cfg.global_repo_access is False
    assert cfg.org_id is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./test.sh tests/unit/test_solution_dev_command.py -v`
Expected: FAIL (`_dev_proxy_config` does not exist).

- [ ] **Step 3: Implement**

In `api/bifrost/commands/solution.py`:

Add above `_serve` (~line 2642):

```python
def _dev_proxy_config(client, chosen, org_info, solution_id, global_repo_access):
    from bifrost.solution_dev.proxy import DevProxyConfig

    return DevProxyConfig(
        upstream_url=client.api_url.rstrip("/"),
        token=client._access_token,
        app_id=chosen.app_id,
        org_id=(org_info or {}).get("id"),
        solution_id=solution_id,
        global_repo_access=global_repo_access,
    )
```

Change `_serve`'s signature and config construction:

```python
async def _serve(client, chosen, org_info, host, port, vite_port, workspace, solution_id, global_repo_access):
    from aiohttp import web

    from bifrost.solution_dev.proxy import build_dev_app
    from bifrost.solution_dev.reload import start_function_watch

    cfg = _dev_proxy_config(client, chosen, org_info, solution_id, global_repo_access)
```

In `start_cmd`, pass the descriptor flag as the final `_serve` argument (after `binding.solution_id`):

```python
                binding.solution_id,
                descriptor.global_repo_access,
```

Also in `start_cmd`, replace the single count line (`click.echo(f"Discovered {len(host.refs())} local function(s).")`, ~line 2149) with:

```python
    refs = host.refs()
    click.echo(f"Discovered {len(refs)} local function(s):")
    for ref in refs:
        click.echo(f"  {ref}")
    for rel, err in sorted(host.failures().items()):
        click.echo(f"  ⚠ import error in {rel}: {err}", err=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./test.sh tests/unit/test_solution_dev_command.py tests/unit/test_solution_start_env.py -v`
Expected: ALL pass.

- [ ] **Step 5: Commit**

```bash
git add api/bifrost/commands/solution.py api/tests/unit/test_solution_dev_command.py
git commit -m "feat(solution-start): thread global_repo_access into the dev proxy; list discovered refs at startup"
```

## Task 6: Scaffold Comment — Advertise the Portable Ref Shapes

**Files:**
- Modify: `api/bifrost/commands/solution.py` (App.tsx template, ~line 570)

- [ ] **Step 1: Replace the stale comment**

In the scaffold `app_tsx` template, replace:

```text
  // is a workflow UUID, a portable `path::function` ref (e.g.
  // "functions/hello.py::main", shipped with this scaffold), or a workflow
  // name — all resolve to THIS install's own workflow. Prefer `path::function`:
  // it's the shape `bifrost solution start` runs LOCALLY (name/UUID refs
  // proxy to the deployed copy).
```

with:

```text
  // is a portable `path::function` ref (e.g. "functions/hello.py::main",
  // shipped with this scaffold) or a workflow name — both resolve to THIS
  // install's own workflow when deployed, and `bifrost solution start` runs
  // both from your local files. (Avoid raw UUID refs: deploy remaps entity
  // ids per install, so a hardcoded UUID won't resolve on a deployed install.)
```

- [ ] **Step 2: Run the scaffold tests**

Run: `./test.sh tests/unit/test_solution_scaffold_dev_wiring.py tests/unit/test_solution_scaffold_app.py -v`
Expected: pass. If an assertion checks the old copy verbatim, update it to the new copy (the intent — teaching the portable shapes — is what the test guards).

- [ ] **Step 3: Commit**

```bash
git add api/bifrost/commands/solution.py api/tests/unit/test_solution_scaffold_dev_wiring.py api/tests/unit/test_solution_scaffold_app.py
git commit -m "docs(solution-start): scaffold comment teaches portable workflow ref shapes"
```

## Task 7: Deploy Warning — Unregistered @workflow Files

**Files:**
- Modify: `api/bifrost/commands/solution.py` (`deploy_cmd`, ~line 1728)
- Test: `api/tests/unit/test_solution_collect_workflows.py`

**Interfaces:**
- Produces: `_unregistered_workflow_files(python_files: dict[str, str], workflows: list[dict]) -> list[str]` in `api/bifrost/commands/solution.py`.

Rationale: `solution start` runs any decorated function on disk, but `deploy` only creates Workflow rows for `.bifrost/workflows.yaml` entries — so an unregistered function works locally and 404s deployed, silently. Warn at deploy time. (File-level check: a registered file with an extra unregistered function in it does not warn — acceptable for a warning.)

- [ ] **Step 1: Write the failing test**

Add to `api/tests/unit/test_solution_collect_workflows.py`:

```python
def test_unregistered_workflow_files_flags_decorated_source_without_manifest_entry():
    from bifrost.commands.solution import _unregistered_workflow_files

    python_files = {
        "functions/registered.py": "from bifrost import workflow\n\n@workflow\nasync def main():\n    return 1\n",
        "functions/loose.py": "from bifrost import workflow\n\n@workflow(name='Loose')\nasync def main():\n    return 2\n",
        "modules/helper.py": "def util():\n    return 3\n",
    }
    workflows = [{"path": "functions/registered.py", "function_name": "main", "name": "reg"}]

    assert _unregistered_workflow_files(python_files, workflows) == ["functions/loose.py"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./test.sh tests/unit/test_solution_collect_workflows.py -v`
Expected: FAIL (`_unregistered_workflow_files` does not exist).

- [ ] **Step 3: Implement**

In `api/bifrost/commands/solution.py`, next to `_collect_workflows` (`re` is already imported at the top of the file; if not, add it):

```python
_WORKFLOW_DECORATOR_RE = re.compile(r"^\s*@workflow\b", re.MULTILINE)


def _unregistered_workflow_files(
    python_files: dict[str, str], workflows: list[dict]
) -> list[str]:
    """Bundled .py files that define @workflow functions but have no
    .bifrost/workflows.yaml entry — they deploy as source with no Workflow row,
    so their refs 404 on the install while working fine under `solution start`.
    """
    registered = {str(w.get("path")) for w in workflows}
    return sorted(
        rel
        for rel, src in python_files.items()
        if rel not in registered and _WORKFLOW_DECORATOR_RE.search(src)
    )
```

In `deploy_cmd`, right after the `found N python file(s)...` echo (~line 1733):

```python
    for rel in _unregistered_workflow_files(python_files, workflows):
        click.echo(
            f"  warning: {rel} defines @workflow function(s) but has no entry in "
            ".bifrost/workflows.yaml — it deploys as source only and its refs "
            "will 404 on the install. Add a workflows.yaml entry to register it.",
            err=True,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./test.sh tests/unit/test_solution_collect_workflows.py -v`
Expected: ALL pass.

- [ ] **Step 5: Commit**

```bash
git add api/bifrost/commands/solution.py api/tests/unit/test_solution_collect_workflows.py
git commit -m "feat(solution-deploy): warn when @workflow source has no manifest entry"
```

## Task 8: Verification

**Files:** none (verification only).

- [ ] **Step 1: Full affected-suite run**

```bash
./test.sh tests/unit/test_solution_dev_function_host.py tests/unit/test_solution_dev_proxy.py tests/unit/test_solution_dev_reload.py tests/unit/test_solution_dev_command.py tests/unit/test_solution_start_env.py tests/unit/test_solution_collect_workflows.py tests/unit/test_solution_scaffold_dev_wiring.py tests/unit/test_solution_scaffold_app.py -v
```

Expected: all pass.

- [ ] **Step 2: Quality + full unit suite**

```bash
./test.sh quality api    # pyright + ruff, 0 errors
./test.sh                # full unit suite — catches blast radius outside the touched files
```

Parse `/tmp/bifrost-<project>/test-results.xml` for pass/fail rather than grepping stdout.

- [ ] **Step 3: Manual debug smoke (live drive)**

From a Solution workspace with a named workflow in `.bifrost/workflows.yaml`, against a running debug stack:

```bash
bifrost solution start
```

Verify, via `curl` against the local proxy (`http://127.0.0.1:3000/api/workflows/execute`, body `{"workflow_id": <ref>, "input_data": {}, "app_id": <app id>}`):

1. Name ref of a local workflow → local result; the debug API log shows NO `/api/workflows/execute`.
2. Startup output lists every discovered `path::fn`.
3. Break the workflow file (add `import nope`) → save → console echoes the import error; executing any ref shape for it returns 200 `{"error": ...}` containing the import error. Fix the file → works again.
4. Edit `.bifrost/workflows.yaml` (rename the workflow) → console echoes a reload; the new name resolves without restart.
5. Unknown ref with `global_repo_access: false` → 200 `{"error": ...}` listing local refs; no upstream call.
6. Flip `global_repo_access: true` (restart start), call a known `_repo` workflow by name → upstream call appears in API logs; capture it and confirm the request body has no `solution_id`/`app_id`/`form_id` and no `X-Bifrost-App` header.
7. UUID ref of a local workflow → runs locally + one-time console warning.

## Self-Review Notes

- **Spec coverage:** local path/name/UUID resolution (Tasks 2–3), ambiguity (2–3), import-failure surfacing (1–3), scope-signal-stripped gated fallback (3), watcher staleness (4), plumbing + startup visibility (5), scaffold copy (6), deploy drift warning (7).
- **Known accepted trade-off:** a non-admin dev's `_repo` fallback can 403 now that `app_id` is stripped (authorization via "app uses this workflow" no longer applies). Loud and debuggable, vs. the silent cloud-copy execution it prevents. If this bites, the follow-up is a server-side "authz-only app reference" — out of scope for a CLI-only change.
- **Known accepted break (carried from rev 1):** apps that relied on `solution start` proxying deployed Solution workflows by UUID lose that path — it conflicts with local-dev semantics.
- **Boundary:** data-plane `/api/*` proxying (tables/configs/files with `?solution=` + `X-Bifrost-App`) is deliberately untouched; only `/api/workflows/execute` changes.
