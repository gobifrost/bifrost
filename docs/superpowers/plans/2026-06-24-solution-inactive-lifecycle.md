# Solution Inactive-Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the orphan-to-org solution-uninstall model with an active/inactive Solution lifecycle: uninstall freezes data in place under `solution_id` and flips `Solution.status` to `inactive`; reinstall-over-inactive prompts then reactivates; hard-delete is the single confirmed destructive path.

**Architecture:** Add `Solution.status` (active|inactive). Uninstall = status flip, no data movement. A single status gate loads the Solution in the request-context resolver and treats inactive-solution data as dormant (browsable/exportable, not servable). Hard-delete drops the Solution row (existing `solution_id ondelete=CASCADE` FKs remove owned rows) + sweeps the `solutions/{id}/` S3 prefix. The entire orphan-to-org subsystem (provenance columns, reattach-by-slug, byte-move, orphan jobs, "show orphaned" toggles) is deleted across all entity types.

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy async / Pydantic v2 / Alembic / PostgreSQL / SeaweedFS(S3); React/TypeScript client.

**Spec:** `docs/superpowers/specs/2026-06-24-solution-inactive-lifecycle-design.md`

## Global Constraints

- **Worktree only.** All work in `/home/jack/GitHub/bifrost/.claude/worktrees/files-sdk-policies` (branch `codex/files-sdk-policies`). Never touch the primary checkout.
- **Greenfield migration.** Pre-release; no deployed orphaned-to-org data. Drop `origin_solution_slug`/`origin_solution_id`/`orphaned_at` columns outright — NO reconciliation.
- **Universal model.** ALL solution-owned entities (workflows, tables, configs, files, forms, agents, apps, custom claims, event sources, config schema) follow ONE lifecycle. No per-entity orphan logic remains anywhere.
- **Uninstall mutates ONLY `Solution.status`.** No data row is touched on uninstall — no re-home, no S3 move, no provenance, no cascade-avoidance.
- **Hard-delete is the ONLY destructive path.** It drops the Solution row (FK cascade) + sweeps `solutions/{id}/` S3. Gated by a backend confirm token AND a frontend type-the-name modal.
- **Dormancy is enforced once, centrally** — load the Solution in the context resolver; inactive ⇒ the solution context is refused for execution/serve paths (browsing/export still work via separate read paths).
- **No mirror-delete on reactivate.** Reinstall-over redeploys entities atop frozen rows via the existing non-destructive upsert; data the bundle omits survives.
- **No dead code.** Removing an orphan code path removes everything only it reached.
- **Tests via `./test.sh`** (Dockerized). The fast filtered form is `./test.sh tests/path::test` (the `e2e <path>` form runs the whole suite). The e2e suite has known pre-existing state-pollution flakes — verify YOUR file in isolation.

---

## File Structure

| File | Responsibility | Action |
|------|----------------|--------|
| `api/src/models/orm/solutions.py` | `Solution.status` column | Mod |
| `api/alembic/versions/<rev>_solution_status_drop_orphan.py` | add `status`; DROP orphan columns on tables/config/file_metadata/file_policies | New |
| `api/src/models/orm/tables.py`, `config.py`, `file_metadata.py` | remove orphan columns | Mod |
| `api/src/models/contracts/solutions.py` | `status` on Solution DTO; `SolutionDeleteSummary` → uninstall vs hard-delete shapes; reinstall-conflict response | Mod |
| `api/src/routers/solutions.py` | uninstall (status flip), hard-delete (cascade+sweep+confirm), list status filter, strip orphan blocks, strip orphan file-job branches | Mod |
| `api/src/core/auth.py` | central status gate: load Solution, refuse inactive for execution context | Mod |
| `api/src/services/solutions/zip_install.py` | reinstall-over-inactive detection + reactivate | Mod |
| `api/src/services/solutions/deploy.py` | strip table/config reattach-by-slug | Mod |
| `api/src/services/solution_files.py` | delete the 4 orphan functions | Mod |
| `api/src/models/orm/solution_file_jobs.py` + `_run_file_job`/`enqueue_file_job` | strip orphan kind | Mod |
| `api/src/repositories/{org_scoped,tables,config}.py` + `routers/{tables,config}.py` | strip `include_orphaned` | Mod |
| `client/src/pages/{Solutions,SolutionDetail,Tables,Config}.tsx`, `services/tables.ts`, `hooks/useConfig.ts` | status badge + show-inactive + uninstall/hard-delete buttons; strip show-orphaned | Mod |

---

## Task 1: `Solution.status` column + greenfield migration (add status, drop orphan columns)

**Files:**
- Modify: `api/src/models/orm/solutions.py`, `api/src/models/orm/tables.py`, `api/src/models/orm/config.py`, `api/src/models/orm/file_metadata.py`
- Create: `api/alembic/versions/<rev>_solution_status_drop_orphan.py`
- Test: `api/tests/unit/test_solution_status_model.py`

**Interfaces:**
- Produces: `Solution.status: Mapped[str]` (`"active"|"inactive"`, server_default `"active"`, not null). The orphan columns `origin_solution_slug`/`origin_solution_id`/`orphaned_at` are GONE from `Table`, `Config`, `FileMetadata`, `FilePolicy`.

- [ ] **Step 1: Write the failing test**

```python
# api/tests/unit/test_solution_status_model.py
from src.models.orm.solutions import Solution
from src.models.orm.tables import Table
from src.models.orm.config import Config
from src.models.orm.file_metadata import FileMetadata, FilePolicy

def test_solution_has_status_default_active():
    s = Solution(slug="x", name="X")
    assert s.status == "active"

def test_orphan_columns_removed():
    for M in (Table, Config, FileMetadata, FilePolicy):
        cols = set(M.__table__.columns.keys())
        assert "origin_solution_slug" not in cols, M.__name__
        assert "origin_solution_id" not in cols, M.__name__
        assert "orphaned_at" not in cols, M.__name__
```

- [ ] **Step 2: Run to verify it fails**

Run: `./test.sh tests/unit/test_solution_status_model.py -v`
Expected: FAIL (status missing / orphan columns still present).

- [ ] **Step 3: Add `status` to Solution, remove orphan columns**

In `api/src/models/orm/solutions.py` (near `setup_complete`, ~line 101), add:

```python
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="active", default="active"
    )
    # "active" — installed & live. "inactive" — uninstalled, data frozen in
    # place under solution_id, dormant (browsable/exportable, not servable).
```

Add a constructor default so `status` is set at construction time (mirror the project's bool-default `__init__` pattern if Solution has one; otherwise rely on `default="active"` which fires on INSERT and add `__init__` only if a test constructs-without-flush and reads `.status` — the test above does, so add it):

```python
    def __init__(self, **kw):
        kw.setdefault("status", "active")
        super().__init__(**kw)
```

In `tables.py` (lines ~64-66), `config.py` (~64-66), `file_metadata.py` (FileMetadata ~49-55 AND FilePolicy ~119-125): DELETE the three `origin_solution_slug` / `origin_solution_id` / `orphaned_at` `mapped_column` lines from each model.

- [ ] **Step 4: Run model test → pass**

Run: `./test.sh tests/unit/test_solution_status_model.py -v` → PASS.

- [ ] **Step 5: Create the migration**

```bash
cd api && alembic revision -m "solution status + drop orphan provenance columns"
```

```python
def upgrade() -> None:
    op.add_column("solutions", sa.Column("status", sa.String(length=16),
                  nullable=False, server_default="active"))
    for table in ("tables", "configs", "file_metadata", "file_policies"):
        op.drop_column(table, "origin_solution_slug")
        op.drop_column(table, "origin_solution_id")
        op.drop_column(table, "orphaned_at")

def downgrade() -> None:
    for table in ("tables", "configs", "file_metadata", "file_policies"):
        op.add_column(table, sa.Column("orphaned_at", sa.DateTime(timezone=True), nullable=True))
        op.add_column(table, sa.Column("origin_solution_id", sa.Uuid(), nullable=True))
        op.add_column(table, sa.Column("origin_solution_slug", sa.String(length=255), nullable=True))
    op.drop_column("solutions", "status")
```

Confirm the actual table name for configs (`configs` vs `config`) by reading `Config.__tablename__`; use the real names. Chain `down_revision` onto the current head (`cd api && alembic heads`).

> The two pure-orphan migrations (`20260606_orphan_provenance.py`, `20260609_orphan_tables_out_of_name_ns.py`) and the orphan-column adds inside `20260623_file_solution_id.py` are NOT edited here — a forward DROP migration is cleaner than rewriting history, and the test stack runs all migrations forward. Leave them; this migration drops what they added.

- [ ] **Step 6: Apply + verify**

Run: `./test.sh stack reset && ./test.sh tests/unit/test_solution_status_model.py -v` → PASS, no migration error.

- [ ] **Step 7: Commit**

```bash
git add api/src/models/orm/solutions.py api/src/models/orm/tables.py api/src/models/orm/config.py api/src/models/orm/file_metadata.py api/alembic/versions/*solution_status*.py api/tests/unit/test_solution_status_model.py
git commit -m "feat(solution-lifecycle): Solution.status + drop orphan provenance columns"
```

---

## Task 2: Delete the orphan file service + the orphan SolutionFileJob branch

**Files:**
- Modify: `api/src/services/solution_files.py` (delete 4 functions)
- Modify: `api/src/routers/solutions.py` (`_run_file_job` + `enqueue_file_job` orphan branches)
- Modify: `api/src/models/orm/solution_file_jobs.py` (docstring)
- Test: `api/tests/e2e/platform/test_solution_files_service.py` (drop the orphan tests)

**Interfaces:**
- Consumes: nothing new.
- Produces: `solution_files.py` retains `enumerate_solution_files`, `read_solution_file`, `write_solution_file` ONLY. No `orphan_*`/`restamp_*`/`delete_solution_files_metadata`. The `orphan` job kind no longer exists.

> This task only DELETES. The callers in `delete_solution` are removed in Task 3 — but since Task 3 depends on these being gone, do this first and let Task 3 remove the now-dangling calls. To keep the tree importable between tasks, this task ALSO removes the `delete_solution` calls to these functions (the orphan blocks at solutions.py ~890-920, ~970-1021) — Task 3 then rebuilds `delete_solution` cleanly. If that coupling is awkward, fold Tasks 2+3 — but the brief keeps them split since the deletions are mechanical and the rebuild is judgment.

- [ ] **Step 1: Delete the 4 orphan functions** in `api/src/services/solution_files.py`: `restamp_solution_files_metadata` (~181-225), `delete_solution_files_metadata` (~228-248), `orphan_solution_files` (~251-309), `orphan_solution_files_by_ids` (~312-375). Remove now-unused imports they alone used (grep each removed import for other users first).

- [ ] **Step 2: Remove the orphan branches** in `api/src/routers/solutions.py`: in `_run_file_job` (~1248-1294) the `kind == "orphan"` dispatch branch (~1282-1289); in `enqueue_file_job` (~1303-1365) the orphan branch (~1318-1343). If `orphan` was the ONLY kind either function handled, and `restore`/`bulk_delete` are unimplemented stubs (they raise `ValueError`), then the enqueue endpoint + `_run_file_job` + `SolutionFileJob` model become dead — grep for any other caller; if none, delete `enqueue_file_job`, `get_file_job`, `_run_file_job`, and the `SolutionFileJob` model + its migration-free import sites. Report what you removed.

- [ ] **Step 3: Remove the orphan-block calls in `delete_solution`** (solutions.py ~890-920 file restamp/delete + ~970-1021 inline move + the SolutionFileJob enqueue). Leave `delete_solution` otherwise intact for now (Task 3 rebuilds it). The handler must still import-cleanly and the Solution-row delete at ~943 stays.

- [ ] **Step 4: Drop the orphan tests** from `api/tests/e2e/platform/test_solution_files_service.py` (the orphan-move + guard-active-orphan tests). Keep write/enumerate/read tests.

- [ ] **Step 5: Verify import + the surviving service tests**

Run: `./test.sh tests/e2e/platform/test_solution_files_service.py -v` → the surviving (write/enumerate/read) tests pass; no ImportError.
Run: `cd api && ruff check src/services/solution_files.py src/routers/solutions.py` → clean (catches dangling imports/unused).

- [ ] **Step 6: Commit**

```bash
git add api/src/services/solution_files.py api/src/routers/solutions.py api/src/models/orm/solution_file_jobs.py api/tests/e2e/platform/test_solution_files_service.py
git commit -m "refactor(solution-lifecycle): delete orphan file service + orphan job (replaced by status model)"
```

---

## Task 3: Uninstall = status flip; hard-delete = the destructive path

**Files:**
- Modify: `api/src/routers/solutions.py` (`delete_solution` → uninstall; new hard-delete endpoint)
- Modify: `api/src/models/contracts/solutions.py` (response shapes)
- Test: `api/tests/e2e/platform/test_solution_uninstall_lifecycle.py`

**Interfaces:**
- Produces:
  - `POST /api/solutions/{id}/uninstall` → flips `status` to `inactive`, returns the updated Solution. Data untouched. (Repurpose or add; keep the DELETE route as hard-delete per below — decide and document.)
  - `DELETE /api/solutions/{id}?confirm=<name>` (or a body `{confirm: name}`) → HARD delete: validates `confirm == solution.slug` (or name), drops the Solution row (FK cascade), sweeps `solutions/{id}/` S3. Returns a summary of what was destroyed.
  - `GET /api/solutions/{id}/deletion-summary` → counts of owned entities (files, tables, configs, …) for the confirmation modal.

> **Route decision (make it explicit):** the existing `DELETE /{id}` currently does orphan-to-org. Repurpose `DELETE /{id}` as the HARD delete (it already means "remove"), and ADD `POST /{id}/uninstall` for the status flip. The hard-delete MUST require an explicit confirm token server-side (don't rely on the modal). Auth: `CurrentSuperuser` (match the existing delete_solution dep).

- [ ] **Step 1: Failing tests**

```python
# api/tests/e2e/platform/test_solution_uninstall_lifecycle.py
# (use existing solution-deploy fixtures; pseudocode — wire to the real helpers)

async def test_uninstall_flips_status_and_freezes_data(deployed_solution, admin_client, db_session):
    sid = deployed_solution.id
    # write a file + the solution has a table
    r = await admin_client.post(f"/api/solutions/{sid}/uninstall")
    assert r.status_code == 200 and r.json()["status"] == "inactive"
    # data frozen in place: the table row still has solution_id == sid (not nulled)
    tbl = (await db_session.execute(select(Table).where(Table.solution_id == sid))).scalars().first()
    assert tbl is not None  # NOT orphaned to org
    # solution row still exists
    assert (await db_session.get(Solution, sid)) is not None

async def test_hard_delete_requires_confirm_and_cascades(deployed_solution, admin_client, db_session):
    sid = deployed_solution.id; slug = deployed_solution.slug
    bad = await admin_client.request("DELETE", f"/api/solutions/{sid}", params={"confirm": "wrong"})
    assert bad.status_code in (400, 422)  # confirm mismatch
    ok = await admin_client.request("DELETE", f"/api/solutions/{sid}", params={"confirm": slug})
    assert ok.status_code == 200
    assert (await db_session.get(Solution, sid)) is None  # row gone
    # owned rows cascaded
    assert (await db_session.execute(select(Table).where(Table.solution_id == sid))).scalars().first() is None
```

- [ ] **Step 2: Run → FAIL** — `./test.sh tests/e2e/platform/test_solution_uninstall_lifecycle.py -v`.

- [ ] **Step 3: Implement uninstall** — add `POST /{id}/uninstall`: load the Solution (404 if absent), set `status = "inactive"` via Core update (Solution row carries no `solution_id`, so the read-only guard doesn't apply — but use Core `update(Solution).where(id==).values(status="inactive")` for consistency), commit, return the updated DTO. NO data mutation, NO S3 ops.

- [ ] **Step 4: Implement hard-delete** — rewrite `DELETE /{id}`: require `confirm` (query or body) equal to the solution's slug (or name — pick slug, it's the stable id; document). On mismatch → 400/422. On match: capture the `solutions/{id}/` prefix, `await ctx.db.delete(sol)` (FK cascade removes owned rows), commit, then sweep the S3 prefix (reuse the existing `_solutions/{id}/` sweep code that was in the old delete_solution). Return a destruction summary. Remove ALL the old orphan blocks (table detach, config orphan, cache invalidate) — they're already gone from Task 2's call-removal; ensure none remain.

- [ ] **Step 5: Deletion-summary endpoint** — `GET /{id}/deletion-summary` returns counts: `enumerate_solution_files` count + `SELECT count(*)` per owned entity (tables/configs/workflows/forms/agents/apps/claims/events WHERE solution_id == id). Shape it as a small `SolutionDeletionSummary` contract for the modal.

- [ ] **Step 6: Run + regen types + commit**

Run: `./test.sh tests/e2e/platform/test_solution_uninstall_lifecycle.py -v` → PASS. Then `cd client && OPENAPI_URL=http://localhost:34212/openapi.json npm run generate:types`.

```bash
git add api/src/routers/solutions.py api/src/models/contracts/solutions.py client/src/lib/v1.d.ts api/tests/e2e/platform/test_solution_uninstall_lifecycle.py
git commit -m "feat(solution-lifecycle): uninstall=status flip + confirmed hard-delete (cascade+S3 sweep)"
```

---

## Task 4: Central status gate — inactive solutions are dormant

**Files:**
- Modify: `api/src/core/auth.py` (context resolver loads Solution, refuses inactive)
- Test: `api/tests/e2e/platform/test_inactive_solution_dormant.py`

**Interfaces:**
- Consumes: `Solution.status`.
- Produces: a request carrying `?solution=<inactive id>` for an EXECUTION/serve path is refused (the solution context is not set / a 4xx is returned), while browse/export read paths (which don't go through the execution context the same way) still resolve the data.

> **Single gate (per the spec).** `get_execution_context` (auth.py ~312-321) currently sets `solution_id = request.query_params.get("solution")` as a raw string. Change it to: if a `solution` param is present, load the Solution row; if `status != "active"`, do NOT set the solution context for execution (raise or leave solution_id unset so execution can't use inactive data). Browsing/export endpoints that read solution files/tables for the UI must NOT be gated here — confirm which code path the Files-browser + export use; if they share this context resolver, add an explicit allow for read-only browse (e.g. the gate refuses only execution/write, or the browse endpoints load data without requiring an active-solution execution context). READ how the Task-25 Files browser + the export endpoint resolve solution scope before choosing where the gate sits — the goal is "inactive = not servable/executable, but still browsable/exportable."

- [ ] **Step 1: Failing tests**

```python
# api/tests/e2e/platform/test_inactive_solution_dormant.py
async def test_inactive_solution_workflow_execution_refused(inactive_solution, admin_client):
    # a workflow scoped to an inactive solution can't execute against its data
    r = await admin_client.post(f"/api/workflows/execute?solution={inactive_solution.id}", json={...})
    assert r.status_code in (403, 409)  # dormant

async def test_inactive_solution_files_still_browsable(inactive_solution, admin_client):
    # the Contents/Files browser CAN still list the inactive solution's files
    r = await admin_client.get(f"/api/solutions/{inactive_solution.id}/entities")
    assert r.status_code == 200 and "files" in r.json()
```

- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement the gate** in `get_execution_context`: load Solution by the `?solution=` id; if inactive, refuse the execution context (the exact mechanism — raise vs unset — depends on how callers use `ctx.solution_id`; pick the one that makes execution paths fail closed and add a clear error). Ensure the solution-entities/browse endpoints are NOT gated (they read directly).
- [ ] **Step 4: Run → PASS.** Confirm an ACTIVE solution still executes normally (regression).
- [ ] **Step 5: Commit** — `feat(solution-lifecycle): central status gate — inactive solutions dormant (browsable, not servable)`

---

## Task 5: Strip reattach-by-slug from deploy

**Files:**
- Modify: `api/src/services/solutions/deploy.py`
- Test: existing deploy/redeploy e2e (adapt)

**Interfaces:** deploy no longer queries/clears orphan provenance. `_upsert_tables` no longer adopts orphans; `_reattach_orphan_configs` is deleted; `_reconcile_deletions` loses its `adopted_table_ids` param.

- [ ] **Step 1:** Delete the table orphan-adoption block in `_upsert_tables` (~946-1000); delete `_reattach_orphan_configs` (~1768-1803) + its call (~405); remove `adopted_table_ids` from `_reconcile_deletions` (~1823) + the docstring note. Remove the result-capture at ~397/405.
- [ ] **Step 2:** Run the deploy/redeploy e2e (grep `tests/e2e` for the deploy + redeploy tests): `./test.sh tests/e2e/platform/test_solution_deploy_files.py -v` and the table-deploy test. Adapt any test that ASSERTED orphan-reattach behavior (those assertions are now wrong — reattach is replaced by reactivate-over-inactive in Task 6). Where a test relied on reattach, either re-point it at the reactivate path (if it fits) or remove the now-invalid assertion and note it.
- [ ] **Step 3:** `cd api && ruff check src/services/solutions/deploy.py` clean. Commit — `refactor(solution-lifecycle): remove reattach-by-slug from deploy (replaced by reactivate)`

---

## Task 6: Reinstall-over-inactive — prompt then reactivate

**Files:**
- Modify: `api/src/services/solutions/zip_install.py` (`_resolve_or_create_solution` / `install_zip`)
- Modify: `api/src/routers/solutions.py` (install endpoint — surface the conflict + a `reactivate` confirm)
- Modify: `api/src/models/contracts/solutions.py` (conflict response)
- Test: `api/tests/e2e/platform/test_reinstall_over_inactive.py`

**Interfaces:**
- Produces: installing a bundle whose slug matches an existing INACTIVE install (same org) WITHOUT a confirm flag → `409` with a structured "inactive install exists; reinstall over it, or hard-delete first" payload. WITH `reactivate=true` (or `confirm`) → flips that install to `active` and redeploys entities atop the retained data (non-destructive upsert; no mirror-delete).

- [ ] **Step 1: Failing tests**

```python
# api/tests/e2e/platform/test_reinstall_over_inactive.py
async def test_reinstall_over_inactive_prompts(inactive_solution, admin_client, bundle_for_same_slug):
    r = await admin_client.post("/api/solutions/install", files=bundle_for_same_slug)  # no reactivate
    assert r.status_code == 409
    assert r.json()["detail"]["reason"] == "inactive_install_exists"

async def test_reinstall_over_inactive_reactivates(inactive_solution, admin_client, bundle_for_same_slug, db_session):
    r = await admin_client.post("/api/solutions/install?reactivate=true", files=bundle_for_same_slug)
    assert r.status_code in (200, 201)
    sol = await db_session.get(Solution, inactive_solution.id)
    assert sol.status == "active"  # SAME install reactivated, not a duplicate
    # retained data still under the SAME solution_id (came back)
```

- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3:** In `_resolve_or_create_solution` (zip_install.py ~354-371): after `find_install(slug, org)` finds an existing install, if its `status == "inactive"` and the caller did NOT pass `reactivate`, raise `InactiveInstallExists` (new exception). The install router catches it → 409 with the structured payload. When `reactivate` is set, flip `status → "active"` and proceed with the normal deploy (which upserts entities over the frozen rows — confirm the deploy's upsert is non-destructive so retained data the bundle omits survives; it is, per O1/Task 5). No new install row is created for that slug while inactive.
- [ ] **Step 4: Run → PASS** (both prompt + reactivate). Regenerate types if the contract changed.
- [ ] **Step 5: DTO/contract gates** if the install request/response changed: `./test.sh tests/unit/test_dto_flags.py tests/unit/test_contract_version.py -v` (refresh fingerprint additively if needed) + `python api/scripts/skill-truth/generate.py`. Commit — `feat(solution-lifecycle): reinstall-over-inactive prompts then reactivates (no duplicate install)`

---

## Task 7: Strip `include_orphaned` from repos + routers (tables, config, org_scoped)

**Files:**
- Modify: `api/src/repositories/org_scoped.py`, `tables.py`, `config.py`; `api/src/routers/tables.py`, `config.py`
- Test: existing tables/config list tests (adapt)

**Interfaces:** `list_tables`/`list_configs` lose the `include_orphaned` param + the `orphaned_at IS NULL` filter. `OrgScopedRepository` loses `_model_has_orphaned_at` + the orphan filter in `list()`. The GET routes lose the `include_orphaned` Query param.

- [ ] **Step 1:** Delete `_model_has_orphaned_at` (org_scoped.py ~49-51) + the orphan `where` in `list()` (~252-253, ~342-343) + the `include_orphaned` param overload (~308-343). Delete `include_orphaned` from `list_tables` (tables.py repo ~28 + where ~46-47) and `list_configs` (config.py ~52 + where ~84-85). Delete the `include_orphaned` Query params from `routers/tables.py` (~735-738, call ~750) and `routers/config.py` (~51-54, call ~72).
- [ ] **Step 2:** Run `./test.sh tests/e2e -k "table or config" ...` — actually use the filtered form: run the specific tables + config list tests (grep for them). Adapt/remove any test asserting orphan-filter behavior. `cd api && ruff check` the 5 files clean.
- [ ] **Step 3: Commit** — `refactor(solution-lifecycle): remove include_orphaned (no orphan state)`

---

## Task 8: Frontend — status badge, show-inactive, uninstall vs hard-delete, strip show-orphaned

**Files:**
- Modify: `client/src/pages/Solutions.tsx` (status badge + show-inactive toggle), `client/src/pages/SolutionDetail.tsx` (uninstall vs hard-delete buttons + type-the-name modal), `client/src/pages/Tables.tsx` + `Config.tsx` (strip show-orphaned), `client/src/services/tables.ts` + `hooks/useConfig.ts` (strip includeOrphaned)
- Test: vitest for the modal + status rendering; Playwright happy-path

**Interfaces:** consumes the Task-3 endpoints (uninstall, hard-delete w/ confirm, deletion-summary) + the `status` field.

- [ ] **Step 1: Failing vitest** — SolutionDetail renders an "Uninstall" action when `status==active` and "Delete permanently" when shown; the hard-delete modal lists the deletion-summary counts and disables Confirm until the typed name matches the slug. Solutions list renders a status badge + a "show inactive" toggle that filters.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3:** Implement: (a) Solutions.tsx — `statusBadge()` + a "show inactive" toggle (default hides inactive; the list endpoint filter from Task 3, or client-side filter on `status`). (b) SolutionDetail.tsx — split the existing delete action into "Uninstall" (calls `/uninstall`) and "Delete permanently" (opens the type-the-name modal → calls DELETE with `confirm=<typed>` after fetching `/deletion-summary`); the modal's Confirm is disabled until `typed === slug`. (c) Tables.tsx/Config.tsx — DELETE the `showOrphaned` state + checkbox; tables.ts/useConfig.ts — DELETE the `includeOrphaned` param. Regenerate types first (`npm run generate:types`).
- [ ] **Step 4:** `cd client && npm run tsc && npm run lint` (0 new errors; no `as unknown as`). `./test.sh client unit`. Add a Playwright `*.admin.spec.ts`: install → uninstall (see inactive) → reactivate via reinstall-over → hard-delete with type-the-name. Run if practical; else write + note.
- [ ] **Step 5: Commit** — `feat(solution-lifecycle): status badge, show-inactive, uninstall vs hard-delete modal; strip show-orphaned`

---

## Task 9: Rewrite the capstone e2e for the inactive lifecycle

**Files:**
- Modify: `api/tests/e2e/platform/test_solution_files_e2e.py` (replace the orphan-survival arc)
- Test: itself

**Interface:** the capstone now proves the inactive lifecycle end-to-end with a REAL solution carrying files + a file policy referencing a named rule (Plan 1).

- [ ] **Step 1: Rewrite the arc:**
```
1. Create solution + workflow + table + FILE (solutions/{install}/docs/readme.md) + file POLICY {"$ref":"admin_bypass"}.
2. Deploy (async job → poll succeeded). Allowed user reads the file; denied user 403.
3. UNINSTALL → status==inactive; the file row STILL has solution_id==install (frozen, NOT orphaned); the file is BROWSABLE/exportable but workflow execution against it is REFUSED (dormant).
4. REINSTALL the same bundle WITHOUT reactivate → 409 inactive-exists. WITH reactivate=true → status==active, data intact (same solution_id), file readable + servable again.
5. Export WITH data → file in encrypted tier + $ref preserved (assert against the exported artifact, not just the DB).
6. HARD-DELETE with confirm=slug → Solution row gone, owned rows cascaded (table/file-metadata gone), S3 prefix swept. confirm-mismatch → 4xx, nothing deleted.
7. No cross-solution leakage throughout.
```
- [ ] **Step 2: Run → iterate to green** — `./test.sh tests/e2e/platform/test_solution_files_e2e.py -v` (isolated). No silent `else: pass`; every behavior a real assertion; pytest.fail on any timeout.
- [ ] **Step 3: Commit** — `test(solution-lifecycle): capstone e2e (uninstall/reactivate/hard-delete, no orphan)`

---

## Task 10: Full verification sweep

- [ ] `cd api && pyright && ruff check .` → 0 errors. (Confirm no dangling reference to a deleted orphan symbol — grep `origin_solution_slug|orphaned_at|orphan_solution_files|include_orphaned|_reattach_orphan` across `api/src` returns NOTHING.)
- [ ] `cd client && npm run generate:types && npm run tsc && npm run lint` → PASS (no `showOrphaned`/`includeOrphaned` left; grep client/src returns nothing).
- [ ] `./test.sh all` → green. Parse `/tmp/bifrost-<project>/test-results.xml`. The e2e suite has known pre-existing state-pollution flakes (esp. ~80 in `test_git_sync_local` with a ManifestPolicy signature that passes in isolation) — triage real-vs-flake by isolating any failing file; a real failure is one that fails ISOLATED on a clean reset.
- [ ] `./test.sh client unit` → PASS.
- [ ] DTO/contract/skill-truth: `./test.sh tests/unit/test_dto_flags.py tests/unit/test_contract_version.py tests/unit/test_skill_appendix_fresh.py -v` → green (regenerate + refresh fingerprint as needed).
- [ ] Grep-sweep for dead orphan code (the inventory's 41 files) — confirm each orphan symbol is gone.
- [ ] Commit any fixups.

---

## Notes for the implementer

- **The cascade already exists.** Every `solution_id` FK is `ondelete=CASCADE` already. Hard-delete = drop the Solution row + S3 sweep. Do NOT re-add cascade-avoidance.
- **Uninstall touches ONLY `Solution.status`.** If you find yourself mutating a data row on uninstall, stop — that's the old model.
- **The status gate is ONE place** (the context resolver). Don't scatter 5 guards — load the Solution once and refuse inactive for execution; leave browse/export ungated.
- **Greenfield:** drop the orphan columns; no data reconciliation. Pre-release.
- **Reactivate redeploys non-destructively** atop frozen rows (no mirror-delete) — data the new bundle omits survives.
- **Hard-delete needs a server-side confirm token** (slug match), not just the modal.
- **Plan 1 (named rules) + Plan-2 non-uninstall work are preserved** — only the orphan/uninstall mechanism is replaced. The status gate plugs into the resolvers they built.
