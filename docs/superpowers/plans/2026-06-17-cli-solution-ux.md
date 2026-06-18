# CLI / Solution UX Improvement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Bifrost CLI repo sync (`push`/`pull`/`sync`/`watch`) and `solution deploy` predictable, observable, and hard to misuse — single-file push, non-interactive one-way push, solution-workspace guards, real progress output, async observable deploy, and correct workflow/app validation.

**Architecture:** Eight cohesive changes on one branch (`worktree-cli-solution-ux`). CLI ergonomics live in `api/bifrost/` (argparse handlers in `cli.py`, click commands in `commands/solution.py`, auth in `credentials.py`/`client.py`). Server-side changes touch the solutions deploy router/job system and the applications validation router. The workflow-name fix is in manifest import. Each task is TDD with its own commit.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy, Pydantic, httpx, click + argparse (CLI), pytest, alembic.

## Global Constraints

- **Worktree only:** all work happens in `.claude/worktrees/cli-solution-ux`, never on `main`.
- **CLI cannot import `src.*`:** `api/bifrost/commands/` and `api/bifrost/*.py` must never `import src.*` — the packaged CLI has no `src` on its path. Test with the installed CLI, not just in-repo.
- **DTO parity:** if any `XxxCreate`/`XxxUpdate` DTO changes, run `./test.sh tests/unit/test_dto_flags.py` and reconcile CLI/MCP/manifest or add to `DTO_EXCLUDES`.
- **Datetime:** `datetime.now(timezone.utc)` only; `DateTime(timezone=True)` columns; no `datetime.utcnow()`.
- **Tests via `./test.sh`** (Dockerized stack), never bare pytest. JUnit XML at `/tmp/bifrost-<project>/test-results.xml`.
- **Migrations** run by the init container: after creating one, restart `bifrost-debug-<project>-init-1` then `-api-1`.
- **No dead code, no unrequested fallbacks.**
- **#3 escape hatch:** HARD FAIL inside solution workspaces; NO `--global-repo` flag this branch.
- **#7:** the `@workflow(name=...)` decorator name IS the execution identity by design — do NOT change the resolver. Fix the import that corrupts `Workflow.name`.

---

## File Structure

| File | Responsibility | Tasks |
|------|----------------|-------|
| `api/bifrost/credentials.py` | `EnvBackend` token resolution from `.env`-loaded URL | 1 |
| `api/bifrost/cli.py` | push/pull/sync/watch arg parsing, single-file support, solution-workspace guard, progress output | 2,3,4 |
| `api/bifrost/_solution_workspace.py` (new) | upward walk for `bifrost.solution.yaml` | 3 |
| `api/src/services/manifest_import.py` | derive `Workflow.name` from decorator, not manifest slug | 5 |
| `api/src/services/solution_deploy_preflight.py` (new) | manifest/decorator mismatch preflight | 5 |
| `api/src/models/orm/applications.py` + migration | `app_model` discriminator column | 6 |
| `api/src/routers/applications.py` | branch validation on `app_model` | 6 |
| `api/src/models/orm/solution_deploy_jobs.py` (new) + migration | async deploy job record | 7 |
| `api/src/routers/solutions.py` | async deploy endpoint + status endpoint | 7 |
| `api/bifrost/commands/solution.py` | deploy timeout, phases, heartbeat, poll, bundle introspection | 7,8 |

---

## Task 1: ~~Resolve `.env` tokens~~ — DROPPED (not a bug)

**Reproduced live 2026-06-17 against the sandbox.** `apps list` and `solution deploy` both authenticate identically via the OS keychain (`KeyringBackend`), which holds a valid entry for `https://sandbox-bifrost.gocovi.app`. Auth was never the problem — `solution deploy` fails at an httpx **`ReadTimeout` after ~108s while the server completes successfully** (verified: the app's `updated_at` advanced). That is Task 7's bug, not an auth bug. **No Task 1 work.** Latent fragility noted for a future pass: keychain URL match is trailing-slash-exact, so a `/`-suffixed `.env` URL would silently miss — not fixed here, no evidence it bit anyone.

## ~~Task 1 (original): Resolve `.env` tokens when only `BIFROST_API_URL` is set~~ (superseded above)

**Root cause:** `EnvBackend.get()` returns `None` unless `BIFROST_API_URL` AND both tokens are in `os.environ`. A `.env` with only the URL (the sandbox case) plus an empty `~/.bifrost/credentials.json` → nothing resolves. `apps list` only worked when tokens happened to be exported. The `.env` IS loaded into `os.environ` at import (`client.py:133` `load_dotenv`), so tokens placed in `.env` ARE in `os.environ` — the bug is only that the guard treats a URL-only `.env` as unusable instead of looking for tokens it does have. The real failure is the user had no tokens in `.env` and none in the store; the fix makes the resolver's behavior honest: if the env has a URL but not tokens, fall through cleanly to the persistent backend (already happens), AND `bifrost login` writing to the persistent store must be the documented path. **The concrete code defect to fix:** `EnvBackend.get()` silently returns `None` with no signal, so a user who set only `BIFROST_API_URL` in `.env` gets the global-default behavior with zero diagnostics. Add a one-line diagnostic to `get_credentials()` when a URL resolves but no credentials back it.

**Files:**
- Modify: `api/bifrost/credentials.py` (`get_credentials`, ~line 395-435)
- Test: `api/tests/unit/test_credentials.py`

**Interfaces:**
- Consumes: `EnvBackend`, `JsonBackend`, `_resolve_url` (existing).
- Produces: `get_credentials(api_url=None)` unchanged signature; emits a stderr diagnostic when URL resolves but no creds found.

- [ ] **Step 1: Write the failing test**

```python
# api/tests/unit/test_credentials.py
import os
from bifrost import credentials

def test_url_only_env_emits_diagnostic(monkeypatch, capsys, tmp_path):
    # Only URL in env, no tokens, empty persistent store.
    monkeypatch.setenv("BIFROST_API_URL", "https://sandbox.example.app")
    monkeypatch.delenv("BIFROST_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("BIFROST_REFRESH_TOKEN", raising=False)
    monkeypatch.setattr(credentials, "get_persistent_backend", lambda: credentials.JsonBackend())
    monkeypatch.setenv("HOME", str(tmp_path))  # empty creds file
    result = credentials.get_credentials()
    assert result is None
    err = capsys.readouterr().err
    assert "BIFROST_API_URL" in err and "sandbox.example.app" in err
    assert "token" in err.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./test.sh tests/unit/test_credentials.py::test_url_only_env_emits_diagnostic -v`
Expected: FAIL — no diagnostic on stderr.

- [ ] **Step 3: Implement the diagnostic in `get_credentials`**

In `api/bifrost/credentials.py`, after the persistent + legacy lookups return nothing, before `return None`:

```python
    # Diagnostic: a URL resolved (from .env or env) but nothing authenticates it.
    # This is the URL-only-.env trap — the user set BIFROST_API_URL but no tokens,
    # and the persistent store has no record for that URL.
    if resolved and not os.environ.get("BIFROST_ACCESS_TOKEN"):
        print(
            f"No credentials for BIFROST_API_URL={resolved}. "
            f"Your .env sets the URL but not tokens, and no saved login matches it. "
            f"Run `bifrost login --url {resolved} ...` to store credentials.",
            file=sys.stderr,
        )
    return None
```

Ensure `import sys` and `import os` are present at module top.

- [ ] **Step 4: Run test to verify it passes**

Run: `./test.sh tests/unit/test_credentials.py::test_url_only_env_emits_diagnostic -v`
Expected: PASS.

- [ ] **Step 5: Regression test — full env still resolves silently**

```python
def test_full_env_resolves_no_diagnostic(monkeypatch, capsys):
    monkeypatch.setenv("BIFROST_API_URL", "https://sandbox.example.app")
    monkeypatch.setenv("BIFROST_ACCESS_TOKEN", "a")
    monkeypatch.setenv("BIFROST_REFRESH_TOKEN", "r")
    result = credentials.get_credentials()
    assert result is not None
    assert result["api_url"] == "https://sandbox.example.app"
    assert capsys.readouterr().err == ""
```

Run: `./test.sh tests/unit/test_credentials.py -v` → both PASS.

- [ ] **Step 6: Commit**

```bash
git add api/bifrost/credentials.py api/tests/unit/test_credentials.py
git commit -m "fix(cli): diagnose URL-only .env with no resolvable tokens"
```

---

## Task 2: Single-file push (and one-way non-interactive default)

**Goal:** `bifrost push path/to/file.py` pushes exactly one file; directory and `.` still work. Push is one-way and non-interactive by default (no TUI for plain push); only `--mirror` confirms.

**Files:**
- Modify: `api/bifrost/cli.py` (`_parse_push_watch_args` ~1593, `handle_push` ~1617, `_collect_push_files` ~2797, `_sync_files` ~2829)
- Test: `api/tests/unit/test_cli_push.py`

**Interfaces:**
- Consumes: `_sync_files(local_path, *, mirror, validate, force, client, single_file=None)`.
- Produces: `handle_push` accepts a file path; `_collect_push_files(path, single_file)` returns the one file when `single_file` set.

- [ ] **Step 1: Write the failing test**

```python
# api/tests/unit/test_cli_push.py
import pathlib
from bifrost import cli

def test_push_accepts_single_file(tmp_path, monkeypatch):
    f = tmp_path / "mod.py"
    f.write_text("x = 1\n")
    captured = {}
    async def fake_sync(local_path, *, mirror, validate, force, client, single_file=None):
        captured["single_file"] = single_file
        captured["local_path"] = local_path
        return 0
    monkeypatch.setattr(cli, "_sync_files", fake_sync)
    monkeypatch.setattr(cli.BifrostClient, "get_instance", lambda **k: object())
    rc = cli.handle_push([str(f)])
    assert rc == 0
    assert captured["single_file"] == str(f)
```

- [ ] **Step 2: Run to verify it fails**

Run: `./test.sh tests/unit/test_cli_push.py::test_push_accepts_single_file -v`
Expected: FAIL — `handle_push` rejects non-directory at `cli.py:1665`.

- [ ] **Step 3: Allow a file path in `handle_push`**

Replace the directory check at `cli.py:1664-1667`:

```python
    resolved = pathlib.Path(parsed.local_path).resolve()
    single_file: str | None = None
    if resolved.is_file():
        single_file = parsed.local_path
        lock_target = resolved.parent
    elif resolved.is_dir():
        lock_target = resolved
    else:
        print(f"Error: {parsed.local_path} is not a valid file or directory", file=sys.stderr)
        return 1
```

Update the `WorkspaceLock(resolved, "push")` call to use `lock_target`, and pass `single_file=single_file` into the `_sync_files(...)` call.

- [ ] **Step 4: Thread `single_file` through `_sync_files`/`_collect_push_files`**

In `_sync_files` signature add `single_file: str | None = None`. Where it calls `_collect_push_files(...)`, pass it. In `_collect_push_files(path, single_file=None)`:

```python
    if single_file is not None:
        p = pathlib.Path(single_file).resolve()
        repo_path = p.name if p.parent == pathlib.Path(path).resolve() else p.relative_to(pathlib.Path(path).resolve()).as_posix()
        return {repo_path: p}  # match the existing return shape
```

(Confirm the real return shape of `_collect_push_files` at `cli.py:2797` and mirror it exactly — it currently maps repo-relative path → local Path.)

- [ ] **Step 5: Run to verify it passes**

Run: `./test.sh tests/unit/test_cli_push.py -v`
Expected: PASS.

- [ ] **Step 6: Make plain push non-interactive (only `--mirror` confirms)**

In `_sync_files`, the interactive TUI branch (`cli.py:3006-3029`) currently triggers when `not force and _is_tty`. Gate it so plain push never opens the per-file TUI; only `--mirror` (destructive) does:

```python
    interactive = (not force) and _is_tty and mirror  # only mirror is destructive enough to review
    if interactive:
        sync_result = await interactive_sync(...)
    else:
        # one-way: apply default actions (push) without prompting
        ...
```

Add a test asserting a non-mirror push with a TTY does not call `interactive_sync` (monkeypatch it to raise).

- [ ] **Step 7: Run + Commit**

Run: `./test.sh tests/unit/test_cli_push.py -v` → PASS.

```bash
git add api/bifrost/cli.py api/tests/unit/test_cli_push.py
git commit -m "feat(cli): single-file push; plain push is one-way non-interactive"
```

---

## Task 3: Fail repo commands inside a Solution workspace

**Goal:** `push`/`pull`/`sync`/`watch` hard-fail when cwd is inside a Solution workspace (upward walk finds `bifrost.solution.yaml`). Hard fail, no escape hatch.

**Files:**
- Create: `api/bifrost/_solution_workspace.py`
- Modify: `api/bifrost/cli.py` (`handle_push`, `handle_pull`, `handle_sync`, `handle_watch`)
- Test: `api/tests/unit/test_solution_workspace_guard.py`

**Interfaces:**
- Produces: `find_solution_root(start: pathlib.Path) -> pathlib.Path | None` and `assert_not_solution_workspace(path: str, command: str) -> None` (raises `SystemExit(1)` with the standard message after printing to stderr).

- [ ] **Step 1: Write the failing test**

```python
# api/tests/unit/test_solution_workspace_guard.py
import pathlib, pytest
from bifrost._solution_workspace import find_solution_root, assert_not_solution_workspace

def test_find_solution_root_walks_up(tmp_path):
    (tmp_path / "bifrost.solution.yaml").write_text("name: demo\n")
    sub = tmp_path / "functions" / "deep"
    sub.mkdir(parents=True)
    assert find_solution_root(sub) == tmp_path

def test_no_solution_root(tmp_path):
    assert find_solution_root(tmp_path) is None

def test_assert_blocks_with_message(tmp_path, capsys):
    (tmp_path / "bifrost.solution.yaml").write_text("name: demo\n")
    with pytest.raises(SystemExit):
        assert_not_solution_workspace(str(tmp_path), "push")
    err = capsys.readouterr().err
    assert "Solution workspace" in err and "solution deploy" in err
```

- [ ] **Step 2: Run to verify it fails**

Run: `./test.sh tests/unit/test_solution_workspace_guard.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement the module**

```python
# api/bifrost/_solution_workspace.py
"""Detect Solution workspaces so global _repo commands can refuse to run in them."""
from __future__ import annotations
import pathlib
import sys

DESCRIPTOR = "bifrost.solution.yaml"

def find_solution_root(start: pathlib.Path) -> pathlib.Path | None:
    cur = start.resolve()
    for parent in [cur, *cur.parents]:
        if (parent / DESCRIPTOR).is_file():
            return parent
    return None

def assert_not_solution_workspace(path: str, command: str) -> None:
    root = find_solution_root(pathlib.Path(path))
    if root is not None:
        print(
            f"This is a Solution workspace ({root}). `bifrost {command}` targets the "
            f"global _repo workspace and is disabled here.\nUse `bifrost solution deploy`.",
            file=sys.stderr,
        )
        raise SystemExit(1)
```

- [ ] **Step 4: Run to verify it passes**

Run: `./test.sh tests/unit/test_solution_workspace_guard.py -v`
Expected: PASS.

- [ ] **Step 5: Wire the guard into all four handlers**

In `cli.py`, at the top of `handle_push`, `handle_pull`, `handle_sync`, `handle_watch` — after the path is resolved and before the `WorkspaceLock` — add:

```python
    from bifrost._solution_workspace import assert_not_solution_workspace
    assert_not_solution_workspace(parsed.local_path, "push")  # command name per handler
```

(Use `"pull"`, `"sync"`, `"watch"` in the respective handlers; `parsed.local_path` or the handler's local var.)

- [ ] **Step 6: Integration test the wiring**

```python
def test_handle_push_blocks_in_solution(tmp_path, monkeypatch):
    (tmp_path / "bifrost.solution.yaml").write_text("name: demo\n")
    f = tmp_path / "x.py"; f.write_text("x=1\n")
    monkeypatch.chdir(tmp_path)
    import pytest
    from bifrost import cli
    with pytest.raises(SystemExit):
        cli.handle_push(["x.py"])
```

Run: `./test.sh tests/unit/test_solution_workspace_guard.py tests/unit/test_cli_push.py -v` → PASS.

- [ ] **Step 7: Commit**

```bash
git add api/bifrost/_solution_workspace.py api/bifrost/cli.py api/tests/unit/test_solution_workspace_guard.py
git commit -m "feat(cli): block push/pull/sync/watch inside a Solution workspace"
```

---

## Task 4: Line-oriented push progress in non-TTY mode

**Goal:** Non-TTY push prints local phases up front and per-file (throttled) upload lines, plus a final summary — so large pushes don't look hung.

**Files:**
- Modify: `api/bifrost/cli.py` (`_sync_files` progress section ~3108)
- Test: `api/tests/unit/test_cli_push_progress.py`

**Interfaces:**
- Consumes: existing `_sync_files` push loop; `_is_tty` flag.
- Produces: stderr/stdout lines: `Scanning files...`, `Scanned N files, M unchanged`, `Uploading i/N <path>`, `Done: N pushed, M unchanged, K skipped, F failed`.

- [ ] **Step 1: Write the failing test**

```python
# api/tests/unit/test_cli_push_progress.py
import asyncio
from bifrost import cli

def test_non_tty_push_prints_progress(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli, "_is_tty", False)
    # minimal fake: 3 files to push, server has none
    # (stub _collect_push_files + client.post to no-op 200; assert lines)
    ...
    out = capsys.readouterr().out
    assert "Scanning files..." in out
    assert "Uploading 1/3" in out
    assert out.strip().endswith("Done: 3 pushed, 0 unchanged, 0 skipped, 0 failed")
```

(Fill the stub bodies against the real `_sync_files` seam — see `cli.py:2829`. The implementer wires the fakes to whatever `_sync_files` actually calls.)

- [ ] **Step 2: Run to verify it fails**

Run: `./test.sh tests/unit/test_cli_push_progress.py -v`
Expected: FAIL — no `Scanning files...` line today.

- [ ] **Step 3: Add phase + per-file lines (non-TTY branch)**

In `_sync_files`, guard a non-TTY progress path. Before scanning:

```python
    if not _is_tty:
        print("Scanning files...", flush=True)
```

After collecting and diffing:

```python
    if not _is_tty:
        print(f"Scanned {total} files, {unchanged} unchanged", flush=True)
        print("Comparing remote state...", flush=True)
```

In the upload loop, throttle: every file when `total <= 50`, else every 25th file or once per second:

```python
    if not _is_tty and (total <= 50 or i % 25 == 0 or (now - last_print) >= 1.0):
        print(f"Uploading {i}/{total} {repo_path}", flush=True)
        last_print = now
```

Final summary:

```python
    if not _is_tty:
        print(f"Done: {pushed} pushed, {unchanged} unchanged, {skipped} skipped, {failed} failed", flush=True)
```

Use `time.monotonic()` (avoid `Date.now`-style banned calls per project datetime rules — `monotonic` is fine).

- [ ] **Step 4: Run to verify it passes**

Run: `./test.sh tests/unit/test_cli_push_progress.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/bifrost/cli.py api/tests/unit/test_cli_push_progress.py
git commit -m "feat(cli): line-oriented push progress in non-TTY mode"
```

---

## Task 5: Fix workflow name corruption in manifest import + deploy preflight

**Goal:** Manifest import must write the **decorator** name into `Workflow.name`, never the manifest dict slug. Add a deploy preflight that catches a manifest/decorator mismatch with a clear error.

**Root cause:** `manifest_import.py:976` does `self._resolve_workflow(mwf.name or key, mwf, cache)`; `ManifestWorkflow.name` defaults to `""`, so a workflow keyed by slug `hello` writes `Workflow.name="hello"` while the decorator says `Sandbox Ticket Snapshot` → execution (`service.py:284` matches `meta.name == workflow_record.name`) fails with "Executable 'hello' not found".

**Files:**
- Modify: `api/src/services/manifest_import.py` (~970-984, `_resolve_workflow`)
- Create: `api/src/services/solution_deploy_preflight.py`
- Modify: `api/bifrost/commands/solution.py` (`_collect_workflows`, deploy flow)
- Test: `api/tests/unit/test_manifest_workflow_name.py`, `api/tests/unit/test_deploy_preflight.py`

**Interfaces:**
- Produces: `extract_workflow_name_from_source(source: str, function_name: str) -> str | None` (AST parse of `@workflow(name=...)`, default to `function_name`).
- Produces: `preflight_workflows(workflows: list[dict]) -> list[str]` returning human-readable mismatch errors (empty = ok).

- [ ] **Step 1: Write the failing test (import name)**

```python
# api/tests/unit/test_manifest_workflow_name.py
from src.services.solution_deploy_preflight import extract_workflow_name_from_source

def test_extracts_decorator_name():
    src = '@workflow(name="Sandbox Ticket Snapshot")\ndef main():\n    pass\n'
    assert extract_workflow_name_from_source(src, "main") == "Sandbox Ticket Snapshot"

def test_defaults_to_function_name():
    src = '@workflow()\ndef main():\n    pass\n'
    assert extract_workflow_name_from_source(src, "main") == "main"
```

- [ ] **Step 2: Run to verify it fails**

Run: `./test.sh tests/unit/test_manifest_workflow_name.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement the AST extractor**

```python
# api/src/services/solution_deploy_preflight.py
"""Preflight + name extraction so the decorator name stays the execution identity."""
from __future__ import annotations
import ast

def extract_workflow_name_from_source(source: str, function_name: str) -> str | None:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            for dec in node.decorator_list:
                if isinstance(dec, ast.Call) and _is_workflow_dec(dec.func):
                    for kw in dec.keywords:
                        if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                            return str(kw.value.value)
                    return function_name  # @workflow() with no name → function name
                if _is_workflow_dec(dec):  # bare @workflow
                    return function_name
    return function_name

def _is_workflow_dec(node: ast.expr) -> bool:
    return (isinstance(node, ast.Name) and node.id == "workflow") or (
        isinstance(node, ast.Attribute) and node.attr == "workflow"
    )

def preflight_workflows(workflows: list[dict]) -> list[str]:
    """workflows: deploy bundle entries with keys: name, function_name, path, source."""
    errors: list[str] = []
    for wf in workflows:
        src = wf.get("source")
        if not src:
            continue
        actual = extract_workflow_name_from_source(src, wf.get("function_name", ""))
        declared = wf.get("name")
        if actual and declared and actual != declared:
            errors.append(
                f"Workflow manifest entry `{declared}` points to {wf.get('path')}::"
                f"{wf.get('function_name')}, but the decorated name is `{actual}`. "
                f"Use @workflow(name=\"{declared}\") or update the manifest."
            )
    return errors
```

- [ ] **Step 4: Run to verify it passes**

Run: `./test.sh tests/unit/test_manifest_workflow_name.py -v`
Expected: PASS.

- [ ] **Step 5: Fix the import to use the decorator name (failing test first)**

```python
# add to test_manifest_workflow_name.py
import pytest
from src.services.manifest_import import ManifestImporter  # adjust to real class

@pytest.mark.asyncio
async def test_import_uses_decorator_name_not_slug(importer_with_file):
    # manifest entry keyed "hello", name="", path points at a file whose
    # decorator is @workflow(name="Sandbox Ticket Snapshot")
    ops = importer_with_file._resolve_workflow_name_for("hello", mwf_with_empty_name, source)
    assert ops == "Sandbox Ticket Snapshot"
```

Then in `manifest_import.py`, replace the name argument at line 976. Read the workflow source (import already gated on `_file_exists(mwf.path)`), extract the decorator name, and pass THAT instead of `mwf.name or key`:

```python
            if await _file_exists(mwf.path):
                source = await _read_file(mwf.path)  # use the same read used elsewhere
                from src.services.solution_deploy_preflight import extract_workflow_name_from_source
                exec_name = extract_workflow_name_from_source(source or "", mwf.function_name) \
                    or mwf.name or key
                await _prog(f"Importing workflow: {exec_name}")
                wf_ops = self._resolve_workflow(exec_name, mwf, cache)
```

(Confirm the file-read helper available in `manifest_import.py`; reuse it — do not invent a new reader.)

- [ ] **Step 6: Wire preflight into deploy (server side)**

In the `/api/solutions/{id}/deploy` handler (`api/src/routers/solutions.py`), before upserting workflows, call `preflight_workflows(workflows)`; if it returns errors, return `422` with the joined messages. Requires the deploy bundle to carry `source` per workflow — update `_collect_workflows` in `commands/solution.py` to include the `.py` source text. Add an e2e test in `tests/e2e/platform/` asserting a mismatched bundle returns 422 with the guidance string.

- [ ] **Step 7: Run + Commit**

Run: `./test.sh tests/unit/test_manifest_workflow_name.py tests/unit/test_deploy_preflight.py -v` → PASS.

```bash
git add api/src/services/solution_deploy_preflight.py api/src/services/manifest_import.py api/bifrost/commands/solution.py api/src/routers/solutions.py api/tests/unit/test_manifest_workflow_name.py api/tests/unit/test_deploy_preflight.py
git commit -m "fix(solutions): keep decorator name as workflow execution identity; deploy preflight"
```

---

## Task 6: v2-aware app validation (column ALREADY EXISTS)

**Verified live 2026-06-17:** `app_model` is ALREADY an ORM column (`applications.py:75`) and the ORM already branches on `standalone_v2` (lines 123, 135). The live API returns `app_model: 'standalone_v2'`. **NO migration, NO column work.** The ONLY gap: the validate endpoint (`applications.py:707-714`) still requires `_layout.tsx` unconditionally and does not branch on `app_model`. This task is just that branch.

**Goal:** `/applications/{id}/validate` must not run v1 `_layout.tsx` checks against `standalone_v2` apps.

**Files:**
- Modify: `api/src/routers/applications.py` (validation, the `_layout.tsx` check at ~707-714 and the Outlet check at ~764)
- Test: `api/tests/e2e/platform/test_app_validation.py`

**Interfaces:**
- Consumes: `application.app_model` (existing column, values `"legacy_v1"` / `"standalone_v2"`).

- [ ] **Step 1: Write the failing test**

```python
# api/tests/e2e/platform/test_app_validation.py
async def test_v2_app_skips_layout_check(client, make_app):
    app = await make_app(app_model="standalone_v2", files={"index.tsx": "export default () => null"})
    resp = await client.post(f"/api/applications/{app.id}/validate")
    issues = resp.json()["issues"]
    assert not any(i["file"] == "_layout.tsx" for i in issues)
```

- [ ] **Step 2: Run to verify it fails**

Run: `./test.sh e2e tests/e2e/platform/test_app_validation.py`
Expected: FAIL — validator requires `_layout.tsx` unconditionally; the error is present.

- [ ] **Step 3: Branch the validator (no migration — column already exists)**

In `applications.py` validation (the `_layout.tsx` check at ~707-714 and the Outlet check at ~764), guard the v1-only checks:

```python
    is_v2 = (app.app_model == "standalone_v2")
    if not is_v2:
        if layout_path not in files:
            errors.append(AppValidationIssue(severity="error", file="_layout.tsx",
                          message="Missing required _layout.tsx file"))
        # ...other v1-only checks
    else:
        # v2: validate buildability / imports; do not require _layout.tsx
        ...
```

- [ ] **Step 5: Run to verify it passes**

Run: `./test.sh e2e tests/e2e/platform/test_app_validation.py`
Expected: PASS. Then `cd client && npm run generate:types` (schema changed) and `npm run tsc`.

- [ ] **Step 6: Commit**

```bash
git add api/src/models/orm/applications.py api/alembic/versions/*app_model* api/src/routers/applications.py api/tests/e2e/platform/test_app_validation.py client/src/lib/v1.d.ts
git commit -m "feat(apps): app_model discriminator; v2 apps skip v1 layout validation"
```

---

## Task 7: Async, observable solution deploy

**Goal:** Deploy becomes job-based: CLI POSTs → server returns `deploy_job_id` → CLI polls a status endpoint with a deploy-specific timeout and heartbeat; final status/errors are inspectable. Quick-fix phases/timeout land here too.

**Files:**
- Create: `api/src/models/orm/solution_deploy_jobs.py` + migration
- Modify: `api/src/routers/solutions.py` (async deploy endpoint + `GET /api/solutions/deploy-jobs/{id}`)
- Modify: `api/bifrost/commands/solution.py` (local phases, deploy-specific timeout, poll loop, heartbeat)
- Modify: `api/bifrost/client.py` (per-request timeout override support if not present)
- Test: `api/tests/e2e/platform/test_solution_deploy_async.py`, `api/tests/unit/test_deploy_cli_poll.py`

**Interfaces:**
- Produces: `SolutionDeployJob(id, install_id, status, error, created_at, updated_at)`; status in `{queued, running, succeeded, failed}`.
- Produces: `POST /api/solutions/{id}/deploy` → `{deploy_job_id}`; `GET /api/solutions/deploy-jobs/{job_id}` → job record.
- Consumes (CLI): `client.post(..., timeout=600)` and a poll loop printing `Still deploying... Ns`.

- [ ] **Step 1: Failing e2e — deploy returns a job id and reaches succeeded**

```python
async def test_async_deploy_completes(client, make_solution_install):
    install = await make_solution_install()
    resp = await client.post(f"/api/solutions/{install.id}/deploy", json=MIN_BUNDLE)
    job_id = resp.json()["deploy_job_id"]
    # poll
    for _ in range(50):
        st = (await client.get(f"/api/solutions/deploy-jobs/{job_id}")).json()
        if st["status"] in ("succeeded", "failed"):
            break
    assert st["status"] == "succeeded"
```

- [ ] **Step 2: Run to verify it fails**

Run: `./test.sh e2e tests/e2e/platform/test_solution_deploy_async.py`
Expected: FAIL — endpoint returns deploy summary, not `deploy_job_id`.

- [ ] **Step 3: Add the job model + migration**

`solution_deploy_jobs.py`: ORM with the fields above, tz-aware `DateTime(timezone=True)` defaults via `lambda: datetime.now(timezone.utc)`. Migration creates the table. Apply via init container.

- [ ] **Step 4: Make the deploy endpoint enqueue + a status endpoint**

In `solutions.py`: create a job row (`queued`), dispatch the existing deploy logic as a background task (FastAPI `BackgroundTasks` or the project's worker queue — match how other async jobs run; check `api/src/jobs/`), update job to `running`→`succeeded`/`failed` with captured error. Add `GET /api/solutions/deploy-jobs/{job_id}`. Run preflight (Task 5) inside the job; failures set `status=failed, error=...`.

- [ ] **Step 5: CLI — phases, timeout, poll, heartbeat (unit test first)**

```python
# test_deploy_cli_poll.py — assert heartbeat + terminal status print
def test_poll_prints_heartbeat_and_result(monkeypatch, capsys):
    ...  # fake client returns running twice then succeeded
    out = capsys.readouterr().out
    assert "Still deploying..." in out
    assert "Deploy complete" in out
```

In `commands/solution.py` deploy flow: before the POST print local phases (`collecting solution files`, `counting files`, `computing bundle size`, `vendoring shared dependencies`, `uploading bundle`). POST with `timeout=600`. Then poll `deploy-jobs/{id}` every 3s, printing `Still deploying... {elapsed}s` each tick; on terminal status print `Deploy complete` or the error. Use `time.monotonic()`.

- [ ] **Step 6: Run + Commit**

Run: `./test.sh e2e tests/e2e/platform/test_solution_deploy_async.py` and `./test.sh tests/unit/test_deploy_cli_poll.py -v` → PASS. `cd client && npm run generate:types && npm run tsc`.

```bash
git add api/src/models/orm/solution_deploy_jobs.py api/alembic/versions/*deploy_job* api/src/routers/solutions.py api/bifrost/commands/solution.py api/bifrost/client.py api/tests/e2e/platform/test_solution_deploy_async.py api/tests/unit/test_deploy_cli_poll.py client/src/lib/v1.d.ts
git commit -m "feat(solutions): async observable deploy with job status + heartbeat"
```

---

## Task 8: Deploy bundle introspection

**Goal:** Deploy prints bundle file count + size, and warns loudly when a large vendored `modules/`/`shared/` tree is included. Reuses Task 7's phase output.

**Files:**
- Modify: `api/bifrost/commands/solution.py` (after bundle assembly, before POST)
- Test: `api/tests/unit/test_deploy_bundle_introspection.py`

**Interfaces:**
- Consumes: the assembled `bundle_python`, `apps`, and `vendored` set from Task 7's flow.
- Produces: printed `Bundle: N files, X.X MB`; a warning when vendored file count exceeds a threshold.

- [ ] **Step 1: Write the failing test**

```python
# test_deploy_bundle_introspection.py
from bifrost.commands.solution import summarize_bundle

def test_summary_counts_and_warns():
    summary = summarize_bundle(python_files={f"m/{i}.py": "x" for i in range(613)},
                               apps=[], vendored_count=613)
    assert summary.file_count == 613
    assert summary.warn is True
    assert "vendored" in summary.message.lower()
```

- [ ] **Step 2: Run to verify it fails**

Run: `./test.sh tests/unit/test_deploy_bundle_introspection.py -v`
Expected: FAIL — `summarize_bundle` missing.

- [ ] **Step 3: Implement `summarize_bundle`**

```python
import dataclasses

@dataclasses.dataclass
class BundleSummary:
    file_count: int
    size_mb: float
    warn: bool
    message: str

def summarize_bundle(python_files: dict, apps: list, vendored_count: int) -> BundleSummary:
    app_files = sum(len(a.get("src_files", {})) + len(a.get("bin_files", {})) for a in apps)
    count = len(python_files) + app_files
    size = sum(len(v.encode()) for v in python_files.values())
    size += sum(len(s.encode()) for a in apps for s in a.get("src_files", {}).values())
    mb = round(size / 1_000_000, 1)
    warn = vendored_count > 200
    msg = (f"This deploy includes {vendored_count} vendored files from modules/ and shared/. "
           f"Bundle size: {mb} MB.") if warn else f"Bundle: {count} files, {mb} MB."
    return BundleSummary(count, mb, warn, msg)
```

- [ ] **Step 4: Print it in the deploy flow**

After assembling the bundle (Task 7), call `summarize_bundle(...)` and `click.echo(summary.message)` (use `err=True` when `summary.warn`).

- [ ] **Step 5: Run + Commit**

Run: `./test.sh tests/unit/test_deploy_bundle_introspection.py -v` → PASS.

```bash
git add api/bifrost/commands/solution.py api/tests/unit/test_deploy_bundle_introspection.py
git commit -m "feat(solutions): deploy bundle size/count + large-vendor warning"
```

---

## Final Verification (after all tasks)

```bash
cd api && pyright && ruff check .
cd ../client && npm run generate:types && npm run tsc && npm run lint
cd .. && ./test.sh all && ./test.sh client unit
./test.sh tests/unit/test_dto_flags.py        # DTO parity (bundle/app_model touched DTOs)
./test.sh tests/unit/test_mcp_thin_wrapper.py  # if any MCP tool touched
```

Then drive it live (see [[feedback_drive_dont_just_test]]) from `~/GitHub/bifrost-sandbox-solution-example`: single-file push, push blocked in solution dir, deploy with heartbeat, a deliberately-mismatched workflow name caught by preflight, a `standalone_v2` app validating clean.

## Self-Review Notes

- Spec coverage: items 1-8 of the Obsidian plan map to Tasks 1-8 (deploy quick-fix folded into Task 7 alongside async; bundle handling = Task 8).
- #4 reframed to the real defect (URL-only `.env` diagnostic) — confirmed empirically against the sandbox workspace.
- #7 keeps the decorator name as identity per Jack's correction; fixes the import slug bug + adds preflight.
- #8 adds the missing `app_model` discriminator the validator needs to branch.
- Order respects dependencies: semantics (2) before guard (3); discriminator before v2 validation (6); preflight extractor (5) reused by async deploy (7); Task 7's bundle assembly reused by Task 8.
