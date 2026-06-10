# Solutions Review Fixes Implementation Plan

> ## ✅ STATUS: COMPLETE (2026-06-10)
>
> All 23 tasks (18 review fixes + T19 verification + Pass 5 versioning/upgrade) are
> implemented, each spec- and quality-reviewed, committed on this branch
> (`a530f41b..` — checkboxes below may be stale; the commit trail is truth).
>
> **Final verification:** backend `pyright` 0 errors, `ruff` clean repo-wide; client
> `tsc`/eslint clean, vitest 1218/1218; full single-session backend run
> **5696 passed / 55 skipped / 1 failed** — the 1 failure is
> `test_backfill_summaries::test_real_run_creates_job_and_flips_runs_to_pending`,
> a pre-existing worker-timing flake in a subsystem this plan never touched
> (file last changed PR #84; the live worker consumes the backfill queue between
> enqueue and the all-pending assertion).
>
> **Verification also fixed four pre-existing branch breaks** the compose flake had
> been hiding (no aggregate run had passed since ~06-05): the git-sync e2e teardown
> FK cascade (81 phantom errors/run + a `FullTable` row that 422'd three table-list
> tests), the vendoring e2e broken by the 06-05 uuid5 remap, `transport.ts` missing
> from the Dockerfile SDK copy list (served SDK + every app build esbuild-failed),
> and deploy's config reattach never invalidating the org config cache.
>
> **Full-suite recipe that works** (compose v5.1.4 recreate flake): see
> `project_test_stack_api_exit_flake` memory — stop api/worker/scheduler **and
> pgbouncer**, terminate backends, DROP/CREATE from template, FLUSHALL, restart,
> then ONE `compose run --no-deps test-runner pytest tests/unit tests/e2e` session.
>
> Codex gate dropped by Jack 2026-06-09; final gate was the in-session full-delta
> review (verdict READY, all notes resolved in follow-up commits).

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix all confirmed findings from the 2026-06-09 full-branch code review of `worktree-solutions-success-criteria` (7 high, ~10 medium/low), in four passes ordered by harm.

**Architecture:** Pass 1 fixes data-loss/stuck-state bugs (git-sync config-schema wipe, uninstall table-detach collision, forms org anchoring). Pass 2 fixes the "names are no longer unique" fallout on endpoints the branch didn't touch (websocket, embed, MCP apps). Pass 3 fixes Windows + local-dev SDK breakage. Pass 4 is hardening + cleanup. Every fix gets a regression test; full verification suite at the end.

**Tech Stack:** FastAPI/SQLAlchemy/Alembic (api), React/TS/vitest (client), Click CLI (api/bifrost), pytest via ./test.sh.

**Review provenance:** Findings were independently verified by adversarial verifier agents (CONFIRMED verdicts with file:line evidence). Where this plan quotes "current code", it was read from the worktree on 2026-06-09.

**Operational notes for implementers:**
- Work in `/home/jack/GitHub/bifrost/.claude/worktrees/solutions-success-criteria` (NEVER the primary checkout).
- `./test.sh` quirk in THIS worktree: the test-stack api container can exit(0) after healthy (`project_test_stack_api_exit_flake`). If `./test.sh` fails on `depends_on:service_healthy`: bring api up in-project, then `docker compose -p bifrost-test-<hash> -f docker-compose.test.yml --profile test run --rm --no-deps test-runner pytest <files>`.
- Client unit tests run on host: `./test.sh client unit` (no stack needed).
- New migrations: restart `bifrost-debug-*-init-1` then `-api-1` if exercising against the debug stack.

---

## Pass 1 — data loss and stuck states

### Task 1: git-connected sync must collect config schemas

**Problem (verified):** `read_workspace_bundle` (api/src/services/solutions/git_sync.py:69-97) builds `SolutionBundle` without `config_schemas`, so the dataclass default `[]` applies. Deploy's `_reconcile_one(SolutionConfigSchema, sid, {ids from bundle.config_schemas})` (deploy.py:1149-1151) then treats every existing declaration as stale and deletes it — every git auto-pull sync wipes the install's config declarations. The zip path (`zip_install.py:_parse_workspace`) and CLI both call `_collect_config_schemas`; git_sync is the only bundle builder that forgot.

**Files:**
- Modify: `api/src/services/solutions/git_sync.py` (read_workspace_bundle)
- Test: `api/tests/unit/test_solution_git_sync.py`

- [ ] **Step 1: Write the failing test**

In `api/tests/unit/test_solution_git_sync.py`, alongside the existing workspace-fixture tests (reuse the existing workspace-builder fixture/helpers in that file — they already write `.bifrost/*.yaml` into a tmp dir):

```python
def test_read_workspace_bundle_collects_config_schemas(tmp_path):
    """A git-connected workspace's declared config schemas must reach the
    bundle — an empty list makes deploy's reconcile sweep DELETE every
    declaration the install owns, on every auto-pull sync."""
    ws = tmp_path / "ws"
    (ws / ".bifrost").mkdir(parents=True)
    (ws / ".bifrost" / "configs.yaml").write_text(
        "configs:\n"
        "  11111111-1111-1111-1111-111111111111:\n"
        "    id: 11111111-1111-1111-1111-111111111111\n"
        "    key: API_KEY\n"
        "    type: secret\n"
        "    required: true\n"
    )
    solution = _make_solution()  # use the same helper the existing tests use
    bundle = read_workspace_bundle(solution, ws)
    assert len(bundle.config_schemas) == 1
    assert bundle.config_schemas[0]["key"] == "API_KEY"
```

Adapt the `configs.yaml` shape to whatever `_collect_config_schemas` in `api/bifrost/commands/solution.py` actually parses — read that function first (it's the same parser the zip path uses) and mirror the fixture format used by `api/tests/unit/test_solution_config_manifest.py`.

- [ ] **Step 2: Run it, verify it fails** — `bundle.config_schemas == []`.

- [ ] **Step 3: Implement** — in `read_workspace_bundle`, extend the existing CLI-helper import and pass the list:

```python
    from bifrost.commands.solution import _collect_apps, _collect_config_schemas

    apps = _collect_apps(workspace)
    return SolutionBundle(
        solution=solution,
        python_files=_collect_python_files(workspace),
        workflows=workflows,
        tables=tables,
        apps=apps,
        forms=forms,
        agents=agents,
        config_schemas=_collect_config_schemas(workspace),
    )
```

- [ ] **Step 4: Run the test file + the existing git-sync tests** — all pass.

- [ ] **Step 5: Commit** — `fix(solutions): git-connected sync collects config schemas (was wiping declarations every sync)`

---

### Task 2: uninstall table detach must not collide with live `_repo/` tables

**Problem (verified):** `delete_solution` detaches tables with `UPDATE tables SET solution_id=NULL, organization_id=<install org>, orphaned_at=now, origin_*=...` (api/src/routers/solutions.py:~329). The `_repo/` namespace unique indexes (`ix_tables_org_name_unique` WHERE `organization_id IS NOT NULL AND solution_id IS NULL`; `ix_tables_global_name_unique` WHERE `organization_id IS NULL AND solution_id IS NULL`, migration `20260606_table_name_solution_scope`) do NOT exclude orphaned rows — so if a same-name live `_repo/` table exists (legal coexistence this branch deliberately enabled), the UPDATE raises IntegrityError and uninstall 500s forever.

**Fix shape:** orphaned rows leave the live unique namespace — add `AND orphaned_at IS NULL` to both `_repo/` indexes. Detach (which always stamps `orphaned_at`) then never collides. Reattach-on-reinstall moves rows into the `(solution_id, name)` namespace, which is unaffected.

**Files:**
- Create: `api/alembic/versions/20260609_orphan_tables_out_of_name_ns.py`
- Modify: `api/src/models/orm/tables.py` (`__table_args__` mirrors the migration)
- Test: `api/tests/e2e/platform/test_solution_delete.py` (new test) and check `api/tests/unit/test_orphan_provenance_columns.py`

- [ ] **Step 1: Write the failing e2e test** in `test_solution_delete.py` (reuse that file's existing install/deploy fixtures):

```python
async def test_uninstall_with_same_name_repo_table_succeeds(...):
    """Coexistence of a _repo/ table and a same-name solution table is legal;
    uninstall must therefore detach without violating the _repo/ name index."""
    # 1. create an ordinary org table named "users" (POST /api/tables)
    # 2. install/deploy a solution bundle shipping a table named "users"
    # 3. DELETE /api/solutions/{id}  -> expect 200, summary.tables_detached == 1
    # 4. GET /api/tables?include_orphaned=true -> exactly two "users" rows:
    #    one live (orphaned_at None), one orphaned with origin_solution_slug set
```

Write it concretely against the endpoints the existing tests in that file already use (they cover deploy→delete round-trips; copy the closest one and add the pre-existing table).

- [ ] **Step 2: Run it — must fail with a 500 (IntegrityError) on the DELETE.**

- [ ] **Step 3: Write the migration:**

```python
"""orphaned tables leave the live _repo/ name namespace

Detach-on-uninstall sets solution_id=NULL + orphaned_at, which previously
moved the row INTO the _repo/ unique name namespace — colliding with a live
same-name table (legal coexistence) and 500ing the uninstall. Orphaned rows
are a shadow namespace: exclude them from the live-name indexes.

Revision ID: 20260609_orphan_tbl_ns
Revises: <current head — run `cd api && alembic heads` and use it>
"""
from alembic import op
import sqlalchemy as sa

revision = "20260609_orphan_tbl_ns"
down_revision = "<current head>"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ix_tables_org_name_unique", table_name="tables")
    op.drop_index("ix_tables_global_name_unique", table_name="tables")
    op.create_index(
        "ix_tables_org_name_unique",
        "tables",
        ["organization_id", "name"],
        unique=True,
        postgresql_where=sa.text(
            "organization_id IS NOT NULL AND solution_id IS NULL AND orphaned_at IS NULL"
        ),
    )
    op.create_index(
        "ix_tables_global_name_unique",
        "tables",
        ["name"],
        unique=True,
        postgresql_where=sa.text(
            "organization_id IS NULL AND solution_id IS NULL AND orphaned_at IS NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index("ix_tables_org_name_unique", table_name="tables")
    op.drop_index("ix_tables_global_name_unique", table_name="tables")
    op.create_index(
        "ix_tables_org_name_unique",
        "tables",
        ["organization_id", "name"],
        unique=True,
        postgresql_where=sa.text("organization_id IS NOT NULL AND solution_id IS NULL"),
    )
    op.create_index(
        "ix_tables_global_name_unique",
        "tables",
        ["name"],
        unique=True,
        postgresql_where=sa.text("organization_id IS NULL AND solution_id IS NULL"),
    )
```

- [ ] **Step 4: Mirror the predicates in the ORM** — update the matching `Index(..., postgresql_where=...)` entries in `api/src/models/orm/tables.py` `__table_args__` to include `orphaned_at IS NULL` (metadata-create test envs must match alembic-applied DBs).

- [ ] **Step 5: Guard the un-orphan paths.** `rg -n "orphaned_at\s*=\s*None|orphaned_at=None" api/src` — every code path that CLEARS `orphaned_at` on a Table re-enters the live namespace and needs a collision check. Expected hits: the reattach path in `deploy.py` `_upsert_tables` (safe — it simultaneously sets `solution_id`, entering the per-install namespace) and any restore/adopt endpoint in `api/src/routers/tables.py`. For any path that clears `orphaned_at` WITHOUT setting `solution_id`, add before the write:

```python
        dup = await db.scalar(
            select(Table.id).where(
                Table.name == row.name,
                org_pred,  # same exact-org predicate the surrounding code uses
                Table.solution_id.is_(None),
                Table.orphaned_at.is_(None),
                Table.id != row.id,
            )
        )
        if dup is not None:
            raise HTTPException(
                status_code=409,
                detail=f"A live table named '{row.name}' already exists in this scope; rename it before restoring the orphan.",
            )
```

If no such restore path exists, note that in the commit message and move on.

- [ ] **Step 6: Run the new e2e test + `api/tests/e2e/platform/test_solution_reattach.py` + `test_tables_include_orphaned.py`** — all pass.

- [ ] **Step 7: Commit** — `fix(solutions): orphaned tables leave the live name namespace; uninstall no longer 500s on coexisting _repo table`

---

### Task 3: form execution resolves and runs in the FORM's org, not the caller's

**Problem (verified):** `api/src/routers/forms.py:817`:

```python
    _wf_repo = WorkflowRepository(db, org_id=ctx.org_id, is_superuser=True)
    _resolved_wf = await _wf_repo.resolve(form.workflow_id, solution_scope=form.solution_id)
```

The cascade is anchored to the CALLER's org. A platform-admin/provider user (home org A) executing org B's solution form gets candidate rows filtered to (A OR global) — the install's org-B workflow is excluded before the `solution_scope` disambiguation runs, so the form silently resolves a global `_repo/` workflow at the same path, or 404s. The execution row is also stamped `organization_id=ctx.org_id` (wrong data world for configs/tables at runtime).

**Decision (blessed in review):** resolution scope comes from the ANCHOR entity. Anchor org = `form.organization_id` when set, else `ctx.org_id`. The same anchor org is used for the execution's `organization_id` so the workflow runs in the form's data world. (For non-cross-org callers this is a no-op: `form.organization_id == ctx.org_id` or the form is global.)

**Files:**
- Modify: `api/src/routers/forms.py` (execute path: repo construction + both `organization_id=` stamps — scheduled insert and the run-workflow call)
- Test: `api/tests/e2e/platform/test_form_execute_solution_scope_e2e.py` (extend — this file already covers solution-form resolution for same-org callers)

- [ ] **Step 1: Write the failing test** (extend the existing file's fixtures — it already deploys a solution form bound to a `path::fn` workflow):

```python
async def test_admin_cross_org_form_execute_resolves_install_workflow(...):
    """Platform admin (home org A) executes org B's solution form: must run
    the install's OWN workflow and record the execution under org B."""
    # 1. deploy solution S into org B shipping workflows/main.py::run + form F
    #    (existing fixture does this)
    # 2. ALSO create a global _repo/ workflow at the same path::fn — the decoy
    #    the buggy cascade falls back to
    # 3. as platform admin whose home org is A (not B): POST /api/forms/{F}/execute
    # 4. assert the execution's workflow_id == the install's workflow UUID
    #    (NOT the decoy's) and execution.organization_id == org B
```

- [ ] **Step 2: Run it — fails (resolves the decoy or 404s).**

- [ ] **Step 3: Implement.** In the execute handler, immediately after the access check:

```python
    # Resolution and execution are anchored to the FORM's world, not the
    # caller's: a cross-org bypass caller (platform admin / provider) executing
    # an org-scoped form must resolve the install's own workflow and run in the
    # form's org. Caller identity was already used for AUTHORIZATION above.
    anchor_org_id = form.organization_id if form.organization_id is not None else ctx.org_id

    _wf_repo = WorkflowRepository(db, org_id=anchor_org_id, is_superuser=True)
    _resolved_wf = await _wf_repo.resolve(form.workflow_id, solution_scope=form.solution_id)
```

Then replace `organization_id=ctx.org_id` with `organization_id=anchor_org_id` in BOTH execution-creation sites in this handler (the `_insert_scheduled_execution(...)` call and the immediate-run path below it). Do not touch other uses of `ctx.org_id` in the file.

- [ ] **Step 4: Run the new test + the whole `test_form_execute_solution_scope_e2e.py` file** — all pass.

- [ ] **Step 5: Commit** — `fix(forms): anchor workflow resolution and execution org to the form, not the caller`

---

## Pass 2 — non-unique-name fallout on untouched endpoints

### Task 4: websocket table-name resolution must apply the canonical filters

**Problem (verified):** `api/src/routers/websocket.py` `_resolve_table_id` name branch (~line 88) selects by bare name with only an org filter — no `solution_id IS NULL`, no `orphaned_at IS NULL` — then calls `result.one_or_none()`. With a legal `_repo/` + solution same-name pair, `MultipleResultsFound` propagates to the connection-level handler and kills the ENTIRE websocket (all channels), including on reconnect.

**Files:**
- Modify: `api/src/routers/websocket.py` (`_resolve_table_id` name branch; check `_load_policies_for_table`'s name branch too and apply the same filters)
- Test: `api/tests/unit/` — new file `test_websocket_table_resolution.py` (or extend an existing websocket unit test file if one exists — `rg -l "_resolve_table_id" api/tests`)

- [ ] **Step 1: Write the failing test** — seed (via the unit-test session fixture) two Table rows in one org with the same name: one live `_repo/` row, one `solution_id`-bearing row. Call `_resolve_table_id(name, user)` for a non-superuser of that org. Assert it returns the `_repo/` row's id (and does not raise).

- [ ] **Step 2: Run it — fails with MultipleResultsFound.**

- [ ] **Step 3: Implement** — in the name branch:

```python
            stmt = select(TableOrm.id, TableOrm.organization_id).where(
                TableOrm.name == name_or_id,
                # By-name resolution is the _repo/ live namespace — mirror
                # OrgScopedRepository.get(): solution-managed rows resolve by
                # id (or ?solution=), orphaned rows don't resolve at all.
                TableOrm.solution_id.is_(None),
                TableOrm.orphaned_at.is_(None),
            )
```

Apply the identical two conditions to the name branch of `_load_policies_for_table` (~line 169).

- [ ] **Step 4: Run the test — passes. Also run existing websocket/table e2e tests** (`rg -l "subscribe" api/tests/e2e | head`).

- [ ] **Step 5: Commit** — `fix(ws): table-name subscribe applies solution/orphan filters (MultipleResultsFound killed the socket)`

---

### Task 5: public embed endpoint disambiguates multi-install slugs by HMAC

**Problem (verified):** `api/src/routers/embed.py:44-49` resolves Application by bare slug with `scalar_one_or_none()`. Slug uniqueness is now per-install, so the same solution installed for two orgs → 2 rows → `MultipleResultsFound` → 500 on a PUBLIC endpoint, before the HMAC check.

**Fix shape:** the embed secret is bound to a specific Application row — fetch ALL rows for the slug and let HMAC verification pick the app. Cryptographically correct disambiguation, no heuristics.

**Files:**
- Modify: `api/src/routers/embed.py` (embed_app)
- Test: `api/tests/e2e/platform/` — extend the existing embed test file (`rg -l "embed" api/tests/e2e --files-with-matches | head`), or `api/tests/unit` if embed coverage is unit-level.

- [ ] **Step 1: Write the failing test** — create two Application rows sharing a slug (one per fake install: set `solution_id` to two different UUIDs, orgs A and B — insert directly via the session fixture since deploy plumbing isn't needed), give org B's row an embed secret, sign params with it, GET `/embed/apps/{slug}?...&hmac=...`. Assert 302 redirect and that the issued token's `app_id` is org B's row (decode the JWT from the fragment).

- [ ] **Step 2: Run it — fails (MultipleResultsFound).**

- [ ] **Step 3: Implement** — replace the single-row lookup + verify loop:

```python
    async with get_db_context() as db:
        result = await db.execute(
            select(Application)
            .where(Application.slug == slug)
            .options(selectinload(Application.embed_secrets))
        )
        candidates = list(result.scalars().all())

    if not candidates:
        raise HTTPException(status_code=404, detail="Application not found")

    # A slug may match multiple installs of the same solution (slug uniqueness
    # is per-install). The embed secret is bound to ONE Application row, so the
    # HMAC itself disambiguates: the row whose active secret verifies wins.
    app = None
    for candidate in candidates:
        for secret_record in (s for s in candidate.embed_secrets if s.is_active):
            raw_secret = decrypt_secret(secret_record.secret_encrypted)
            if verify_embed_hmac(query_params, raw_secret, secret_record.hmac_scheme):
                app = candidate
                break
        if app is not None:
            break

    if app is None:
        if not any(s.is_active for c in candidates for s in c.embed_secrets):
            raise HTTPException(status_code=403, detail="No embed secrets configured")
        raise HTTPException(status_code=403, detail="Invalid HMAC signature")
```

The rest of the handler (`token_data`, redirect) is unchanged and now uses the verified `app`.

- [ ] **Step 4: Run the embed tests — pass.**

- [ ] **Step 5: Commit** — `fix(embed): multi-install slugs disambiguate by HMAC instead of 500ing`

---

### Task 6: MCP app tools use the disambiguating slug lookup

**Problem (verified):** `api/src/services/mcp_server/tools/apps.py` has three bare-slug `scalar_one_or_none()` lookups (~lines 143, 256, 1023). Platform-admin calls error with MultipleResultsFound for multi-install slugs; the create-app duplicate check falsely blocks a `_repo/` app when only a solution-managed row holds the slug.

**Files:**
- Modify: `api/src/services/mcp_server/tools/apps.py`
- Test: `api/tests/unit/test_mcp_solution_managed.py` (extend — it already fixtures MCP tool contexts with solution-managed apps)

- [ ] **Step 1: Write the failing tests** (two):

```python
async def test_mcp_get_app_multi_install_slug_does_not_error(...):
    # two Application rows, same slug, different solution_id/org; platform-admin
    # context; get_app(slug=...) must return one row (prefer caller-org, then
    # global — same semantics as ApplicationRepository.get_by_slug_global),
    # not an error_result containing "Multiple rows".

async def test_mcp_create_app_allows_repo_slug_shadowing_solution(...):
    # one SOLUTION-managed row with slug "dashboard" (different org); create_app
    # (slug="dashboard") for a _repo app in the caller's org must NOT be
    # rejected as duplicate — the partial unique index permits it. Duplicate
    # check must only consider rows in the namespace the new row will occupy
    # (solution_id IS NULL, caller's org-or-global).
```

- [ ] **Step 2: Run — both fail.**

- [ ] **Step 3: Implement.** For the two read sites (get_app ~256, get_app_dependencies ~1023): replace the bare select with `ApplicationRepository(db, org_id=context.org_id, ...).get_by_slug_global(slug)` (the branch already built this disambiguation — reuse it; check the constructor signature in `api/src/repositories/applications.py` for the right arguments, and keep the existing org filter for non-admin contexts). For the create-app duplicate check (~143): scope the existence check to the namespace the new row occupies:

```python
            existing = await db.execute(
                select(Application.id).where(
                    Application.slug == slug,
                    Application.solution_id.is_(None),
                    (Application.organization_id == context.org_id)
                    | (Application.organization_id.is_(None)),
                )
            )
```

- [ ] **Step 4: Run the test file — passes. Run `api/tests/unit/test_mcp_thin_wrapper.py`** — if it flags the repository import, route through the REST endpoint instead per the thin-wrapper rule (read `_http_bridge.py` pattern); these are pre-existing thick tools, so matching the file's current style is acceptable if the lint test allows it.

- [ ] **Step 5: Commit** — `fix(mcp): app slug lookups survive multi-install slugs; create-app duplicate check is namespace-scoped`

---

## Pass 3 — Windows and local-dev

### Task 7: `bifrost solution start` must work on Windows (npm.cmd)

**Problem (verified):** `api/bifrost/commands/solution.py:890` checks `shutil.which("npm") is None` then discards the result; `:894 subprocess.run(["npm", "install"], ...)` and `:908 subprocess.Popen(["npm", "run", "dev", ...])` pass the literal `"npm"`, which CreateProcess can't resolve on Windows (npm is `npm.cmd`). FileNotFoundError traceback; feature unusable on Windows.

**Files:**
- Modify: `api/bifrost/commands/solution.py`
- Test: `api/tests/unit/test_solution_dev_command.py` (extend)

- [ ] **Step 1: Write the failing test:**

```python
def test_npm_invocations_use_which_result(monkeypatch):
    """Windows: 'npm' is npm.cmd — CreateProcess can't resolve the bare name.
    Every spawn must use the shutil.which() result as argv[0]."""
    calls = []
    monkeypatch.setattr(shutil, "which", lambda name: r"C:\nodejs\npm.cmd" if name == "npm" else None)
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: calls.append(argv) or types.SimpleNamespace(returncode=0))
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **kw: calls.append(argv) or _FakeProc())
    # invoke the npm-install + dev-server spawn helpers (extract if needed —
    # see Step 3) and assert:
    assert all(c[0] == r"C:\nodejs\npm.cmd" for c in calls)
```

Adapt to how the existing tests in that file invoke the command internals (they already fixture a workspace + monkeypatched subprocess; follow their pattern).

- [ ] **Step 2: Run — fails (`argv[0] == "npm"`).**

- [ ] **Step 3: Implement:**

```python
    npm = shutil.which("npm")
    if npm is None:
        raise click.ClickException(
            "npm not found on PATH. Install Node.js (https://nodejs.org) and retry."
        )
    ...
    subprocess.run([npm, "install"], cwd=chosen.app_dir, check=True)
    ...
    proc = subprocess.Popen([npm, "run", "dev", ...], ...)
```

(Keep the existing flags/kwargs; only argv[0] changes. If install/spawn happen in different scopes, resolve `npm` once near the existing which() check and thread it through.)

- [ ] **Step 4: Run the test file — passes.**

- [ ] **Step 5: Commit** — `fix(cli): solution start resolves npm via shutil.which (Windows npm.cmd)`

---

### Task 8: reload watcher skip-dirs must work on Windows paths

**Problem (verified):** `api/bifrost/solution_dev/reload.py:23` — `any(f"/{d}/" in path for d in _SKIP_DIRS)` never matches Windows backslash paths, so `.venv`/`node_modules` events trigger reload storms. `function_host.py:41` already does it right (`any(part in _SKIP_DIRS for part in rel_parts)`). reload.py's skip set is also missing `.bifrost` (function_host has it).

**Files:**
- Modify: `api/bifrost/solution_dev/reload.py`
- Test: `api/tests/unit/test_solution_dev_reload.py` (extend)

- [ ] **Step 1: Write the failing test:**

```python
def test_skip_dirs_match_windows_separators():
    handler = _make_handler()  # follow the file's existing construction pattern
    event = types.SimpleNamespace(src_path=r"C:\ws\.venv\Lib\site-packages\x.py", is_directory=False)
    handler.on_any_event(event)
    assert handler.reload_count == 0  # or assert the host.reload mock NOT called

def test_skip_dirs_include_dot_bifrost():
    event = types.SimpleNamespace(src_path="/ws/.bifrost/state.py", is_directory=False)
    handler.on_any_event(event)
    assert handler.reload_count == 0
```

- [ ] **Step 2: Run — first fails (reload fired).**

- [ ] **Step 3: Implement** — replace the substring check with the parts-based check `function_host.py` uses:

```python
import pathlib

_SKIP_DIRS = {"node_modules", "dist", ".venv", "venv", "__pycache__", ".git", ".bifrost"}
...
        parts = pathlib.PurePath(path).parts
        if any(part in _SKIP_DIRS for part in parts):
            return
```

- [ ] **Step 4: Run the test file — passes.**

- [ ] **Step 5: Commit** — `fix(cli): solution-dev reload skip-dirs are separator-agnostic and include .bifrost`

---

### Task 9: `scaffold-app --path` writes the manifest at the real solution root, POSIX-relative

**Problem (verified):** `api/bifrost/commands/solution.py:111` — with `--path`, the solution root is GUESSED as `app_dir.parent.parent` (wrong for nested paths: manifests land outside the root and deploy never sees them) and `:144` stores `rel_path = str(app_dir)` (cwd-relative, backslashes on Windows) which the POSIX comparisons in `_app_source_dirs` (~:449) never match → app .py files double-collect as workflow source.

**Fix shape:** the solution root is where the descriptor lives — find it by walking up from cwd (`is_solution_workspace` from `bifrost.solution_descriptor` already answers "is this a root"), never by guessing from the app path; store `rel_path = app_dir.resolve().relative_to(root.resolve()).as_posix()` and refuse `--path` targets outside the root.

**Files:**
- Modify: `api/bifrost/commands/solution.py` (scaffold-app)
- Test: `api/tests/unit/test_solution_scaffold_app.py` (extend)

- [ ] **Step 1: Write the failing tests:**

```python
def test_scaffold_app_nested_path_manifest_at_root(tmp_path, runner):
    # init a solution workspace at tmp_path (use the existing init helper),
    # cwd=tmp_path, run: scaffold-app dash --path src/apps/dash
    # assert (tmp_path / ".bifrost" / "apps.yaml").exists()
    # assert NOT (tmp_path / "src" / ".bifrost").exists()
    # assert the manifest entry's path == "src/apps/dash"  (POSIX, root-relative)

def test_scaffold_app_path_outside_root_refused(tmp_path, runner):
    # scaffold-app dash --path ../elsewhere/dash -> ClickException, no files written
```

- [ ] **Step 2: Run — first fails (manifest written under src/).**

- [ ] **Step 3: Implement:**

```python
    root = pathlib.Path.cwd()
    while not is_solution_workspace(root):
        if root.parent == root:
            raise click.ClickException(
                "Not inside a solution workspace (no solution descriptor found). "
                "Run this from your solution root (created by `bifrost solution init`)."
            )
        root = root.parent

    app_dir = (pathlib.Path(path) if path else root / "apps" / app_name).resolve()
    try:
        rel_path = app_dir.relative_to(root.resolve()).as_posix()
    except ValueError:
        raise click.ClickException(
            f"--path must point inside the solution workspace ({root})"
        )
    bifrost_dir = root  # manifests ALWAYS live at the root
```

Adapt names to the function's existing locals; keep the no-`--path` default behavior byte-identical (`apps/<name>` at the root).

- [ ] **Step 4: Run the scaffold test file + `test_solution_scaffold_dev_wiring.py` — pass.**

- [ ] **Step 5: Commit** — `fix(cli): scaffold-app --path anchors manifests at the descriptor root, stores POSIX root-relative paths`

---

### Task 10: SDK transport must be installed before children mount

**Problem (verified):** `client/src/lib/app-sdk/provider.tsx:111` installs the module-global transport in the provider's `useEffect`, but React runs child effects first and `tables.ts` reads the transport synchronously — first-mount `useTable`/`tables.*` calls go out with no baseUrl/token/X-Bifrost-App and nothing retries when the transport lands (transport is not in `use-table`'s deps).

**Fix shape:** install the transport synchronously during render (idempotent module-global assignment keyed on the config values), keep the unmount-restore in the effect. Synchronous render-time assignment of a module global is safe here because it's idempotent and last-writer-wins is the correct semantic for nested providers.

**Files:**
- Modify: `client/src/lib/app-sdk/provider.tsx`
- Test: `client/src/lib/app-sdk/provider.test.tsx` (extend)

- [ ] **Step 1: Write the failing test:**

```tsx
it("installs the transport before child effects run", () => {
  const seen: Array<string | undefined> = [];
  function Probe() {
    React.useEffect(() => {
      seen.push(getBifrostTransport().baseUrl); // export a getter if one doesn't exist
    }, []);
    return null;
  }
  render(
    <BifrostProvider baseUrl="https://remote.example" token="t">
      <Probe />
    </BifrostProvider>
  );
  expect(seen[0]).toBe("https://remote.example");
});
```

(If `tables.ts` has no transport getter, export `getBifrostTransport()` — trivial and useful for tests.)

- [ ] **Step 2: Run `./test.sh client unit -- provider` — fails (undefined/"").**

- [ ] **Step 3: Implement** — in BifrostProvider's render body, before returning JSX:

```tsx
  // Install synchronously: child mount effects (useTable's first query) run
  // BEFORE this component's own useEffect, so an effect-time install loses the
  // race on first paint. Render-time assignment is idempotent.
  const installedRef = React.useRef<string | null>(null);
  const installKey = `${baseUrl}|${token ?? ""}|${orgScope ?? ""}`;
  if (installedRef.current !== installKey) {
    installedRef.current = installKey;
    setBifrostTransport({ baseUrl, token, ... });   // same object the effect currently builds
    setDefaultAppScope(orgScope);
  }
```

Keep the existing `useEffect` ONLY for the unmount restore (return the `restore`/`restoreScope` cleanups; delete the install half). Make sure the effect's install no longer runs (or is a no-op re-install with the same key) so behavior is single-sourced.

- [ ] **Step 4: Run the provider + use-table + tables vitest files — pass.**

- [ ] **Step 5: Commit** — `fix(sdk): install transport during provider render — child mount effects fired before the effect-time install`

---

### Task 11: table live-subscribe goes through the transport with token auth

**Problem (verified):** `client/src/lib/app-sdk/ws-client.ts:23` — `new URL("/ws/connect", window.location.href)` + no token: in npm-dev / `solution start` modes the subscribe socket hits the wrong origin or closes 4001 unauthenticated, with no error surfaced — tables load once and silently never update. Server auth accepts a `token` query param (api/src/core/auth.py:436-448).

**Files:**
- Modify: `client/src/lib/app-sdk/ws-client.ts`, `client/src/lib/app-sdk/tables.ts` (pass transport into the ws URL builder)
- Test: `client/src/lib/app-sdk/tables.test.ts` / new `ws-client.test.ts`

- [ ] **Step 1: Write the failing test:**

```ts
it("builds the ws URL from the transport baseUrl with token auth", () => {
  setBifrostTransport({ baseUrl: "https://remote.example", token: "tok" });
  const url = buildWsUrl(); // extract URL construction into a pure function
  expect(url).toBe("wss://remote.example/ws/connect?token=tok");
});

it("falls back to window origin with no token when transport is default", () => {
  // restore default transport; expect ws(s)://<window origin>/ws/connect (no token param)
});
```

- [ ] **Step 2: Run — fails (buildWsUrl doesn't exist / wrong URL).**

- [ ] **Step 3: Implement** — extract and export `buildWsUrl()` in ws-client.ts:

```ts
export function buildWsUrl(): string {
  const t = getBifrostTransport();
  const base = t.baseUrl ? new URL(t.baseUrl) : new URL(window.location.href);
  const proto = base.protocol === "https:" ? "wss:" : "ws:";
  const url = new URL("/ws/connect", `${proto}//${base.host}`);
  if (t.token) url.searchParams.set("token", t.token);
  return url.toString();
}
```

Use it at the connect site. Also add `onerror`/`onclose` logging (`console.warn("[bifrost-sdk] table subscription closed", ev.code)`) so the silent-death mode is at least observable.

- [ ] **Step 4: Run the SDK vitest files — pass.**

- [ ] **Step 5: Commit** — `fix(sdk): live table subscribe respects transport baseUrl + token (was silently dead off-origin)`

---

### Task 12: `useWorkflow.run()` honors status, sequences overlapping runs

**Problem (verified):** `client/src/lib/app-sdk/use-workflow.ts:50-86` — (a) only `body.error` is checked; `{status:"failed", error:null}` resolves as success with `data=null`; (b) no sequencing: a slow run A overwrites a newer run B's data, and `loading` flips false while a run is still in flight.

**Files:**
- Modify: `client/src/lib/app-sdk/use-workflow.ts`
- Test: `client/src/lib/app-sdk/use-workflow.test.tsx` (extend)

- [ ] **Step 1: Write the failing tests:**

```tsx
it("treats status='failed' with empty error as a failure", async () => {
  mockFetchResolved({ status: "failed", error: null, result: null });
  // run(); expect error state set, data unchanged, promise rejected
});

it("a stale slow run never overwrites a newer run's result", async () => {
  // start run A (slow), then run B (fast). Resolve B, then A.
  // expect data === B's result and loading === false only after BOTH settle.
});
```

- [ ] **Step 2: Run — both fail.**

- [ ] **Step 3: Implement:**

```ts
  const seqRef = useRef(0);
  const run = useCallback(async (params?: ...) => {
    const seq = ++seqRef.current;
    setLoading(true);
    setError(null);
    try {
      const body = await executeWorkflow(...);
      if (body.error || body.status === "failed") {
        throw new Error(body.error ?? `Workflow failed (status: ${body.status})`);
      }
      if (seq === seqRef.current) setData(body.result);
      return body.result;
    } catch (e) {
      if (seq === seqRef.current) setError(e instanceof Error ? e : new Error(String(e)));
      throw e;
    } finally {
      if (seq === seqRef.current) setLoading(false);
    }
  }, [...]);
```

- [ ] **Step 4: Run the use-workflow vitest file — passes.**

- [ ] **Step 5: Commit** — `fix(sdk): useWorkflow honors failed status and sequences overlapping runs`

---

### Task 13: solution-start proxy: echo websocket subprotocols, cancel pumps on half-close

**Problem (verified):** `api/bifrost/solution_dev/proxy.py:102-125` — (a) `web.WebSocketResponse()` never echoes `Sec-WebSocket-Protocol`, so browsers kill any subprotocol'd socket (Vite HMR's `vite-hmr` only survives via Vite's direct-connect fallback); upstream `ws_connect` also drops the requested protocols and cookies. (b) `asyncio.gather(c2s(), s2c())` never returns on half-close (the common page-reload case) — handler + per-connection ClientSession + upstream socket leak per reload.

**Files:**
- Modify: `api/bifrost/solution_dev/proxy.py`
- Test: `api/tests/unit/test_solution_dev_proxy.py` (extend — it already starts the aiohttp app with a fake upstream)

- [ ] **Step 1: Write the failing tests:**

```python
async def test_ws_proxy_echoes_subprotocol(...):
    # connect to the proxy with protocols=("vite-hmr",) against a fake upstream
    # that accepts it; assert the client handshake response selected "vite-hmr".

async def test_ws_proxy_closes_upstream_when_client_disconnects(...):
    # open proxied ws, close the CLIENT side, assert the fake upstream's
    # connection closes within a timeout (no leaked half-open pump).
```

- [ ] **Step 2: Run — both fail.**

- [ ] **Step 3: Implement:**

```python
    requested = [
        p.strip()
        for p in request.headers.get("Sec-WebSocket-Protocol", "").split(",")
        if p.strip()
    ]
    ws_server = web.WebSocketResponse(protocols=tuple(requested))
    ...
    ws_client = await session.ws_connect(
        target_ws_url,
        protocols=tuple(requested),
        headers={k: v for k, v in request.headers.items() if k.lower() == "cookie"},
    )
    ...
    done, pending = await asyncio.wait(
        [asyncio.ensure_future(c2s()), asyncio.ensure_future(s2c())],
        return_when=asyncio.FIRST_COMPLETED,
    )
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    await ws_client.close()
    await ws_server.close()
```

- [ ] **Step 4: Run the proxy test file — passes.**

- [ ] **Step 5: Commit** — `fix(cli): ws proxy echoes subprotocols and tears down on half-close (HMR + leak)`

---

## Pass 4 — hardening and cleanup

### Task 14: ambiguous unscoped path-ref resolution fails loudly instead of guessing

**Problem (verified):** `api/src/repositories/workflows.py:151` — unscoped fallback `return repo_rows[0] if repo_rows else rows[0]`: with no `_repo/` row and 2+ visible solution rows, the pick is unordered-result nondeterministic (wrong install's workflow, varying run to run). MCP execute (`tools/workflow.py:65`) passes no scope and hits this.

**Files:**
- Modify: `api/src/repositories/workflows.py` (`_resolve_by_path_ref` unscoped branch)
- Test: `api/tests/unit/repositories/test_workflow_repository.py` (extend)

- [ ] **Step 1: Write the failing test** — seed two same-org solution workflows sharing `path::fn`, no `_repo/` row; `resolve(ref)` with no solution_scope must return None (ambiguous → loud 404 at the caller), and with exactly ONE solution row must still return that row.

- [ ] **Step 2: Run — fails (returns an arbitrary row).**

- [ ] **Step 3: Implement:**

```python
        repo_rows = [w for w in rows if w.solution_id is None]
        if repo_rows:
            return repo_rows[0]
        if len(rows) == 1:
            return rows[0]
        # 2+ solution rows, no _repo/ row, no caller scope: never guess which
        # install executes — the callers 404 with the ref in the detail.
        logger.warning(
            "Ambiguous unscoped path-ref %s::%s matches %d solution workflows; refusing",
            path, function_name, len(rows),
        )
        return None
```

- [ ] **Step 4: Run the repository test file + `test_workflow_pathref_solution_scope.py` — pass.**

- [ ] **Step 5: Commit** — `fix(workflows): refuse ambiguous unscoped path-ref resolution instead of picking arbitrarily`

---

### Task 15: `create_application` takes the same slug advisory lock as deploy

**Problem (verified):** `api/src/repositories/applications.py:160-164` SELECT-then-INSERT without the `pg_advisory_xact_lock(hashtext('bifrost:appslug:' || slug))` deploy takes (deploy.py:750-752) → `apps create X` racing `solution deploy` (shipping app X, same org) lands two rows; subsequent opens 500 durably.

**Files:**
- Modify: `api/src/repositories/applications.py` (create_application)
- Test: `api/tests/unit/` — extend the applications repository test file (`rg -l "create_application" api/tests/unit`)

- [ ] **Step 1: Write the failing test** — assert the lock is taken: monkeypatch/spy `session.execute` and assert a `pg_advisory_xact_lock` statement with the slug hash runs before the existence SELECT (mirror however deploy's lock is unit-tested — `rg -n "advisory" api/tests`). If deploy's lock has no unit test, a simple "first statement executed is the advisory lock" spy test is enough.

- [ ] **Step 2: Run — fails.**

- [ ] **Step 3: Implement** — first line of `create_application`:

```python
        # Serialize against solution deploys of the same slug (deploy.py takes
        # the same lock): both sides SELECT-then-INSERT into disjoint partial
        # unique indexes, so without the lock a racing pair lands two rows and
        # every subsequent open 500s with MultipleResultsFound.
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext('bifrost:appslug:' || :s))"),
            {"s": data.slug},
        )
```

- [ ] **Step 4: Run the test — passes.**

- [ ] **Step 5: Commit** — `fix(apps): create_application serializes on the deploy slug lock`

---

### Task 16: solution app builds get timeouts

**Problem (verified):** `api/src/services/solutions/app_build.py:153-164` — `npm install` / `npx vite build` run with `check=True, capture_output=True` and NO timeout, inside the self-renewing per-install write lock (`write_lock.py:95-104` renews forever). A hanging postinstall wedges the install until process restart.

**Files:**
- Modify: `api/src/services/solutions/app_build.py`
- Test: `api/tests/unit/test_solution_app_build.py` (extend)

- [ ] **Step 1: Write the failing test** — monkeypatch `subprocess.run` to assert `timeout` is passed (`assert kwargs.get("timeout") == 600`), and that `subprocess.TimeoutExpired` is translated to the build-failure exception type the file already raises for nonzero exits (read the existing error path — `SolutionDeployError` or similar — and assert that type).

- [ ] **Step 2: Run — fails (no timeout kwarg).**

- [ ] **Step 3: Implement** — add `timeout=600` to both `subprocess.run` calls and wrap:

```python
        try:
            subprocess.run([...], ..., timeout=600)
        except subprocess.TimeoutExpired as exc:
            raise <existing build-error type>(
                f"npm/vite step timed out after 600s for app {app_slug}; "
                "a dependency's install script may be hanging"
            ) from exc
```

- [ ] **Step 4: Run the app-build test file — passes.**

- [ ] **Step 5: Commit** — `fix(solutions): app build subprocesses time out instead of wedging the install lock`

---

### Task 17: cache the SDK tarball per version

**Problem (verified):** `api/src/routers/cli.py:2587-2598` rebuilds the tarball (tempdir + node esbuild subprocess + tar.gz) per request; `app_build.py:124` per app per deploy. Output depends only on version + in-image source.

**Files:**
- Modify: `api/src/services/sdk_package/__init__.py`
- Test: `api/tests/unit/test_sdk_package.py` (extend)

- [ ] **Step 1: Write the failing test:**

```python
def test_build_sdk_tarball_cached_per_version(monkeypatch):
    calls = []
    monkeypatch.setattr(sdk_package, "_bundle", lambda *a, **k: calls.append(1) or ORIGINAL_RESULT_STUB)
    build_sdk_tarball.cache_clear()
    a = build_sdk_tarball("1.0.0"); b = build_sdk_tarball("1.0.0")
    assert a == b and len(calls) == 1
```

(Adapt the monkeypatch target to the internal the existing tests already stub.)

- [ ] **Step 2: Run — fails (2 calls).**

- [ ] **Step 3: Implement** — `@functools.lru_cache(maxsize=2)` on `build_sdk_tarball(version: str) -> bytes` (pure function of version; returns bytes so caching is safe). maxsize=2 covers a rolling upgrade window.

- [ ] **Step 4: Run the sdk_package test file — passes.**

- [ ] **Step 5: Commit** — `perf(sdk): cache the built SDK tarball per version (was esbuild-per-request)`

---

### Task 18: cleanup batch (each its own commit)

**Files/problems (all verified):**

- [ ] **18a — duplicate solutions unique indexes.** `20260604_add_solutions.py` creates `uq_solutions_slug_org`/`uq_solutions_slug_global`; `20260605_solution_unique_per_scope.py` creates the semantically identical `ix_solutions_slug_org_unique`/`ix_solutions_slug_global_unique`; ORM declares only the `ix_` pair. New migration: `DROP INDEX IF EXISTS uq_solutions_slug_org, uq_solutions_slug_global` (IF EXISTS because already-migrated DBs may have only the ix_ pair — that drift is the bug). Downgrade recreates them. Commit: `fix(db): drop duplicate solutions slug unique indexes`.
- [ ] **18b — dead code in `simple_worker.py:237`.** `if name in modules_to_clear: continue` is unreachable (`modules_to_clear` empty at loop entry, sys.modules keys unique). Delete the two lines; run `api/tests/unit/services/execution/test_service.py` + the module-isolation tests. Commit: `chore(execution): remove unreachable modules_to_clear guard`.
- [ ] **18c — `client/src/services/solutions.ts:17` private `errorMessage`** duplicates `getErrorMessage` from `client/src/lib/api-error.ts:210` (handles strictly more shapes, identical signature). Replace usages, delete the local copy, update `solutions.test.ts` if it referenced it. Commit: `refactor(client): solutions service uses shared getErrorMessage`.
- [ ] **18d — `deploy.py` `_reconcile_apps` (1157-1176) duplicates `_reconcile_one` (1178-1200)**. Change `_reconcile_one` to return the stale id set; `count = len(stale)` at callers; delete `_reconcile_apps`; the apps call site keeps its special handling using the returned set. Run `api/tests/unit/test_solution_deploy_reconcile.py`. Commit: `refactor(solutions): single reconcile primitive`.
- [ ] **18e — `api/bifrost/commands/solution.py:725`** — `installs = resp.json().get("solutions", []) if resp.status_code == 200 else []` swallows GET failures and yields a misleading downstream 409. Replace with: non-200 → `raise click.ClickException(f"Failed to list installs ({resp.status_code}): {resp.text[:200]}")`. Extend `test_cli_solution_run.py`/the deploy-cmd test with a 500-list case asserting the message. Commit: `fix(cli): deploy fails loudly when the install list can't be fetched`.
- [ ] **18f — `api/src/routers/app_code_files.py:793-796`** — blanket `except Exception → 404` with no log on dist-asset reads. Narrow: catch the storage-client's not-found exception type → 404 (read `app_build.py:read_dist` / the storage layer to name it — likely `ClientError` with NoSuchKey, or a FileNotFoundError translation); everything else: `logger.exception("dist asset read failed: app=%s rel=%s", app.id, rel)` then re-raise (FastAPI → 500). Add a unit test asserting a generic RuntimeError from read_dist is NOT mapped to 404. Commit: `fix(apps): dist-asset reads only 404 on not-found; real storage errors surface`.

---

## Pass 5 — Solutions versioning + upgrade-in-place (scope change 2026-06-09)

> Source: `docs/plans/2026-06-09-solutions-upgrade-scope-change.md` (Jack, via the
> second-opinion session). Upgrade is an explicit verb, replace is the semantics.
> No migration framework in v1. Codex review requirement DROPPED by Jack
> (2026-06-09) — final gate is the in-session full-diff review instead.

### Task 20: server — version on the install record, deploy records it, downgrades refused

**Files:**
- Create: `api/alembic/versions/20260610_solution_version.py` (revises the then-current head — check `alembic heads`)
- Modify: `api/src/models/orm/solutions.py` (add `version: str | None`, `upgraded_from_version: str | None`), `api/src/models/contracts/solutions.py` (expose both on the response model; add `version` to the deploy/install request models and `force: bool = False` to deploy/zip-install requests), `api/src/services/solutions/deploy.py` (SolutionBundle carries `version: str | None`; `deploy()` compares bundle.version vs install.version — older → raise new `SolutionDowngradeBlocked` unless `force`; on success set `upgraded_from_version = old` when versions differ, then `version = new`), `api/src/services/solutions/zip_install.py` + `api/src/services/solutions/git_sync.py` (thread version from descriptor into the bundle), `api/src/routers/solutions.py` (map `SolutionDowngradeBlocked` → 409 with a detail naming both versions and the force override; accept `force` on deploy + zip-install)
- Tests: `api/tests/unit/test_solution_deploy_version.py` (new), extend `api/tests/e2e/platform/test_solution_zip_install_e2e.py`

- [ ] Failing tests first: (a) deploy with version "1.1.0" over install at "1.0.0" → succeeds, install.version == "1.1.0", upgraded_from_version == "1.0.0"; (b) deploy "0.9.0" over "1.0.0" → SolutionDowngradeBlocked (409 at the endpoint); (c) same with force=True → succeeds; (d) non-PEP440 versions (e.g. "abc") → comparison falls back to inequality-without-ordering: never blocked, recorded verbatim; (e) version absent (older bundles) → never blocked, version untouched.
- [ ] Version comparison via `packaging.version.Version` with `InvalidVersion` → treat as unordered (allow, record). One helper, unit-tested.
- [ ] Commit: `feat(solutions): version on install record; deploy-over-install records upgrade; downgrades 409 unless forced`

### Task 21: CLI — descriptor version + deploy sends it + --force

**Files:**
- Modify: `api/bifrost/solution_descriptor.py` (descriptor gains optional `version`), `api/bifrost/commands/solution.py` (`init --version` default "0.1.0" written to the descriptor; `deploy_cmd` includes `version` in the deploy payload; new `--force` flag forwarded; a 409-downgrade response prints the server detail + hint to use `--force`)
- Tests: extend `api/tests/unit/test_solution_descriptor.py`, `api/tests/unit/test_solution_resolve_install.py` (deploy payload includes version; --force forwarded; downgrade 409 → loud message with hint)

- [ ] Commit: `feat(cli): solution version in descriptor; deploy --force for downgrades`

### Task 22: preview diff — upgrades are previewed, never blind

**Files:**
- Modify: `api/src/services/solutions/zip_install.py` (preview path: when an install matching slug+scope exists, the preview response gains `existing_install` {id, version, name} and a `diff` — entities added/removed by type (compare manifest ids/names vs the install's current solution-owned rows) and config declarations added/removed/changed (key/type/required)), `api/src/models/contracts/solutions.py` (preview response models), `api/src/routers/solutions.py` (wire-through)
- Tests: `api/tests/unit/test_solution_preview_diff.py` (new) + extend the zip e2e: preview of a v2 zip over a v1 install reports the diff and the existing install id

- [ ] Commit: `feat(solutions): upgrade preview diff (entities + config declarations) against the existing install`

### Task 23: UI — version surfaced; drag-drop routes to upgrade with preview

**Files:**
- Modify: `client/src/pages/Solutions.tsx` (upload flow: when preview returns existing_install, the dialog becomes "Upgrade <name> vOLD → vNEW" with the diff list; downgrade (server 409) → confirm dialog that retries with force; NEVER a second install), `client/src/pages/SolutionDetail.tsx` (version + upgraded-from shown), `client/src/services/solutions.ts` (preview/deploy params), regen `client/src/lib/v1.d.ts`
- Tests: extend `Solutions.test.tsx` (upgrade dialog renders diff + versions; downgrade confirm path sends force) and `SolutionDetail.test.tsx` (version rendered)

- [ ] Commit: `feat(ui): solutions upgrade flow — version display, preview diff, downgrade confirm`

---

### Task 19 (runs LAST, after Pass 5): full verification + findings-doc update

- [ ] **Step 1:** `cd api && pyright && ruff check .` — 0 errors.
- [ ] **Step 2:** debug stack up for this worktree; `cd client && npm run generate:types` (OPENAPI_URL per ./debug.sh status if non-default port); `npm run tsc && npm run lint` — clean.
- [ ] **Step 3:** `./test.sh all` (workaround from the header if the api-container flake bites) and `./test.sh client unit` — green; parse `/tmp/bifrost-<project>/test-results.xml` for failures, don't grep stdout.
- [ ] **Step 4:** Final full-diff review in-session (dispatch a fresh reviewer over the whole branch delta for this plan's commits). ~~Codex done-bar~~ — dropped by Jack 2026-06-09.
- [ ] **Step 5:** Update `docs/plans/2026-06-09-solutions-qa-fanout-findings.md` STATUS block: the "0 high open" claim is superseded — link this plan and mark each finding fixed with its commit.

---

## Self-review

- **Coverage vs the review:** all 10 capped findings → Tasks 1-6, 10-11 (finding 7+8), 16 (finding 10), Task 3 (finding 3), Task 2 (finding 2), Task 1 (finding 1), Tasks 4-5 (findings 4-5), Tasks 7-9+13 (finding 6 + Windows family + proxy). Below-cap confirmed items → Tasks 12, 14, 15, 17, 18a-f. NOT fixed (deliberately): module-cache eviction storm + import-hook S3 LIST (perf, needs design — the eviction behavior is documented-intentional isolation; raise as a follow-up issue, not a drive-by), thread-local solution context (rare, needs design), the ~40-call-site read-only guard altitude concern (verified intact; refactor is its own project), `get_by_slug_global` deterministic-pick semantics (documented trade-off), StandaloneV2App token-rotation remount (needs a token-refresh-without-remount design; follow-up issue).
- **Placeholders:** Task 2 step 1, Task 3 step 1, Task 5 step 1 describe test shape rather than paste-ready code because they reuse session/deploy fixtures local to those files — the implementer must read the sibling tests first (stated explicitly). All implementation steps carry concrete code.
- **Type consistency:** `anchor_org_id` (Task 3), `buildWsUrl` (Task 11), `build_sdk_tarball(version)` (Task 17) used consistently.
