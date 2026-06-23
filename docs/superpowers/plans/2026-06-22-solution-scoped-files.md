# Solution-Scoped Files — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax. This plan executes AFTER `2026-06-22-named-policy-rules.md` (it relies on file policies + the resolver landing first).

**Goal:** Files can belong to an installed Solution — isolated by `solution_id` under `solutions/{install_id}/…`, resolved own-first→org→global, exported/imported with the bundle (sidecar files), orphaned-to-org on uninstall, surfaced on the Solution Contents list, and exercised end-to-end (REST + CLI + MCP + git-sync) by a real-solution e2e.

**Architecture:** Add `solution_id` to `FileMetadata`/`FilePolicy` (Core-write only). A freeform `solutions` location maps to `solutions/{install_id}/{path}` via the existing `resolve_s3_key`. The file scope resolver (`_file_org_id`) becomes solution-aware (own-first). Bundle capture writes file bytes as **sidecar files in the zip** + a `ManifestSolutionFile` index; install writes them back with replace/skip merge (no mirror). Uninstall **orphans** files to the org (re-stamp + S3 move) as a background job, mirroring how tables are detached. The Solution Contents list gets a Files row linking to the standard Files page scoped to the install.

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy async / Pydantic v2 / Alembic / PostgreSQL / SeaweedFS(S3); FastAPI `BackgroundTasks` + a job-row+poll pattern; Click CLI; React/TS client.

**Spec:** `docs/superpowers/specs/2026-06-22-solutions-files-open-decisions.md` (decided items D1, D2, D3-revised, D4, D6, O1, O2, O3-revised, O4, O5).

## Codex round-2 corrections (folded in)

Verified against real code; each is reflected in the tasks below:

- **C2 — metadata write path stamps the wrong column.** `record_file_write_metadata`
  (`file_storage/service.py:282`) coerces any UUID `scope` → `organization_id`, and
  `FilePolicyService.upsert_metadata` only accepts `organization_id`. A `scope=<install_id>` write
  would stamp the **install UUID into `organization_id`** — corrupt. **Task 2/4 must extend
  `upsert_metadata` + `record_file_write_metadata` + the signed-upload metadata path to carry
  `solution_id`**, and route solution writes so the install_id lands in `solution_id`, not
  `organization_id`.
- **C3 — uninstall job must not cascade away / must query durably.** If `SolutionFileJob` copies
  `SolutionDeployJob`'s `install_id FK ondelete=CASCADE` (`solution_deploy_jobs.py:31`), deleting the
  Solution deletes the job row mid-flight; and after re-stamp the job can't query by `solution_id`.
  **The orphan job's FK must NOT cascade from `solutions` (nullable, no cascade), and the job
  queries by `origin_solution_id` or a captured old-key list** passed at enqueue.
- **H4 — existing org/global unique indexes must exclude solution rows.** `file_metadata`/
  `file_policies` org+global unique indexes (`file_metadata.py:48,95`) don't exclude solution rows.
  **Task 1 must add `solution_id IS NULL` to the existing org/global unique predicates** (mirror the
  table migration), then add the solution-tier unique.
- **H5 — the read-only guard misses `session.new`.** `guard.py:77` checks `dirty`/`deleted`, not
  `new`. Core-write is still required, but **tests must assert the Core path directly** (don't rely
  on the guard tripping on an ORM insert), and solution write paths must NOT reuse the ORM helpers
  (`file_policy_service.py:55,149,192`) unchanged.
- **H6 — presign: superusers honor arbitrary `scope`; policy auth collapses scope→org.** Non-supers
  are pinned, but a superuser can pass any `scope` (`org_filter.py:164`), and `_authorize_file_policy`
  (`files.py:299`) turns scope back into an org UUID only. **Task 2: the solution branch resolves
  BEFORE org resolution, and policy auth receives BOTH `organization_id` and `solution_id`.**
- **M7 — file bytes ride the ENCRYPTED export tier.** `include_data` requires full-mode + password
  and table data goes into encrypted `.bifrost/secrets.enc` (`export.py:202`). **Decision: solution
  file sidecars + index are encrypted into the same `secrets.enc` tier** (NOT plaintext zip members),
  matching table-data confidentiality. The zip is written by `export.py:64` (NOT `capture.py`) —
  Task 6 wires capture to produce the bytes+index; **export.py** places them into the encrypted tier.
- **M8 — Contents UI needs backend.** `SolutionEntities` (`contracts/solutions.py:159`) has no
  `files` list and `FilesExplorer` has no scope param. **Task 12 must add a `files` entry to the
  solution-entities API response AND make `FilesExplorer` scope-param-aware** — an href alone won't
  surface a row or scope the explorer.

## Global Constraints

- **Worktree only** (`/home/jack/GitHub/bifrost/.claude/worktrees/files-sdk-policies`, branch `codex/files-sdk-policies`). Build on the named-rules commits.
- **Freeform `solutions` location** → `solutions/{scope}/{path}`; a solution's `scope = str(install_id)`. Files must NOT use `workspace` (unscoped → `_repo/`, no isolation). `install_id` is `Solution.id` = the `solution_id` stamped on entities.
- **Core writes for solution-managed file rows.** `FileMetadata`/`FilePolicy` with `solution_id` trip the `before_flush` read-only guard under ORM *update/delete*. Deploy/uninstall/cascade writes use Core `insert()/update()/delete()`. **H5: the guard does NOT check `session.new`** (only `dirty`/`deleted`, `guard.py:77`) — so an ORM *insert* of a solution row slips past it. Therefore tests must **assert the Core path directly** (e.g. patch the ORM helper to fail, or assert no ORM object is added), not merely rely on the guard tripping; and solution write paths must not reuse the existing ORM helpers (`file_policy_service.py:55,149,192`) unchanged.
- **Resolution precedence (D4):** own-solution → org → global, for both file content scope and file policy cascade. Mirrors the workflow/table own-first resolver. Content isolation is structural (prefix) — NO content fallback to the org pool (O5); only policy cascades.
- **Presign scope is server-resolved (O2).** `_file_org_id` derives the scope from context (incl. solution); the signed key goes through `resolve_s3_key`; policy check precedes signing. Client never names a foreign scope. Three failure-mode tests required.
- **No mirror-delete (O1).** Update writes bundle files with a whole-bundle replace-or-skip choice; never deletes a file absent from the new bundle. Same on import.
- **Uninstall ORPHANS, never sweeps (O3-revised).** Files re-stamp to the org (`solution_id→NULL`, `origin_solution_*`/`orphaned_at`, S3 move to org scope) — consistent with table detach. Runs as a background job.
- **Mass file ops are background jobs (D6):** bundle restore (install/deploy with files), uninstall orphan-move, folder delete above `FILE_BULK_INLINE_CAP`. Use the `SolutionDeployJob`-style row + poll endpoint; never inline in a request.
- **Three parallel surfaces** (CLI/MCP/REST) stay in sync; DTO parity + contract-version tripwire + skill-truth regen after DTO/CLI/MCP changes.
- **Tests use `./test.sh`.** Backend logic → unit; endpoints/deploy/sync → e2e. Client → vitest + Playwright (`*.admin.spec.ts` is local-only).

---

## File Structure

| File | Responsibility | New/Mod |
|------|----------------|---------|
| `api/src/models/orm/file_metadata.py` | `solution_id` on `FileMetadata` + `FilePolicy` (+ origin/orphan cols, partial unique) | Mod |
| `api/alembic/versions/<rev>_file_solution_id.py` | columns + indexes | New |
| `api/shared/file_paths.py` | (no change — `solutions` is just a freeform location; verify it validates) | — |
| `api/src/routers/files.py` | `_file_org_id` solution-aware; own-first list/read/write; presign scope | Mod |
| `api/src/services/file_policy_service.py` | policy cascade own-solution→org→global | Mod |
| `api/src/services/solution_files.py` | enumerate/read/write/orphan-move helpers (Core writes) | New |
| `api/src/services/solutions/capture.py` | `_solution_file_entries` → sidecar bytes + index | Mod |
| `api/src/services/solutions/deploy.py` | install files (replace/skip), no mirror | Mod |
| `api/src/routers/solutions.py` | uninstall enqueues orphan-move job; file-sweep/restore job rows + poll | Mod |
| `api/src/models/orm/solution_file_jobs.py` | `SolutionFileJob` orchestration row | New |
| `api/bifrost/manifest.py` | `ManifestSolutionFile` index entry; `FilePolicy.solution_id` portability | Mod |
| `api/src/services/manifest_generator.py` + `manifest_import.py` | serialize/import the file index + sidecars | Mod |
| `api/bifrost/commands/solution.py` | deploy/export/install carry `files/` sidecars | Mod |
| `api/bifrost/commands/files.py` | `--solution` scope on file commands | Mod |
| `api/src/services/mcp_server/tools/files.py` | solution scope honored | Mod |
| `client/src/pages/SolutionDetail.tsx` | "files" entity kind → Files page link | Mod |
| `client/src/components/files/FilesExplorer.tsx` | accept `install=<id>` param → solutions/{id} scope | Mod |
| `client/src/pages/Files.tsx` | pass through the install param | Mod |

---

## Task 1: `solution_id` (+ orphan provenance) on `FileMetadata` & `FilePolicy` + migration

**Files:**
- Modify: `api/src/models/orm/file_metadata.py`
- Create: `api/alembic/versions/<rev>_file_solution_id.py`
- Test: `api/tests/unit/test_file_metadata_solution_columns.py`

**Interfaces:**
- Produces: both models gain `solution_id: UUID|None` (FK `solutions.id`, `ondelete="CASCADE"`), `origin_solution_slug:str|None`, `origin_solution_id:UUID|None`, `orphaned_at:datetime|None`; partial unique `(solution_id, location, path) WHERE solution_id IS NOT NULL`.

- [ ] **Step 1: Failing test**

```python
# api/tests/unit/test_file_metadata_solution_columns.py
from src.models.orm.file_metadata import FileMetadata, FilePolicy

def test_solution_columns_present():
    for M in (FileMetadata, FilePolicy):
        for c in ("solution_id", "origin_solution_slug", "origin_solution_id", "orphaned_at"):
            assert c in M.__table__.columns, f"{M.__name__}.{c}"

def test_solution_partial_unique():
    names = {i.name for i in FileMetadata.__table__.indexes if i.unique}
    assert "uq_file_metadata_solution_location_path" in names
```

- [ ] **Step 2: Run → FAIL** — `./test.sh tests/unit/test_file_metadata_solution_columns.py -v`

- [ ] **Step 3: Add columns + index to BOTH models**

On `FileMetadata` and `FilePolicy`, add (mirroring the existing partial-unique pattern at `file_metadata.py:95`):

```python
    solution_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("solutions.id", ondelete="CASCADE"), nullable=True, default=None)
    origin_solution_slug: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None)
    origin_solution_id: Mapped[UUID | None] = mapped_column(nullable=True, default=None)
    orphaned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
```

**H4 — fix the EXISTING org/global uniques first, then add the solution tier.** The current
`uq_*_org_location_path` (`WHERE organization_id IS NOT NULL`) and `uq_*_global_location_path`
(`WHERE organization_id IS NULL`) don't exclude solution rows → a solution row collides with / is
ambiguous against org/global. Add `solution_id IS NULL` to BOTH existing predicates, then add the
solution tier. For each table (`file_metadata`/`file_policies`):

```python
        # MODIFY the two existing predicates to exclude solution rows:
        Index("uq_file_metadata_org_location_path", "organization_id", "location", "path", unique=True,
              postgresql_where=text("organization_id IS NOT NULL AND solution_id IS NULL")),
        Index("uq_file_metadata_global_location_path", "location", "path", unique=True,
              postgresql_where=text("organization_id IS NULL AND solution_id IS NULL")),
        # ADD the solution tier:
        Index("uq_file_metadata_solution_location_path", "solution_id", "location", "path",
              unique=True, postgresql_where=text("solution_id IS NOT NULL")),
        Index("ix_file_metadata_solution_id", "solution_id"),
```

The migration must `DROP` + recreate the two existing unique indexes with the new predicate (not just add the third), or old rows still collide. Add a test asserting all three predicates are mutually exclusive.

- [ ] **Step 4: Run model test → PASS**

- [ ] **Step 5: Migration** (`alembic revision`; `op.add_column` ×4 per table + the two indexes per table; downgrade drops them). After editing: `./test.sh stack reset && ./test.sh tests/unit/test_file_metadata_solution_columns.py -v` → PASS.

- [ ] **Step 6: Commit**

```bash
git add api/src/models/orm/file_metadata.py api/alembic/versions/*file_solution_id*.py api/tests/unit/test_file_metadata_solution_columns.py
git commit -m "feat(solution-files): solution_id + orphan provenance on file metadata/policy + migration"
```

---

## Task 2: Solution-aware file scope resolver (own-first) + presign scope (O2)

**Files:**
- Modify: `api/src/routers/files.py` (`_file_org_id` / `_storage_scope` → solution scope; `_build_signed_url` uses it)
- Test: `api/tests/e2e/platform/test_solution_file_scope.py`

**Interfaces:**
- A request carrying a solution context (the install) resolves `scope = str(install_id)` for the `solutions` location; reads/writes/list/presign all target `solutions/{install_id}/…`. A non-solution caller is unchanged. The signed key is built by `resolve_s3_key` from the **server-resolved** scope (client cannot inject a foreign scope).

> **O2 already half-holds:** `_build_signed_url` (`files.py:633`) already routes through `_storage_scope(_file_org_id(ctx, location, scope))` and policy-checks before signing. This task makes `_file_org_id` solution-aware and ADDS the three failure-mode tests; it does not rebuild presign.

- [ ] **Step 1: Failing tests (incl. the 3 O2 failure modes)**

```python
# api/tests/e2e/platform/test_solution_file_scope.py
import pytest

@pytest.mark.asyncio
async def test_solution_write_then_read_isolated(solution_client, other_solution_client):
    # write via solution A's context → lands under solutions/{A}/; readable by A.
    await solution_client.put("/api/files/write", json={"location":"solutions","path":"r/x.txt","content_b64":"aGk="})
    got = await solution_client.post("/api/files/read", json={"location":"solutions","path":"r/x.txt"})
    assert got.status_code == 200
    # solution B cannot read A's file at the same logical path (different install scope).
    miss = await other_solution_client.post("/api/files/read", json={"location":"solutions","path":"r/x.txt"})
    assert miss.status_code in (403, 404)

@pytest.mark.asyncio
async def test_presign_rejects_client_supplied_foreign_scope(solution_client, other_org_id):
    # O2 #1: a client cannot presign into a foreign scope by passing scope=<other org>.
    r = await solution_client.post("/api/files/signed-url",
        json={"location":"solutions","path":"x","method":"GET","scope":str(other_org_id)})
    # server ignores/overrides the supplied scope → URL targets the caller's own scope (or 403).
    assert r.status_code in (200, 403)
    if r.status_code == 200:
        assert str(other_org_id) not in r.json()["path"]   # signed key is NOT in the foreign scope

@pytest.mark.asyncio
async def test_presign_rejects_path_traversal(solution_client):
    # O2 #2
    r = await solution_client.post("/api/files/signed-url",
        json={"location":"solutions","path":"../../other/x","method":"GET"})
    assert r.status_code == 400

@pytest.mark.asyncio
async def test_presign_put_cannot_plant_in_foreign_scope(solution_client, other_org_id):
    # O2 #3
    r = await solution_client.post("/api/files/signed-url",
        json={"location":"solutions","path":"x","method":"PUT","content_type":"text/plain","scope":str(other_org_id)})
    assert r.status_code in (200, 403)
    if r.status_code == 200:
        assert str(other_org_id) not in r.json()["path"]
```

(Use/create solution-context client fixtures — a client whose auth context carries `solution_id`. Grep `tests/e2e` for how solution-scoped requests set `?solution=` / context; mirror it.)

- [ ] **Step 2: Run → FAIL** — `./test.sh e2e tests/e2e/platform/test_solution_file_scope.py -v`

- [ ] **Step 3: Make `_file_org_id` solution-aware (H6)**

Read `_file_org_id` / `_storage_scope` / `_authorize_file_policy` in `files.py`. Extend so that when the context carries a solution install (`ctx.solution_id`, set from `?solution=` per `auth.py:312`), the `solutions` location resolves `scope = str(install_id)`. **The solution branch must run BEFORE org resolution** — `ctx.solution_id` wins over any `request.scope`, including for a superuser (who otherwise honors arbitrary `scope` per `org_filter.py:164`). **`_authorize_file_policy` must receive BOTH `organization_id` AND `solution_id`** (it currently collapses scope→org only at `files.py:299`) so the policy cascade (Task 3) can resolve own-solution. `request.scope` is ignored whenever a solution context is present. The existing `_build_signed_url` then signs the correct key for free.

- [ ] **Step 3b: Metadata write path carries solution_id (C2)**

`record_file_write_metadata` (`file_storage/service.py:282`) currently coerces any UUID `scope`→`organization_id` and calls `upsert_metadata(organization_id=...)`. For a solution write this would stamp the **install_id into `organization_id`** — corrupt. Extend `upsert_metadata` (`file_policy_service.py:37`) and `record_file_write_metadata` + the signed-upload metadata path to accept `solution_id`, and when the resolved scope is a solution install, write `solution_id=<install_id>, organization_id=<install's org>` via the **Core** path (Task 4's service), NOT the ORM helper. Add a test: a `location=solutions` write lands `solution_id` set, `organization_id` = the install's org (never the install UUID in `organization_id`).

- [ ] **Step 4: Run → PASS** (all four). Then own-first read/list: a solution read with no own file falls back to org/global **for policy authorization** but NOT for content (O5) — assert a solution can't read an org file by content path unless explicitly granted. Add that assertion.

- [ ] **Step 5: Commit**

```bash
git add api/src/routers/files.py api/tests/e2e/platform/test_solution_file_scope.py
git commit -m "feat(solution-files): solution-aware file scope + presign O2 hardening (3 failure-mode tests)"
```

---

## Task 3: File policy cascade — own-solution → org → global

**Files:**
- Modify: `api/src/services/file_policy_service.py` (`load_policy` adds the solution arm)
- Test: `api/tests/e2e/platform/test_solution_file_policy_cascade.py`

**Interfaces:** `load_policy` resolves a solution's own prefix policy first, then the org, then global — matching the entity own-first model. A solution shipping a prefix policy locks it (solution-managed); a path it doesn't cover inherits org/global.

- [ ] **Step 1: Failing test** — a solution-scoped `FilePolicy` for `solutions/{install}/x/` wins over an org/global policy for the same logical prefix; an uncovered path falls back to global.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3:** Extend `load_policy` (it currently does org→global via longest-prefix). Add a solution arm: when resolving for a solution context, query `solution_id == install_id` first (longest-prefix within the solution), else fall to the existing org→global. Keep the existing arm untouched for non-solution callers.
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** — `feat(solution-files): file policy cascade own-solution→org→global`

---

## Task 4: `solution_files` service — enumerate / read / write / orphan-move (Core)

**Files:**
- Create: `api/src/services/solution_files.py`
- Test: `api/tests/e2e/platform/test_solution_files_service.py`

**Interfaces:**
- `enumerate_solution_files(db, install_id) -> list[SolutionFileEntry{location,path,sha256,size}]`
- `read_solution_file(db, install_id, location, path) -> bytes`
- `write_solution_file(db, install_id, location, path, content, *, mode: 'replace'|'skip') -> bool` (Core-upsert metadata + backend write; returns whether written)
- `orphan_solution_files(db, install_id, org_id, slug) -> int` (Core-update metadata to org + S3 move; returns count)

> All metadata writes are **Core** (`insert()/update()`), never ORM, so the read-only guard never sees a dirty solution-managed row. Install the guard in tests.

- [ ] **Step 1: Failing tests** (write→enumerate→read round-trip; replace vs skip; orphan-move re-stamps to org + moves S3 key; guard active during orphan-move).
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3:** Implement using the backend (`S3Backend.read/write/list`) for bytes and Core statements for `FileMetadata`. `orphan_solution_files`: for each row, Core-`update` (`solution_id=None, organization_id=org_id, origin_solution_slug=slug, origin_solution_id=install_id, orphaned_at=now`) AND move the S3 object (read old key → write new org-scoped key → delete old). The S3 move uses `resolve_s3_key("solutions", install_id, path)` → `resolve_s3_key("solutions", org_id, path)`.
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** — `feat(solution-files): solution_files service (enumerate/read/write/orphan-move, Core writes)`

---

## Task 5: `SolutionFileJob` + background mass-op plumbing (D6)

**Files:**
- Create: `api/src/models/orm/solution_file_jobs.py` (+ migration)
- Modify: `api/src/routers/solutions.py` (enqueue + poll endpoints, mirroring `SolutionDeployJob`)
- Test: `api/tests/e2e/platform/test_solution_file_jobs.py`

**Interfaces:** a `SolutionFileJob{id, install_id, origin_solution_id, kind('restore'|'orphan'|'bulk_delete'), status, error, result, timestamps}`; `POST` enqueues via `BackgroundTasks.add_task`, `GET /api/solutions/file-jobs/{id}` polls. Worker runs under a fresh session.

> **C3 — FK must NOT cascade, query durably.** Unlike `SolutionDeployJob` (which has
> `install_id FK ondelete=CASCADE`, `solution_deploy_jobs.py:31`), an **orphan** job runs *while the
> Solution is being deleted* — a cascading FK would delete the job row mid-flight. So `install_id`
> here is **nullable with NO cascade** (or just a plain UUID column, no FK), and the job carries
> `origin_solution_id` + (for orphan) a **captured old-key list** passed at enqueue, because after
> the in-txn re-stamp the rows no longer have `solution_id == install_id` to query by.

- [ ] **Step 1: Failing test** (enqueue an orphan job → poll → succeeded with a count).
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3:** Mirror `solution_deploy_jobs.py` + the `_run_deploy_job`/poll pattern (`solutions.py:911,1101`). Worker dispatches by `kind` into the Task-4 service functions.
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** — `feat(solution-files): SolutionFileJob + background enqueue/poll for mass file ops`

---

## Task 6: Bundle capture — file sidecars + manifest index (D3-revised)

**Files:**
- Modify: `api/src/services/solutions/capture.py` (`SolutionBundle.solution_files`, `_solution_file_entries` — DATA only), `api/src/services/solutions/export.py` (place bytes+index into the ENCRYPTED tier), `api/bifrost/manifest.py` (`ManifestSolutionFile`)
- Test: `api/tests/unit/test_solution_file_capture.py`, `api/tests/e2e/platform/test_solution_export_files.py`

**Interfaces:** `bundle_for(..., include_data=True)` populates `SolutionBundle.solution_files: list[SolutionFileEntry{location, path, sha256, size, content_bytes}]`; **`export.py` places the bytes + the `ManifestSolutionFile` index into the encrypted `.bifrost/secrets.enc` tier** alongside `table_data` (M7), NOT as plaintext zip members. File cap + loud warning + omit-empty per `_table_data`.

> **M7 + correct file locations.** `capture.py::bundle_for` ASSEMBLES data; **`export.py:64` writes the zip members** and `export.py:202` is where `include_data` content goes into the encrypted `secrets.enc` (gated on full-mode + password). So: Task 6 adds `_solution_file_entries` to capture (enumerate+read bytes); the **export.py** change places those bytes + index into the encrypted tier — file bytes get the SAME confidentiality as table rows. Do NOT write plaintext `files/` members.

- [ ] **Step 1: Failing tests** (export a solution with 2 files → bundle has both sidecars + index entries with correct sha; empty → omitted; cap warning logged).
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3:** Add `_solution_file_entries(solution)` (parallel to `_table_data`) using the Task-4 enumerate/read; thread it into `bundle_for` under `include_data`; write sidecars where the zip is assembled (grep capture.py for where `python_files`/zip bytes are written). Add `ManifestSolutionFile` (mirror `ManifestConfig` `classify` — `location`/`path`/`sha256`/`size` are CONTENT; no env fields).
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** — `feat(solution-files): bundle capture writes file sidecars + manifest index`

---

## Task 7: Install/deploy — write file sidecars (replace/skip, no mirror, O1)

**Files:**
- Modify: `api/src/services/solutions/deploy.py` (write bundle files post-upsert)
- Test: `api/tests/e2e/platform/test_solution_deploy_files.py`

**Interfaces:** deploy writes each bundle file via the Task-4 `write_solution_file(..., mode=<install choice>)`; **no mirror-delete** of files absent from the bundle. The install's replace/skip choice rides the deploy request (default replace for shipped). Runs inside the deploy job (already async).

- [ ] **Step 1: Failing tests** (deploy a bundle with files → files present under the install scope; redeploy with a file dropped → old file SURVIVES (no mirror); replace overwrites, skip preserves a pre-existing user file).
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3:** In `deploy.py` after entity upserts, iterate `bundle.solution_files`, read each sidecar's bytes, `write_solution_file(install_id, location, path, content, mode=...)`. NO reconcile-delete for files (unlike entities — explicitly skip the `id NOT IN bundle` sweep for files). Honor the replace/skip flag from the deploy request DTO (add the flag; default `replace`).
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** — `feat(solution-files): deploy writes bundle files (replace/skip, no mirror)`

---

## Task 8: Uninstall — orphan files to the org (O3-revised), as a job

**Files:**
- Modify: `api/src/routers/solutions.py` (`delete_solution` enqueues the orphan job after commit)
- Test: `api/tests/e2e/platform/test_solution_uninstall_files.py`

**Interfaces:** `delete_solution` enqueues a `SolutionFileJob(kind="orphan")` after the DB commit (next to the existing S3 sweep of `_solutions/`). Files survive: metadata re-stamped to the org with `origin_solution_*`/`orphaned_at`, S3 objects moved to org scope. The `solution_id` FK `ondelete=CASCADE` would otherwise delete `FileMetadata` rows — so the orphan-move (which nulls `solution_id`) must run **before** the `Solution` delete, exactly like the table detach at `solutions.py:789`.

- [ ] **Step 1: Failing test** (install a solution with files → uninstall → files still readable under the org, metadata `orphaned_at` set, `solution_id` NULL, S3 object at the org key not the install key).
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3:** In `delete_solution`, **before** `ctx.db.delete(sol)`, call `orphan_solution_files` (Core update nulls `solution_id` so the cascade can't reach them — mirroring the table detach). The S3 move is the large part → enqueue it as the job AFTER commit (metadata re-stamp is in-txn; byte move is the job). Sequence: re-stamp metadata in-txn → commit → enqueue S3-move job.
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** — `feat(solution-files): uninstall orphans files to org (re-stamp in-txn + S3-move job)`

---

## Task 9: Manifest / git-sync round-trip (file index + `FilePolicy.solution_id`)

**Files:**
- Modify: `api/src/services/manifest_generator.py` (emit `solution_files` index + sidecars), `api/src/services/manifest_import.py` (`_resolve_solution_file`; file-policy `solution_id`)
- Test: `api/tests/unit/test_manifest.py`, `api/tests/e2e/platform/test_git_sync_local.py`

**Interfaces:** export emits the `ManifestSolutionFile` index + sidecar bytes; import writes them back via the Task-4 service (replace/skip, no mirror) and fails closed on a missing sidecar. `FilePolicy` carries `solution_id` through the manifest (ENVIRONMENT-classed, like other scope fields).

- [ ] **Step 1: Failing tests** (a solution with files round-trips through export→import into a clean DB: files present, sha matches; a file policy's `solution_id` survives; a manifest referencing a sidecar that's missing fails closed).
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3:** Add serialize/import; order file-index resolution after entities, before finalize. Match the existing non-destructive upsert discipline (CLAUDE.md manifest section).
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** — `feat(solution-files): manifest + git-sync round-trip for solution files`

---

## Task 10: CLI parity — `bifrost solution` carries files; `bifrost files --solution`

**Files:**
- Modify: `api/bifrost/commands/solution.py` (deploy/export/install package + restore the `files/` sidecars), `api/bifrost/commands/files.py` (`--solution` scope flag)
- Test: `api/tests/e2e/platform/test_cli_solution_files.py`

**Interfaces:** `bifrost solution export --include-data` writes `files/` into the zip; `bifrost solution install/deploy` restores them (observable job); `bifrost files {read,write,list} --solution <slug|id>` targets the install scope.

- [ ] **Step 1: Failing CLI e2e** (export a solution with a file → zip contains `files/...`; install into a clean org → file readable; `bifrost files list --solution X` shows it).
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3:** Wire the sidecar packaging into the CLI zip build; add `--solution` to the file commands (resolve slug→install_id via `RefResolver`). The deploy already polls the job; reuse that observable flow for the file restore.
- [ ] **Step 4: DTO parity + contract tripwire + skill-truth** (`./test.sh tests/unit/test_dto_flags.py tests/unit/test_contract_version.py`; `python api/scripts/skill-truth/generate.py`).
- [ ] **Step 5: Commit** — `feat(solution-files): CLI parity (solution export/install files + files --solution)`

---

## Task 11: MCP parity — file tools honor solution scope

**Files:**
- Modify: `api/src/services/mcp_server/tools/files.py`
- Test: `api/tests/unit/test_mcp_thin_wrapper.py` (+ a tool test)

**Interfaces:** the MCP file tools accept/forward a solution scope (thin HTTP bridge — the REST endpoint does the resolution). No ORM.

- [ ] **Step 1: Failing test** (MCP file write/read with a solution scope round-trips via REST; thin-wrapper enforcement passes).
- [ ] **Step 2: Run → FAIL.** **Step 3:** forward the scope param through `call_rest`. **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** — `feat(solution-files): MCP file tools honor solution scope`

---

## Task 12: Frontend — Files entry on the Solution Contents list → standard Files page

**Files:**
- Modify (backend, M8): `api/src/models/contracts/solutions.py` (`SolutionEntities` gains a `files` list), the solution-entities endpoint that fills it (enumerate solution files for the install)
- Modify (frontend): `client/src/pages/SolutionDetail.tsx` (`EntityKind` + `ENTITY_TABS` + `entityHref`), `client/src/components/files/FilesExplorer.tsx` (accept `install` param → scope), `client/src/pages/Files.tsx`
- Test: backend e2e for the `files` entities field; `client/src/pages/SolutionDetail.test.tsx`, a vitest for FilesExplorer param, Playwright

**Interfaces:** the Contents list shows a **Files** row (icon `FolderOpen`) linking to `/files?install=<solution_id>&from=solution:<id>`; the standard Files page reads `install` and scopes `FilesExplorer` to `location="solutions"`, `scope=<install_id>`, with a "Solution › {name} › Files" breadcrumb + back link.

> **M8 — the href alone is insufficient.** `SolutionEntities` (`contracts/solutions.py:159`) has no
> `files` list, `SolutionDetail` hard-codes six entity kinds (`SolutionDetail.tsx:132`), and
> `FilesExplorer` uses local org/global state with **no scope param** (`FilesExplorer.tsx:44`). This
> task therefore has THREE parts: (1) backend — add `files` to the solution-entities response
> (enumerate `FileMetadata WHERE solution_id == install`); (2) `FilesExplorer` accepts an `install`
> prop/param and, when set, pins `location="solutions"` + `scope=install` + disables the org/global
> selector; (3) `SolutionDetail` renders the row + href. All three are required for a working row.

- [ ] **Step 1: Failing tests** (SolutionDetail renders a Files row with the right href; FilesExplorer given `install` requests `location=solutions&scope=<id>`).
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3:** Add `"files"` to `EntityKind` + `ENTITY_TABS` + the `entityHref` switch (`return \`/files?install=${solutionId}${from}\``). Make `FilesExplorer` accept an optional `install` (from the query param), and when set, pin `location="solutions"` + `scope=install` + show the breadcrumb/back. Refer to how `tables` links from Contents (`/tables/${id}?from=solution:`) for the back-link convention (O4).
- [ ] **Step 4: tsc + lint + vitest + Playwright** (`./test.sh client unit`; add a Playwright step: open a solution → Contents → Files → see the install's files).
- [ ] **Step 5: Commit** — `feat(solution-files): Files entry on Solution Contents → scoped Files page`

---

## Task 13: Full real-solution end-to-end

**Files:**
- Create: `api/tests/e2e/platform/test_solution_files_e2e.py`
- Test: itself

**Interface:** one e2e proving the whole arc with a REAL solution that has files + a file policy + a named policy rule (from the named-rules plan).

- [ ] **Step 1: Write the e2e**

```
1. Create a solution; add a workflow, a table, a FILE (solutions/{install}/docs/readme.md),
   a file POLICY for that prefix that references a NAMED RULE {"$ref":"admin_bypass"}.
2. Deploy it (async job → poll succeeded).
3. As a regular org user with the rule's grant: READ the file (allowed); as a denied user: 403.
4. Export the solution WITH data → assert the zip has files/docs/readme.md + the manifest index +
   the file policy with the $ref preserved.
5. Install the SAME bundle into a CLEAN org → file present, policy resolves the $ref (admin_bypass
   seeded), table present.
6. Uninstall the original → file SURVIVES as an org file (orphaned_at set, solution_id NULL,
   readable at the org scope).
7. Assert no cross-solution leakage throughout (a second install can't read the first's file).
```

- [ ] **Step 2: Run → iterate to green** — `./test.sh e2e tests/e2e/platform/test_solution_files_e2e.py -v`
- [ ] **Step 3: Commit** — `test(solution-files): full real-solution e2e (files+policy+named-rule, deploy/export/install/uninstall)`

---

## Task 14: Full verification sweep (both plans)

- [ ] `cd api && pyright && ruff check .` → 0 errors.
- [ ] `cd client && npm run generate:types && npm run tsc && npm run lint` → PASS.
- [ ] `./test.sh all` → green (incl. named-rules suites + solution-files suites + the e2e). Parse the JUnit XML.
- [ ] `./test.sh client unit && ./test.sh client e2e files-explorer.admin.spec.ts` → PASS.
- [ ] Choke-point + guard smell checks: no raw policy `model_validate` on an eval path outside the loader; no ORM mutation of a `solution_id`-bearing file row outside Core writes.
- [ ] Commit any fixups.

---

## Notes for the implementer

- **`install_id` is `Solution.id`**; the file scope for a solution is `str(install_id)`.
- **Core writes** for every `solution_id`-bearing file metadata/policy mutation (deploy, install, orphan). Read with `select`, write with Core `insert/update/delete`. Install the read-only guard in tests.
- **Never mirror-delete files** (O1) — not on update, not on install. Uninstall **orphans**, never sweeps (O3-revised) — re-stamp `solution_id→NULL` BEFORE the `Solution` delete so the FK cascade can't reach the rows (mirror the table detach at `solutions.py:789`).
- **Presign scope is server-resolved** — a solution caller's `request.scope` is ignored; the signed key comes from the resolved context via `resolve_s3_key`. The three O2 tests are non-negotiable.
- **Content does not cascade** (O5) — only policy does. A solution can't read an org file by content path unless a policy grants it.
- **Mass ops are jobs** — restore, orphan-move, bulk delete go through `SolutionFileJob` + poll, never inline.
