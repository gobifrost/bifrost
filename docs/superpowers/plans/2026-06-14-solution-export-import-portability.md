# Solution Export/Import Portability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a Solution genuinely portable — export EVERYTHING it currently owns (live, not a stale cache), import puts EVERYTHING back into a fresh org, with a clear secrets/config story and a gated full-backup mode carrying encrypted secret values + table data.

**Architecture:** Extend the proven `capture._bundle_for → build_workspace_zip` bundler and the `zip_install.install_zip` (lock → deploy → apply → commit) path (approach A). Export becomes a live rebuild. A `mode=full` export adds ONE Fernet-encrypted blob (`.bifrost/secrets.enc`, password-derived key) carrying config values + table rows; import optionally decrypts and applies it with per-content-type collision prompts. No parallel backup/restore service, no second import path.

**Tech Stack:** FastAPI, SQLAlchemy (async), Pydantic contracts, `cryptography.fernet` (existing `derive_fernet_key`/`decrypt_with_key` in `api/src/core/security.py`), React + openapi-react-query client, Click CLI (`api/bifrost/commands/solution.py`), `./test.sh` (pytest in Docker) + vitest.

**Spec:** `docs/superpowers/specs/2026-06-14-solution-export-import-portability-design.md`

**Constraints (do not violate):** Branch `worktree-solutions-success-criteria` / draft PR #347 — stays DRAFT, NO push, NO merge, NO un-draft. `./test.sh` for tests, never two concurrent in this worktree. Full pre-completion verification (pyright/ruff/tsc/lint + tests) before claiming done. Never write to prod. Mock secrets / dummy clients. No client specifics in the repo. Codex review gate after each phase.

---

## File Structure

| File | Responsibility | Change |
|------|----------------|--------|
| `api/src/routers/solutions.py` | `export_solution` endpoint | **Modify** — live rebuild + `mode`/`password` params (was stale-store read) |
| `api/src/services/solutions/export.py` | `build_workspace_zip` + (DELETE) `SolutionExportStore` | **Modify** — accept encrypted content section; remove the stale store |
| `api/src/services/solutions/capture.py` | `_bundle_for` + value/data gathering | **Modify** — gather config values + table rows for `mode=full`; stop persisting `export_zip` via the store |
| `api/src/services/solutions/secrets_blob.py` | Encrypt/decrypt the content blob; serialize/parse | **Create** — the `.bifrost/secrets.enc` codec |
| `api/src/core/security.py` | Fernet helpers | **Modify** — add `encrypt_with_key` mirror of `decrypt_with_key` (both now live) |
| `api/src/services/solutions/zip_install.py` | `install_zip` apply path | **Modify** — decrypt + apply values/data with collision handling |
| `api/src/services/solutions/deploy.py` | `SolutionBundle` dataclass | **Modify** — optional `config_values` + `table_data` carriers |
| `api/src/models/orm/solutions.py` | `Solution` ORM | **Modify** — add `setup_complete` boolean |
| `api/alembic/versions/*` | migration | **Create** — `setup_complete` column |
| `api/src/models/contracts/solutions.py` | DTOs | **Modify** — export params, install request (password/replace flags), required-config setup response, status |
| `api/bifrost/commands/solution.py` | CLI | **Modify** — `export` subcommand; `install --password/--replace-secrets/--replace-data` |
| `client/src/services/solutions.ts` | client API | **Modify** — export mode/password; install flags; setup-status fetch |
| `client/src/components/solutions/SolutionActionsMenu.tsx` | menu | **Modify** — "Export Workspace" → "Export Solution" |
| `client/src/components/solutions/ExportSolutionDialog.tsx` | export mode picker | **Create** — two-radio + password |
| `client/src/components/solutions/SolutionSetupChecklist.tsx` | required-config setup | **Create** — Setup tab + incomplete badge |
| `client/src/pages/SolutionDetail.tsx` | detail page | **Modify** — wire dialog + setup tab |

---

## Phase 1 — Core round-trip (the release-critical floor)

Live export rebuild + "Export Solution" rename + verify a Shareable bundle round-trips into a fresh org. No secrets/data yet.

### Task 1: Export endpoint rebuilds live from owned entities

**Files:**
- Modify: `api/src/routers/solutions.py:146` (`export_solution`)
- Test: `api/tests/e2e/platform/test_solution_export_live.py` (Create)

- [ ] **Step 1: Write the failing E2E test**

```python
# api/tests/e2e/platform/test_solution_export_live.py
import io
import zipfile
import pytest

pytestmark = pytest.mark.asyncio


async def test_export_reflects_currently_owned_app_not_stale_cache(
    superuser_client, make_solution_with_app
):
    """Regression: export used to serve a stale stored zip, so an app captured
    AFTER the last deploy was missing. Export must rebuild live."""
    sol = await make_solution_with_app()  # captures an app AFTER any deploy
    resp = await superuser_client.get(f"/api/solutions/{sol.id}/export")
    assert resp.status_code == 200
    names = zipfile.ZipFile(io.BytesIO(resp.content)).namelist()
    # The captured app's source directory must be present in the live bundle.
    assert any(n.startswith(f"apps/") for n in names), names
```

- [ ] **Step 2: Run it to verify it fails**

Run: `./test.sh tests/e2e/platform/test_solution_export_live.py -v`
Expected: FAIL — either the fixture is missing or the export omits the app (stale store).
(Check `/tmp/bifrost-<project>/test-results.xml` for the result.)

- [ ] **Step 3: Add the `make_solution_with_app` fixture**

Add to `api/tests/e2e/conftest.py` (or the nearest solutions conftest) a fixture that: creates a solution, registers a standalone_v2 app + a workflow, then calls `SolutionCaptureService.capture(...)` to stamp ownership — WITHOUT a subsequent deploy, so a stale store would miss the app. (Mirror the capture setup already used in `test_cli_solution_capture.py`.)

- [ ] **Step 4: Rewrite the endpoint to rebuild live**

```python
# api/src/routers/solutions.py  (replace the body of export_solution)
async def export_solution(
    solution_id: UUID, ctx: Context, user: CurrentSuperuser
) -> Response:
    """Rebuild the install's workspace bundle LIVE from the entities it
    currently owns, so the export always reflects present ownership (not the
    last capture/deploy). Directly re-installable via the zip-install path."""
    from src.services.solutions.capture import SolutionCaptureService
    from src.services.solutions.export import build_workspace_zip

    sol = await ctx.db.get(SolutionORM, solution_id)
    if sol is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Solution not found")

    bundle = await SolutionCaptureService(ctx.db)._bundle_for(sol, include_imports=True)
    data = build_workspace_zip(bundle)
    filename = f"{sol.slug}-{sol.version or 'unversioned'}.zip"
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
```

(Note: `_bundle_for` is currently "private"; promote it to a public `bundle_for` method on the service in the same edit — rename the def and its one internal caller in `capture()` — so the endpoint isn't reaching through an underscore. Keep behavior identical.)

- [ ] **Step 5: Run the test to verify it passes**

Run: `./test.sh tests/e2e/platform/test_solution_export_live.py -v`
Expected: PASS — the app dir is in the live bundle.

- [ ] **Step 6: Commit**

```bash
git add api/src/routers/solutions.py api/tests/e2e/platform/test_solution_export_live.py api/tests/e2e/conftest.py api/src/services/solutions/capture.py
git commit -m "fix(solutions): export rebuilds live from owned entities (was stale cache)"
```

### Task 2: Remove the stale SolutionExportStore

**Files:**
- Modify: `api/src/services/solutions/export.py` (delete `SolutionExportStore`)
- Modify: `api/src/services/solutions/capture.py` (`capture()` no longer persists `export_zip`)
- Modify: deploy path that wrote the store (grep first)

- [ ] **Step 1: Find every consumer of the store**

Run: `rg -n "SolutionExportStore|export_zip" api/`
Expected: usages in `export.py` (def), `capture.py` (`SolutionCaptureResult.export_zip` + build), the deploy writer, and possibly `solutions.py`. Confirm NONE are read by anything other than the (now-rewritten) export endpoint.

- [ ] **Step 2: Write a test asserting the store class is gone**

```python
# api/tests/unit/test_solution_export_store_removed.py
def test_solution_export_store_is_removed():
    import src.services.solutions.export as export_mod
    assert not hasattr(export_mod, "SolutionExportStore"), (
        "stale export store must be deleted — export rebuilds live now"
    )
```

- [ ] **Step 3: Run it to verify it fails**

Run: `./test.sh tests/unit/test_solution_export_store_removed.py -v`
Expected: FAIL — class still present.

- [ ] **Step 4: Delete the store + its writes**

Delete the `SolutionExportStore` class from `export.py`. In `capture.py`, drop `export_zip` from `SolutionCaptureResult` and stop calling `build_workspace_zip` for storage in `capture()` (the bundle is only built on demand by the export endpoint now). Remove the store write in the deploy path. Remove the dead `export_zip`-related imports. (Per the no-dead-code rule — delete everything only reachable from the store.)

- [ ] **Step 5: Run unit + capture tests**

Run: `./test.sh tests/unit/test_solution_export_store_removed.py tests/e2e/platform/test_solution_export_live.py -v`
Run: `cd api && ruff check . && pyright`
Expected: PASS, 0 ruff/pyright errors (no orphaned imports).

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "refactor(solutions): delete stale SolutionExportStore (export is live now)"
```

### Task 3: Rename "Export Workspace" → "Export Solution" (client)

**Files:**
- Modify: `client/src/components/solutions/SolutionActionsMenu.tsx:72`
- Test: `client/src/components/solutions/SolutionActionsMenu.test.tsx`

- [ ] **Step 1: Write/extend the failing vitest**

```tsx
// in SolutionActionsMenu.test.tsx
it("labels the export action 'Export Solution'", () => {
  render(<SolutionActionsMenu exporting={false} /* …required props */ />);
  expect(screen.getByTestId("export-solution")).toHaveTextContent("Export Solution");
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `./test.sh client unit SolutionActionsMenu`
Expected: FAIL — text is "Export Workspace".

- [ ] **Step 3: Change the label**

In `SolutionActionsMenu.tsx:72`, replace `Export Workspace` with `Export Solution`.

- [ ] **Step 4: Run it to verify it passes**

Run: `./test.sh client unit SolutionActionsMenu`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add client/src/components/solutions/SolutionActionsMenu.tsx client/src/components/solutions/SolutionActionsMenu.test.tsx
git commit -m "feat(solutions): rename Export Workspace -> Export Solution"
```

### Task 4: Round-trip E2E — Shareable export → install into a fresh org

**Files:**
- Test: `api/tests/e2e/platform/test_solution_roundtrip.py` (Create)

- [ ] **Step 1: Write the failing round-trip test**

```python
# api/tests/e2e/platform/test_solution_roundtrip.py
import pytest
pytestmark = pytest.mark.asyncio


async def test_shareable_export_installs_into_fresh_org(
    superuser_client, make_solution_with_app, make_org
):
    src = await make_solution_with_app()  # owns app + workflow + table schema
    export = await superuser_client.get(f"/api/solutions/{src.id}/export")
    assert export.status_code == 200

    target_org = await make_org()
    files = {"file": ("sol.zip", export.content, "application/zip")}
    resp = await superuser_client.post(
        "/api/solutions/install",
        files=files,
        data={"organization_id": str(target_org.id)},
    )
    assert resp.status_code == 200, resp.text
    installed_id = resp.json()["id"]

    # The installed solution owns the app + workflow in the target org.
    detail = await superuser_client.get(f"/api/solutions/{installed_id}")
    body = detail.json()
    assert body["organization_id"] == str(target_org.id)
    # app + workflow present (assert via the solution's entity listing endpoint)
```

- [ ] **Step 2: Run it to verify it fails or passes**

Run: `./test.sh tests/e2e/platform/test_solution_roundtrip.py -v`
Expected: If install already covers apps end-to-end, this may PASS immediately — that's a valid outcome (it proves the round-trip). If it FAILS, the failure pinpoints the install-side gap (e.g. app dist not rebuilt). Fix the gap in `install_zip`/`deploy` minimally, then re-run.

- [ ] **Step 3: Commit**

```bash
git add api/tests/e2e/platform/test_solution_roundtrip.py
git commit -m "test(solutions): shareable export round-trips into a fresh org"
```

### Phase 1 close-out

- [ ] Run `cd api && ruff check . && pyright` — 0 errors.
- [ ] Run `cd client && npm run tsc && npm run lint` — 0 errors.
- [ ] Run `./test.sh tests/e2e/platform/test_solution_export_live.py tests/e2e/platform/test_solution_roundtrip.py tests/unit/test_solution_export_store_removed.py -v` — all PASS.
- [ ] `/codex` review on the Phase 1 diff; triage via `receiving-code-review`.

---

## Phase 2 — Secrets/config on install (Shareable bundles)

Set-on-install for required configs, a `setup_complete` status, a Setup checklist UI, and a loud runtime error on a missing required value. (CLI `--set` already exists.)

### Task 5: `setup_complete` column + migration

**Files:**
- Modify: `api/src/models/orm/solutions.py`
- Create: `api/alembic/versions/<rev>_solution_setup_complete.py`
- Test: `api/tests/unit/test_solution_setup_complete_default.py`

- [ ] **Step 1: Write the failing test**

```python
# api/tests/unit/test_solution_setup_complete_default.py
def test_solution_has_setup_complete_default_true():
    from src.models.orm.solutions import Solution
    col = Solution.__table__.c.setup_complete
    assert col.default.arg is True or col.server_default is not None
```

- [ ] **Step 2: Run it to verify it fails**

Run: `./test.sh tests/unit/test_solution_setup_complete_default.py -v`
Expected: FAIL — no such column.

- [ ] **Step 3: Add the column**

```python
# api/src/models/orm/solutions.py  (with the other Mapped columns)
setup_complete: Mapped[bool] = mapped_column(
    Boolean, default=True, server_default=text("true"), nullable=False
)
```

- [ ] **Step 4: Create + apply the migration**

```bash
cd api && alembic revision -m "solution setup_complete"
```

Edit the new file:

```python
def upgrade() -> None:
    op.add_column("solutions", sa.Column(
        "setup_complete", sa.Boolean(), nullable=False, server_default=sa.text("true")))

def downgrade() -> None:
    op.drop_column("solutions", "setup_complete")
```

Apply to the debug DB (per memory: restart init then api):
```bash
docker restart bifrost-debug-<project>-init-1 && docker restart bifrost-debug-<project>-api-1
```

- [ ] **Step 5: Run it to verify it passes**

Run: `./test.sh tests/unit/test_solution_setup_complete_default.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add api/src/models/orm/solutions.py api/alembic/versions/*setup_complete*.py api/tests/unit/test_solution_setup_complete_default.py
git commit -m "feat(solutions): add setup_complete column (default true)"
```

### Task 6: Required-config setup status — service + endpoint

**Files:**
- Create: `api/src/services/solutions/setup_status.py`
- Modify: `api/src/routers/solutions.py` (new `GET /{id}/setup`)
- Modify: `api/src/models/contracts/solutions.py` (`SolutionSetupItem`, `SolutionSetupStatus`)
- Test: `api/tests/e2e/platform/test_solution_setup_status.py`

- [ ] **Step 1: Write the failing E2E test**

```python
# api/tests/e2e/platform/test_solution_setup_status.py
import pytest
pytestmark = pytest.mark.asyncio


async def test_setup_status_lists_required_unset_configs(
    superuser_client, make_solution_with_required_config
):
    sol = await make_solution_with_required_config(key="api_key", required=True)
    resp = await superuser_client.get(f"/api/solutions/{sol.id}/setup")
    assert resp.status_code == 200
    body = resp.json()
    assert body["setup_complete"] is False
    keys = {i["key"]: i for i in body["items"]}
    assert keys["api_key"]["is_set"] is False
    assert keys["api_key"]["required"] is True
```

- [ ] **Step 2: Run it to verify it fails**

Run: `./test.sh tests/e2e/platform/test_solution_setup_status.py -v`
Expected: FAIL — endpoint 404.

- [ ] **Step 3: Add the contracts**

```python
# api/src/models/contracts/solutions.py
class SolutionSetupItem(BaseModel):
    key: str
    type: str
    required: bool
    is_set: bool
    description: str | None = None

class SolutionSetupStatus(BaseModel):
    setup_complete: bool
    items: list[SolutionSetupItem]
```

- [ ] **Step 4: Implement the status service**

```python
# api/src/services/solutions/setup_status.py
from __future__ import annotations
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.contracts.solutions import SolutionSetupItem, SolutionSetupStatus
from src.models.orm.config import Config
from src.models.orm.solution_config_schema import SolutionConfigSchema
from src.models.orm.solutions import Solution


async def compute_setup_status(db: AsyncSession, solution: Solution) -> SolutionSetupStatus:
    decls = (await db.execute(
        select(SolutionConfigSchema).where(SolutionConfigSchema.solution_id == solution.id)
    )).scalars().all()

    org = solution.organization_id
    set_keys = set((await db.execute(
        select(Config.key).where(
            Config.organization_id == org if org is not None else Config.organization_id.is_(None)
        )
    )).scalars().all())

    items = [
        SolutionSetupItem(
            key=d.key, type=str(d.type), required=bool(d.required),
            is_set=d.key in set_keys, description=d.description,
        )
        for d in decls
    ]
    complete = all(i.is_set for i in items if i.required)
    return SolutionSetupStatus(setup_complete=complete, items=items)
```

- [ ] **Step 5: Add the endpoint**

```python
# api/src/routers/solutions.py
@router.get("/{solution_id}/setup", response_model=SolutionSetupStatus,
            summary="Required-config setup status (admin only)")
async def solution_setup(solution_id: UUID, ctx: Context, user: CurrentSuperuser) -> SolutionSetupStatus:
    from src.services.solutions.setup_status import compute_setup_status
    sol = await ctx.db.get(SolutionORM, solution_id)
    if sol is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Solution not found")
    return await compute_setup_status(ctx.db, sol)
```

- [ ] **Step 6: Add the `make_solution_with_required_config` fixture** (mirror the config-declaration setup in `capture.py` tests: create a `SolutionConfigSchema` row with `required=True` and no matching `Config`).

- [ ] **Step 7: Run it to verify it passes**

Run: `./test.sh tests/e2e/platform/test_solution_setup_status.py -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add api/src/services/solutions/setup_status.py api/src/routers/solutions.py api/src/models/contracts/solutions.py api/tests/e2e/platform/test_solution_setup_status.py api/tests/e2e/conftest.py
git commit -m "feat(solutions): setup-status endpoint lists required-unset configs"
```

### Task 7: Install recomputes `setup_complete`

**Files:**
- Modify: `api/src/services/solutions/zip_install.py` (`install_zip`, after apply)
- Test: `api/tests/e2e/platform/test_solution_setup_status.py` (extend)

- [ ] **Step 1: Write the failing test**

```python
async def test_install_with_set_value_flips_setup_complete(
    superuser_client, make_required_config_zip, make_org
):
    zip_bytes = await make_required_config_zip(key="api_key", required=True)
    org = await make_org()
    files = {"file": ("s.zip", zip_bytes, "application/zip")}
    # install WITHOUT the value → incomplete
    r1 = await superuser_client.post("/api/solutions/install", files=files,
                                     data={"organization_id": str(org.id)})
    sid = r1.json()["id"]
    s1 = await superuser_client.get(f"/api/solutions/{sid}/setup")
    assert s1.json()["setup_complete"] is False
    # install WITH the value → complete
    r2 = await superuser_client.post("/api/solutions/install", files=files,
        data={"organization_id": str(org.id),
              "config_values": '{"api_key": "xyz"}'})
    s2 = await superuser_client.get(f"/api/solutions/{r2.json()['id']}/setup")
    assert s2.json()["setup_complete"] is True
```

- [ ] **Step 2: Run it to verify it fails**

Run: `./test.sh tests/e2e/platform/test_solution_setup_status.py -v`
Expected: FAIL — `setup_complete` not recomputed on install.

- [ ] **Step 3: Recompute after apply**

In `install_zip`, after the `_apply_config_values` block (still inside the lock, after the final commit), add:

```python
from src.services.solutions.setup_status import compute_setup_status
status_now = await compute_setup_status(db, solution)
solution.setup_complete = status_now.setup_complete
await db.commit()
```

- [ ] **Step 4: Run it to verify it passes**

Run: `./test.sh tests/e2e/platform/test_solution_setup_status.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(solutions): install recomputes setup_complete from required configs"
```

### Task 8: Loud runtime error on a missing required config

**Files:**
- Modify: the config-resolution chokepoint a workflow hits (grep `ConfigRepository.get` / `get_config`; the SDK `configs` read path)
- Test: `api/tests/unit/test_required_config_runtime_error.py`

- [ ] **Step 1: Locate the read path**

Run: `rg -n "def get_config|async def get\b" api/src/repositories/config.py`
Identify where a config read returns "not found" to a workflow.

- [ ] **Step 2: Write the failing test**

```python
# api/tests/unit/test_required_config_runtime_error.py
import pytest

@pytest.mark.asyncio
async def test_missing_required_solution_config_raises_actionable_error(make_required_config_decl, db):
    # A declared-required config with no value, read in a solution scope, must
    # raise an error naming the key and how to set it — not a bare None/KeyError.
    from src.repositories.config import ConfigRepository, RequiredConfigUnset
    repo = ConfigRepository(db, org_id=None, is_superuser=True)
    with pytest.raises(RequiredConfigUnset) as ei:
        await repo.require("api_key")
    assert "api_key" in str(ei.value)
    assert "set config" in str(ei.value).lower()
```

- [ ] **Step 3: Run it to verify it fails**

Run: `./test.sh tests/unit/test_required_config_runtime_error.py -v`
Expected: FAIL — `RequiredConfigUnset` / `require` missing.

- [ ] **Step 4: Add the explicit error + `require` accessor**

```python
# api/src/repositories/config.py
class RequiredConfigUnset(RuntimeError):
    def __init__(self, key: str):
        super().__init__(
            f"Required config '{key}' is not set. Set it with "
            f"`bifrost configs set {key} <value>` or in the solution's Setup tab."
        )

# on ConfigRepository:
async def require(self, key: str):
    value = await self.get_config(key)  # existing read
    if value is None:
        raise RequiredConfigUnset(key)
    return value
```

- [ ] **Step 5: Run it to verify it passes**

Run: `./test.sh tests/unit/test_required_config_runtime_error.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add api/src/repositories/config.py api/tests/unit/test_required_config_runtime_error.py
git commit -m "feat(config): RequiredConfigUnset names the key + how to set it"
```

### Task 9: Setup checklist UI + incomplete badge (client)

**Files:**
- Create: `client/src/components/solutions/SolutionSetupChecklist.tsx` (+ `.test.tsx`)
- Modify: `client/src/services/solutions.ts` (fetch setup status, set config value)
- Modify: `client/src/pages/SolutionDetail.tsx` (Setup tab + badge)

- [ ] **Step 1: Add the service + failing vitest**

```ts
// client/src/services/solutions.ts
export async function getSolutionSetup(id: string) {
  return apiClient.get<SolutionSetupStatus>(`/api/solutions/${id}/setup`);
}
```

```tsx
// SolutionSetupChecklist.test.tsx
it("lists required-unset configs and shows a Set control", () => {
  render(<SolutionSetupChecklist items={[
    { key: "api_key", type: "secret", required: true, is_set: false }
  ]} setupComplete={false} onSet={vi.fn()} />);
  expect(screen.getByText("api_key")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /set/i })).toBeInTheDocument();
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `./test.sh client unit SolutionSetupChecklist`
Expected: FAIL — component missing.

- [ ] **Step 3: Implement the component + wire the tab**

Build `SolutionSetupChecklist` (rows of key/type/required, masked input for `type==="secret"`, a Set button calling `onSet(key, value)` → `configs set`). In `SolutionDetail.tsx`, add a "Setup" tab driven by `getSolutionSetup`, and render an "⚠ Incomplete" badge in the header when `setup_complete === false`. Regenerate types first: `cd client && npm run generate:types` (dev stack must be up; use the worktree's port from `./debug.sh status`).

- [ ] **Step 4: Run it to verify it passes**

Run: `./test.sh client unit SolutionSetupChecklist`
Run: `cd client && npm run tsc && npm run lint`
Expected: PASS, 0 errors.

- [ ] **Step 5: Commit**

```bash
git add client/src/components/solutions/SolutionSetupChecklist.tsx client/src/components/solutions/SolutionSetupChecklist.test.tsx client/src/services/solutions.ts client/src/pages/SolutionDetail.tsx client/src/lib/v1.d.ts
git commit -m "feat(solutions): Setup checklist tab + incomplete badge"
```

### Phase 2 close-out

- [ ] `cd api && ruff check . && pyright`; `cd client && npm run tsc && npm run lint` — 0 errors.
- [ ] `./test.sh tests/e2e/platform/test_solution_setup_status.py tests/unit/test_solution_setup_complete_default.py tests/unit/test_required_config_runtime_error.py -v` — PASS.
- [ ] `./test.sh client unit SolutionSetupChecklist SolutionActionsMenu` — PASS.
- [ ] `/codex` review on the Phase 2 diff; triage via `receiving-code-review`.

---

## Phase 3 — Full backup (encrypted secret values)

A `mode=full` export carries config VALUES in one Fernet-encrypted blob; import decrypts and applies with per-type collision handling. (Table data comes in Phase 4 into the same blob.)

### Task 10: `encrypt_with_key` mirror in security.py

**Files:**
- Modify: `api/src/core/security.py`
- Test: `api/tests/unit/test_security_encrypt_with_key.py`

- [ ] **Step 1: Write the failing round-trip test**

```python
# api/tests/unit/test_security_encrypt_with_key.py
from src.core.security import encrypt_with_key, decrypt_with_key

def test_encrypt_decrypt_with_password_roundtrips():
    token = encrypt_with_key("s3cret-value", "correct horse battery staple")
    assert decrypt_with_key(token, "correct horse battery staple") == "s3cret-value"

def test_wrong_password_fails():
    import pytest
    from cryptography.fernet import InvalidToken
    token = encrypt_with_key("v", "pw-A")
    with pytest.raises(InvalidToken):
        decrypt_with_key(token, "pw-B")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `./test.sh tests/unit/test_security_encrypt_with_key.py -v`
Expected: FAIL — `encrypt_with_key` undefined.

- [ ] **Step 3: Add the mirror (revives the dead helper's partner)**

```python
# api/src/core/security.py  (next to decrypt_with_key)
def encrypt_with_key(plaintext: str, secret_key: str) -> str:
    """Encrypt with an explicit password-derived key (mirror of decrypt_with_key).
    Used to build a portable, password-protected solution export."""
    key = derive_fernet_key(secret_key)
    f = Fernet(key)
    encrypted = f.encrypt(plaintext.encode())
    return base64.urlsafe_b64encode(encrypted).decode()
```

- [ ] **Step 4: Run it to verify it passes**

Run: `./test.sh tests/unit/test_security_encrypt_with_key.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/src/core/security.py api/tests/unit/test_security_encrypt_with_key.py
git commit -m "feat(security): encrypt_with_key — password-derived Fernet (revives dead pair)"
```

### Task 11: The secrets-blob codec

**Files:**
- Create: `api/src/services/solutions/secrets_blob.py`
- Test: `api/tests/unit/test_secrets_blob.py`

- [ ] **Step 1: Write the failing test**

```python
# api/tests/unit/test_secrets_blob.py
from src.services.solutions.secrets_blob import (
    SolutionContent, encode_secrets_blob, decode_secrets_blob,
)

def test_blob_roundtrips_values_and_data():
    content = SolutionContent(
        config_values={"api_key": "xyz", "region": "us-east"},
        table_data={"widgets": [{"id": 1, "name": "a"}]},
    )
    blob = encode_secrets_blob(content, password="pw")
    out = decode_secrets_blob(blob, password="pw")
    assert out.config_values == content.config_values
    assert out.table_data == content.table_data

def test_wrong_password_raises():
    import pytest
    from cryptography.fernet import InvalidToken
    blob = encode_secrets_blob(SolutionContent(config_values={"a": "b"}), password="A")
    with pytest.raises(InvalidToken):
        decode_secrets_blob(blob, password="B")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `./test.sh tests/unit/test_secrets_blob.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement the codec**

```python
# api/src/services/solutions/secrets_blob.py
from __future__ import annotations
import json
from dataclasses import dataclass, field
from typing import Any

from src.core.security import encrypt_with_key, decrypt_with_key

BLOB_VERSION = 1


@dataclass
class SolutionContent:
    config_values: dict[str, str] = field(default_factory=dict)
    table_data: dict[str, list[dict[str, Any]]] = field(default_factory=dict)


def encode_secrets_blob(content: SolutionContent, *, password: str) -> str:
    payload = json.dumps({
        "version": BLOB_VERSION,
        "config_values": content.config_values,
        "table_data": content.table_data,
    })
    return encrypt_with_key(payload, password)


def decode_secrets_blob(blob: str, *, password: str) -> SolutionContent:
    payload = json.loads(decrypt_with_key(blob, password))
    return SolutionContent(
        config_values=payload.get("config_values", {}),
        table_data=payload.get("table_data", {}),
    )
```

- [ ] **Step 4: Run it to verify it passes**

Run: `./test.sh tests/unit/test_secrets_blob.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/src/services/solutions/secrets_blob.py api/tests/unit/test_secrets_blob.py
git commit -m "feat(solutions): encrypted secrets-blob codec (.bifrost/secrets.enc)"
```

### Task 12: Bundle carries values; zip writes the blob; export mode param

**Files:**
- Modify: `api/src/services/solutions/deploy.py` (`SolutionBundle` fields)
- Modify: `api/src/services/solutions/capture.py` (`bundle_for` gathers values when asked)
- Modify: `api/src/services/solutions/export.py` (`build_workspace_zip` writes the blob)
- Modify: `api/src/routers/solutions.py` (`export_solution` accepts `mode`/`password`)
- Test: `api/tests/e2e/platform/test_solution_export_full.py`

- [ ] **Step 1: Write the failing E2E test**

```python
# api/tests/e2e/platform/test_solution_export_full.py
import io, zipfile, pytest
pytestmark = pytest.mark.asyncio


async def test_full_export_includes_encrypted_secrets_blob(
    superuser_client, make_solution_with_set_config
):
    sol = await make_solution_with_set_config(key="api_key", value="xyz")
    # full mode requires a password
    bad = await superuser_client.get(f"/api/solutions/{sol.id}/export?mode=full")
    assert bad.status_code == 422
    ok = await superuser_client.get(
        f"/api/solutions/{sol.id}/export?mode=full&password=pw")
    assert ok.status_code == 200
    names = zipfile.ZipFile(io.BytesIO(ok.content)).namelist()
    assert ".bifrost/secrets.enc" in names

    # shareable export must NOT include the blob
    sh = await superuser_client.get(f"/api/solutions/{sol.id}/export")
    sh_names = zipfile.ZipFile(io.BytesIO(sh.content)).namelist()
    assert ".bifrost/secrets.enc" not in sh_names
```

- [ ] **Step 2: Run it to verify it fails**

Run: `./test.sh tests/e2e/platform/test_solution_export_full.py -v`
Expected: FAIL — `mode` ignored / no blob.

- [ ] **Step 3: Extend SolutionBundle**

```python
# api/src/services/solutions/deploy.py  (SolutionBundle fields)
config_values: dict[str, str] = field(default_factory=dict)
table_data: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
```

- [ ] **Step 4: Gather values in `bundle_for` when requested**

```python
# api/src/services/solutions/capture.py
async def bundle_for(self, solution, *, include_imports=False, include_values=False):
    ...
    if include_values:
        bundle.config_values = await self._config_values(solution)  # key -> decrypted value
    return bundle
```

Add `_config_values` reading each declared key's `Config.value` (decrypting secret types via `decrypt_secret`). (Table data added in Phase 4.)

- [ ] **Step 5: Write the blob in `build_workspace_zip`**

```python
# api/src/services/solutions/export.py
def build_workspace_zip(bundle, *, password: str | None = None) -> bytes:
    ...
    if password and (bundle.config_values or bundle.table_data):
        from src.services.solutions.secrets_blob import SolutionContent, encode_secrets_blob
        put(".bifrost/secrets.enc",
            encode_secrets_blob(
                SolutionContent(config_values=bundle.config_values,
                                table_data=bundle.table_data),
                password=password))
```

- [ ] **Step 6: Endpoint accepts `mode`/`password`**

```python
# api/src/routers/solutions.py export_solution signature + body
async def export_solution(solution_id, ctx, user,
                          mode: str = "shareable", password: str | None = None):
    if mode == "full" and not password:
        raise HTTPException(422, "full export requires a password")
    include_values = mode == "full"
    bundle = await SolutionCaptureService(ctx.db).bundle_for(
        sol, include_imports=True, include_values=include_values)
    data = build_workspace_zip(bundle, password=password if include_values else None)
```

- [ ] **Step 7: Run it to verify it passes**

Run: `./test.sh tests/e2e/platform/test_solution_export_full.py -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add -A && git commit -m "feat(solutions): full-mode export writes encrypted secrets blob"
```

### Task 13: Install decrypts + applies values with collision handling

**Files:**
- Modify: `api/src/services/solutions/zip_install.py` (`install_zip`, `_apply_config_values`)
- Modify: `api/src/routers/solutions.py` (`install_solution` gains `password`, `replace_secrets`)
- Modify: `api/src/models/contracts/solutions.py` (install request flags)
- Test: `api/tests/e2e/platform/test_solution_import_secrets.py`

- [ ] **Step 1: Write the failing tests**

```python
# api/tests/e2e/platform/test_solution_import_secrets.py
import pytest
pytestmark = pytest.mark.asyncio


async def test_full_import_fills_empty_secret_slot(
    superuser_client, make_full_backup_zip, make_org):
    zip_bytes = await make_full_backup_zip(values={"api_key": "xyz"}, password="pw")
    org = await make_org()
    files = {"file": ("s.zip", zip_bytes, "application/zip")}
    r = await superuser_client.post("/api/solutions/install", files=files,
        data={"organization_id": str(org.id), "password": "pw"})
    assert r.status_code == 200
    s = await superuser_client.get(f"/api/solutions/{r.json()['id']}/setup")
    assert next(i for i in s.json()["items"] if i["key"] == "api_key")["is_set"]


async def test_full_import_collision_refuses_without_replace_flag(
    superuser_client, make_full_backup_zip, install_with_value, make_org):
    org = await make_org()
    await install_with_value(org, key="api_key", value="EXISTING")
    zip_bytes = await make_full_backup_zip(values={"api_key": "NEW"}, password="pw")
    files = {"file": ("s.zip", zip_bytes, "application/zip")}
    r = await superuser_client.post("/api/solutions/install", files=files,
        data={"organization_id": str(org.id), "password": "pw"})
    assert r.status_code == 409
    assert "api_key" in r.text  # names the colliding key

    r2 = await superuser_client.post("/api/solutions/install", files=files,
        data={"organization_id": str(org.id), "password": "pw",
              "replace_secrets": "true"})
    assert r2.status_code == 200


async def test_wrong_password_rejected(superuser_client, make_full_backup_zip, make_org):
    zip_bytes = await make_full_backup_zip(values={"api_key": "x"}, password="pw")
    org = await make_org()
    files = {"file": ("s.zip", zip_bytes, "application/zip")}
    r = await superuser_client.post("/api/solutions/install", files=files,
        data={"organization_id": str(org.id), "password": "WRONG"})
    assert r.status_code == 422
```

- [ ] **Step 2: Run it to verify it fails**

Run: `./test.sh tests/e2e/platform/test_solution_import_secrets.py -v`
Expected: FAIL — params/decryption not wired.

- [ ] **Step 3: Wire decrypt + collision into `install_zip`**

Add params `password: str | None = None`, `replace_secrets: bool = False` to `install_zip`. Per the spec default — **wrong/missing password refuses the WHOLE import (no code lands either)** — the decrypt happens BEFORE `deployer.deploy`, inside the lock. Only after a successful decrypt does deploy proceed; content is applied after finalize.

```python
# sketch inside install_zip
async with solution_write_lock(solution.id):
    content = None
    secrets_path = workspace / ".bifrost" / "secrets.enc"
    if secrets_path.exists():
        if not password:
            raise BadExportPassword("this bundle carries secrets — a password is required")
        try:
            content = decode_secrets_blob(secrets_path.read_text(), password=password)
        except InvalidToken as exc:
            raise BadExportPassword("wrong password for this bundle") from exc
        # Collision check BEFORE deploy too, so a refused import touches nothing.
        await _assert_no_unforced_collisions(
            db, solution=solution, content=content,
            replace_secrets=replace_secrets, replace_data=replace_data)

    deployer = SolutionDeployer(db)
    result = await deployer.deploy(bundle, force=force)
    await db.commit()
    await result.finalize_s3()

    if config_values:
        await _apply_config_values(db, solution=solution,
            config_values=config_values, deployer_email=deployer_email)
    if content is not None:
        await _apply_content(db, solution=solution, content=content,
            replace_secrets=replace_secrets, replace_data=replace_data,
            deployer_email=deployer_email)
    await db.commit()
```

Implement two helpers:
- `_assert_no_unforced_collisions(...)` — pure check: a config key whose `Config.value` is already set, or a table that already has rows, that the bundle would overwrite AND the matching `replace_*` flag is False → raise `ContentCollision(ValueError)` naming every colliding key/table. (Runs before deploy so a refused import touches nothing.)
- `_apply_content(...)` — applies values (empty slots always; colliding only when `replace_secrets`) reusing `_apply_config_values`, and table data in Phase 4. Re-checks nothing (the assert already passed under the lock).

- [ ] **Step 4: Map the new errors in `install_solution`**

`BadExportPassword` → 422; `ContentCollision` → 409. Add `password`, `replace_secrets`, `replace_data` as `FastapiForm()` params and thread them into `install_zip`.

- [ ] **Step 5: Run it to verify it passes**

Run: `./test.sh tests/e2e/platform/test_solution_import_secrets.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat(solutions): import decrypts secrets blob with per-key collision handling"
```

### Task 14: Export dialog + import password/replace prompts (client)

**Files:**
- Create: `client/src/components/solutions/ExportSolutionDialog.tsx` (+ `.test.tsx`)
- Modify: `client/src/services/solutions.ts` (export mode/password; install flags)
- Modify: `client/src/pages/SolutionDetail.tsx` (open dialog; collision prompt)

- [ ] **Step 1: Add service params + failing vitest**

```ts
// solutions.ts
export async function exportSolution(id: string, mode: "shareable" | "full", password?: string) {
  const q = new URLSearchParams({ mode, ...(password ? { password } : {}) });
  return apiClient.getBlob(`/api/solutions/${id}/export?${q}`);
}
```

```tsx
// ExportSolutionDialog.test.tsx
it("requires a password when Full backup is selected", async () => {
  render(<ExportSolutionDialog onExport={vi.fn()} />);
  await userEvent.click(screen.getByLabelText(/full backup/i));
  expect(screen.getByLabelText(/password/i)).toBeRequired();
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `./test.sh client unit ExportSolutionDialog`
Expected: FAIL — component missing.

- [ ] **Step 3: Build the dialog + wire collision prompt**

Two-radio dialog (Shareable default / Full backup), password field shown+required for Full. On import 409 collision, surface a confirm ("Replace existing secret values?") that re-posts with `replace_secrets=true`. Regenerate types if contracts changed.

- [ ] **Step 4: Run it to verify it passes**

Run: `./test.sh client unit ExportSolutionDialog`; `cd client && npm run tsc && npm run lint`
Expected: PASS, 0 errors.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(solutions): export-mode dialog + import replace prompt"
```

### Task 15: CLI `export` subcommand + install flags

**Files:**
- Modify: `api/bifrost/commands/solution.py` (new `export` command; `install --password/--replace-secrets/--replace-data`)
- Test: `api/tests/unit/test_cli_solution_export.py` (Create), extend `test_cli_solution_*`

- [ ] **Step 1: Write the failing CLI test**

```python
# api/tests/unit/test_cli_solution_export.py
from click.testing import CliRunner
from bifrost.commands.solution import solution

def test_export_full_requires_password(monkeypatch):
    runner = CliRunner()
    # full without --password → usage error before any HTTP
    res = runner.invoke(solution, ["export", "some-slug", "--mode", "full"])
    assert res.exit_code != 0
    assert "password" in res.output.lower()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `./test.sh tests/unit/test_cli_solution_export.py -v`
Expected: FAIL — no `export` command.

- [ ] **Step 3: Add the `export` command + install flags**

`bifrost solution export <id-or-slug> [--mode shareable|full] [--password …] [--out file.zip]` → GET the export endpoint, write bytes. Validate full⇒password client-side. Add `--password`, `--replace-secrets`, `--replace-data` to `install_cmd` and pass through as form fields. (`--set` already exists.) CLI is non-interactive: a 409 collision without `--replace-*` prints the colliding keys and exits non-zero.

- [ ] **Step 4: Run it to verify it passes**

Run: `./test.sh tests/unit/test_cli_solution_export.py -v`
Expected: PASS.

- [ ] **Step 5: DTO parity + contract tripwire**

Run: `./test.sh tests/unit/test_dto_flags.py tests/unit/test_contract_version.py -v`
If the install DTO changed shape, bump `CONTRACT_VERSION` in BOTH `api/shared/contract_version.py` and `api/bifrost/contract_version.py` and refresh the fingerprint (per CLAUDE.md). Commit that with this task.

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat(cli): solution export subcommand + install password/replace flags"
```

### Phase 3 close-out

- [ ] `cd api && ruff check . && pyright`; `cd client && npm run tsc && npm run lint` — 0 errors.
- [ ] `./test.sh tests/e2e/platform/test_solution_export_full.py tests/e2e/platform/test_solution_import_secrets.py tests/unit/test_secrets_blob.py tests/unit/test_security_encrypt_with_key.py tests/unit/test_cli_solution_export.py -v` — PASS.
- [ ] `/codex` review on the Phase 3 diff; triage via `receiving-code-review`.

---

## Phase 4 — Include data (table rows)

Table rows ride in the same encrypted blob; import applies per-table, wholesale.

### Task 16: `bundle_for` gathers table rows for full mode

**Files:**
- Modify: `api/src/services/solutions/capture.py` (`_table_data`)
- Test: `api/tests/unit/test_solution_table_data_bundle.py`

- [ ] **Step 1: Find how table rows are read**

Run: `rg -n "class Table\b|table.*rows|TableRow|def list_rows|data" api/src/models/orm/tables.py api/src/repositories/tables.py | head`
Identify the row-read API for a Bifrost table.

- [ ] **Step 2: Write the failing test**

```python
# api/tests/unit/test_solution_table_data_bundle.py
import pytest
pytestmark = pytest.mark.asyncio

async def test_bundle_includes_table_rows_when_requested(db, make_solution_with_table_rows):
    from src.services.solutions.capture import SolutionCaptureService
    sol = await make_solution_with_table_rows(table="widgets", rows=[{"id": 1, "name": "a"}])
    bundle = await SolutionCaptureService(db).bundle_for(sol, include_values=True, include_data=True)
    assert bundle.table_data["widgets"] == [{"id": 1, "name": "a"}]
```

- [ ] **Step 3: Run it to verify it fails**

Run: `./test.sh tests/unit/test_solution_table_data_bundle.py -v`
Expected: FAIL — `include_data` / `table_data` unpopulated.

- [ ] **Step 4: Implement `_table_data` + the `include_data` flag**

Add `include_data=False` to `bundle_for`; when set, for each owned table read its rows via the table repo and populate `bundle.table_data[name]`. Surface a row-count cap (log + truncate with a clear warning if a table exceeds a sane limit, e.g. 50k rows — no silent truncation, per the no-silent-caps rule).

- [ ] **Step 5: Run it to verify it passes**

Run: `./test.sh tests/unit/test_solution_table_data_bundle.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat(solutions): full export bundles table rows (capped, logged)"
```

### Task 17: Export `include_data`; import applies table data with collision

**Files:**
- Modify: `api/src/routers/solutions.py` (`export_solution` gains `include_data`; default off)
- Modify: `api/src/services/solutions/zip_install.py` (`_apply_content` writes table rows)
- Test: `api/tests/e2e/platform/test_solution_import_data.py`

- [ ] **Step 1: Write the failing round-trip-with-data test**

```python
# api/tests/e2e/platform/test_solution_import_data.py
import pytest
pytestmark = pytest.mark.asyncio


async def test_full_export_with_data_restores_rows_in_fresh_org(
    superuser_client, make_solution_with_table_rows, make_org):
    sol = await make_solution_with_table_rows(table="widgets", rows=[{"id": 1, "name": "a"}])
    exp = await superuser_client.get(
        f"/api/solutions/{sol.id}/export?mode=full&password=pw&include_data=true")
    assert exp.status_code == 200
    org = await make_org()
    files = {"file": ("s.zip", exp.content, "application/zip")}
    r = await superuser_client.post("/api/solutions/install", files=files,
        data={"organization_id": str(org.id), "password": "pw"})
    assert r.status_code == 200
    # widgets table in the target org now has the row (assert via tables API)


async def test_data_collision_refuses_without_replace_data(
    superuser_client, make_solution_with_table_rows, install_with_rows, make_org):
    org = await make_org()
    await install_with_rows(org, table="widgets", rows=[{"id": 9, "name": "old"}])
    sol = await make_solution_with_table_rows(table="widgets", rows=[{"id": 1, "name": "new"}])
    exp = await superuser_client.get(
        f"/api/solutions/{sol.id}/export?mode=full&password=pw&include_data=true")
    files = {"file": ("s.zip", exp.content, "application/zip")}
    r = await superuser_client.post("/api/solutions/install", files=files,
        data={"organization_id": str(org.id), "password": "pw"})
    assert r.status_code == 409 and "widgets" in r.text
    r2 = await superuser_client.post("/api/solutions/install", files=files,
        data={"organization_id": str(org.id), "password": "pw", "replace_data": "true"})
    assert r2.status_code == 200
```

- [ ] **Step 2: Run it to verify it fails**

Run: `./test.sh tests/e2e/platform/test_solution_import_data.py -v`
Expected: FAIL — `include_data` ignored / data not applied.

- [ ] **Step 3: Wire export `include_data` + import apply**

`export_solution` gains `include_data: bool = False`; pass to `bundle_for(include_data=...)`. In `_apply_content`, for each table in `content.table_data`: if the target table is empty → insert all rows; if non-empty → collision; apply only when `replace_data` (wholesale: clear table rows, insert bundle rows). Name colliding tables in the `ContentCollision` error alongside any secret keys.

- [ ] **Step 4: Run it to verify it passes**

Run: `./test.sh tests/e2e/platform/test_solution_import_data.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(solutions): import restores table data, per-table wholesale w/ collision"
```

### Task 18: Export dialog "Include table data" + row-count preview (client)

**Files:**
- Modify: `client/src/components/solutions/ExportSolutionDialog.tsx` (+ test)

- [ ] **Step 1: Extend the failing vitest**

```tsx
it("offers Include table data only in Full backup mode", async () => {
  render(<ExportSolutionDialog onExport={vi.fn()} />);
  expect(screen.queryByLabelText(/include table data/i)).toBeNull();
  await userEvent.click(screen.getByLabelText(/full backup/i));
  expect(screen.getByLabelText(/include table data/i)).toBeInTheDocument();
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `./test.sh client unit ExportSolutionDialog`
Expected: FAIL.

- [ ] **Step 3: Add the checkbox + thread `include_data` through `exportSolution`.**

(The Full-backup radio already gates the password; a nested "Include table data" checkbox sets `include_data`. Show a warning line that data may contain sensitive records.)

- [ ] **Step 4: Run it to verify it passes**

Run: `./test.sh client unit ExportSolutionDialog`; `cd client && npm run tsc && npm run lint`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(solutions): export dialog include-table-data toggle"
```

### Phase 4 close-out + full verification

- [ ] `cd api && ruff check . && pyright` — 0 errors.
- [ ] `cd client && npm run generate:types && npm run tsc && npm run lint` — 0 errors.
- [ ] `./test.sh all` — backend unit + e2e green (run on a fresh clone / one session per the e2e-session note in memory).
- [ ] `./test.sh client unit` — green.
- [ ] `./test.sh client e2e` — export→import happy path green (if a Playwright spec was added).
- [ ] **Drive it live** (per `[[feedback_drive_dont_just_test]]`): debug stack up (`BIFROST_FORCE_PORT=1 ./debug.sh up`), build a covi-csp-shaped solution, Export Solution (Full + data, password), install into a fresh org via UI, confirm the app renders + workflows run + secrets/rows landed.
- [ ] `/codex` review on the Phase 4 diff; triage via `receiving-code-review`.

---

## Self-review notes (author)

- **Spec coverage:** D1 (Task 1), D2 (Task 3), D3/D4 (Tasks 10–12), D5 (Tasks 13/17), D6 (Tasks 5–9), D7 (architecture — every task extends the existing path), D8 (Tasks 16–17, schema-before-data noted), D9 (out of scope — no tasks). The wrong-password=refuse-whole-import default IS implemented in Task 13: decrypt + collision-assert run BEFORE `deployer.deploy`, inside the lock, so a refused import lands nothing (code or content) — matching the spec's stated default.
- **Contract tripwire:** Task 15 Step 5 covers the `CONTRACT_VERSION` bump if the install DTO shape changes.
- **Migration:** Task 5 follows the memory runbook (restart init then api on the debug stack).
- **Confirm with Jack at Phase 3 start (not a blocker):** the plan chose strict atomic "nothing lands on bad password" (decrypt-before-deploy). If he'd rather let code/schema deploy and only fail content, the change is local to Task 13's ordering.
