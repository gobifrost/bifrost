# Files Explorer Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

---

## STATUS — Files explorer SHIPPED ✅ (branch `codex/files-sdk-policies`, 18 commits, all green)

The original plan below (Tasks 1–22) is **done**: backend (seeded `admin_bypass`, `POST /api/files/structure`, 403-vs-404), the full 3-pane responsive `FilesExplorer`, and tests (backend e2e, vitest, Playwright). On top of that, several live-driven UX rounds shipped — all committed, all tests green, none pushed/merged:

- **Two real bugs caught by live-drive** (unit tests missed both): Global scope must send the explicit `"global"` sentinel (not `null`, which the write path reads as the caller's own org); Test Access must list all users, not just the share's org.
- **Debug-stack S3 fix:** `BIFROST_S3_PUBLIC_ENDPOINT_URL=/s3` in `docker-compose.debug.yml` so presigned URLs route through the Vite `/s3` proxy (browser uploads/previews work). Image preview also switched to authenticated bytes (`files.readBytes` → blob URL) so it no longer depends on S3 host reachability.
- **Upload UX:** moved to the header next to **New Share** (outline secondary vs Upload primary — no blue-on-blue); empty-folder click-to-upload dropzone + full-pane drag overlay.
- **Policies surface:** Browse/Policies tab toggle + flat `PoliciesView` table (Policy auto-sizes, Rules grows, icon Edit + Delete actions, responsive). The policy editor is now the shared **`JsonYamlEditor`** (colored YAML default + JSON tab) with an **Insert template…** dropdown and a **`FilePolicyReferencePanel`** slideout (via shared `HelpSlideout`) documenting file actions, `{user:…}`/`{file:…}` fields, operators, worked examples, footguns.
- **Reference examples are YAML-first** with a per-example JSON toggle — extracted a shared `PolicyExampleBlock` now used by both the Tables and Files reference panels.
- **Polish:** spinner loaders (`InlineLoader`), friendly preview errors (no raw "Forbidden"), title-case context-menu labels (no "...here"), folder context menus in the listing, canonical shadcn pane theming.

**Verification note for the next session:** Jack asked to **stop using headless Chrome** to drive the debug stack — rely on the vitest/e2e suites (and ask before any browser drive).

### NEXT UP → see the new section at the end: **"Reusable / named policy templates"**

---

**Goal:** Replace the admin Files page with a full-height, responsive 3-pane "mapped-drives" explorer (shares → folders → preview/ACL) backed by an admin-only structural-list endpoint, a seeded `admin_bypass` policy on policy creation, and clean 403-vs-404 semantics.

**Architecture:** Backend gains (1) a `seed_admin_bypass` step in `FilePolicyService.upsert_policy`'s create path mirroring Tables' `make_seed_admin_bypass`, (2) an admin-only **structural** list/discover endpoint (`POST /api/files/structure`) that lists what physically exists in a scope *regardless of policy* (powers the tree so nothing is orphaned), and (3) a 403-vs-404 distinction on the policy-gated read/list path. Frontend replaces `FileBrowser.tsx` + the inline tester/editor wiring in `Files.tsx` with a `FilesExplorer` shell (org-scope selector + breadcrumbs + responsive layout) composed of `ShareTree`, `FolderListing`, `FilePreview`, `EffectiveAccessPanel`, plus `TestAccessModal`, `PolicyEditorModal`, `NewShareDialog`.

**Tech Stack:** FastAPI + SQLAlchemy (async) backend; React + TypeScript + Vite + shadcn/ui (Radix + Tailwind) frontend; vitest (component), pytest via `./test.sh` (backend), Playwright (`*.admin.spec.ts`).

## Global Constraints

- **Worktree only.** All work happens in `/home/jack/GitHub/bifrost/.claude/worktrees/files-sdk-policies` on branch `codex/files-sdk-policies`. Never touch the primary `main` checkout.
- **No hardcoded evaluator bypass.** Admin access is granted ONLY by a visible, revocable seeded `admin_bypass` policy. Do NOT add an `is_platform_admin` short-circuit to `FilePolicyService.is_allowed`. (Spec §"Mental model", §Backend change 1.)
- **Reserved locations hidden/read-only.** `workspace` and `temp` are platform-internal and excluded from every explorer-facing endpoint. `uploads` is surfaced **read-only** (browse/preview/download only — no upload, no delete, no policy-edit). Reserved set lives in `shared/file_paths.py::RESERVED_LOCATIONS = {"workspace","uploads","temp"}`; blocked direct prefixes `{"_repo","_tmp","_apps"}`.
- **Storage layout is `{location}/{scope}/{path}`** for custom locations; `global` is the literal scope segment for global files (`shared.file_paths.resolve_s3_key`). `workspace` is unscoped (`_repo/`). Scope resolution stays on canonical `_file_org_id` → `resolve_target_org`; never hand-roll org filtering.
- **No "all scopes" view.** The user is always in exactly one scope: a specific org **or** Global. Switching scope re-roots the tree.
- **No backend ORM in new MCP/router business logic beyond the established pattern.** Routers stay thin; logic goes in `FilePolicyService` / a new `FileStructureService`. (CLAUDE.md "Backend" rules.)
- **Org→global policy cascade (`load_policy`) is unchanged.** `FileMetadata`/`FilePolicy` classification (allow-listed in `IDENTITY_MODELS`, prefix-keyed resolver) is unchanged.
- **Type generation:** after any contract change, run `cd client && npm run generate:types` against the running debug stack (CLAUDE.md). Never hand-edit `client/src/lib/v1.d.ts`.
- **Functional `client/src/services/**` and `client/src/lib/**` modules require sibling vitest.** New components require sibling `*.test.tsx`.
- **Responsive is a first-class acceptance criterion** (spec §"Responsive behavior"): no horizontal page scroll at any breakpoint; use shadcn `Sheet` (note: `drawer.tsx` does NOT exist — use `sheet.tsx`), Tailwind breakpoints, and the established `min-h-0` flex scroll pattern. `breadcrumb.tsx` does NOT exist — build a small inline breadcrumb.

---

## Key existing references (read before coding)

| Need | Location | Notes |
|------|----------|-------|
| Tables' seed pattern to mirror | `api/shared/policies/probe.py::make_seed_admin_bypass()` (lines 101-116) | Returns `{"policies":[{"name":"admin_bypass","description":...,"actions":[...],"when":{"user":"is_platform_admin"}}]}`. File actions are `read/write/delete/list` (NOT table `read/create/update/delete`). |
| Policy service | `api/src/services/file_policy_service.py` | `upsert_policy`, `load_policy`, `is_allowed`, `list_policies`, `get_policy_exact`, `delete_policy`. `_principal_matches_org` (line 322) bypasses org-match for admins but NOT allow/deny — leave as is. |
| Policy contracts | `api/src/models/contracts/policies.py` | `FileAction = Literal["read","write","delete","list"]` (line 292); `FilePolicyRule`, `FilePolicies` (313-328). |
| Router | `api/src/routers/files.py` | `_file_org_id` (235), `_storage_scope` (250), `_organization_id_for_policy` (258), `_require_file_policy` (314), policy endpoints (428-585), `read_file` (684), `list_files_simple` (834). |
| S3 raw listing primitive | `api/src/services/file_storage/service.py::list_raw_s3(prefix)` (332) | Flat recursive key list under a prefix. Use for structural enumeration; derive direct children + folders in the service. |
| Path resolution | `api/shared/file_paths.py::resolve_s3_key` (72), `validate_location_name` (45), `RESERVED_LOCATIONS`/`BLOCKED_LOCATION_NAMES` (39-40) | |
| Org-scope selector (REUSE) | `client/src/components/forms/OrganizationSelect.tsx` | Props: `value: string|null|undefined`, `onChange(value)`, `showGlobal?` (default true), `showAll?` (default false). `null`=Global, UUID=org. For the explorer pass `showAll={false}` so there is no "all scopes". |
| Orgs hook | `client/src/hooks/useOrganizations.ts` | `{ data: Organization[] }`, `org.id`, `org.name`. |
| Auth | `client/src/contexts/AuthContext.tsx::useAuth()` | `isPlatformAdmin: boolean`. |
| Users hook | `client/src/hooks/useUsers.ts` | `useUsers()` / `useUsersFiltered(scope?)`. |
| Searchable picker primitive | `client/src/components/ui/combobox.tsx` | `ComboboxOption[] = {value,label}`. Use for the Test Access user dropdown. |
| Scroll pattern | `client/src/components/ui/data-table.tsx` | Wrapper `flex flex-col min-h-0 max-h-full overflow-hidden`; inner scroll `overflow-auto flex-1 min-h-0`. Parent must be `flex-1 min-h-0`. |
| Existing SDK | `client/src/lib/app-sdk/files.ts` + `use-files.ts` | `files.list/read/download/delete/signedUrl`; `FileAccessDeniedError`; `useFiles(prefix,{location,scope,includeMetadata})→{files,filesMetadata,loading,denied,empty,error,refetch}`. |
| Policy service (client) | `client/src/services/filePolicies.ts` | `listFilePolicies`, `saveFilePolicy`, `deleteFilePolicy`, `testFileAccess`. |
| Components being replaced | `client/src/components/files/{FileBrowser,EffectiveAccessTester,FilePolicyEditor}.tsx` | Reuse `FilePolicyEditor` inside `PolicyEditorModal`; replace `FileBrowser` + `EffectiveAccessTester`. |

---

## File Structure

**Backend (create/modify):**
- Modify `api/src/services/file_policy_service.py` — add `seed_admin_bypass` into the `upsert_policy` create path; add `FilePolicyDenied`/`FileNotFound` distinction is via router (no service change for that).
- Create `api/src/services/file_structure_service.py` — admin-only structural enumeration (shares, folders, files) for a scope, reserved-aware, `uploads` flagged read-only.
- Create `api/shared/file_policies_seed.py` — `make_seed_admin_bypass_file()` so both router and service share one definition. (Mirrors `probe.py` but for file actions.)
- Modify `api/src/routers/files.py` — add `POST /api/files/structure` (admin-only), tighten `read_file`/`list_files_simple` 403-vs-404, and a "new share" thin endpoint reuse of `set_file_policy`.
- Modify `api/src/models/contracts/files.py` (or inline in router) — `FileStructureRequest`/`FileStructureResponse` models.

**Frontend (create/modify):**
- Modify `client/src/services/filePolicies.ts` — add `listShares`/`listStructure` calls + an `effectiveAccess` fetch (cascade) and a per-principal multi-action test helper.
- Create `client/src/services/fileStructure.ts` — typed wrapper for `POST /api/files/structure`.
- Create `client/src/components/files/FilesExplorer.tsx` — page shell (scope, breadcrumbs, responsive layout).
- Create `client/src/components/files/ShareTree.tsx`.
- Create `client/src/components/files/FolderListing.tsx`.
- Create `client/src/components/files/FilePreview.tsx`.
- Create `client/src/components/files/EffectiveAccessPanel.tsx`.
- Create `client/src/components/files/TestAccessModal.tsx`.
- Create `client/src/components/files/PolicyEditorModal.tsx` (wraps existing `FilePolicyEditor`).
- Create `client/src/components/files/NewShareDialog.tsx`.
- Create `client/src/components/files/Breadcrumbs.tsx` (small inline component; no shadcn breadcrumb exists).
- Modify `client/src/pages/Files.tsx` — render `<FilesExplorer/>`; remove inline tester/editor wiring.
- Delete `client/src/components/files/FileBrowser.tsx` + `FileBrowser.test.tsx` and `EffectiveAccessTester.tsx` + `EffectiveAccessTester.test.tsx` (replaced).
- Sibling `*.test.tsx` for each new component; `*.test.ts` for the new service.
- Modify/extend `client/e2e/files-explorer.admin.spec.ts` (new Playwright happy-path, desktop + narrow).

---

## Phase 1 — Backend: seeded admin_bypass

### Task 1: Shared file-policy seed helper

**Files:**
- Create: `api/shared/file_policies_seed.py`
- Test: `api/tests/unit/policies/test_file_policies_seed.py`

**Interfaces:**
- Produces: `make_seed_admin_bypass_file() -> dict` returning a `FilePolicies`-valid dict.

- [ ] **Step 1: Write the failing test**

```python
# api/tests/unit/policies/test_file_policies_seed.py
from shared.file_policies_seed import make_seed_admin_bypass_file
from src.models.contracts.policies import FilePolicies


def test_seed_is_valid_file_policies_with_admin_rule():
    seed = make_seed_admin_bypass_file()
    parsed = FilePolicies.model_validate(seed)
    assert len(parsed.policies) == 1
    rule = parsed.policies[0]
    assert rule.name == "admin_bypass"
    assert set(rule.actions) == {"read", "write", "delete", "list"}
    assert rule.when == {"user": "is_platform_admin"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./test.sh tests/unit/policies/test_file_policies_seed.py -v`
Expected: FAIL — `ModuleNotFoundError: shared.file_policies_seed`.

- [ ] **Step 3: Write minimal implementation**

```python
# api/shared/file_policies_seed.py
"""Seed policy for a freshly-created file share/prefix.

Mirrors `shared.policies.probe.make_seed_admin_bypass` for Tables, but uses
the file action vocabulary (read/write/delete/list). Stored verbatim into the
new FilePolicy at create time so a platform admin is allowed by a VISIBLE,
revocable rule — there is no hardcoded bypass in the evaluator.
"""

from __future__ import annotations


def make_seed_admin_bypass_file() -> dict:
    return {
        "policies": [
            {
                "name": "admin_bypass",
                "description": (
                    "Platform admins bypass all checks. "
                    "Edit or delete to enforce stricter access."
                ),
                "actions": ["read", "write", "delete", "list"],
                "when": {"user": "is_platform_admin"},
            }
        ]
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./test.sh tests/unit/policies/test_file_policies_seed.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/shared/file_policies_seed.py api/tests/unit/policies/test_file_policies_seed.py
git commit -m "feat(files): shared admin_bypass seed for file policies"
```

### Task 2: Seed admin_bypass when a new file policy is created

**Files:**
- Modify: `api/src/services/file_policy_service.py:110-138` (`upsert_policy`)
- Test: `api/tests/unit/policies/test_file_policy_seed_on_create.py`

**Interfaces:**
- Consumes: `make_seed_admin_bypass_file()` from Task 1.
- Produces: `upsert_policy(..., seed_admin_bypass: bool = True)` — on the CREATE branch only, if the incoming document has no rule named `admin_bypass`, prepend the seed rule. On UPDATE, leave the document exactly as given (so an admin can revoke it).

**Design note:** The seed must be *mergeable*, not overwriting. If a caller's create document already contains an `admin_bypass` rule (e.g. the `NewShareDialog` sends one), do not duplicate it. The seed is prepended only when absent. This keeps "revoking it denies the admin" true: once created, an UPDATE that drops the rule sticks.

- [ ] **Step 1: Write the failing tests**

```python
# api/tests/unit/policies/test_file_policy_seed_on_create.py
import pytest
from src.models.contracts.policies import FilePolicies
from src.services.file_policy_service import FilePolicyService


@pytest.mark.asyncio
async def test_create_seeds_admin_bypass(db_session):
    svc = FilePolicyService(db_session)
    row = await svc.upsert_policy(
        organization_id=None,
        location="gallery",
        path="",
        policies=FilePolicies(policies=[]),
    )
    names = [r["name"] for r in row.policies["policies"]]
    assert "admin_bypass" in names


@pytest.mark.asyncio
async def test_create_does_not_duplicate_existing_admin_bypass(db_session):
    svc = FilePolicyService(db_session)
    doc = FilePolicies.model_validate({
        "policies": [{
            "name": "admin_bypass",
            "actions": ["read"],
            "when": {"user": "is_platform_admin"},
        }]
    })
    row = await svc.upsert_policy(
        organization_id=None, location="gallery", path="", policies=doc,
    )
    names = [r["name"] for r in row.policies["policies"]]
    assert names.count("admin_bypass") == 1


@pytest.mark.asyncio
async def test_update_does_not_re_add_revoked_admin_bypass(db_session):
    svc = FilePolicyService(db_session)
    await svc.upsert_policy(
        organization_id=None, location="gallery", path="",
        policies=FilePolicies(policies=[]),
    )
    # Admin revokes admin_bypass on update:
    revoked = FilePolicies.model_validate({
        "policies": [{
            "name": "team", "actions": ["read"],
            "when": {"user": "is_platform_admin"},
        }]
    })
    row = await svc.upsert_policy(
        organization_id=None, location="gallery", path="", policies=revoked,
    )
    names = [r["name"] for r in row.policies["policies"]]
    assert "admin_bypass" not in names
```

Note: the async unit DB fixture is `db_session` (confirmed at `api/tests/conftest.py:134`). These policy-service tests need a DB but no HTTP, so they live under `tests/unit/policies/` and run with `./test.sh` (unit). They still require the test stack up (DB-touching). If a pure-unit DB session proves unavailable in that path, fall back to `tests/e2e/` with the sync `e2e_client` harness (see Tasks 3/5/6).

- [ ] **Step 2: Run to verify failure**

Run: `./test.sh tests/unit/policies/test_file_policy_seed_on_create.py -v`
Expected: FAIL — `test_create_seeds_admin_bypass` fails (no admin_bypass present); update test passes incidentally.

- [ ] **Step 3: Implement**

```python
# In api/src/services/file_policy_service.py, top imports:
from shared.file_policies_seed import make_seed_admin_bypass_file

# Replace the create branch of upsert_policy:
    async def upsert_policy(
        self,
        *,
        organization_id: UUID | None,
        location: str,
        path: str,
        policies: FilePolicies,
        created_by: UUID | str | None = None,
        seed_admin_bypass: bool = True,
    ) -> FilePolicy:
        existing = await self._get_policy_exact(
            organization_id=organization_id,
            location=location,
            path=path,
        )
        if existing is None:
            doc = policies.model_dump(mode="json")
            if seed_admin_bypass and not any(
                rule.get("name") == "admin_bypass"
                for rule in doc.get("policies", [])
            ):
                seed = make_seed_admin_bypass_file()["policies"][0]
                doc["policies"] = [seed, *doc.get("policies", [])]
            row = FilePolicy(
                organization_id=organization_id,
                location=location,
                path=path,
                policies=doc,
                created_by=_coerce_uuid(created_by),
            )
            self.db.add(row)
            await self.db.flush()
            return row

        existing.policies = policies.model_dump(mode="json")
        await self.db.flush()
        return existing
```

- [ ] **Step 4: Run to verify pass**

Run: `./test.sh tests/unit/policies/test_file_policy_seed_on_create.py -v`
Expected: PASS (all three).

- [ ] **Step 5: Commit**

```bash
git add api/src/services/file_policy_service.py api/tests/unit/policies/test_file_policy_seed_on_create.py
git commit -m "feat(files): seed admin_bypass on first file-policy create"
```

### Task 3: E2E — admin allowed via seeded policy; revoking denies

**Files:**
- Test: `api/tests/e2e/api/test_file_policy_admin_bypass.py`

**Interfaces:**
- Consumes: live `PUT /api/files/policies/{path}` (already seeds via Task 2), `POST /api/files/policies/test`.

- [ ] **Step 1: Write the failing test**

E2E tests in this repo are **synchronous** methods that take the session-scoped `e2e_client` + actor fixtures (`platform_admin`, `org1_user`, `org1`), each exposing `.headers`. The policy `PUT` path is URL-encoded — use `quote(prefix or "/", safe="")` exactly like `api/tests/e2e/file_policy_helpers.py::grant_file_policy`. Don't `await`.

```python
# api/tests/e2e/api/test_file_policy_admin_bypass.py
from urllib.parse import quote


def _put_policy(e2e_client, headers, *, location, scope, policies):
    return e2e_client.put(
        f"/api/files/policies/{quote('/', safe='')}",
        headers=headers,
        params={"location": location, "scope": scope},
        json={"policies": {"policies": policies}},
    )


def _test_access(e2e_client, headers, *, path, location, scope, action="read"):
    return e2e_client.post(
        "/api/files/policies/test",
        headers=headers,
        json={"path": path, "location": location, "action": action, "scope": scope},
    )


class TestAdminBypassSeed:
    def test_admin_allowed_via_seeded_then_denied_when_revoked(
        self, e2e_client, platform_admin
    ):
        # 1. Create a policy on a fresh share → seeds admin_bypass.
        r = _put_policy(e2e_client, platform_admin.headers,
                        location="gallery", scope="global", policies=[])
        assert r.status_code == 200, r.text
        assert any(p["name"] == "admin_bypass" for p in r.json()["policies"]["policies"])

        # 2. Admin is allowed to read under it.
        t = _test_access(e2e_client, platform_admin.headers,
                         path="pic.png", location="gallery", scope="global")
        assert t.json()["allowed"] is True

        # 3. Revoke admin_bypass (update with empty doc — seed NOT re-added).
        r2 = _put_policy(e2e_client, platform_admin.headers,
                         location="gallery", scope="global", policies=[])
        assert not any(p["name"] == "admin_bypass"
                       for p in r2.json()["policies"]["policies"])

        # 4. Admin now denied.
        t2 = _test_access(e2e_client, platform_admin.headers,
                          path="pic.png", location="gallery", scope="global")
        assert t2.json()["allowed"] is False
```

- [ ] **Step 2: Run to verify it fails (then passes)**

Run: `./test.sh e2e tests/e2e/api/test_file_policy_admin_bypass.py -v`
(Note: `./test.sh e2e <path>` runs the whole e2e suite with the path as a filter arg per repo quirk — confirm it scopes; if not, run full `./test.sh e2e` and grep the JUnit XML at `/tmp/bifrost-<project>/test-results.xml`.)
Expected: PASS — the create-path seed and the no-re-add-on-update behavior already hold from Task 2.

- [ ] **Step 3: Commit**

```bash
git add api/tests/e2e/api/test_file_policy_admin_bypass.py
git commit -m "test(files): e2e admin allowed via seeded admin_bypass, denied when revoked"
```

---

## Phase 2 — Backend: structural list/discover endpoint

### Task 4: `FileStructureService` — enumerate shares/folders/files in a scope

**Files:**
- Create: `api/src/services/file_structure_service.py`
- Test: `api/tests/e2e/api/test_file_structure_service.py` (e2e because it needs real S3/SeaweedFS)

**Interfaces:**
- Consumes: `FileStorageService.list_raw_s3(prefix)`, `shared.file_paths` (`RESERVED_LOCATIONS`, `resolve_s3_key`), `FilePolicyService.list_policies`.
- Produces:
  - `class StructureEntry(BaseModel)`: `{ name: str, kind: Literal["folder","file"], path: str }` (`path` = path relative to the location root, excluding the `{scope}/` segment).
  - `class FileStructureService.list_shares(*, org_id: UUID|None) -> list[ShareEntry]` where `ShareEntry = { location: str, read_only: bool, has_policy: bool }`. Shares = the set of distinct top-level `{location}` prefixes that have at least one object under `{location}/{scope}/` OR a policy in that scope, EXCLUDING `RESERVED_LOCATIONS` except `uploads` which is included with `read_only=True`.
  - `async list_prefix(*, org_id: UUID|None, location: str, prefix: str) -> list[StructureEntry]` — direct children (folders + files) physically present under `{location}/{scope}/{prefix}` via delimiter-style derivation from `list_raw_s3`.

**Design notes:**
- `scope` segment derivation: `scope_seg = "global" if org_id is None else str(org_id)`.
- Direct-children derivation from a flat key list: full prefix = `resolve_s3_key(location, scope_seg, prefix)`; for each key, strip the prefix, take the first segment; if it contains a remaining `/` it's a folder (dedupe), else a file. This avoids needing a new S3 delimiter method.
- `list_shares` enumerates locations by listing `list_raw_s3("")` once and bucketing top-level prefixes? No — that scans the whole bucket. Instead: candidate locations come from (a) distinct `FilePolicy.location` values in the scope (via `list_policies(organization_id=org_id)`), plus (b) a bounded `list_raw_s3` per known candidate. Since locations are freeform, derive the candidate set from policies + a single `list_raw_s3("")` filtered to exclude `_repo/`,`_tmp/`,`_apps/` and bucket by first segment. Reserved `workspace`/`temp` map to `_repo`/`_tmp` and are already excluded; `uploads` (prefix `uploads/`) is included read-only.

- [ ] **Step 1: Write the failing test**

```python
# api/tests/e2e/api/test_file_structure_service.py
import pytest
from src.services.file_structure_service import FileStructureService
from src.services.file_storage import FileStorageService
from src.services.file_policy_service import FilePolicyService
from src.models.contracts.policies import FilePolicies
from shared.file_paths import resolve_s3_key


@pytest.mark.asyncio
async def test_list_prefix_returns_direct_children_only(db_session):
    storage = FileStorageService(db_session)
    # Seed two files + a nested file under gallery/global/
    await storage.write_raw_to_s3(resolve_s3_key("gallery", "global", "a.png"), b"x")
    await storage.write_raw_to_s3(resolve_s3_key("gallery", "global", "sub/b.png"), b"y")

    svc = FileStructureService(db_session)
    entries = await svc.list_prefix(org_id=None, location="gallery", prefix="")
    names = {(e.name, e.kind) for e in entries}
    assert ("a.png", "file") in names
    assert ("sub", "folder") in names
    assert ("b.png", "file") not in names  # not a direct child


@pytest.mark.asyncio
async def test_list_shares_excludes_reserved_includes_uploads_readonly(db_session):
    storage = FileStorageService(db_session)
    await storage.write_raw_to_s3(resolve_s3_key("gallery", "global", "a.png"), b"x")
    await storage.write_raw_to_s3(resolve_s3_key("uploads", "global", "u.png"), b"x")

    svc = FileStructureService(db_session)
    shares = await svc.list_shares(org_id=None)
    by_loc = {s.location: s for s in shares}
    assert "gallery" in by_loc and by_loc["gallery"].read_only is False
    assert "uploads" in by_loc and by_loc["uploads"].read_only is True
    assert "workspace" not in by_loc
    assert "temp" not in by_loc


@pytest.mark.asyncio
async def test_list_shares_includes_policied_but_empty_share(db_session):
    await FilePolicyService(db_session).upsert_policy(
        organization_id=None, location="reports", path="",
        policies=FilePolicies(policies=[]),
    )
    svc = FileStructureService(db_session)
    shares = await svc.list_shares(org_id=None)
    assert "reports" in {s.location for s in shares}  # has_policy, no files yet
```

- [ ] **Step 2: Run to verify failure**

Run: `./test.sh e2e tests/e2e/api/test_file_structure_service.py -v`
Expected: FAIL — `ModuleNotFoundError: src.services.file_structure_service`.

- [ ] **Step 3: Implement the service**

```python
# api/src/services/file_structure_service.py
"""Admin-only STRUCTURAL enumeration of file shares/folders/files.

This is NOT policy-gated: it reports what physically exists in a scope so the
explorer tree never orphans a file. Content access (read/write/...) stays
policy-governed elsewhere. Excludes reserved locations (workspace/temp);
includes `uploads` flagged read-only.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from shared.file_paths import (
    BLOCKED_LOCATION_NAMES,
    UPLOADS_PREFIX,
    resolve_s3_key,
)
from src.services.file_policy_service import FilePolicyService
from src.services.file_storage import FileStorageService

# Top-level S3 prefixes that map to reserved/internal locations and must never
# appear as explorer shares.
_HIDDEN_TOP_PREFIXES = {"_repo", "_tmp", "_apps"}


class StructureEntry(BaseModel):
    name: str
    kind: Literal["folder", "file"]
    path: str  # relative to location root (no scope segment)


class ShareEntry(BaseModel):
    location: str
    read_only: bool
    has_policy: bool


def _scope_seg(org_id: UUID | None) -> str:
    return "global" if org_id is None else str(org_id)


class FileStructureService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.storage = FileStorageService(db)
        self.policies = FilePolicyService(db)

    async def list_prefix(
        self, *, org_id: UUID | None, location: str, prefix: str
    ) -> list[StructureEntry]:
        scope = _scope_seg(org_id)
        base = resolve_s3_key(location, scope, prefix)
        if prefix and not base.endswith("/"):
            base += "/"
        keys = await self.storage.list_raw_s3(base)
        folders: dict[str, StructureEntry] = {}
        files: dict[str, StructureEntry] = {}
        rel_prefix = prefix.rstrip("/") + "/" if prefix else ""
        for key in keys:
            rel = key[len(base):]
            if not rel:
                continue
            head, _, tail = rel.partition("/")
            if tail:  # nested → folder
                folders[head] = StructureEntry(
                    name=head, kind="folder", path=f"{rel_prefix}{head}"
                )
            else:
                files[head] = StructureEntry(
                    name=head, kind="file", path=f"{rel_prefix}{head}"
                )
        return sorted(
            [*folders.values(), *files.values()],
            key=lambda e: (e.kind != "folder", e.name),
        )

    async def list_shares(self, *, org_id: UUID | None) -> list[ShareEntry]:
        scope = _scope_seg(org_id)
        # Locations carrying files: bucket every key's top segment.
        all_keys = await self.storage.list_raw_s3("")
        file_locations: set[str] = set()
        for key in all_keys:
            top, _, rest = key.partition("/")
            if not rest:
                continue
            if top in _HIDDEN_TOP_PREFIXES:
                continue
            if top == UPLOADS_PREFIX.rstrip("/"):
                # uploads/{scope}/...
                seg2 = rest.split("/", 1)[0]
                if seg2 == scope:
                    file_locations.add("uploads")
                continue
            # custom location: {location}/{scope}/...
            seg2 = rest.split("/", 1)[0]
            if seg2 == scope:
                file_locations.add(top)
        # Locations carrying a policy in this scope (so a freshly-policied,
        # empty share still appears).
        policy_rows = await self.policies.list_policies(organization_id=org_id)
        policy_locations = {
            r.location for r in policy_rows
            if r.location not in {"workspace", "temp"}
        }
        locations = sorted(file_locations | policy_locations)
        return [
            ShareEntry(
                location=loc,
                read_only=(loc == "uploads"),
                has_policy=(loc in policy_locations),
            )
            for loc in locations
        ]
```

- [ ] **Step 4: Run to verify pass**

Run: `./test.sh e2e tests/e2e/api/test_file_structure_service.py -v`
Expected: PASS (all three).

- [ ] **Step 5: Commit**

```bash
git add api/src/services/file_structure_service.py api/tests/e2e/api/test_file_structure_service.py
git commit -m "feat(files): FileStructureService — admin structural enumeration of shares/prefixes"
```

### Task 5: `POST /api/files/structure` endpoint (admin-only)

**Files:**
- Modify: `api/src/routers/files.py` (add request/response models near line 196; add endpoint near the policy admin endpoints ~line 425)
- Test: `api/tests/e2e/api/test_file_structure_endpoint.py`

**Interfaces:**
- Consumes: `FileStructureService`, `_organization_id_for_policy` (router helper, line 258), `CurrentSuperuser`.
- Produces:
  - `FileStructureRequest`: `{ location: str | None = None, prefix: str = "", scope: str | None = None }`. When `location is None` → return shares (depth=0 / discover mode). When `location` is set → return that prefix's direct children.
  - `FileStructureResponse`: `{ shares: list[ShareEntry] | None, entries: list[StructureEntry] | None }`.
  - Gated by `CurrentSuperuser` (admin-only structural listing per spec §Backend change 2). Non-admins get 403 from the dependency.

- [ ] **Step 1: Write the failing test**

Seed files through the HTTP write endpoint (sync, same harness) rather than a service call, so the whole test stays synchronous. Grant a write policy first (`grant_file_policy`), then assert structure. `org1_user` is a non-admin actor.

```python
# api/tests/e2e/api/test_file_structure_endpoint.py
from api.tests.e2e.file_policy_helpers import grant_file_policy  # adjust import to repo style


def _write(e2e_client, headers, *, path, location, scope, content="x"):
    return e2e_client.post("/api/files/write", headers=headers, json={
        "path": path, "content": content, "location": location, "scope": scope,
    })


class TestFileStructureEndpoint:
    def test_structure_shares_then_prefix(self, e2e_client, platform_admin):
        grant_file_policy(e2e_client, platform_admin.headers,
                          location="gallery", scope="global", prefix="")
        w = _write(e2e_client, platform_admin.headers,
                   path="a.png", location="gallery", scope="global")
        assert w.status_code in (200, 204), w.text

        # Discover shares (no location).
        r = e2e_client.post("/api/files/structure", headers=platform_admin.headers,
                            json={"scope": "global"})
        assert r.status_code == 200, r.text
        assert "gallery" in {s["location"] for s in r.json()["shares"]}

        # List a prefix.
        r2 = e2e_client.post("/api/files/structure", headers=platform_admin.headers,
                             json={"location": "gallery", "prefix": "", "scope": "global"})
        assert "a.png" in {e["name"] for e in r2.json()["entries"]}

    def test_structure_forbidden_for_non_admin(self, e2e_client, org1_user):
        r = e2e_client.post("/api/files/structure", headers=org1_user.headers,
                            json={"scope": "global"})
        assert r.status_code == 403
```

Confirm the exact import path for `grant_file_policy` (it lives at `api/tests/e2e/file_policy_helpers.py`; existing tests import it as `from tests.e2e.file_policy_helpers import grant_file_policy` or via a conftest re-export — match a neighbor).

- [ ] **Step 2: Run to verify failure**

Run: `./test.sh e2e tests/e2e/api/test_file_structure_endpoint.py -v`
Expected: FAIL — 404 (route missing).

- [ ] **Step 3: Implement endpoint + models**

```python
# In api/src/routers/files.py — add near the other request models (~line 196):
class FileStructureRequest(BaseModel):
    location: str | None = Field(default=None, description="Location to list; omit to discover shares")
    prefix: str = Field(default="", description="Prefix under the location")
    scope: str | None = Field(default=None, description="Org scope: None/'global' or a UUID")


class FileStructureResponse(BaseModel):
    shares: list[dict] | None = None
    entries: list[dict] | None = None


# Add the endpoint near the policy admin endpoints (after test_file_policy_access):
@router.post("/structure", response_model=FileStructureResponse)
async def list_file_structure(
    request: FileStructureRequest,
    ctx: Context,
    user: CurrentSuperuser,
    db: AsyncSession = Depends(get_db),
) -> FileStructureResponse:
    """Admin-only STRUCTURAL listing (not policy-gated): what physically exists
    in a scope, so the explorer tree never orphans a file. Excludes reserved
    workspace/temp; flags uploads read-only."""
    from src.services.file_structure_service import FileStructureService

    try:
        org_id = _organization_id_for_policy(request.location or "workspace", request.scope)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    svc = FileStructureService(db)
    if request.location is None:
        shares = await svc.list_shares(org_id=org_id)
        return FileStructureResponse(shares=[s.model_dump() for s in shares])
    if request.location in {"workspace", "temp"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Reserved location")
    entries = await svc.list_prefix(org_id=org_id, location=request.location, prefix=request.prefix)
    return FileStructureResponse(entries=[e.model_dump() for e in entries])
```

- [ ] **Step 4: Run to verify pass**

Run: `./test.sh e2e tests/e2e/api/test_file_structure_endpoint.py -v`
Expected: PASS.

- [ ] **Step 5: Regenerate types + commit**

```bash
# debug stack must be up; OPENAPI_URL per ./debug.sh status if non-default port
cd client && npm run generate:types && cd ..
git add api/src/routers/files.py api/tests/e2e/api/test_file_structure_endpoint.py client/src/lib/v1.d.ts
git commit -m "feat(files): POST /api/files/structure admin structural listing endpoint"
```

### Task 6: 403-vs-404 distinction on policy-gated read & list

**Files:**
- Modify: `api/src/routers/files.py` — `read_file` (684-722), `list_files_simple` (834-919)
- Test: `api/tests/e2e/api/test_files_403_vs_404.py`

**Interfaces:**
- Consumes: existing `_require_file_policy` (raises 403), backend `read`/`list`.
- Produces: read returns **403** when policy denies, **404** when policy allows but the object is absent. (Today `read_file` checks policy first → 403 before the FileNotFound can surface, which is already correct for read; verify and lock it with a test. The conflation the spec calls out is on the **list** path, where a denied directory with zero visible children raised 403 even when the prefix simply doesn't exist.)

**Design note:** Read path is already 403-then-404 (policy gate precedes the backend read). The fix is to make that ordering explicit and tested, and to ensure `list_files_simple` returns 404 (not 403) when the caller *is* allowed to list but the prefix has no objects AND does not exist. Distinguish: if `directory_allowed` is True and the listing is empty, return an empty list (200) — an allowed-but-empty dir is not an error. Only return 403 when `not directory_allowed and not files`. That is the current behavior at line 911-912, so the real gap is: when `directory_allowed` is True, never 403. Add an explicit 404 only if the location/scope is structurally invalid. Keep changes minimal; the test pins the contract.

- [ ] **Step 1: Write the failing/contract test**

```python
# api/tests/e2e/api/test_files_403_vs_404.py
from urllib.parse import quote
from api.tests.e2e.file_policy_helpers import grant_file_policy  # adjust to repo style


def _seed_admin_bypass(e2e_client, headers, *, location, scope):
    # Empty doc → backend seeds admin_bypass on create.
    return e2e_client.put(
        f"/api/files/policies/{quote('/', safe='')}",
        headers=headers, params={"location": location, "scope": scope},
        json={"policies": {"policies": []}},
    )


class TestFiles403vs404:
    def test_read_denied_is_403(self, e2e_client, org1_user):
        # No policy grants this non-admin user → 403, not 404.
        r = e2e_client.post("/api/files/read", headers=org1_user.headers, json={
            "path": "nope.txt", "location": "gallery", "scope": None,
        })
        assert r.status_code == 403

    def test_read_allowed_but_missing_is_404(self, e2e_client, platform_admin):
        _seed_admin_bypass(e2e_client, platform_admin.headers,
                           location="gallery", scope="global")
        r = e2e_client.post("/api/files/read", headers=platform_admin.headers, json={
            "path": "absent.txt", "location": "gallery", "scope": "global",
        })
        assert r.status_code == 404

    def test_list_allowed_but_empty_is_200_empty(self, e2e_client, platform_admin):
        _seed_admin_bypass(e2e_client, platform_admin.headers,
                           location="gallery", scope="global")
        r = e2e_client.post("/api/files/list", headers=platform_admin.headers, json={
            "directory": "emptydir", "location": "gallery", "scope": "global",
        })
        assert r.status_code == 200
        assert r.json()["files"] == []
```

- [ ] **Step 2: Run to verify current behavior**

Run: `./test.sh e2e tests/e2e/api/test_files_403_vs_404.py -v`
Expected: `test_read_denied_is_403` and `test_list_allowed_but_empty_is_200_empty` likely PASS; `test_read_allowed_but_missing_is_404` PASS (policy gate precedes read). If any fail, that pinpoints the conflation to fix.

- [ ] **Step 3: Fix only what the failing test exposes**

If `read_allowed_but_missing` returns 403: confirm the admin_bypass seed reached `is_allowed` (the principal-org match for `scope="global"` → `organization_id=None` → `_principal_matches_org` returns True). If `list_allowed_but_empty` returns 403: relax the `if not directory_allowed and not files` guard so an *allowed* empty dir returns 200 (it already should — only fix if the test proves otherwise). Make the minimal change; do not add new branches the test doesn't require.

- [ ] **Step 4: Run to verify pass**

Run: `./test.sh e2e tests/e2e/api/test_files_403_vs_404.py -v`
Expected: PASS (all three).

- [ ] **Step 5: Commit**

```bash
git add api/src/routers/files.py api/tests/e2e/api/test_files_403_vs_404.py
git commit -m "test(files): pin 403-denied vs 404-missing vs 200-empty contract on read/list"
```

---

## Phase 3 — Frontend service layer

### Task 7: `fileStructure.ts` service wrapper + tests

**Files:**
- Create: `client/src/services/fileStructure.ts`
- Test: `client/src/services/fileStructure.test.ts`

**Interfaces:**
- Consumes: `authFetch` from `@/lib/api-client` (same pattern as `filePolicies.ts`).
- Produces:
  - `type StructureScope = string | null` (null = Global).
  - `interface ShareEntry { location: string; readOnly: boolean; hasPolicy: boolean }`
  - `interface StructureEntry { name: string; kind: "folder"|"file"; path: string }`
  - `async function listShares(scope: StructureScope): Promise<ShareEntry[]>` → `POST /api/files/structure {scope}`.
  - `async function listStructure(location: string, prefix: string, scope: StructureScope): Promise<StructureEntry[]>` → `POST /api/files/structure {location,prefix,scope}`.

- [ ] **Step 1: Write the failing test**

```ts
// client/src/services/fileStructure.test.ts
import { describe, it, expect, vi, beforeEach } from "vitest";
import { listShares, listStructure } from "./fileStructure";

vi.mock("@/lib/api-client", () => ({
  authFetch: vi.fn(),
}));
import { authFetch } from "@/lib/api-client";

function jsonResponse(body: unknown) {
  return { ok: true, status: 200, json: async () => body } as Response;
}

describe("fileStructure service", () => {
  beforeEach(() => vi.mocked(authFetch).mockReset());

  it("listShares maps snake_case to camelCase", async () => {
    vi.mocked(authFetch).mockResolvedValue(
      jsonResponse({ shares: [{ location: "gallery", read_only: false, has_policy: true }] }),
    );
    const shares = await listShares(null);
    expect(shares[0]).toEqual({ location: "gallery", readOnly: false, hasPolicy: true });
    const [, init] = vi.mocked(authFetch).mock.calls[0];
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({ scope: null });
  });

  it("listStructure sends location + prefix + scope", async () => {
    vi.mocked(authFetch).mockResolvedValue(
      jsonResponse({ entries: [{ name: "a.png", kind: "file", path: "a.png" }] }),
    );
    const entries = await listStructure("gallery", "sub", "org-1");
    expect(entries[0]).toEqual({ name: "a.png", kind: "file", path: "a.png" });
    const [, init] = vi.mocked(authFetch).mock.calls[0];
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({
      location: "gallery", prefix: "sub", scope: "org-1",
    });
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `./test.sh client unit fileStructure`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

```ts
// client/src/services/fileStructure.ts
import { authFetch } from "@/lib/api-client";

export type StructureScope = string | null;

export interface ShareEntry {
  location: string;
  readOnly: boolean;
  hasPolicy: boolean;
}

export interface StructureEntry {
  name: string;
  kind: "folder" | "file";
  path: string;
}

async function parse<T>(res: Response): Promise<T> {
  if (res.ok) return (await res.json()) as T;
  const body = await res.json().catch(() => ({}));
  throw new Error((body as { detail?: string }).detail ?? `Request failed: ${res.status}`);
}

export async function listShares(scope: StructureScope): Promise<ShareEntry[]> {
  const res = await authFetch("/api/files/structure", {
    method: "POST",
    body: JSON.stringify({ scope }),
  });
  const body = await parse<{ shares?: Array<{ location: string; read_only: boolean; has_policy: boolean }> }>(res);
  return (body.shares ?? []).map((s) => ({
    location: s.location,
    readOnly: s.read_only,
    hasPolicy: s.has_policy,
  }));
}

export async function listStructure(
  location: string,
  prefix: string,
  scope: StructureScope,
): Promise<StructureEntry[]> {
  const res = await authFetch("/api/files/structure", {
    method: "POST",
    body: JSON.stringify({ location, prefix, scope }),
  });
  const body = await parse<{ entries?: StructureEntry[] }>(res);
  return body.entries ?? [];
}
```

- [ ] **Step 4: Run to verify pass**

Run: `./test.sh client unit fileStructure`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add client/src/services/fileStructure.ts client/src/services/fileStructure.test.ts
git commit -m "feat(files): fileStructure service wrapper for structural listing"
```

### Task 8: Extend `filePolicies.ts` — effective-access cascade + multi-action test

**Files:**
- Modify: `client/src/services/filePolicies.ts`
- Test: `client/src/services/filePolicies.test.ts` (create if absent; else extend)

**Interfaces:**
- Consumes: existing `testFileAccess` (single action) + `listFilePolicies`.
- Produces:
  - `async function effectiveAccess(location: string, path: string, scope: string|null): Promise<FilePolicy[]>` — returns the cascade of policies whose prefix matches `path` (longest-prefix-first), reusing `listFilePolicies` + client-side prefix filter/sort (mirror `bestPolicyForPath` in `Files.tsx`, but return ALL matching, sorted). No new endpoint needed.
  - `async function testAllActions(req: { location: string; path: string; scope: string|null; userId: string }): Promise<Record<FilePolicyAction, FileAccessTestResult>>` — fan four `testFileAccess` calls (read/write/delete/list) for one principal and collect by action.

- [ ] **Step 1: Write the failing tests**

```ts
// client/src/services/filePolicies.test.ts  (add to existing or create)
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/lib/api-client", () => ({ authFetch: vi.fn() }));
import { authFetch } from "@/lib/api-client";
import { effectiveAccess, testAllActions } from "./filePolicies";

function jsonResponse(body: unknown) {
  return { ok: true, status: 200, json: async () => body } as Response;
}

describe("effectiveAccess", () => {
  beforeEach(() => vi.mocked(authFetch).mockReset());
  it("returns matching policies longest-prefix first", async () => {
    vi.mocked(authFetch).mockResolvedValue(jsonResponse({ policies: [
      { id: "1", location: "gallery", path: "", organization_id: null, policies: { policies: [] } },
      { id: "2", location: "gallery", path: "team/", organization_id: null, policies: { policies: [] } },
      { id: "3", location: "gallery", path: "other/", organization_id: null, policies: { policies: [] } },
    ] }));
    const result = await effectiveAccess("gallery", "team/pic.png", null);
    expect(result.map((p) => p.id)).toEqual(["2", "1"]);
  });
});

describe("testAllActions", () => {
  beforeEach(() => vi.mocked(authFetch).mockReset());
  it("collects all four actions", async () => {
    vi.mocked(authFetch).mockImplementation(async (_url, init) => {
      const action = JSON.parse((init as RequestInit).body as string).action;
      return jsonResponse({ allowed: action === "read", path: "p", location: "gallery", action });
    });
    const result = await testAllActions({ location: "gallery", path: "p", scope: null, userId: "u" });
    expect(result.read.allowed).toBe(true);
    expect(result.write.allowed).toBe(false);
    expect(Object.keys(result).sort()).toEqual(["delete", "list", "read", "write"]);
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `./test.sh client unit filePolicies`
Expected: FAIL — `effectiveAccess`/`testAllActions` undefined.

- [ ] **Step 3: Implement (append to `filePolicies.ts`)**

```ts
const FILE_ACTIONS: FilePolicyAction[] = ["read", "write", "delete", "list"];

export async function effectiveAccess(
  location: string,
  path: string,
  scope: string | null,
): Promise<FilePolicy[]> {
  const { policies } = await listFilePolicies({ location, scope: scope ?? undefined });
  return policies
    .filter((p) => p.location === location && path.startsWith(p.path))
    .sort((a, b) => b.path.length - a.path.length);
}

export async function testAllActions(req: {
  location: string;
  path: string;
  scope: string | null;
  userId: string;
}): Promise<Record<FilePolicyAction, FileAccessTestResult>> {
  const results = await Promise.all(
    FILE_ACTIONS.map((action) =>
      testFileAccess({
        location: req.location,
        path: req.path,
        scope: req.scope,
        userId: req.userId,
        action,
      }).then((r) => [action, r] as const),
    ),
  );
  return Object.fromEntries(results) as Record<FilePolicyAction, FileAccessTestResult>;
}
```

- [ ] **Step 4: Run to verify pass**

Run: `./test.sh client unit filePolicies`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add client/src/services/filePolicies.ts client/src/services/filePolicies.test.ts
git commit -m "feat(files): effectiveAccess cascade + testAllActions multi-action helpers"
```

---

## Phase 4 — Frontend components

> Each component task: write the vitest first (render + key interaction), verify fail, implement, verify pass, commit. Components are presentational + hook-driven; mock the service modules in tests.

### Task 9: `Breadcrumbs` component

**Files:**
- Create: `client/src/components/files/Breadcrumbs.tsx`
- Test: `client/src/components/files/Breadcrumbs.test.tsx`

**Interfaces:**
- Produces: `function Breadcrumbs(props: { scopeLabel: string; location: string | null; segments: string[]; onNavigate: (depth: number) => void }): JSX.Element`. `depth` semantics: `-1` = root/shares, `0` = location root, `n` = after the nth path segment. Long segments truncate (`max-w-[12rem] truncate`) with `title`.

- [ ] **Step 1: Write the failing test**

```tsx
// client/src/components/files/Breadcrumbs.test.tsx
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { Breadcrumbs } from "./Breadcrumbs";

describe("Breadcrumbs", () => {
  it("renders scope + location + segments and navigates", () => {
    const onNavigate = vi.fn();
    render(
      <Breadcrumbs scopeLabel="Global" location="gallery" segments={["team", "q1"]} onNavigate={onNavigate} />,
    );
    expect(screen.getByText("Global")).toBeInTheDocument();
    expect(screen.getByText("gallery")).toBeInTheDocument();
    fireEvent.click(screen.getByText("team"));
    expect(onNavigate).toHaveBeenCalledWith(0);
  });
});
```

- [ ] **Step 2: Run to verify failure** — `./test.sh client unit Breadcrumbs` → FAIL.
- [ ] **Step 3: Implement** — render `scopeLabel` (onNavigate(-1)), `location` (onNavigate(0)), then each segment (onNavigate(i)) separated by a chevron; each clickable, truncated with `title`.
- [ ] **Step 4: Run to verify pass** — `./test.sh client unit Breadcrumbs` → PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(files): Breadcrumbs component"`.

### Task 10: `ShareTree` component

**Files:**
- Create: `client/src/components/files/ShareTree.tsx`
- Test: `client/src/components/files/ShareTree.test.tsx`

**Interfaces:**
- Consumes: `listShares`, `listStructure` from `@/services/fileStructure`.
- Produces: `function ShareTree(props: { scope: string | null; selectedLocation: string | null; selectedPrefix: string; onSelect: (location: string, prefix: string) => void; onContextAction: (action: "effective"|"test"|"newFolder"|"upload"|"newPolicy", location: string, prefix: string) => void }): JSX.Element`. Lazy-loads children per prefix on expand; renders an `uploads` share with a read-only badge.

- [ ] **Step 1: Write the failing test**

```tsx
// client/src/components/files/ShareTree.test.tsx
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/services/fileStructure", () => ({
  listShares: vi.fn(),
  listStructure: vi.fn(),
}));
import { listShares, listStructure } from "@/services/fileStructure";
import { ShareTree } from "./ShareTree";

describe("ShareTree", () => {
  beforeEach(() => {
    vi.mocked(listShares).mockResolvedValue([
      { location: "gallery", readOnly: false, hasPolicy: true },
      { location: "uploads", readOnly: true, hasPolicy: false },
    ]);
    vi.mocked(listStructure).mockResolvedValue([
      { name: "team", kind: "folder", path: "team" },
    ]);
  });

  it("lists shares and marks uploads read-only", async () => {
    render(<ShareTree scope={null} selectedLocation={null} selectedPrefix="" onSelect={vi.fn()} onContextAction={vi.fn()} />);
    expect(await screen.findByText("gallery")).toBeInTheDocument();
    expect(screen.getByText("uploads")).toBeInTheDocument();
    expect(screen.getByText(/read-only/i)).toBeInTheDocument();
  });

  it("expands a share to load folders and selects on click", async () => {
    const onSelect = vi.fn();
    render(<ShareTree scope={null} selectedLocation={null} selectedPrefix="" onSelect={onSelect} onContextAction={vi.fn()} />);
    fireEvent.click(await screen.findByText("gallery"));
    expect(onSelect).toHaveBeenCalledWith("gallery", "");
    await waitFor(() => expect(listStructure).toHaveBeenCalledWith("gallery", "", null));
    expect(await screen.findByText("team")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run to verify failure** — `./test.sh client unit ShareTree` → FAIL.
- [ ] **Step 3: Implement** — `useEffect` loads `listShares(scope)` on `scope` change; clicking a share calls `onSelect(loc,"")` and lazy-loads `listStructure`; folder rows recurse; wrap each row in `ContextMenu` emitting `onContextAction`; `read-only` badge for `readOnly` shares (suppress upload/newFolder/newPolicy context items for those). Use `min-h-0 overflow-auto` for internal scroll.
- [ ] **Step 4: Run to verify pass** — PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(files): ShareTree lazy tree of shares/folders"`.

### Task 11: `FilePreview` component

**Files:**
- Create: `client/src/components/files/FilePreview.tsx`
- Test: `client/src/components/files/FilePreview.test.tsx`

**Interfaces:**
- Consumes: `files` SDK (`files.read`, `files.signedUrl`, `files.download`) from `@/lib/app-sdk/files`.
- Produces: `function FilePreview(props: { location: string; scope: string | null; path: string | null }): JSX.Element`. Reuses the text/image/download `previewKind` logic from the old `FileBrowser` (extract a `previewKind(path)` helper here). Text: first 6000 chars; image: signed GET URL; else download-only with button.

- [ ] **Step 1..5:** Test renders "Select a file" when `path` is null; with a `.txt` path mocks `files.read` and asserts the text shows; with `.png` mocks `files.signedUrl` and asserts an `<img>`. Implement, verify, commit (`feat(files): FilePreview pane`).

### Task 12: `EffectiveAccessPanel` component

**Files:**
- Create: `client/src/components/files/EffectiveAccessPanel.tsx`
- Test: `client/src/components/files/EffectiveAccessPanel.test.tsx`

**Interfaces:**
- Consumes: `effectiveAccess` from `@/services/filePolicies`.
- Produces: `function EffectiveAccessPanel(props: { location: string; scope: string | null; path: string | null; onOpenTest: () => void; onManagePolicy: () => void }): JSX.Element`. Renders the resolved cascade (each matching rule, its actions, and which policy wins — the first/longest-prefix one is "winning"). Includes a "Test access…" button (→ `onOpenTest`) and "Manage policy" (→ `onManagePolicy`).

- [ ] **Step 1..5:** Test mocks `effectiveAccess` → two policies, asserts both render and the longest-prefix one is labeled winning; asserts clicking "Test access" fires `onOpenTest`. Implement, verify, commit (`feat(files): EffectiveAccessPanel`).

### Task 13: `TestAccessModal` component

**Files:**
- Create: `client/src/components/files/TestAccessModal.tsx`
- Test: `client/src/components/files/TestAccessModal.test.tsx`

**Interfaces:**
- Consumes: `useUsersFiltered` (`@/hooks/useUsers`), `Combobox` (`@/components/ui/combobox`), `testAllActions` (`@/services/filePolicies`), `Dialog`/`Sheet`.
- Produces: `function TestAccessModal(props: { open: boolean; onOpenChange: (o: boolean) => void; location: string; scope: string | null; path: string }): JSX.Element`. User dropdown (searchable, real users), location/path prefilled + locked. On pick → `testAllActions` → render four rows (read/write/delete/list) each Allowed/Denied + deciding policy + rule. Full-screen sheet on small screens.

- [ ] **Step 1..5:** Test mocks `useUsersFiltered` → `[{id,email}]` and `testAllActions` → mixed results; selects a user; asserts four action rows render with correct Allowed/Denied. Implement, verify, commit (`feat(files): TestAccessModal per-principal four-action tester`).

### Task 14: `PolicyEditorModal` component

**Files:**
- Create: `client/src/components/files/PolicyEditorModal.tsx`
- Test: `client/src/components/files/PolicyEditorModal.test.tsx`

**Interfaces:**
- Consumes: existing `FilePolicyEditor` (`@/components/files/FilePolicyEditor`), `saveFilePolicy`/`deleteFilePolicy`/`listFilePolicies` (`@/services/filePolicies`), `Dialog`/`Sheet`.
- Produces: `function PolicyEditorModal(props: { open: boolean; onOpenChange: (o: boolean) => void; location: string; scope: string | null; path: string; onSaved?: () => void }): JSX.Element`. Loads the best-matching policy (or a draft) for `(location,scope,path)`, wraps `FilePolicyEditor`, handles save/delete with toasts, calls `onSaved`. Reuses `bestPolicyForPath`/`makeDefaultPolicy` logic (extract into a small `policyDraft.ts` util shared with the old logic; or inline). Full-screen sheet on small screens.

- [ ] **Step 1..5:** Test mocks `listFilePolicies` + `saveFilePolicy`; opens modal; edits + saves; asserts `saveFilePolicy` called with the right `(location,path,scope)` and `onSaved` fired. Implement, verify, commit (`feat(files): PolicyEditorModal wrapping FilePolicyEditor`).

### Task 15: `NewShareDialog` component

**Files:**
- Create: `client/src/components/files/NewShareDialog.tsx`
- Test: `client/src/components/files/NewShareDialog.test.tsx`

**Interfaces:**
- Consumes: `saveFilePolicy` (`@/services/filePolicies`), `Dialog`.
- Produces: `function NewShareDialog(props: { open: boolean; onOpenChange: (o: boolean) => void; scope: string | null; onCreated: (location: string) => void }): JSX.Element`. Per the spec's lean: creating a share opens this dialog, which creates the FIRST policy (an empty doc → backend seeds `admin_bypass`) at `(location, path="", scope)`; the share then appears in the root. Validates the location name client-side (reject `workspace`/`uploads`/`temp`/`_repo`/`_tmp`/`_apps` and empty); on success calls `onCreated(location)`.

- [ ] **Step 1..5:** Test mocks `saveFilePolicy`; rejects reserved name with an inline error (asserts `saveFilePolicy` NOT called); accepts `gallery` → asserts `saveFilePolicy` called with `{location:"gallery", path:"", organizationId:scope, policies:{policies:[]}}` and `onCreated("gallery")`. Implement, verify, commit (`feat(files): NewShareDialog creates first policy (seeds admin_bypass)`).

### Task 16: `FolderListing` component

**Files:**
- Create: `client/src/components/files/FolderListing.tsx`
- Test: `client/src/components/files/FolderListing.test.tsx`

**Interfaces:**
- Consumes: `listStructure` (`@/services/fileStructure`), `files` SDK (`signedUrl` PUT + `completeUpload` for upload; `download`, `delete`), `useFiles` is NOT used here (structural listing drives the center pane so un-policied files still appear; content actions go through the SDK and surface 403). `DataTable`, `ContextMenu`, drag-drop.
- Produces: `function FolderListing(props: { scope: string | null; location: string | null; prefix: string; readOnly: boolean; onOpenFolder: (prefix: string) => void; onSelectFile: (path: string) => void; onRowAction: (action: "preview"|"download"|"delete"|"policy"|"test", path: string) => void; onUploaded: () => void }): JSX.Element`. Upload button + drag-drop targeting `(location, scope, prefix)`; hidden when `readOnly`. Folders open on click; files select. Row actions via buttons + context menu. Empty allowed-folder shows "No files here" (not a dead "denied").

**Upload flow:** for each dropped/selected file → `files.signedUrl(path,{method:"PUT",location,scope,contentType})` → browser `PUT` to the URL → `files.completeUpload({path,location,scope,contentType,sizeBytes,sha256})`. Confirm the exact SDK method names in `client/src/lib/app-sdk/files.ts` (there is `signedUrl`; verify a `completeUpload`/`completeSignedUpload` wrapper exists — if not, add one calling `POST /api/files/complete-upload`, with its own test, as a sub-step here).

- [ ] **Step 1: Write the failing test** — mock `listStructure` → one folder + one file; assert both render; assert clicking the folder calls `onOpenFolder`; assert the upload button is absent when `readOnly`. Add a second test: drop a file → asserts `files.signedUrl` then `files.completeUpload` called, then `onUploaded` fired (mock the SDK).
- [ ] **Step 2: Run to verify failure** — FAIL.
- [ ] **Step 3: Implement** — load `listStructure(location,prefix,scope)` on change; render folders-then-files in `DataTable`; upload via the flow above; `readOnly` suppresses upload + delete.
- [ ] **Step 4: Run to verify pass** — PASS.
- [ ] **Step 5: Commit** — `feat(files): FolderListing with upload + drag-drop + row actions`.

---

## Phase 5 — Page assembly + responsive shell

### Task 17: `FilesExplorer` shell (desktop 3-pane)

**Files:**
- Create: `client/src/components/files/FilesExplorer.tsx`
- Test: `client/src/components/files/FilesExplorer.test.tsx`

**Interfaces:**
- Consumes: `useAuth` (`isPlatformAdmin`), `OrganizationSelect` (`@/components/forms/OrganizationSelect`), `Breadcrumbs`, `ShareTree`, `FolderListing`, `FilePreview`, `EffectiveAccessPanel`, `TestAccessModal`, `PolicyEditorModal`, `NewShareDialog`.
- Produces: `function FilesExplorer(): JSX.Element`. Owns state: `scope: string|null` (default `null` = Global), `location: string|null`, `prefix: string`, `selectedFile: string|null`, and modal open flags. `OrganizationSelect` with `showAll={false}` (no "all scopes"); switching scope resets `location/prefix/selectedFile`. Wires context/row actions to the modals. Desktop: 3-column grid `lg:grid-cols-[18rem_minmax(0,1fr)_24rem]`, each pane `min-h-0 overflow-auto`, page never overflows (follow data-table scroll pattern).

- [ ] **Step 1: Write the failing test** — render `FilesExplorer` (mock all child components + `useAuth` → admin); assert the scope selector, "New share" button, `ShareTree`, and `FolderListing` render; selecting a scope value resets the breadcrumb to root. Keep it shallow (mock children to spies) to avoid re-testing leaves.
- [ ] **Step 2: Run to verify failure** — FAIL.
- [ ] **Step 3: Implement** the shell + state wiring + desktop grid.
- [ ] **Step 4: Run to verify pass** — PASS.
- [ ] **Step 5: Commit** — `feat(files): FilesExplorer 3-pane shell + scope/breadcrumb state`.

### Task 18: Responsive behavior (md drawer + sm navigation-stack)

**Files:**
- Modify: `client/src/components/files/FilesExplorer.tsx`
- Test: extend `client/src/components/files/FilesExplorer.test.tsx`

**Interfaces:**
- Produces: at `md`, the preview/ACL pane becomes a `Sheet` (right) toggled when a file is selected or Effective Access opens; the tree may collapse to a rail. At `<md`, single-pane stack: tree behind a hamburger `Sheet`; selecting a folder shows the listing full-width; selecting a file opens preview/ACL as a full-screen `Sheet` with a back affordance; breadcrumbs remain the primary up control. Modals (`TestAccessModal`/`PolicyEditorModal`/`NewShareDialog`) render as full-screen sheets at `<md`. No horizontal page scroll at any breakpoint; long paths truncate with `title`.

**Implementation note:** Use Tailwind breakpoint classes for layout switching (`hidden lg:flex`, `lg:hidden`) plus a small `useMediaQuery` if one exists in the repo (search `client/src/hooks` for `useMediaQuery`/`useIsMobile`; if absent, gate purely with Tailwind classes + always-mount the `Sheet` and toggle `open`). Prefer CSS-only switching where possible to keep the test surface small.

- [ ] **Step 1: Write the failing test** — assert that a "menu"/hamburger trigger exists (for tree) and that the preview pane container carries the responsive classes (query by `data-testid="preview-pane"` and assert `lg:` vs sheet wrapper). Keep assertions on presence of the sheet trigger + testids, not pixel layout.
- [ ] **Step 2: Run to verify failure** — FAIL.
- [ ] **Step 3: Implement** the responsive variants.
- [ ] **Step 4: Run to verify pass** — PASS.
- [ ] **Step 5: Commit** — `feat(files): responsive md-drawer + sm navigation-stack for explorer`.

### Task 19: Swap `Files.tsx` to `FilesExplorer`; delete replaced components

**Files:**
- Modify: `client/src/pages/Files.tsx`
- Delete: `client/src/components/files/FileBrowser.tsx`, `FileBrowser.test.tsx`, `EffectiveAccessTester.tsx`, `EffectiveAccessTester.test.tsx`
- Test: existing page-level coverage; rely on `FilesExplorer.test.tsx`.

**Interfaces:**
- Consumes: `FilesExplorer`.
- Produces: `Files.tsx` becomes a thin wrapper: header + `<FilesExplorer/>`. Remove `FileBrowser`, `EffectiveAccessTester`, the inline `policies`/`activePath`/`testerPath` state, `bestPolicyForPath`/`makeDefaultPolicy` (moved into `PolicyEditorModal`/`NewShareDialog` where still needed).

- [ ] **Step 1: Implement the swap** — replace the body of `Files.tsx` with the header + `<FilesExplorer/>`; delete the four replaced files; remove now-dead imports.
- [ ] **Step 2: Grep for stale imports**

Run: `rg -n "FileBrowser|EffectiveAccessTester" client/src`
Expected: no references except in deleted files (none).

- [ ] **Step 3: Typecheck + lint + vitest**

Run: `cd client && npm run tsc && npm run lint && cd .. && ./test.sh client unit files`
Expected: PASS, no unused-symbol or missing-import errors.

- [ ] **Step 4: Commit**

```bash
git add client/src/pages/Files.tsx
git rm client/src/components/files/FileBrowser.tsx client/src/components/files/FileBrowser.test.tsx client/src/components/files/EffectiveAccessTester.tsx client/src/components/files/EffectiveAccessTester.test.tsx
git commit -m "feat(files): mount FilesExplorer; remove legacy FileBrowser + EffectiveAccessTester"
```

---

## Phase 6 — E2E + verification

### Task 20: Playwright happy-path (`files-explorer.admin.spec.ts`) — desktop

**Files:**
- Create: `client/e2e/files-explorer.admin.spec.ts`

**Interfaces:**
- Consumes: the running stack via the standard admin-auth Playwright fixture (pattern-match `client/e2e/policies-app-direct.admin.spec.ts` for login/setup).

- [ ] **Step 1: Write the spec** — as admin at desktop viewport: select scope (Global) → click "New share" → create `e2e-gallery` → upload a small text file → see it in the listing → click it → preview shows its text → open "Test access" → pick a user → see four per-action results → navigate to an un-policied prefix → see the structural listing (NOT a dead "denied") → trigger a content read that policy denies → see the denied helper with the "add admin_bypass here" affordance.
- [ ] **Step 2: Run** `./test.sh client e2e e2e/files-explorer.admin.spec.ts` → PASS.
- [ ] **Step 3: Commit** — `test(files): Playwright explorer happy-path (desktop)`.

### Task 21: Playwright responsive run (narrow viewport) + screenshots

**Files:**
- Modify: `client/e2e/files-explorer.admin.spec.ts` (parametrize viewport, or add a narrow-viewport test)

**Interfaces:**
- Produces: the same happy-path at a mobile width (e.g. 390×844) asserting: tree reachable via hamburger `Sheet`, listing usable, preview/ACL openable as full-screen sheet, upload + Test Access reachable, and `await expect(page).toHaveNoHorizontalOverflow()`-equivalent (assert `document.scrollingElement.scrollWidth <= clientWidth`).

- [ ] **Step 1: Write the narrow-viewport variant** (reuse the desktop steps via a shared helper; set `test.use({ viewport: { width: 390, height: 844 } })`).
- [ ] **Step 2: Run with screenshots** — `./test.sh client e2e e2e/files-explorer.admin.spec.ts --screenshots` → PASS; eyeball the captured PNGs (per memory: screenshots at `~/Sync/Screenshots/` or the test output dir) for clipping.
- [ ] **Step 3: Commit** — `test(files): Playwright explorer responsive narrow-viewport + screenshots`.

### Task 22: Full pre-completion verification sweep

**Files:** none (verification only).

- [ ] **Step 1: Backend quality** — `cd api && pyright && ruff check . && cd ..` → 0 errors.
- [ ] **Step 2: Regenerate types** — debug stack up; `cd client && npm run generate:types && cd ..` (OPENAPI_URL per `./debug.sh status`); commit if `v1.d.ts` changed.
- [ ] **Step 3: Frontend quality** — `cd client && npm run tsc && npm run lint && cd ..` → PASS.
- [ ] **Step 4: Backend tests** — `./test.sh all`; parse `/tmp/bifrost-<project>/test-results.xml`. Confirm the new file tests pass and nothing regressed (DTO-parity `test_dto_flags.py`, contract-version `test_contract_version.py` — the new `/structure` endpoint adds a route, not a CLI/SDK DTO, so the contract fingerprint should be unaffected; if `test_contract_version.py` reds, follow CLAUDE.md "Keeping CLI, MCP, and manifest in sync" step 4).
- [ ] **Step 5: Client tests** — `./test.sh client unit` (all green) and `./test.sh client e2e e2e/files-explorer.admin.spec.ts`.
- [ ] **Step 6: Drive it live** (per `[[feedback_drive_dont_just_test]]`) — boot debug stack, install the matched CLI in a scratch dir, log in as `dev@gobifrost.com`, click through the explorer as a real admin: create a share, upload, preview, test access against a seeded regular user, revoke `admin_bypass` and confirm the denied helper appears. Note any rough edges as follow-ups.
- [ ] **Step 7: Final commit / branch status** — ensure all work committed on `codex/files-sdk-policies`; do NOT open a PR or merge without explicit user consent (`[[feedback_explicit_merge_consent]]`).

---

## Self-Review

**Spec coverage:**
- No upload → Task 16 (FolderListing upload + drag-drop). ✓
- Hardcoded locations → replaced by shares root from `listShares` (Tasks 4/5/10). ✓
- Flat list → tree + breadcrumbs (Tasks 9/10). ✓
- Instant "Access denied" for admins → seeded `admin_bypass` (Tasks 1/2/3) + structural listing so the tree isn't policy-gated (Tasks 4/5). ✓
- Bespoke org selector → `OrganizationSelect` (Task 17). ✓
- Backend defect 1 (no admin bypass) → Tasks 1/2/3, via seeded visible policy, no evaluator bypass. ✓
- Backend defect 2 (403 vs 404) → Task 6. ✓
- Backend change 2 (structural endpoint, admin-only, excludes workspace/temp, uploads read-only) → Tasks 4/5. ✓
- Backend change 4 (discover-locations) → folded into `/structure` with `location omitted` (Task 5), per open-question lean. ✓
- Mental model: shares/scope/two layers (structural vs content), policied-but-empty shares appear → Task 4 (`has_policy`), Task 10 (render). ✓
- Effective Access (static cascade) + Test Access (per-principal four actions, deciding rule) → Tasks 8/12/13. ✓
- Layout & components (8 new components, remove FileBrowser + inline wiring) → Tasks 9–19. ✓
- Responsive (lg/md/sm, sheets, no h-scroll, truncation) → Tasks 17/18/21. ✓
- Testing (vitest per component, backend unit+e2e, Playwright desktop+narrow+screenshots, existing `files-app-direct.admin.spec.ts` untouched) → Tasks 3,4,5,6,7,8,9–18 (vitest), 20/21. ✓
- Out of scope respected: no cross-org pool, no SDK shape change, no scope-resolution change. ✓
- Open questions resolved per user: single `/structure` endpoint with location-omitted = discover mode (Task 5); "New share" creates first policy seeding `admin_bypass` (Task 15). ✓

**Placeholder scan:** No TBD/TODO; every code step has concrete code. Real harness fixtures confirmed and used: async unit DB `db_session` (`api/tests/conftest.py:134`); sync e2e `e2e_client` + actor fixtures `platform_admin` / `org1_user` / `org1` (each `.headers`), defined in `api/tests/e2e/fixtures/setup.py`; policy-grant helper `grant_file_policy` at `api/tests/e2e/file_policy_helpers.py`. E2E tests are synchronous methods (no `await`). The only remaining "confirm against a neighbor" note is the exact `grant_file_policy` import path — a one-line verification, not a placeholder.

**Type consistency:** `ShareEntry`/`StructureEntry` field names match between `file_structure_service.py` (snake `read_only`/`has_policy`) and `fileStructure.ts` (camel `readOnly`/`hasPolicy`) with the mapping in Task 7. `effectiveAccess`/`testAllActions`/`listShares`/`listStructure` names are stable across producer and consumer tasks. `FileAction` vocabulary (`read/write/delete/list`) is consistent everywhere (NOT the table `create/update`).

---

# NEXT: Reusable / named policy templates (design — next session)

**Status:** Design brainstorm, NOT yet a task plan. Start the next session by brainstorming this with Jack (use `superpowers:brainstorming`) before writing tasks.

## The ask (Jack, verbatim intent)

> "Make policies things you can template out so we can have one `admin_bypass` for example and apply it to many different places."

Today there is no notion of a *named, reusable* policy. The same rule (e.g. `admin_bypass`, `everyone_read`) is **copied inline** everywhere it's used. Jack wants to **define a policy once and apply/reference it across many targets** (many file prefixes, many tables, …), so editing the canonical one updates everywhere — or at least so you stop hand-copying the same JSON.

## Current state (what "a policy" is today)

- **File policies:** `FilePolicy` row (`file_policies` table) keyed by `(organization_id, location, path)`, with a `policies: JSONB` column holding `{policies: [ {name, description, actions, when}, ... ]}`. Resolution: org→global cascade + longest-prefix (`FilePolicyService.load_policy`). Seeded `admin_bypass` is **copied inline** on first create (`shared/file_policies_seed.py`).
- **Table policies:** `Table.access: JSONB` column (contract field `policies`) holding the same shape with table actions (`read/create/update/delete`). Templates exist only as a **client-side insert helper** (`client/src/components/tables/policy-templates.ts` → deep-copies a rule into the doc) — there is no server entity.
- **Shared AST:** both use the same when-expression validator (`api/src/models/contracts/policies.py`), file policies add the `{file:…}` namespace. Functions: `has_role`. User namespace + operators are shared.
- **Frontend templates today** (just-shipped): `client/src/components/{tables,files}/*-policy-templates.ts` + the "Insert template…" dropdown — these **copy** a rule into the editor buffer. That's the "poor man's template" we want to replace/upgrade with something real and reusable.

## The core design question: **reference vs. snapshot**

This is the decision the whole feature hinges on — brainstorm it first.

1. **Insert-time snapshot (copy).** "Apply template X" deep-copies its rules into the target's inline doc (what the client does now, but promote the catalog to the server so it's shared/named/governed). 
   - **Pros:** zero change to evaluation (rules stay inline; the cascade/longest-prefix engine is untouched); no new resolution path; trivially safe.
   - **Cons:** editing the canonical template does NOT propagate — you'd "re-apply" to update, and drift is invisible. Doesn't fully satisfy "edit once, applies everywhere."
2. **Live reference (named policy resolved at evaluation).** The target stores a *reference* (e.g. a rule of the form `{template: "admin_bypass"}` or a separate binding row), resolved against a `PolicyTemplate` catalog when access is evaluated.
   - **Pros:** true "edit once → applies everywhere."
   - **Cons:** new resolution path in BOTH evaluators (file + table); versioning/`when`-namespace compatibility (a table template can't reference `{file:…}` and vice-versa); a referenced template that's edited can silently widen/narrow access across many targets (blast radius — needs an "affected targets" view); validation must expand references; export/Solutions portability (do templates travel with a Solution? are they org-scoped or global?).
3. **Hybrid.** Reference by default, with a "snapshot/detach" action that inlines a copy when someone wants to diverge. (Salesforce-ish: managed vs. customized.)

**Recommendation to pursue in brainstorming:** start at option 2 (live reference) since it's the only one that truly satisfies the ask, but scope it tightly (see MVP below) and design the blast-radius/affected-targets surface up front.

## Open questions to resolve in the brainstorm (before any tasks)

1. **Scope of the catalog.** Org-scoped templates, global templates, or both (cascade like everything else)? Who can create/edit a global template (platform admin / provider org)?
2. **One catalog for files + tables, or per-domain?** They share the AST but differ in actions (`read/write/delete/list` vs `read/create/update/delete`) and the `{file:…}` namespace. A single catalog needs a "kind/applies-to" tag and validation that rejects applying a file-only template to a table (and vice-versa). Likely: one `PolicyTemplate` entity with a `kind: file | table | both` + action-set validation.
3. **Reference shape.** How does a target point at a template? Options: a reserved rule `{template: "<name|id>"}` inside the existing `policies` list (keeps one column, evaluator expands it), OR a separate binding table `(template_id, target_kind, target_key)`. The inline-rule form is less invasive; the binding table is cleaner for "list all targets using template X".
4. **Versioning + propagation semantics.** Live (edits propagate instantly) vs. pinned-version-with-opt-in-upgrade. Live is simpler to build but riskier; pinned needs a version column + an upgrade action.
5. **Blast radius UX (required).** Before saving an edit to a referenced template, show "this affects N targets across M tables / K file prefixes." Need an efficient "where is template X used" query — informs the reference-shape decision (#3).
6. **The seeded `admin_bypass`.** Today it's inlined on create. If templates land, should the seed become a *reference* to the canonical `admin_bypass` template? That'd make "revoke admin_bypass everywhere" a single edit — powerful and dangerous. Decide whether the seed references or keeps copying.
7. **Solutions / export portability.** Do templates travel inside a Solution bundle? If a Solution references a global template the target env doesn't have, install must either carry it or fail closed. ([[project_solutions_implementation]] context.)
8. **Evaluation safety.** A missing/deleted referenced template must **fail closed** (deny), consistent with "unknown file field → null → deny". Cycles (template referencing template) — disallow or bound depth like the AST `_DEPTH_LIMIT`.

## Likely shape of an MVP (to pressure-test in brainstorming — not committed)

- New entity `PolicyTemplate` (org→global cascade): `{ id, organization_id|null, name, kind: file|table|both, description, rules: [ {name, description, actions, when}, ... ], created_by, version? }`. Seed the built-ins (`admin_bypass`, `everyone_read`, …) as global templates.
- **Reference form:** a rule `{ template: "<id>" }` allowed inside the existing `policies` list. Both evaluators expand it at load time (resolve template → splice its rules in) with fail-closed on missing + cycle guard.
- **CRUD surface:** templates are entity mutations → CLI (`bifrost policy-template …`) + MCP thin wrapper + REST, per the "three parallel surfaces" rule (CLAUDE.md). A `GET …/templates/{id}/usages` for blast radius.
- **UI:** the existing "Insert template…" dropdown gains a "reference" mode (insert `{template: id}` instead of a copy) sourced from the server catalog; a small templates admin (list/edit/where-used) — possibly a tab on the Tables and Files policy surfaces, or a dedicated page.
- **Tests:** evaluator expansion (file + table), fail-closed on missing template, cycle guard, cascade resolution, the three-surface DTO parity + contract-version tripwire, where-used query, Solutions round-trip if in scope.

## Files the next session will touch (orientation)

- Backend AST/contracts: `api/src/models/contracts/policies.py` (reference rule shape + validation), new `api/src/models/orm/policy_template.py`, migration.
- Evaluators: `api/src/services/file_policy_service.py` (`load_policy`/`is_allowed` expansion) + the table-policy evaluation path (find via `Table.access` consumers / `api/shared/policies/`).
- Seed: `api/shared/file_policies_seed.py` + `api/shared/policies/probe.py::make_seed_admin_bypass` (decide reference vs copy).
- Surfaces: new router + CLI command + MCP thin wrapper (mirror `roles.py`/`configs.py` pattern); skill-truth regen.
- Frontend: promote `client/src/components/{tables,files}/*-policy-templates.ts` to a server-backed catalog; reference mode in the "Insert template…" dropdown; a where-used / templates admin surface; reuse the shared `JsonYamlEditor` + `PolicyExampleBlock`.

**Do first in the next session:** `superpowers:brainstorming` on reference-vs-snapshot (the §"core design question") and the open questions, THEN `superpowers:writing-plans` for a task-by-task plan. Don't start coding until the reference/versioning/blast-radius semantics are decided with Jack.
