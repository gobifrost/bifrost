# Solution Storage Scope Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` to implement this plan task-by-task. Use a fresh implementer subagent per task, then run spec-compliance and code-quality reviews before marking the task done. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make solution-scoped files and tables usable from real solution workflows/apps: declared locations/tables resolve under `solution_id`, undeclared writes fail, `global_repo_access` gates org/global data fallback, and full solution import/export remains memory-safe for very large file payloads.

**Architecture:** A solution is a scope on ordinary storage locations, not a special location. Runtime context (`?solution=<install_id>` or `X-Bifrost-App`) selects the solution tier for declared locations/tables, and the server decides whether fallback to org/global data is allowed by loading the `Solution.global_repo_access` flag. File-location declarations live in `.bifrost/files.yaml`; file bytes are transported with streaming payload entries, not JSON/base64 in `secrets.enc`.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy async, Pydantic v2, Alembic, PostgreSQL, SeaweedFS/S3 via aiobotocore, Click CLI, React/TypeScript, Vite.

---

## Confirmed Decisions

- Solution context scopes any declared file location by install id: `finance/{solution_id}/path.ext`.
- `location == "solutions"` is not a control-flow branch. `"solutions"` may still be a normal declarable location string.
- `workspace` is reserved and maps to shared `_repo`; solution-context file APIs reject `location="workspace"` instead of half-scoping metadata over shared bytes.
- `global_repo_access=false` means sealed data runtime: own-solution tier only. No org/global fallback for files or tables.
- `global_repo_access=true` allows read fallback: own-solution -> org -> global. It does not allow undeclared solution writes.
- File policies are evaluated for the tier that actually serves the bytes: solution policy for solution bytes, org policy for org bytes, global policy for global bytes.
- Table policies are evaluated on the resolved table row, as today, after table-name resolution chooses the tier.
- File-location declarations are separate from `solution_files`. `solution_files` is a data export index; `files.yaml` declares runtime storage locations.
- Table auto-create is disabled in solution context. A solution can write only to tables declared in its manifest.
- Full solution exports/imports must not read all file bytes into memory. 30+ GB of files is a required design target.

## Codex Execution Model

- Worktree: `/home/jack/GitHub/bifrost/.claude/worktrees/files-sdk-policies`
- Branch: `codex/files-sdk-policies`
- Never edit the primary checkout.
- Stage commits by explicit file list only. Never `git add -A`.
- Use `./test.sh` for backend tests.
- Use subagents for implementation and review because the user explicitly requested delegation. In this Codex environment, the controller should spawn subagents explicitly; they do not automatically inherit a mutable shared worktree the way a long Claude Code session may feel like it does.
- Dispatch implementers sequentially for code-writing tasks to avoid overlapping edits. Explorers and reviewers may run in parallel when their scopes do not overlap.
- The controller owns todo tracking in this file and marks a task complete only after implementation, focused tests, spec review, and code quality review pass.

## Todo Tracker

- [x] Task 0: Preflight and baseline facts
- [x] Task 1: Files SDK appends `?solution=` for every file REST call
- [x] Task 2: Server derives solution context for any file location
- [x] Task 3: Declare file locations in `.bifrost/files.yaml`
- [x] Task 4: Enforce declared-only solution writes for files and tables
- [x] Task 5: Files read/list/exists resolve by tier with `global_repo_access`
- [x] Task 6: Tables name resolution and auto-create respect solution declarations and `global_repo_access`
- [x] Task 7: Policy resolution is tier-correct and solution policies never leak upward
- [x] Task 8: Web SDK/app file calls honor solution scope
- [x] Task 9: Streaming solution file payload import/export replaces in-memory file blobs
- [x] Task 10: Re-point capstone and `location="solutions"` tests to the real model
- [x] Task 11: Full deployed-solution end-to-end and large-file memory tests
- [x] Task 12: Final verification sweep (backend green; client e2e deferred to Jack)
- [ ] Task 13: Release-acceptance — live full-stack drive + large-file (30GB-class) memory proof + client e2e

## Task 13: Release-Acceptance Drive (Jack's "ready" bar)

The implementation + tests are complete (Tasks 0-12). Backend suite green
(6700 pass; the lone full-run failure is `test_subscriptions.py` — a
pre-existing repo-wide timeout flake, unchanged on this branch, passes isolated;
see `docs/plans/2026-06-24-test-flake-root-cause.md`). What remains is Jack's
higher bar for "ready," which automated tests do NOT yet cover:

**13a. Live full-stack drive (a real solution with files, end to end).**
- `BIFROST_FORCE_PORT=1 ./debug.sh up` (port mode — Chrome/Playwright can't drive
  netbird; large-file curl is also simpler over localhost).
- Install the API-matched CLI in a scratch venv (CLAUDE.md "Spinning up" recipe).
- Build/deploy a solution that declares a `files.yaml` location + a table + a
  workflow that does `sdk.files.write/read` and `sdk.tables.upsert/query`.
- Drive it as a user: run the workflow, confirm bytes land at
  `<location>/{solution_id}/...` in S3, confirm a sealed (`global_repo_access=
  false`) install can't read org/global fallback and an open one can.
- Export the solution, reinstall into a second org, confirm files + table rows
  round-trip. (This mirrors `test_solution_storage_full_e2e.py` but LIVE.)

**13b. Large-file / OOM proof (the explicit 30GB concern).**
- Gap: `test_solution_large_file_memory.py` proves bounded 8MiB chunking and
  no `content_b64` with FAKE S3 chunks. It does NOT prove a live stack streams
  multi-GB without API RSS ballooning (a real aiobotocore/zip-writer buffering
  regression would slip past the fake).
- Drive: write N real large files into a solution location summing to a
  meaningful total (start ~5-10GB to fit the dev box; the design target is 30GB).
  Seed via `dd`-generated files pushed through `sdk.files.write_bytes` or the
  signed-upload path. Then `bifrost solution export` and `solution install`.
- MEASURE during export AND import: watch the API/worker container RSS
  (`docker stats <project>-api-1 <project>-worker-1` or
  `cat /sys/fs/cgroup/.../memory.current`). PASS = peak RSS stays bounded
  (roughly a few × the 8MiB chunk + zip overhead), NOT proportional to total
  file bytes. If RSS tracks file size → streaming is broken somewhere
  (capture/export/zip_install/secrets_blob); that's a real bug to fix before merge.
- Key code under test: `file_storage/s3_client.py::iter_raw_s3_chunks` +
  `put_object_from_chunks`, `solutions/file_payloads.py`, `solutions/export.py`,
  `solutions/zip_install.py`, `solutions/secrets_blob.py` (file bytes must NOT
  enter the Fernet JSON blob — only the per-chunk encrypted `.bin.enc` members).

**13c. Client e2e (deferred from Task 12).**
- Branch specs are `*.admin.spec.ts` (not in CI). Run under port mode:
  `./test.sh client e2e` (Playwright). Or accept they're out-of-CI and skip with
  a note — Jack's call.

**13d. Scoping confirmation (Jack asked for the explicit map).**
- The scoping-rule → test map is in the handoff prompt / this session's summary.
  Spot-check by re-running the focused scope suites:
  `./test.sh tests/e2e/platform/test_solution_file_cascade_gated.py
  tests/e2e/platform/test_table_solution_cascade_gated.py
  tests/e2e/platform/test_solution_policy_solution_only.py
  tests/e2e/platform/test_solution_declared_only.py
  tests/unit/test_files_sdk_solution_scope.py -v`

## Current Handoff Snapshot

As of 2026-06-24, Tasks 0-11 are committed. Task 12 final verification is in progress (continued by Claude after Codex hit usage limits).

Task 11 committed as `ae07974f8 test(solution-storage): cover full install and streaming file payloads`.

Task 12 verification results so far:
- Step 1 hardcode smell: `rg 'location == "solutions"' api/src` → no matches. CLEAN.
- Step 2 capstone: `./test.sh tests/e2e/platform/test_solution_storage_full_e2e.py -v` → 1 passed.
- Step 3 backend: `cd api && ruff check .` → all checks passed; `pyright` → 0 errors.
- Step 4 frontend: hit 2 real strict-TS errors on this branch (NOT from the storage plan, but caught in the sweep):
  - `TestAccessModal.test.tsx` result() helper `action` was `string`, must be `FilePolicyAction`.
  - `FilePreview.tsx` Blob part rejected `Uint8Array<ArrayBufferLike>` from readBytes — copy into plain `new Uint8Array(bytes)`.
  - Fixed and committed `2cd415da3 fix(files): satisfy strict TS for Blob part and test action typing`. `npm run tsc` exit 0; `npm run lint` 0 errors (1 pre-existing FormRenderer warning).
  - NOTE: did NOT regen `v1.d.ts` — debug stack mounts the MAIN repo, not this worktree, so its schema is wrong for this branch; committed types are authoritative.
- Step 5 client unit: `./test.sh client unit` → 1554 passed, 1 timeout-flake (`ExecutionHistory.test.tsx`, untouched on this branch, passes 14/14 in isolation — flaked under parallel backend-suite load).
- Step 5 backend `./test.sh all` run 1 → 8 failed / 6690 passed. Triaged:
  - 6 real, all fixed in `6620abd34`:
    1. files list non-workspace branch skipped per-file creator filtering when the directory had no broad list-allow → 403 (REAL feature bug in this branch). Fixed by enumerating + per-file filtering like the workspace branch.
    2. `ManifestFiles.locations` untagged (field-class tripwire). Tagged CONTENT.
    3. `ManifestFiles.locations` had no domain-valid roundtrip sentinel. Added `["finance"]` to generators DOMAIN_VALUES.
    4. stale `routers/tables.py` inline-org-scoping allow-list entries (Task 6 moved the lookup to `solution_scope.py`). Removed.
    5. `test_solution_entities_endpoint::...includes_files` wrote to an undeclared location. Now declares it first.
    (counts as 5 fixes covering the 6 — items 2 and 3 are the same new field.)
  - 2 pre-existing order/state flakes, NOT this branch (pass in isolation): `test_solution_connection_refs_e2e::test_export_scrubs_connection_template_and_carries_no_secret`, `test_manifest_scope_aware::test_no_solution_id_is_repo_scoped`.
- Step 5 backend `./test.sh all` run 2 (post-fix) → 3 failed / 6695 passed:
  - 1 NEW real, caused by the run-1 creator-list fix being too broad: `test_list_skips_fallback_tier_when_directory_list_policy_denies` — enumerating + per-file filtering on EVERY tier leaked solution org/global fallback files through their own per-file list grants. Fixed in `4367a0e3b`: gate per-file filtering to the PRIMARY tier (index 0); fallback tiers stay directory-gated. Both conflicting tests now pass together (14/14 in the cascade+policies suites).
  - 2 pre-existing order-flakes (UNCHANGED on this branch, exist on main, pass in isolation): `test_solution_connection_refs_e2e::test_export_scrubs_connection_template_and_carries_no_secret` and `test_manifest_scope_aware::test_no_solution_id_is_repo_scoped`. Same files as run 1. Pre-existing test-isolation pollution in main's manifest/solution tests (see flake-clusters memory), NOT introduced here. Not fixed — out of scope for storage-scope; documented so they aren't mistaken for a regression at PR time.
- Step 5 backend `./test.sh all` run 3 (post-refined-fix) → 1 failed / 6697 passed. The only failure is the pre-existing order-flake `test_manifest_scope_aware::test_no_solution_id_is_repo_scoped` (passes 2/2 in isolation immediately after; unchanged on branch; exists on main; the other run-2 flake `test_solution_connection_refs_e2e` passed this run, confirming intermittency). Backend suite is effectively GREEN — the storage-scope work introduced no failing tests; 7 net-new passing.
- Step 5 client e2e: the branch's specs are `*.admin.spec.ts` (not in CI per Files SDK history); Playwright cannot drive the netbird debug stack (HMR ws hang). Would require `BIFROST_FORCE_PORT=1 ./debug.sh up`. NOT run — pending Jack's call (heavy: full port-mode stack boot; specs aren't gating CI).

## Final state (Task 12 complete)

All Task 12 steps done except client e2e (deferred — see above):
- Step 1 hardcode smell: clean.
- Step 2 capstone E2E: passed.
- Step 3 backend pyright (0 errors) + ruff (clean).
- Step 4 frontend tsc (0) + lint (0 errors); fixed 2 real strict-TS errors.
- Step 5 backend full suite: 6697 passed, 1 pre-existing order-flake (not this branch).
- Step 5 client unit: 1554 passed, 1 load-flake (passes isolated).
- Step 6 fixups committed.

Fix commits this session:
- `ae07974f8` test(solution-storage): cover full install and streaming file payloads (Task 11)
- `2cd415da3` fix(files): satisfy strict TS for Blob part and test action typing
- `6620abd34` fix(files): per-file creator list + close ManifestFiles harness gaps
- `4367a0e3b` fix(files): gate list per-file filtering to primary tier only

## File Structure

| File | Responsibility | Action |
|------|----------------|--------|
| `api/bifrost/files.py` | Python files SDK REST client | Append solution query on all file operations |
| `api/bifrost/tables.py` | Python tables SDK auto-create behavior | Do not auto-create tables from solution context |
| `api/bifrost/manifest.py` | Split manifest model and parser/serializer | Add `.bifrost/files.yaml` declaration support |
| `api/src/models/orm/solution_file_location.py` | Install-owned file-location declarations | Create |
| `api/src/models/orm/__init__.py` | ORM model export | Register declaration model |
| `api/alembic/versions/20260624_solution_file_locations.py` | DB schema | Create declaration table |
| `api/src/services/solution_scope.py` | Shared solution helpers | Create helper for solution row, declarations, tier candidates |
| `api/src/routers/files.py` | File runtime resolution | Remove hardcode, enforce declarations, tiered read resolution |
| `api/src/services/file_backend.py` | S3 file backend | Add read/list/exists by explicit S3 key or tier candidates if needed |
| `api/src/services/file_policy_service.py` | File policy and metadata lookup | Add tier-aware metadata/policy helpers |
| `api/src/routers/tables.py` | Table runtime resolution | Gate fallback and block solution auto-create |
| `api/src/services/manifest_generator.py` | DB to manifest | Emit declared file locations |
| `api/src/services/manifest_import.py` | Manifest to DB | Import declared file locations |
| `api/src/services/solutions/deploy.py` | Solution install full replace | Upsert/reconcile file-location declarations |
| `api/src/services/solutions/capture.py` | Solution export bundle construction | Carry declarations; stop loading file bytes into `SolutionBundle` |
| `api/src/services/solutions/export.py` | Zip/export writer | Stream file payloads into zip entries |
| `api/src/services/solutions/zip_install.py` | Zip/import reader | Stream file payloads from zip to S3 |
| `api/src/services/solutions/secrets_blob.py` | Encrypted config/table sensitive tier | Remove solution file bytes from JSON/base64 payload |
| `api/src/services/solution_files.py` | Solution file metadata and payload helpers | Add streaming read/write helpers |
| `api/src/services/file_storage/s3_client.py` | Low-level S3 operations | Add chunked read/write/copy helpers |
| `client/src/lib/app-sdk/files.ts` | Web app SDK file calls | Confirm `X-Bifrost-App` path and add tests |
| Focused backend and client test files named in each task | Verification | Add focused tests per task |

## Manifest Shape

Use `.bifrost/files.yaml` for declared solution runtime file locations:

```yaml
locations:
  - finance
  - reports
```

Implementation details:

- Add `ManifestFiles` in `api/bifrost/manifest.py`:

```python
class ManifestFiles(BaseModel):
    locations: list[str] = Field(default_factory=list)

    @field_validator("locations")
    @classmethod
    def normalize_locations(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value if item and item.strip()]
        if len(set(normalized)) != len(normalized):
            raise ValueError("files.locations must not contain duplicates")
        blocked = {"workspace", "temp", "uploads", "_repo", "_tmp", "_apps"}
        bad = sorted(set(normalized) & blocked)
        if bad:
            raise ValueError(f"reserved file locations cannot be declared: {', '.join(bad)}")
        return sorted(normalized)
```

- Add `files: ManifestFiles = Field(default_factory=ManifestFiles)` to `Manifest`.
- Add `MANIFEST_FILES["files"] = "files.yaml"`.
- Special-case split manifest serialization/parsing so `.bifrost/files.yaml` writes top-level `locations:` rather than nesting under `files:`.
- Keep legacy single-file serialization as `files: { locations: ["finance"] }`.
- Keep `solution_files` as the full-export data index. It is not a declaration list.

## Task 0: Preflight And Baseline Facts

**Files:**
- Read only: plan, spec, git state, relevant router/SDK files

- [x] **Step 1: Confirm branch/worktree**

Run:

```bash
pwd
git status --short
git log --oneline --decorate -3
```

Expected:
- `pwd` is `/home/jack/GitHub/bifrost/.claude/worktrees/files-sdk-policies`
- worktree has no unrelated edits before starting
- `HEAD` is on `codex/files-sdk-policies`

- [x] **Step 2: Confirm known bad seams**

Run:

```bash
rg -n 'location == "solutions"|location=="solutions"' api/src/routers/files.py
rg -n '_scope_query|solution=' api/bifrost/tables.py api/bifrost/files.py
rg -n 'content_b64|solution_files|content_bytes|read_uploaded_file' api/src/services/solutions api/src/services/solution_files.py api/src/services/file_storage
```

Expected:
- `files.py` router still has `location == "solutions"` branches before Task 2.
- tables SDK has `_scope_query`; files SDK does not before Task 1.
- file export/import still has in-memory/base64 file payload paths before Task 9.

- [x] **Step 3: Mark Task 0 complete**

No commit unless this task discovers and documents a plan correction.

## Task 1: Files SDK Appends `?solution=`

**Files:**
- Modify: `api/bifrost/files.py`
- Test: `api/tests/unit/test_files_sdk_solution_scope.py`

- [x] **Step 1: Write failing tests**

Create `api/tests/unit/test_files_sdk_solution_scope.py` with tests that patch `get_client()` and assert every SDK method uses a URL containing `solution=<id>` when `ExecutionContext.solution_id` is set, and no solution query otherwise.

Required cases:

```python
@pytest.mark.parametrize(
    ("method_name", "args", "kwargs"),
    [
        ("read", ("x.txt",), {"location": "finance"}),
        ("read_bytes", ("x.bin",), {"location": "finance"}),
        ("write", ("x.txt", "hi"), {"location": "finance"}),
        ("write_bytes", ("x.bin", b"hi"), {"location": "finance"}),
        ("list", ("",), {"location": "finance"}),
        ("delete", ("x.txt",), {"location": "finance"}),
        ("exists", ("x.txt",), {"location": "finance"}),
        ("get_signed_url", ("x.txt",), {"location": "finance", "method": "GET"}),
    ],
)
async def test_file_sdk_appends_solution_query(method_name, args, kwargs, monkeypatch):
    captured_urls: list[str] = []

    class FakeResponse:
        status_code = 200

        def json(self):
            if method_name == "list":
                return {"files": []}
            if method_name == "exists":
                return {"exists": True}
            if method_name == "get_signed_url":
                return {"url": "https://example.invalid/signed", "path": "finance/abc/x.txt"}
            return {"content": ""}

        def raise_for_status(self):
            return None

    class FakeClient:
        async def post(self, url, json=None):
            captured_urls.append(url)
            return FakeResponse()

    set_execution_context(ExecutionContext(solution_id="11111111-1111-1111-1111-111111111111"))
    monkeypatch.setattr(files_sdk, "get_client", lambda: FakeClient())
    await getattr(files_sdk.files, method_name)(*args, **kwargs)
    assert captured_urls
    assert "solution=11111111-1111-1111-1111-111111111111" in captured_urls[0]
```

- [x] **Step 2: Run failing test**

Run:

```bash
./test.sh tests/unit/test_files_sdk_solution_scope.py -v
```

Expected: FAIL because URLs do not include `?solution=`.

- [x] **Step 3: Implement**

Mirror `api/bifrost/tables.py::_scope_query`. Add a helper in `api/bifrost/files.py`:

```python
from urllib.parse import urlencode
from ._context import _execution_context

def _current_context():
    return _execution_context.get()

def _solution_query() -> str:
    ctx = _current_context()
    solution_id = getattr(ctx, "solution_id", None) if ctx is not None else None
    return f"?{urlencode({'solution': str(solution_id)})}" if solution_id else ""
```

Append `_solution_query()` to every file endpoint URL.

- [x] **Step 4: Run passing test**

Run:

```bash
./test.sh tests/unit/test_files_sdk_solution_scope.py -v
```

Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add api/bifrost/files.py api/tests/unit/test_files_sdk_solution_scope.py
git commit -m "feat(solution-files): pass solution context from files SDK"
```

## Task 2: Server Scopes Any File Location By Solution Context

**Files:**
- Modify: `api/src/routers/files.py`
- Test: `api/tests/e2e/platform/test_solution_file_scope.py`

- [x] **Step 1: Write failing tests**

Add tests proving a solution request to `location="finance"` writes metadata with `FileMetadata.solution_id == install_id`, uses S3 key `finance/{install_id}/q1.csv`, and does not honor a conflicting request body `scope`.

- [x] **Step 2: Run failing tests**

```bash
./test.sh tests/e2e/platform/test_solution_file_scope.py::test_solution_scopes_arbitrary_location_by_install_id -v
```

Expected: FAIL because only `location="solutions"` uses `ctx.solution_id`.

- [x] **Step 3: Implement**

Update helpers in `api/src/routers/files.py`:

```python
def _resolve_effective_scope(ctx: Context, location: str, requested_scope: str | None) -> str | None:
    if ctx.solution_id is not None:
        return str(ctx.solution_id)
    return _storage_scope(_file_org_id(ctx, location, requested_scope))

def _ctx_solution_id(ctx: Context, location: str) -> UUID | None:
    if ctx.solution_id is None:
        return None
    try:
        return UUID(str(ctx.solution_id))
    except (ValueError, AttributeError, TypeError):
        return None
```

Replace all `location == "solutions"` policy/scope branches with `solution_id is not None`.

- [x] **Step 4: Run passing tests**

```bash
./test.sh tests/e2e/platform/test_solution_file_scope.py -v
```

Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add api/src/routers/files.py api/tests/e2e/platform/test_solution_file_scope.py
git commit -m "feat(solution-files): scope all locations by solution context"
```

## Task 3: Declare File Locations In `.bifrost/files.yaml`

**Files:**
- Create: `api/src/models/orm/solution_file_location.py`
- Create: `api/alembic/versions/20260624_solution_file_locations.py`
- Modify: `api/src/models/orm/__init__.py`
- Modify: `api/bifrost/manifest.py`
- Modify: `api/src/services/manifest_generator.py`
- Modify: `api/src/services/manifest_import.py`
- Modify: `api/src/services/solutions/deploy.py`
- Test: `api/tests/unit/test_manifest.py`
- Test: `api/tests/unit/test_solution_file_locations.py`

- [x] **Step 1: Write failing tests**

Tests must cover:
- `write_manifest_to_dir()` emits `.bifrost/files.yaml` as top-level `locations:`.
- `read_manifest_from_dir()` reads that shape into `manifest.files.locations`.
- Deploy registers locations into the DB.
- Redeploy reconciles removed locations only when no solution-owned file metadata still uses them; otherwise deploy raises a conflict naming the location.

- [x] **Step 2: Run failing tests**

```bash
./test.sh tests/unit/test_manifest.py::TestManifestFilesDeclaration tests/unit/test_solution_file_locations.py -v
```

Expected: FAIL because declarations do not exist.

- [x] **Step 3: Implement DB declaration model**

Create ORM model:

```python
class SolutionFileLocation(Base):
    __tablename__ = "solution_file_locations"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    solution_id: Mapped[UUID] = mapped_column(
        ForeignKey("solutions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    location: Mapped[str] = mapped_column(String(255), nullable=False)

    __table_args__ = (
        UniqueConstraint("solution_id", "location", name="uq_solution_file_locations_solution_location"),
    )
```

Migration creates the table and index. Do not store `organization_id`; the install scope comes from `solutions.organization_id`.

- [x] **Step 4: Implement manifest shape**

Add `ManifestFiles`, `Manifest.files`, `MANIFEST_FILES["files"]`, and custom split parse/serialize for `files.yaml` top-level `locations:`.

- [x] **Step 5: Implement deploy/import registration**

In deploy, full-replace declarations for the install:
- Insert declared locations.
- Delete stale declarations only if no `FileMetadata` row exists for `(solution_id, stale_location)`.
- If rows exist, raise `SolutionDeployConflict("cannot remove file location 'finance' while files still exist")`, substituting the actual stale location name.

- [x] **Step 6: Run passing tests**

```bash
./test.sh tests/unit/test_manifest.py::TestManifestFilesDeclaration tests/unit/test_solution_file_locations.py -v
```

Expected: PASS.

- [x] **Step 7: Commit**

```bash
git add api/bifrost/manifest.py api/src/models/orm/__init__.py api/src/models/orm/solution_file_location.py api/alembic/versions/*solution_file_locations.py api/src/services/manifest_generator.py api/src/services/manifest_import.py api/src/services/solutions/deploy.py api/tests/unit/test_manifest.py api/tests/unit/test_solution_file_locations.py
git commit -m "feat(solution-storage): declare solution file locations"
```

## Task 4: Enforce Declared-Only Solution Writes

**Files:**
- Create or modify: `api/src/services/solution_scope.py`
- Modify: `api/src/routers/files.py`
- Modify: `api/src/routers/tables.py`
- Modify: `api/bifrost/tables.py`
- Test: `api/tests/e2e/platform/test_solution_declared_only.py`
- Test: `api/tests/unit/test_tables_sdk_solution_scope.py`

- [x] **Step 1: Write failing tests**

Required tests:
- Solution write to declared file location succeeds.
- Solution write to undeclared file location returns 404 and creates no metadata/S3 object.
- Non-solution write to a new custom location still works.
- Solution table insert into declared table succeeds.
- Solution table insert/upsert into undeclared table returns 404 and does not auto-create an org/global table.
- Non-solution table insert still auto-creates on first write.

- [x] **Step 2: Run failing tests**

```bash
./test.sh tests/e2e/platform/test_solution_declared_only.py tests/unit/test_tables_sdk_solution_scope.py -v
```

Expected: FAIL.

- [x] **Step 3: Implement declaration helpers**

Create `api/src/services/solution_scope.py`:

```python
async def get_active_solution(db: AsyncSession, solution_id: UUID) -> Solution | None:
    row = await db.get(Solution, solution_id)
    if row is None or row.status != "active":
        return None
    return row

async def solution_allows_global(db: AsyncSession, solution_id: UUID) -> bool:
    row = await db.get(Solution, solution_id)
    return bool(row and row.global_repo_access)

async def solution_declares_file_location(db: AsyncSession, solution_id: UUID, location: str) -> bool:
    result = await db.execute(
        select(SolutionFileLocation.id).where(
            SolutionFileLocation.solution_id == solution_id,
            SolutionFileLocation.location == location,
        )
    )
    return result.scalar_one_or_none() is not None

async def solution_declares_table_name(db: AsyncSession, solution_id: UUID, name: str) -> bool:
    result = await db.execute(
        select(Table.id).where(Table.solution_id == solution_id, Table.name == name)
    )
    return result.scalar_one_or_none() is not None
```

- [x] **Step 4: Enforce files**

Before file write, signed PUT creation, and signed upload completion in solution context:
- Require the location to be declared.
- Return 404 for undeclared locations.
- Do not touch S3 or metadata.

- [x] **Step 5: Enforce tables**

In solution context:
- `api/bifrost/tables.py` must not call `_ensure_table_exists()` after a 404.
- Server create path must not create a table from `?solution=` auto-create.
- Name resolution for undeclared table returns 404.

- [x] **Step 6: Run passing tests**

```bash
./test.sh tests/e2e/platform/test_solution_declared_only.py tests/unit/test_tables_sdk_solution_scope.py -v
```

Expected: PASS.

- [x] **Step 7: Commit**

```bash
git add api/src/services/solution_scope.py api/src/routers/files.py api/src/routers/tables.py api/bifrost/tables.py api/tests/e2e/platform/test_solution_declared_only.py api/tests/unit/test_tables_sdk_solution_scope.py
git commit -m "feat(solution-storage): enforce declared-only solution writes"
```

## Task 5: Files Tiered Read/List/Exists With `global_repo_access`

**Files:**
- Modify: `api/src/services/solution_scope.py`
- Modify: `api/src/routers/files.py`
- Modify: `api/src/services/file_backend.py`
- Modify: `api/src/services/file_policy_service.py`
- Test: `api/tests/e2e/platform/test_solution_file_cascade_gated.py`

- [x] **Step 1: Write failing tests**

Required matrix:
- `global_repo_access=false`: solution reads own file, cannot read org/global fallback.
- `global_repo_access=true`: solution reads own first, then org, then global.
- Own solution file wins over org/global same path.
- `exists` follows the same tier result.
- `list` returns union of allowed tiers when open, solution-only when sealed, without duplicate paths.

- [x] **Step 2: Run failing tests**

```bash
./test.sh tests/e2e/platform/test_solution_file_cascade_gated.py -v
```

Expected: FAIL.

- [x] **Step 3: Implement tier candidates**

In `solution_scope.py`:

```python
@dataclass(frozen=True)
class FileTier:
    name: Literal["solution", "org", "global"]
    scope: str
    organization_id: UUID | None
    solution_id: UUID | None

async def file_read_tiers(db: AsyncSession, ctx: Context, location: str, requested_scope: str | None) -> list[FileTier]:
    solution_id = UUID(str(ctx.solution_id)) if ctx.solution_id else None
    if solution_id is None:
        org_id = _file_org_id(ctx, location, requested_scope)
        return [FileTier("global" if org_id is None else "org", _storage_scope(org_id), org_id, None)]
    solution = await db.get(Solution, solution_id)
    if solution is None:
        return []
    tiers = [FileTier("solution", str(solution_id), solution.organization_id, solution_id)]
    if solution.global_repo_access:
        if solution.organization_id is not None:
            tiers.append(FileTier("org", str(solution.organization_id), solution.organization_id, None))
        tiers.append(FileTier("global", "global", None, None))
    return tiers
```

Rules:
- No solution context: current single org/global behavior.
- Solution context: first tier is solution.
- If `global_repo_access` is true and the solution has `organization_id`, append org tier.
- If `global_repo_access` is true, append global tier.
- If false, append no fallback tiers.

- [x] **Step 4: Implement tiered read/exists/list**

Do not call `backend.read()` once for a solution fallback read. For each tier:
- Authorize policy using that tier.
- Resolve S3 key with that tier scope.
- Try S3.
- Return first successful read/exists.
- For list, merge tiers in priority order and de-duplicate paths.

- [x] **Step 5: Run passing tests**

```bash
./test.sh tests/e2e/platform/test_solution_file_cascade_gated.py -v
```

Expected: PASS.

- [x] **Step 6: Commit**

```bash
git add api/src/services/solution_scope.py api/src/routers/files.py api/src/services/file_backend.py api/src/services/file_policy_service.py api/tests/e2e/platform/test_solution_file_cascade_gated.py
git commit -m "feat(solution-files): resolve reads by solution data tier"
```

## Task 6: Tables Fallback And Auto-Create Gates

**Files:**
- Modify: `api/src/services/solution_scope.py`
- Modify: `api/src/routers/tables.py`
- Modify: `api/bifrost/tables.py`
- Test: `api/tests/e2e/platform/test_table_solution_cascade_gated.py`

- [x] **Step 1: Write failing tests**

Required tests:
- Open solution resolves own table first, then org, then global.
- Sealed solution resolves own table only.
- Undeclared table name in solution context returns 404 even if auto-create would previously have run.
- Non-solution table auto-create still works.

- [x] **Step 2: Run failing tests**

```bash
./test.sh tests/e2e/platform/test_table_solution_cascade_gated.py -v
```

Expected: FAIL.

- [x] **Step 3: Implement**

Update `_resolve_solution_table_by_name()` and related table get/create paths:
- Parse `ctx.solution_id` or `ctx.app_id -> Application.solution_id`.
- Require the table name to be declared by the solution when resolving a solution-owned table.
- After own miss, call org/global fallback only when `await solution_allows_global(ctx.db, solution_id)` is true.
- Keep non-solution `OrgScopedRepository` behavior unchanged.

- [x] **Step 4: Run passing tests**

```bash
./test.sh tests/e2e/platform/test_table_solution_cascade_gated.py -v
```

Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add api/src/services/solution_scope.py api/src/routers/tables.py api/bifrost/tables.py api/tests/e2e/platform/test_table_solution_cascade_gated.py
git commit -m "feat(solution-tables): gate fallback and auto-create by solution scope"
```

## Task 7: Tier-Correct Policies

**Files:**
- Modify: `api/src/services/file_policy_service.py`
- Modify: `api/src/routers/files.py`
- Test: `api/tests/e2e/platform/test_solution_policy_solution_only.py`
- Test: `api/tests/unit/services/test_file_policy_service.py`

- [x] **Step 1: Write failing tests**

Required tests:
- A solution file policy governs only the solution tier.
- Org fallback data is governed by org policy, not solution policy.
- Global fallback data is governed by global policy, not solution policy.
- Non-solution org/global policy lookup never considers `solution_id IS NOT NULL` rows.
- Signed GET resolves to the tier that actually serves fallback bytes.
- Policy listing and policy-test debug output do not leak or misreport solution rows.
- Workspace metadata listings honor revoked admin-bypass policies.

- [x] **Step 2: Run failing tests**

```bash
./test.sh tests/e2e/platform/test_solution_policy_solution_only.py tests/unit/services/test_file_policy_service.py -v
```

Expected: FAIL or PASS-and-harden depending on current filters.

- [x] **Step 3: Implement**

Policy lookup must be called per `FileTier`:
- solution tier: pass `solution_id=<install_id>` and install org id.
- org tier: pass `solution_id=None` and org id.
- global tier: pass `solution_id=None` and organization id `None`.

Keep `FilePolicy.solution_id.is_(None)` filters on all org/global lookup arms.

- [x] **Step 4: Run passing tests**

```bash
./test.sh tests/e2e/platform/test_solution_policy_solution_only.py tests/unit/services/test_file_policy_service.py -v
```

Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add api/src/services/file_policy_service.py api/src/routers/files.py api/tests/e2e/platform/test_solution_policy_solution_only.py api/tests/unit/services/test_file_policy_service.py
git commit -m "feat(solution-files): evaluate policies against resolved data tier"
```

Completed:
- `9a1d3f9c2 feat(solution-files): evaluate policies against resolved data tier`
- `2fa8c4bfe fix(solution-files): scope policies to resolved tiers`

Verified:
- `cd api && ruff check src/services/file_policy_service.py src/routers/files.py tests/e2e/platform/test_solution_policy_solution_only.py tests/unit/services/test_file_policy_service.py tests/e2e/platform/test_cli_push_pull.py tests/unit/routers/test_files_signed_url.py`
- `./test.sh tests/unit/routers/test_files_signed_url.py -v` (17 passed)
- `./test.sh tests/e2e/platform/test_solution_policy_solution_only.py tests/unit/services/test_file_policy_service.py tests/e2e/platform/test_solution_file_cascade_gated.py tests/e2e/platform/test_cli_push_pull.py::test_list_with_metadata_filters_denied_platform_admin_paths -v` (26 passed)

## Task 8: Web SDK/App File Calls Honor Solution Scope

**Files:**
- Modify: `client/src/lib/app-sdk/files.test.ts`
- Modify: `client/src/lib/app-sdk/provider.test.tsx`
- Modify: `api/src/routers/files.py`
- Test: `api/tests/e2e/platform/test_solution_file_scope.py`

- [x] **Step 1: Write failing tests**

Add/adjust tests proving app SDK file calls carry `X-Bifrost-App`, server maps that to `Application.solution_id`, and file scope resolves to the install id for declared locations.

- [x] **Step 2: Run failing tests**

```bash
./test.sh client unit -- client/src/lib/app-sdk/files.test.ts
./test.sh tests/e2e/platform/test_solution_file_scope.py::test_solution_app_files_resolve_to_install_scope -v
```

Expected: FAIL if app path does not reach solution tier.

- [x] **Step 3: Implement**

Confirm browser auth client includes `X-Bifrost-App` for app SDK calls. If missing, add it in the existing app SDK request layer rather than per-method ad hoc headers.

- [x] **Step 4: Run passing tests**

```bash
./test.sh client unit -- client/src/lib/app-sdk/files.test.ts
./test.sh tests/e2e/platform/test_solution_file_scope.py::test_solution_app_files_resolve_to_install_scope -v
```

Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add client/src/lib/app-sdk/files.ts client/src/lib/app-sdk/files.test.ts api/tests/e2e/platform/test_solution_file_scope.py
git commit -m "feat(solution-files): app SDK resolves files in install scope"
```

Completed:
- `ef31db6dd test(solution-files): prove app file calls use install scope`
- `82776535f fix(solution-files): harden app file scope checks`

Verified:
- `./test.sh client unit -- src/lib/app-sdk/files.test.ts` (13 passed)
- `./test.sh tests/e2e/platform/test_solution_file_scope.py::test_solution_app_files_resolve_to_install_scope -v` (1 passed)
- `cd api && ruff check tests/e2e/platform/test_solution_file_scope.py`
- `cd api && ruff check src/routers/files.py tests/e2e/platform/test_solution_file_scope.py`
- `./test.sh client unit -- src/lib/app-sdk/files.test.ts src/lib/app-sdk/provider.test.tsx` (21 passed)
- `./test.sh tests/e2e/platform/test_solution_file_scope.py::test_solution_app_files_resolve_to_install_scope tests/e2e/platform/test_solution_file_scope.py::test_solution_app_read_requires_declared_file_location -v` (2 passed)
- `./test.sh tests/e2e/platform/test_solution_file_cascade_gated.py tests/e2e/platform/test_solution_policy_solution_only.py tests/unit/routers/test_files_signed_url.py -v` (27 passed)

Notes:
- A broader `test_solution_file_scope.py` run still has legacy failures where older tests use undeclared `location="solutions"`/`reports`; that cleanup is intentionally tracked in Task 10.

## Task 9: Streaming Solution File Payload Import/Export

**Files:**
- Modify: `api/src/services/file_storage/s3_client.py`
- Modify: `api/src/services/file_storage/service.py`
- Modify: `api/src/services/solution_files.py`
- Modify: `api/src/services/solutions/capture.py`
- Modify: `api/src/services/solutions/export.py`
- Modify: `api/src/services/solutions/zip_install.py`
- Modify: `api/src/services/solutions/secrets_blob.py`
- Test: `api/tests/unit/test_solution_file_capture.py`
- Test: `api/tests/unit/test_solution_export.py`
- Test: `api/tests/e2e/platform/test_solution_export_files.py`
- Test: `api/tests/e2e/platform/test_solution_files_e2e.py`

- [x] **Step 1: Write failing tests**

Tests must prove:
- Exporting solution files does not call `read_uploaded_file()` for payload bytes.
- `SolutionContent.solution_files` no longer contains `content_b64`.
- Export streams payload entries under `.bifrost/file-payloads/<sha256-or-id>`.
- Import streams payload entries to S3 and upserts metadata.
- Peak in-process payload chunk size remains bounded. Use a fake 64 MiB stream and assert no full-object read method is invoked.

- [x] **Step 2: Run failing tests**

```bash
./test.sh tests/unit/test_solution_export_streaming.py tests/e2e/platform/test_solution_export_files.py tests/e2e/platform/test_solution_import_data.py -v
```

Expected: FAIL because current code reads bytes into memory/base64.

- [x] **Step 3: Implement S3 chunk helpers**

Add helpers similar to:

```python
async def iter_object_chunks(self, key: str, chunk_size: int = 8 * 1024 * 1024) -> AsyncIterator[bytes]:
    async with self.get_client() as s3:
        response = await s3.get_object(Bucket=self.settings.s3_bucket, Key=key)
        async for chunk in response["Body"].iter_chunks(chunk_size):
            if chunk:
                yield chunk

async def put_object_from_chunks(self, key: str, chunks: AsyncIterator[bytes], content_type: str) -> tuple[int, str]:
    # multipart upload when size is unknown; abort on error; return (size, sha256)
```

Use multipart upload for streamed writes. Do not concatenate chunks.

- [x] **Step 4: Implement export payload entries**

Keep `.bifrost/solution-files.yaml` or `manifest.solution_files` as the metadata index. Add payload reference fields if needed:

```yaml
solution_files:
  - location: finance
    path: q1.csv
    sha256: 64f1b8f58b8df7c7a0fe5d8a2054b89f527ef0a2d6f6f3ef2b5f2f3f6c0f2a11
    size: 123
    payload: .bifrost/file-payloads/<stable-id>.bin
```

Write payload entries with `zipfile.ZipFile.open(payload, "w")` and stream chunks from S3. Do not put file bytes in `SolutionBundle` or `secrets.enc`.

- [x] **Step 5: Implement import payload streaming**

Read `ZipFile.open(payload, "r")` in fixed chunks and stream to S3 multipart upload, then write metadata with `solution_id`, `location`, `path`, `sha256`, and size.

- [x] **Step 6: Decide encrypted large-file behavior**

Decision: keep `secrets.enc` for config values, table rows, and the encrypted file payload index. File bytes travel as separate `.bifrost/file-payloads/*.bin.enc` members encrypted per chunk with the export password. Do not reintroduce one giant `content_b64` / Fernet JSON blob.

- [x] **Step 7: Run passing tests**

```bash
./test.sh tests/unit/test_solution_export_streaming.py tests/e2e/platform/test_solution_export_files.py tests/e2e/platform/test_solution_import_data.py -v
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add api/src/services/file_storage/s3_client.py api/src/services/file_storage/service.py api/src/services/solution_files.py api/src/services/solutions/capture.py api/src/services/solutions/export.py api/src/services/solutions/zip_install.py api/src/services/solutions/secrets_blob.py api/tests/unit/test_solution_export_streaming.py api/tests/e2e/platform/test_solution_export_files.py api/tests/e2e/platform/test_solution_import_data.py
git commit -m "feat(solution-export): stream solution file payloads"
```

Implemented in current working tree:
- Added S3 chunk read/write helpers and solution-file streaming write/read wrappers.
- Changed capture to keep solution file entries metadata-only.
- Changed full export to write encrypted per-file payload members and a small encrypted file index with `payload` refs, never `content_b64`.
- Changed full import to decrypt payload members chunk-by-chunk, validate sha256/size, and stream to S3.
- Changed solution zip preview/install/deploy endpoints to spool uploads to temp files instead of `await file.read()`.
- Changed export endpoint to return a temp-file `FileResponse` instead of `Response(content=data)`.
- Added path-copy helpers for stored source artifacts and streaming overlay of live runtime payloads.
- Fixed hard-delete of installs with declared file locations by avoiding ORM delete-orphan handling for `Solution.file_locations`.

Verified:
- `cd api && ruff check src/services/file_storage/s3_client.py src/services/file_storage/service.py src/services/solution_files.py src/services/solutions/file_payloads.py src/services/solutions/export.py src/services/solutions/zip_install.py src/services/solutions/source_artifact.py src/services/solutions/secrets_blob.py src/routers/solutions.py tests/unit/test_solution_file_capture.py tests/unit/test_solution_export.py tests/e2e/platform/test_solution_export_files.py`
- `./test.sh tests/unit/test_solution_file_capture.py tests/unit/test_solution_export.py tests/unit/test_solution_zip_install.py -v` (22 passed)
- `./test.sh tests/e2e/platform/test_solution_export_files.py -v` (3 passed)
- `./test.sh tests/e2e/platform/test_solution_files_e2e.py::TestSolutionInactiveLifecycleCapstone::test_arc_1_deploy_through_uninstall tests/e2e/platform/test_solution_files_e2e.py::TestSolutionInactiveLifecycleCapstone::test_arc_2_reactivate_export_harddelete -v` (2 passed)
- `cd api && pyright` (0 errors)
- `./test.sh tests/unit/test_solution_file_capture.py tests/unit/test_solution_export.py tests/unit/test_solution_zip_install.py tests/e2e/platform/test_solution_export_files.py tests/e2e/platform/test_solution_files_e2e.py::TestSolutionInactiveLifecycleCapstone::test_arc_1_deploy_through_uninstall tests/e2e/platform/test_solution_files_e2e.py::TestSolutionInactiveLifecycleCapstone::test_arc_2_reactivate_export_harddelete -v` (28 passed)

## Task 10: Re-Point Capstone And `location="solutions"` Tests

**Files:**
- Modify: `api/tests/e2e/platform/test_solution_files_e2e.py`
- Modify: `api/tests/e2e/platform/test_solution_file_scope.py`
- Modify: any test found by grep that uses `location="solutions"` as a scope shortcut

- [x] **Step 1: Find tests**

```bash
rg -n 'location.*solutions|"solutions"' api/tests client/src -g '*.py' -g '*.ts' -g '*.tsx'
```

- [x] **Step 2: Update tests**

Use declared location `finance` or `reports` for scope-model tests. Keep a literal `"solutions"` location test only if it proves it is treated as an ordinary declared custom location.

- [x] **Step 3: Run tests**

```bash
./test.sh tests/e2e/platform/test_solution_files_e2e.py tests/e2e/platform/test_solution_file_scope.py -v
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add api/tests/e2e/platform/test_solution_files_e2e.py api/tests/e2e/platform/test_solution_file_scope.py
git commit -m "test(solution-storage): exercise declared locations instead of solutions hardcode"
```

Implemented in current working tree:
- Updated file-scope E2E tests to seed `SolutionFileLocation` declarations before exercising solution file writes/presign paths.
- Updated CLI solution-file E2E tests to declare the `solutions` location before REST setup writes while keeping CLI invocations in synchronous tests.
- Updated capstone lifecycle setup to avoid pre-deploy file writes and preserve declarations through the test reinstall ZIP.

Verified:
- `cd api && ruff check tests/e2e/platform/test_solution_file_scope.py`
- `./test.sh tests/e2e/platform/test_solution_file_scope.py -v` (11 passed)
- `cd api && ruff check tests/e2e/platform/test_cli_solution_files.py`
- `./test.sh tests/e2e/platform/test_cli_solution_files.py -v` (5 passed)
- `./test.sh tests/e2e/platform/test_solution_files_e2e.py::TestSolutionInactiveLifecycleCapstone::test_arc_1_deploy_through_uninstall tests/e2e/platform/test_solution_files_e2e.py::TestSolutionInactiveLifecycleCapstone::test_arc_2_reactivate_export_harddelete -v` (2 passed)

## Task 11: Full Deployed Solution E2E And Large-File Memory Tests

**Files:**
- Create: `api/tests/e2e/platform/test_solution_storage_full_e2e.py`
- Create: `api/tests/unit/test_solution_large_file_memory.py`
- Modify as needed: solution test fixtures

- [x] **Step 1: Write full deployed solution E2E**

The test must:
- Build/install a solution declaring `files.yaml` location `finance` and a table.
- Run a real solution workflow using Python SDK `files.write/read` and `tables.upsert/query`.
- Verify S3 key `finance/{solution_id}/q1.csv`.
- Export full solution with file payloads.
- Install/import into a second solution and verify file bytes and metadata round-trip.

Note: sealed/open fallback remains covered by the focused Task 5/6 file and table
scope tests. This capstone covers the integrated deploy -> workflow SDK ->
full export -> install path.

- [x] **Step 2: Write large-file memory regression**

Use fake streaming readers/writers, not a real 30 GB fixture. Simulate at least 128 MiB with 8 MiB chunks and assert:
- no full-object read function is called;
- no `content_b64` appears in solution payload data;
- chunk helper sees bounded chunk size;
- metadata hash/size is correct.

- [x] **Step 3: Run focused tests**

```bash
./test.sh tests/e2e/platform/test_solution_storage_full_e2e.py tests/unit/test_solution_large_file_memory.py -v
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add api/tests/e2e/platform/test_solution_storage_full_e2e.py api/tests/unit/test_solution_large_file_memory.py
git commit -m "test(solution-storage): cover full install and streaming file payloads"
```

Status:
- Added `api/tests/unit/test_solution_large_file_memory.py`, simulating a 128 MiB solution file in 8 MiB chunks and asserting bounded chunk handling, payload refs, no `content_b64`, and correct hash/size metadata.
- Added `api/tests/e2e/platform/test_solution_storage_full_e2e.py`, deploying a solution with `finance` file location, table, and workflow; executing real SDK file/table calls; exporting a full backup; installing into a second org; and verifying file bytes, metadata, and table rows.
- Fixed the E2E deploy JSON-to-ZIP shim in `api/tests/e2e/conftest.py` so legacy JSON deploy bodies carry `file_locations` into the production ZIP path.

Verification:
- `cd api && ruff check tests/e2e/conftest.py tests/e2e/platform/test_solution_storage_full_e2e.py tests/unit/test_solution_large_file_memory.py`
- `./test.sh tests/unit/test_solution_large_file_memory.py tests/e2e/platform/test_solution_storage_full_e2e.py -v` (2 passed)

## Task 12: Final Verification Sweep

- [ ] **Step 1: Hardcode smell check**

```bash
rg -n 'location == "solutions"|location=="solutions"' api/src
```

Expected: no matches.

- [ ] **Step 2: SDK reachability proof**

```bash
./test.sh tests/e2e/platform/test_solution_storage_full_e2e.py -v
```

Expected: PASS.

- [ ] **Step 3: Backend checks**

```bash
cd api
pyright
ruff check .
```

Expected: 0 errors.

- [ ] **Step 4: Frontend checks**

```bash
./debug.sh status | grep -q "Status:   UP" || ./debug.sh up
cd client
npm run generate:types
npm run tsc
npm run lint
```

Expected: PASS.

- [ ] **Step 5: Test suites**

```bash
./test.sh stack up
./test.sh all
./test.sh client unit
./test.sh client e2e
```

Expected: PASS, or isolate known flakes and document exact isolated pass/fail results.

- [ ] **Step 6: Commit final fixups**

```bash
git status --short
```

If final verification required fixups, stage only the exact files changed by those fixups and commit them with:

```bash
git commit -m "test(solution-storage): verify solution-scoped storage end to end"
```

## Self-Review Checklist

- [ ] The plan has no placeholder tasks.
- [ ] Declaration comes before declared-only enforcement.
- [ ] File reads/lists/exists are tiered, not single-key only.
- [ ] Policy evaluation happens per resolved tier.
- [ ] Table auto-create remains for non-solution only.
- [ ] File payload import/export is streaming and does not use `content_b64`.
- [ ] Capstone uses a normal declared location such as `finance`.
- [ ] Final verification includes a real deployed solution workflow path.
