# Solution Storage Scope Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make solution-scoped storage work the way a real workflow/SDK author reaches it: a solution scopes any declared location/table by `solution_id` (`finance/{solution_id}/…`), reachable from the SDK; `global_repo_access` gates the data cascade; declared-only inside solutions; solution policies stay solution-only.

**Architecture:** Remove the `location=="solutions"` hardcode so solution context scopes ANY location by `solution_id` (the S3 primitive `resolve_s3_key` already produces `{location}/{scope}/{path}`). The files SDK appends `?solution=<install_id>` exactly like the tables SDK. The server loads the Solution and gates the org→global *data* cascade on `global_repo_access` (today it gates code only). A solution declares its file locations + tables in the manifest; undeclared references inside a solution resolve not-found (no auto-create). Tables get the same declared-only + gated-cascade treatment. The DB columns, policy cascade, Core writes, inactive-lifecycle, manifest round-trip, and status gate all survive (they key on `solution_id`, not the literal location).

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy async / Pydantic v2 / Alembic / PostgreSQL / SeaweedFS(S3); Click CLI; React/TS client.

**Spec:** `docs/superpowers/specs/2026-06-24-solution-storage-scope-redesign.md` (+ the BROKEN-findings companion).

## Global Constraints

- **Worktree only.** `/home/jack/GitHub/bifrost/.claude/worktrees/files-sdk-policies` (branch `codex/files-sdk-policies`). Never touch the primary checkout.
- **Solution = a SCOPE on any location, NOT a location.** `location=="solutions"` special-casing is REMOVED; solution context (`?solution=` / `ctx.solution_id`) drives the scope (`{location}/{solution_id}/…`) for ANY location.
- **`global_repo_access` gates the DATA cascade.** ON → own-solution→org→global. OFF → sealed to the solution's own scope (no org/global data). Decided SERVER-SIDE (load the Solution; the SDK ExecutionContext does NOT carry the flag).
- **Declared-only inside solutions; `_repo/` workspace unchanged.** A solution cannot auto-create a table/file-location it didn't declare → undeclared reference resolves NOT-FOUND. Non-solution `_repo/` keeps implicit auto-create-on-write.
- **Solution policies (file + table) apply ONLY to `{solution_id}` — never cascade to global.**
- **SDK reachability is the load-bearing fix.** Files SDK appends `?solution=` as a QUERY PARAM on the URL (not the JSON body), mirroring `bifrost/tables.py::_scope_query`. The server reads `request.query_params.get("solution")`.
- **No mirror-delete; no implicit fallback the spec didn't ask for.** No dead code.
- **Tests via `./test.sh`** (Dockerized). Fast filter form: `./test.sh tests/path::test` (the `e2e <path>` form runs the WHOLE suite). The e2e suite has known pre-existing state-pollution flakes (test_manifest_scope_aware, ExecutionHistory, git-sync ManifestPolicy) — verify YOUR file in isolation.
- **Stage commits by explicit file list — NEVER `git add -A`.** This worktree has had stranded chat-v2 changes; a broad add re-introduces them.

---

## File Structure

| File | Responsibility | Action |
|------|----------------|--------|
| `api/bifrost/files.py` | SDK: append `?solution=<install_id>` (query) on every file op, from ExecutionContext | Mod |
| `api/src/routers/files.py` | `_resolve_effective_scope`: solution_id scopes ANY location; read `?solution=` query; metadata/content own-solution→org→global cascade gated by global_repo_access; declared-only enforcement | Mod |
| `api/src/services/file_policy_service.py` | metadata read cascade (mirror the existing policy `load_policy` cascade) + `solution_id IS NULL` org lookups (already fixed) + global gate | Mod |
| `api/src/routers/tables.py` + table resolution | gate org→global data cascade on global_repo_access; declared-only (no implicit create) inside a solution | Mod |
| `api/src/core/auth.py` | ensure ctx carries what the data-gate needs (solution_id already; load global_repo_access server-side where the cascade runs) | Mod (maybe) |
| `api/bifrost/manifest.py` + `models/contracts/solutions.py` | declared **file-locations** set on the manifest/solution (mirror `Manifest.tables`) | Mod |
| `api/src/services/solutions/deploy.py` + `manifest_import.py` | register declared locations on deploy/import | Mod |
| `api/tests/e2e/platform/test_solution_file_scope.py` + `test_solution_files_e2e.py` (capstone) + others | re-point off `location="solutions"` onto the scope model; add SDK-reachability tests | Mod |
| `client/src/...` (web SDK file calls) | honor solution scope (the app `X-Bifrost-App` path) for files | Mod |

---

## Task 1: Files SDK appends `?solution=` (the reachability fix)

**Files:**
- Modify: `api/bifrost/files.py` (all file ops)
- Test: `api/tests/unit/test_files_sdk_solution_scope.py`

**Interfaces:**
- Produces: every files-SDK REST call carries `?solution=<install_id>` as a URL query param when `ctx.solution_id` is set (read from the ExecutionContext), exactly like `bifrost/tables.py::_scope_query`. Omitted outside a solution execution (unchanged `_repo/`/org behavior).

> This is the SINGLE change that makes solution-scoped files reachable from a workflow at all — it's first because nothing else matters if the SDK can't signal solution context. `?solution=` is a QUERY param on the URL (not the JSON body); the server already reads `request.query_params.get("solution")` (auth.py:318).

- [ ] **Step 1: Write the failing test**

```python
# api/tests/unit/test_files_sdk_solution_scope.py
import bifrost.files as files_sdk
from bifrost._context import set_execution_context
from bifrost._execution_context import ExecutionContext

def test_file_ops_append_solution_query_when_in_solution(monkeypatch):
    captured = {}
    async def fake_post(url, json=None, **kw):
        captured["url"] = url
        class R: status_code = 200; 
        def _j(): return {"content": ""}
        R.json = staticmethod(_j); R.raise_for_status = lambda self=None: None
        return R
    # set a solution execution context
    set_execution_context(ExecutionContext(solution_id="11111111-1111-1111-1111-111111111111"))
    monkeypatch.setattr(files_sdk, "_post", fake_post, raising=False)  # adapt to the SDK's HTTP call
    import asyncio
    asyncio.run(files_sdk.read("finance/x.txt", location="finance"))
    assert "solution=11111111-1111-1111-1111-111111111111" in captured["url"]

def test_file_ops_omit_solution_query_outside_solution(monkeypatch):
    captured = {}
    async def fake_post(url, json=None, **kw):
        captured["url"] = url
        class R: status_code = 200
        R.json = staticmethod(lambda: {"content": ""}); R.raise_for_status = lambda self=None: None
        return R
    set_execution_context(ExecutionContext(solution_id=None))
    monkeypatch.setattr(files_sdk, "_post", fake_post, raising=False)
    import asyncio
    asyncio.run(files_sdk.read("x.txt"))
    assert "solution=" not in captured["url"]
```

> The exact monkeypatch target depends on how `bifrost/files.py` issues HTTP (read it — it uses an `apiClient`/`_post`/httpx call). Adapt the test to capture the actual request URL. The ASSERTION (solution= in URL when ctx.solution_id set, absent otherwise) is the contract.

- [ ] **Step 2: Run → FAIL** — `./test.sh tests/unit/test_files_sdk_solution_scope.py -v`.

- [ ] **Step 3: Implement** — In `api/bifrost/files.py`, add a helper mirroring `tables.py::_scope_query`:

```python
from urllib.parse import urlencode
from ._context import _current_context

def _solution_query() -> str:
    ctx = _current_context()
    solution_id = getattr(ctx, "solution_id", None) if ctx is not None else None
    return f"?{urlencode({'solution': str(solution_id)})}" if solution_id else ""
```

Append `_solution_query()` to the URL of every file op (read, read_bytes, write, write_bytes, list, + the others at ~227/251/291). The `scope` (org) stays in the JSON body as today; `solution` goes on the URL. (If the SDK already builds a URL with a query, merge params rather than double-`?`.)

- [ ] **Step 4: Run → PASS.** Confirm both tests pass.

- [ ] **Step 5: Commit**

```bash
git add api/bifrost/files.py api/tests/unit/test_files_sdk_solution_scope.py
git commit -m "feat(solution-files): files SDK appends ?solution= from ExecutionContext (reachability)"
```

---

## Task 2: `_resolve_effective_scope` scopes ANY location by solution_id (drop the hardcode)

**Files:**
- Modify: `api/src/routers/files.py` (`_resolve_effective_scope` ~line 275, `_ctx_solution_id` ~291)
- Test: `api/tests/e2e/platform/test_solution_file_scope.py` (add non-"solutions" location cases)

**Interfaces:**
- Produces: when `ctx.solution_id` is set (from `?solution=`), `_resolve_effective_scope` returns `str(install_id)` as the scope for ANY location (not just `"solutions"`), so `resolve_s3_key(location, install_id, path)` → `{location}/{install_id}/{path}`. `request.scope` is ignored whenever a solution context is present (the H6 rule, now generalized).

- [ ] **Step 1: Failing test** — a solution write to `location="finance"` (with `?solution=<install>`) lands at `finance/{install}/...` and is readable by that solution; a second solution can't read it; the metadata row has `solution_id == install`, `location == "finance"`.

```python
# add to api/tests/e2e/platform/test_solution_file_scope.py
async def test_solution_scopes_arbitrary_location(solution_client, db_session, install_id):
    await solution_client.put("/api/files/write", params={"solution": str(install_id)},
        json={"location":"finance","path":"q1.csv","content_b64":"aGk=","mode":"cloud"})
    got = await solution_client.post("/api/files/read", params={"solution": str(install_id)},
        json={"location":"finance","path":"q1.csv","mode":"cloud"})
    assert got.status_code == 200
    # the S3 key + metadata are finance/{install}/..., not org-scoped
    md = (await db_session.execute(select(FileMetadata).where(
        FileMetadata.location=="finance", FileMetadata.solution_id==install_id))).scalars().first()
    assert md is not None
```

(Use/extend the existing solution-context client fixtures in this file — they already drive `?solution=`. Mirror their setup but with `location="finance"` instead of `"solutions"`.)

- [ ] **Step 2: Run → FAIL** (today it scopes finance to the org, so the metadata query finds nothing / the read 404s differently).

- [ ] **Step 3: Implement** — in `_resolve_effective_scope` (files.py:275):

```python
def _resolve_effective_scope(ctx, location, requested_scope):
    # Solution context scopes ANY location by the install id (the S3 key becomes
    # {location}/{install_id}/...). Generalizes the former location=="solutions"
    # special-case. request.scope is ignored under a solution context (H6).
    if ctx.solution_id is not None:
        return str(ctx.solution_id)
    return _storage_scope(_file_org_id(ctx, location, requested_scope))
```

And `_ctx_solution_id` (files.py:291): return the install_id whenever `ctx.solution_id` is set (drop the `location == "solutions"` condition). Verify the metadata-write path (Task 15's C2) now stamps `solution_id` for ANY location under a solution context (it keys off `_ctx_solution_id`). Grep files.py for every `location == "solutions"` literal and remove the special-case (there are ~3: ~286, ~296, ~358 per the findings) — replacing with the `ctx.solution_id is not None` test.

- [ ] **Step 4: Run → PASS.** Also run the existing scope tests: `./test.sh tests/e2e/platform/test_solution_file_scope.py -v` — the old `location="solutions"` tests must STILL pass (solutions is just one location now) AND the new finance test passes.

- [ ] **Step 5: Commit**

```bash
git add api/src/routers/files.py api/tests/e2e/platform/test_solution_file_scope.py
git commit -m "feat(solution-files): solution_id scopes ANY location (drop location==solutions hardcode)"
```

---

## Task 3: File metadata/content read cascade own-solution → org → global, gated by global_repo_access

**Files:**
- Modify: `api/src/services/file_policy_service.py` (the metadata lookup) + `api/src/routers/files.py` (read/list resolution)
- Test: `api/tests/e2e/platform/test_solution_file_cascade_gated.py`

**Interfaces:**
- Produces: a solution file READ resolves own-solution (`solution_id`) first, then — IF `global_repo_access` is ON — org, then global. With `global_repo_access` OFF, resolution STOPS at the solution scope (sealed). The file POLICY cascade (`load_policy`, Task 16) already does own-solution→org→global; this task (a) mirrors it for metadata/content reads and (b) adds the global_repo_access gate to BOTH.

> The policy cascade at `file_policy_service.load_policy` (~245-304) already does the own→org→global steps. This task GATES steps 1-2 (org, global) on `global_repo_access` (load the Solution by install_id; if `global_repo_access` is False, skip org+global). And it adds the SAME cascade for the metadata/content read path (the bytes the solution reads), so a sealed solution can't read an org/global file.

- [ ] **Step 1: Failing tests** —
  - (gated-open) solution with `global_repo_access=True`: a file present only at GLOBAL scope is readable from the solution context (cascades).
  - (gated-sealed) solution with `global_repo_access=False`: the same global file is NOT readable (404/not-found) — sealed.
  - own-solution always wins over org/global for the same (location, path).

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement** — load the Solution (`SolutionORM` by install_id) where the cascade runs; thread `global_repo_access` into both `load_policy`'s org/global steps and the metadata/content read cascade. When False, the resolver returns own-solution-only (no org/global fallback). When True, the existing cascade runs. Mirror the table cascade gating from Task 4 (do Task 4's `global_repo_access` helper first if shared, or define a small `_solution_allows_global(db, install_id) -> bool`). Keep the own-solution arm always-on.

- [ ] **Step 4: Run → PASS.** Regression: a NON-solution file read (no solution context) is unchanged.

- [ ] **Step 5: Commit** — `feat(solution-files): metadata/content read cascade gated by global_repo_access (sealed when off)`

---

## Task 4: Tables — gate the org→global data cascade on global_repo_access

**Files:**
- Modify: `api/src/routers/tables.py` (the `_resolve_solution_table_by_name` → org→global fallback) + `api/src/repositories/org_scoped.py` (or where the table name cascade runs)
- Test: `api/tests/e2e/platform/test_table_solution_cascade_gated.py`

**Interfaces:**
- Produces: a solution table name lookup resolves own-solution → (if `global_repo_access`) org → global. With the flag OFF, sealed to own-solution. (Today the table data cascade is UNGATED — README:508-519. This makes `global_repo_access` gate data, matching files Task 3.)

- [ ] **Step 1: Failing tests** — solution `global_repo_access=True`: a `_repo/`/global table resolves by name from the solution; `=False`: it does NOT (not-found). Own-solution table always wins.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** — in the table-by-name resolution (`routers/tables.py:622-673` + the `repo.get`/cascade), after the own-solution miss, gate the org→global fallback on the Solution's `global_repo_access`. Define/reuse the shared `_solution_allows_global(db, install_id)` helper from Task 3. Keep non-solution (`_repo/`) resolution unchanged.
- [ ] **Step 4: Run → PASS.** Regression: non-solution table resolution unchanged; the existing solution-table tests still pass (own-solution still wins).
- [ ] **Step 5: Commit** — `feat(solution-tables): gate org→global table cascade on global_repo_access`

---

## Task 5: Declared-only inside solutions (no implicit create) — files + tables

**Files:**
- Modify: `api/src/routers/files.py` (write path) + `api/src/routers/tables.py` (the `_ensure_table_exists`/auto-create path) 
- Test: `api/tests/e2e/platform/test_solution_declared_only.py`

**Interfaces:**
- Produces: inside a solution context, writing/creating a table or file-location that the solution did NOT declare → NOT-FOUND/refused (no auto-create). Non-solution `_repo/` workspace KEEPS auto-create-on-write (unchanged).

> Declaration source: Task 6 adds the declared file-locations to the manifest; tables are declared via `Manifest.tables`. This task enforces "declared-only" at the WRITE/create path for solution context. If Task 6's declaration data isn't available yet, this task can land the ENFORCEMENT POINT (the check + the not-found behavior) keyed on "is this location/table in the solution's declared set" with the declared-set lookup wired in Task 6 — OR sequence Task 6 before Task 5. Recommend Task 6 first; this brief assumes the declared set is queryable.

- [ ] **Step 1: Failing tests** — solution writes to an UNDECLARED location → not-found/refused (no row created, no S3 write); solution writes to a DECLARED location → succeeds; a `_repo/` (non-solution) write to a new location/table STILL auto-creates.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** — in the solution write/create paths, check the solution's declared set (file locations from Task 6 / declared tables from `Manifest.tables` registered at deploy). Undeclared → return not-found (mirror how a missing table reads as not-found; do NOT 500). Skip the check entirely when there's no solution context (`_repo/` unchanged).
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** — `feat(solution-storage): declared-only inside solutions (no implicit create); _repo unchanged`

---

## Task 6: Manifest declares the solution's file locations

**Files:**
- Modify: `api/bifrost/manifest.py` (+ `models/contracts/solutions.py`), `api/src/services/manifest_generator.py`, `api/src/services/manifest_import.py`, `api/src/services/solutions/deploy.py`
- Test: `api/tests/unit/test_manifest.py` + `api/tests/e2e/platform/test_git_sync_local.py`

**Interfaces:**
- Produces: a `Manifest.file_locations` declaration (mirror `Manifest.tables: dict[str, ManifestTable]`) — the set of file locations the solution owns/scopes. Round-trips through export→import; registered on deploy so the Task-5 declared-only check + the Task-2/3 scope resolution know the solution's owned locations.

- [ ] **Step 1: Failing tests** — a solution declaring file location `finance` round-trips through the manifest (export emits it, import restores it); the deployed solution's declared set includes `finance`; a non-declared location is absent.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** — add the declaration model (mirror `ManifestSolutionConfigSchema`/`Manifest.tables` shape) to `manifest.py`; serialize in `manifest_generator.py`; import + register on the solution in `manifest_import.py`/`deploy.py` (a `solution_file_locations` set on the Solution, or a small declared-locations table — mirror how config-schema declarations are stored). Non-destructive upsert per CLAUDE.md. Where the declared set is read (Tasks 2/3/5), query this registration.
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** — `feat(solution-storage): declare solution file locations in the manifest (round-trip + deploy registration)`

---

## Task 7: Solution policies apply only to the solution scope (never global)

**Files:**
- Modify: `api/src/services/file_policy_service.py` + table policy resolution (confirm the policy cascade never serves a solution policy for org/global resolution)
- Test: `api/tests/e2e/platform/test_solution_policy_solution_only.py`

**Interfaces:**
- Produces: a file/table policy carrying `solution_id` governs ONLY `{solution_id}` resolution — it is never consulted when resolving an org or global file/table. (The cascade's solution arm already runs first for solution context; this task confirms the REVERSE — a solution policy never leaks UP into org/global evaluation.)

- [ ] **Step 1: Failing test** — seed a solution-scoped file policy for a prefix; resolve the SAME prefix in an ORG (non-solution) context → the solution policy is NOT applied (the org/global policy or default governs). And a global resolution never sees the solution policy.
- [ ] **Step 2: Run → FAIL** (if the org cascade currently can see a solution policy row) or PASS-and-harden (if it's already excluded — then add the test as a guard + note).
- [ ] **Step 3: Implement/confirm** — ensure the org and global cascade arms filter `solution_id IS NULL` (the Codex fix added this to the exact-match lookups; confirm the longest-prefix cascade arms do too). A solution policy must never be a candidate for org/global resolution.
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** — `feat(solution-storage): solution policies never apply to org/global resolution`

---

## Task 8: Web SDK / app file calls honor solution scope

**Files:**
- Modify: `client/src/...` (the web SDK file client used by solution-mounted apps) + confirm `routers/files.py` honors the app's `X-Bifrost-App` → solution scope for files (mirroring the worker `?solution=` path)
- Test: vitest for the web file client + a Playwright/e2e if practical

**Interfaces:**
- Produces: a file call from a solution-mounted app resolves to the app's install scope (the `X-Bifrost-App` header → `ctx.solution_id` → `{location}/{install}/...`), so the web SDK reaches solution files the same way the Python SDK does via `?solution=`.

- [ ] **Step 1: Failing test** — a web-SDK file read/write from a solution app context targets the install scope (the request carries the app/solution context; the resolved scope is the install, not the user's org).
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** — ensure the web file client forwards the app/solution context and `routers/files.py`'s `_resolve_effective_scope` honors `X-Bifrost-App`→solution for files (auth.py already maps `X-Bifrost-App`→ctx; confirm files read it like the worker reads `?solution=`). If the app path already sets `ctx.solution_id` (via the L4 X-Bifrost-App gate work), this may be a confirm + test.
- [ ] **Step 4: Run → PASS.** tsc/lint clean.
- [ ] **Step 5: Commit** — `feat(solution-files): web SDK / app file calls honor solution scope`

---

## Task 9: Re-point the capstone + all `location="solutions"` tests onto the scope model

**Files:**
- Modify: `api/tests/e2e/platform/test_solution_files_e2e.py` (capstone), `test_solution_file_scope.py`, and any test hardcoding `location="solutions"`
- Test: themselves

**Interface:** the tests prove the REAL model — a solution scopes a DECLARED location (e.g. `finance`) by solution_id, reachable via the SDK/`?solution=` path, gated by global_repo_access, declared-only — not the `location="solutions"` literal.

- [ ] **Step 1:** Grep `grep -rn 'location.*solutions\|"solutions"' api/tests` — for each, decide: is it testing the SCOPE behavior (re-point to a declared location like `finance` + the solution context) or genuinely the literal "solutions" location (keep only if "solutions" remains a valid declarable location — per the spec it's just one location now, so most should move to `finance`/a declared name). Update the capstone's file step to write to a DECLARED location via the solution context and assert `{location}/{install}/...`.
- [ ] **Step 2: Run → iterate to green** — `./test.sh tests/e2e/platform/test_solution_files_e2e.py tests/e2e/platform/test_solution_file_scope.py -v` (isolated). Every behavior a real assertion; no silent pass.
- [ ] **Step 3: Commit** — `test(solution-storage): re-point capstone + scope tests onto the solution-as-scope model`

---

## Task 10: Full verification sweep

- [ ] **Choke/scope smell check:** `grep -rn 'location == "solutions"\|location=="solutions"' api/src` → NOTHING (the hardcode is gone; solutions is just a location string in data, not a control-flow branch).
- [ ] **SDK reachability proof:** a solution WORKFLOW (not a raw REST call) writes + reads a file at a declared location — covered by the capstone; confirm it passes.
- [ ] **Backend:** `cd api && pyright && ruff check .` → 0 errors.
- [ ] **Types + frontend:** `cd client && npm run generate:types && npm run tsc && npm run lint` → pass (no new errors).
- [ ] **Full suite:** `./test.sh all` → green (parse the JUnit XML; triage real-vs-flake by isolating any failing file — known flakes: test_manifest_scope_aware, ExecutionHistory, git-sync ManifestPolicy, all pass isolated).
- [ ] **Client:** `./test.sh client unit`.
- [ ] **global_repo_access matrix:** confirm sealed (OFF) vs open (ON) for BOTH files and tables — undeclared/org/global not-found when sealed, cascades when open.
- [ ] Commit any fixups.

---

## Notes for the implementer

- **The S3 primitive is already right.** `resolve_s3_key` produces `{location}/{scope}/{path}`. Do NOT change it. The bug was the router hardcode + the SDK, not storage.
- **Mirror the table SDK exactly.** `bifrost/tables.py::_scope_query` is the canonical `?solution=` pattern. Files append `?solution=` as a URL QUERY param (the server reads `request.query_params.get("solution")`), keeping `scope` (org) in the JSON body.
- **`global_repo_access` is decided SERVER-SIDE.** The SDK ExecutionContext carries `solution_id`, NOT `global_repo_access`. Load the Solution where the cascade runs to read the flag. Define ONE helper `_solution_allows_global(db, install_id)` and use it for both files (Task 3) and tables (Task 4).
- **Declared-only is solutions-only.** A null solution context (`_repo/`) keeps implicit auto-create. Never gate the non-solution path.
- **Most plumbing survives.** DB columns, the inactive-lifecycle (L1-L10), Core writes, manifest round-trip, status gate all key on `solution_id` — they don't care if the location is `solutions` or `finance`. You're correcting the scope-resolution + SDK + declaration + global-gate layer.
- **Watch the worktree.** Stage explicit files only; never `git add -A` (stranded chat-v2 changes).
- **Sequence note:** Task 6 (declaration) feeds Task 5 (declared-only enforcement) — do 6 before 5, or land 5's enforcement point and wire the declared-set query in 6. Tasks 3+4 share the `_solution_allows_global` helper — define it once (Task 3) and reuse (Task 4).
