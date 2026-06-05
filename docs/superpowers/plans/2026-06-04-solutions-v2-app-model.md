# Solutions v2 Standalone App Model — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a v2 "standalone React app" model for Solutions — own `createRoot`, own router, the `bifrost` SDK as a real import, an `npm run dev` loop, built to `dist/` and served from `_apps/` — **alongside** the untouched inline-render v1 model (criterion 12).

**Architecture:** A new `Application.app_model` column (`inline_v1` default | `standalone_v2`) discriminates the render path. v2 apps mount full-page via `createRoot` with their own `<BrowserRouter basename='/apps/{slug}'>`; the SDK ships a real `<BifrostProvider>` (auth/session/org) + optional `<BifrostHeader>`. One canonical server-side Vite build (`src → _apps/{id}/dist/`) is always used for git-connected installs; `bifrost deploy` may pre-build + ship `dist/` to skip it. App `src/` is transient build input, never persisted under `_solutions/`.

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy / Alembic, React/Vite/TS, vitest, the `bifrost` CLI, `./test.sh` + `./debug.sh`.

**Spec:** `docs/superpowers/specs/2026-06-04-solutions-v2-app-model-design.md`

---

## Conventions

- All work in this worktree (`solutions-success-criteria`). Commit after every green step with the Co-Authored-By trailer.
- Backend tests via `./test.sh` (stack up once); client tests via `./test.sh client unit`.
- After a `XxxCreate/Update` DTO change: `./test.sh tests/unit/test_dto_flags.py`.
- After API/model changes: regenerate client types from the worktree API container's OpenAPI (extract to a file, `npx openapi-typescript <file> -o client/src/lib/v1.d.ts`).
- **Codex second-opinion gate** at the end (Task 9), scoped to this plan's commit range; triage with `superpowers:receiving-code-review`.

## File structure (created + touched)

- Modify: `api/src/models/orm/applications.py` — add `app_model` column.
- Create: `api/alembic/versions/<rev>_add_app_model.py` — migration.
- Modify: `api/bifrost/manifest.py` (`ManifestApp`) + `api/src/services/manifest_generator.py` + `api/src/services/manifest_import.py` — carry `app_model`.
- Modify: `api/src/models/contracts/applications.py` (`ApplicationPublic`, `ApplicationCreate`) — expose `app_model`.
- Create: `api/src/services/solutions/app_build.py` — `SolutionAppBuilder` (server-side vite build → `_apps/{id}/dist/`).
- Modify: `api/src/services/solutions/deploy.py` — `SolutionBundle.apps`, `_upsert_apps`, reconcile apps.
- Modify: `api/src/models/contracts/solutions.py` + `api/src/routers/solutions.py` — deploy request carries `apps`.
- Modify: `api/bifrost/commands/solution.py` — `_collect_apps` + optional local `vite build`.
- Create: `client/src/lib/app-sdk/provider.tsx` — `BifrostProvider` + context.
- Create: `client/src/components/solutions/BifrostHeader.tsx` — optional platform header for v2 apps.
- Modify: `client/src/components/jsx-app/BundledAppShell.tsx` — branch on `app_model`; v2 standalone mount.
- Modify: `api/src/routers/app_code_files.py:537` (`bundle-manifest`) — surface `app_model`.
- Create: `client/src/lib/app-sdk/provider.test.tsx`, `client/src/components/solutions/BifrostHeader.test.tsx`.
- Create: `api/tests/unit/test_solution_app_build.py`, `api/tests/unit/test_solution_app_deploy.py`, `api/tests/e2e/platform/test_solution_v2_app_e2e.py`.

---

## Task 1: `app_model` column + migration

**Files:** Modify `api/src/models/orm/applications.py`; Create `api/alembic/versions/20260604_add_app_model.py`; Test `api/tests/unit/test_app_model_column.py`.

- [ ] **Step 1: Failing test**

```python
# api/tests/unit/test_app_model_column.py
from src.models.orm.applications import Application

def test_application_has_app_model_default_inline():
    col = Application.__table__.columns["app_model"]
    assert col.default.arg == "inline_v1"
```

- [ ] **Step 2: Run — FAIL** — `./test.sh tests/unit/test_app_model_column.py -v` → KeyError (no column).

- [ ] **Step 3: Add the column** in `applications.py` after `access_level`:

```python
    # Render model: 'inline_v1' (legacy inline render) | 'standalone_v2'
    # (own createRoot + router + real SDK). See the v2 app model spec.
    app_model: Mapped[str] = mapped_column(
        String(20), default="inline_v1", server_default="inline_v1"
    )
```

- [ ] **Step 4: Migration**

```python
# api/alembic/versions/20260604_add_app_model.py
from alembic import op
import sqlalchemy as sa

revision = "20260604_add_app_model"
down_revision = "20260604_add_solutions"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column("applications", sa.Column(
        "app_model", sa.String(length=20), nullable=False, server_default="inline_v1"))

def downgrade() -> None:
    op.drop_column("applications", "app_model")
```

(Set `down_revision` to the current head — verify with `docker exec <api> sh -lc 'cd /app && alembic heads'`.)

- [ ] **Step 5: Rebuild template + run — PASS** — `./test.sh stack reset` then `./test.sh tests/unit/test_app_model_column.py -v` → PASS.

- [ ] **Step 6: Commit** (`feat(db): Application.app_model column (inline_v1 default)`).

## Task 2: Carry `app_model` through manifest + ApplicationPublic

**Files:** Modify `api/bifrost/manifest.py` (`ManifestApp`), `api/src/services/manifest_generator.py` (serialize_app), `api/src/services/manifest_import.py` (`_resolve_app`), `api/src/models/contracts/applications.py` (`ApplicationPublic`, `ApplicationCreate`); Test `api/tests/unit/test_manifest.py` (add an app_model round-trip case).

- [ ] **Step 1: Failing round-trip test** — add to `test_manifest.py`:

```python
def test_app_model_round_trips_through_manifest():
    from bifrost.manifest import ManifestApp
    m = ManifestApp(id="a1", path="apps/x", app_model="standalone_v2")
    assert m.app_model == "standalone_v2"
    # default
    assert ManifestApp(id="a2", path="apps/y").app_model == "inline_v1"
```

- [ ] **Step 2: Run — FAIL** (no field).

- [ ] **Step 3: Add `app_model` to `ManifestApp`** in `manifest.py`:

```python
    app_model: str = Field(default="inline_v1", description="Render model: inline_v1 | standalone_v2")
```
Add serialization in `manifest_generator.py serialize_app(...)`: `app_model=app.app_model`. Add deserialization in `manifest_import.py _resolve_app(...)` `app_values["app_model"] = getattr(mapp, "app_model", "inline_v1")`. Add `app_model: str = Field(default="inline_v1")` to `ApplicationPublic` (read) and `ApplicationCreate` (so CLI/MCP can set it).

- [ ] **Step 4: Run — PASS.** Run `./test.sh tests/unit/test_dto_flags.py` (ApplicationCreate changed); add `app_model` to the CLI apps create flags or to `DTO_EXCLUDES` with a one-line reason if UI/deploy-managed.

- [ ] **Step 5: Commit** (`feat(apps): app_model through manifest + ApplicationPublic`).

## Task 3: `SolutionAppBuilder` — server-side vite build → `_apps/{id}/dist/`

**Files:** Create `api/src/services/solutions/app_build.py`; Test `api/tests/unit/test_solution_app_build.py`.

> Reuse the existing Node toolchain location used by `app_bundler` (it already runs esbuild via a vendored node_modules — see `api/src/services/app_bundler/`). The v2 builder invokes `vite build` on a temp copy of the app `src/` and uploads the `dist/` output to `_apps/{app_id}/dist/` via `SolutionStorage`-style S3 writes to the `_apps/` prefix (reuse the app artifact writer the bundler uses).

- [ ] **Step 1: Failing test** — given a minimal app src dict + a fake "already built" dist, the builder uploads dist and is idempotent; given prebuilt dist in the input, it skips the vite build:

```python
# api/tests/unit/test_solution_app_build.py
import pytest
from src.services.solutions.app_build import SolutionAppBuilder

@pytest.mark.e2e
async def test_prebuilt_dist_is_used_without_building(monkeypatch):
    built = {"index.html": b"<html>v2</html>", "assets/app-abc.js": b"//js"}
    calls = {"vite": 0}
    b = SolutionAppBuilder()
    monkeypatch.setattr(b, "_run_vite_build", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not build")))
    out = await b.build(app_id="11111111-1111-1111-1111-111111111111", src_files={}, dependencies={}, prebuilt_dist=built)
    assert set(out) == set(built)
```

- [ ] **Step 2: Run — FAIL** (module missing).

- [ ] **Step 3: Implement** `SolutionAppBuilder.build(app_id, src_files, dependencies, prebuilt_dist=None)`:
  - If `prebuilt_dist` is provided and non-empty → upload it to `_apps/{app_id}/dist/`, return it (skip build).
  - Else write `src_files` to a tempdir, write a minimal `package.json` (with `dependencies`) + `vite.config.ts` if absent, run `_run_vite_build(tmp)` (subprocess `npx vite build`), read `dist/`, upload to `_apps/{app_id}/dist/`.
  - Never writes to `_solutions/`.
  Keep `_run_vite_build` a thin seam (subprocess) so tests can stub it.

- [ ] **Step 4: Run — PASS.**

- [ ] **Step 5: Commit** (`feat(solutions): SolutionAppBuilder server-side vite build`).

## Task 4: Deploy wiring — `SolutionBundle.apps` + `_upsert_apps` + reconcile

**Files:** Modify `api/src/services/solutions/deploy.py`; Test `api/tests/unit/test_solution_app_deploy.py`.

- [ ] **Step 1: Failing test** — deploy a bundle with one v2 app → `Application` row stamped `solution_id`, scope, `app_model=standalone_v2`; redeploy without it → removed for this install only; a `_repo/` app id collision raises `SolutionDeployConflict`.

```python
# api/tests/unit/test_solution_app_deploy.py (sketch — mirror test_solution_table_deploy.py)
@pytest.mark.e2e
async def test_deploy_v2_app_stamps_model_and_scope(db_session, monkeypatch):
    # stub SolutionAppBuilder.build so no real vite runs
    ...
    await SolutionDeployer(db).deploy(SolutionBundle(solution=sol, apps=[
        {"id": app_id, "slug": "dash", "name": "Dash", "app_model": "standalone_v2",
         "dependencies": {}, "dist_files": {"index.html": "<html></html>"}}]))
    app = await db.get(Application, UUID(app_id))
    assert app.solution_id == sol.id and app.app_model == "standalone_v2"
```

- [ ] **Step 2: Run — FAIL** (`SolutionBundle` has no `apps`).

- [ ] **Step 3: Implement** — add `apps: list[dict] = field(default_factory=list)` to `SolutionBundle`; `_upsert_apps(solution, apps)`: ownership guard (mirror `_upsert_tables`), upsert `Application` (name/slug/repo_path/dependencies/access_level/app_model + `solution_id` + scope) via `Upsert`, then call `SolutionAppBuilder().build(app_id, src_files=app.get("src_files",{}), dependencies=..., prebuilt_dist=app.get("dist_files"))`. Add `Application` to `_reconcile_deletions`. Extend `DeployResult` with `apps_upserted/apps_deleted`.

- [ ] **Step 4: Run — PASS.**

- [ ] **Step 5: Commit** (`feat(solutions): deploy v2 apps (build + scoped reconcile)`).

## Task 5: REST + CLI deploy carry `apps`

**Files:** Modify `api/src/models/contracts/solutions.py` (`SolutionDeployRequest.apps`, response counts), `api/src/routers/solutions.py` (pass `apps`), `api/bifrost/commands/solution.py` (`_collect_apps` from `.bifrost/apps.yaml` + app source dirs; optional local `vite build` when present); Test extend `api/tests/unit/test_cli_headless.py` or a new CLI collector test.

- [ ] **Step 1: Failing test** — `_collect_apps(workspace)` reads `.bifrost/apps.yaml` (keyed by UUID) + the app source dir into `{id, slug, name, app_model, dependencies, src_files}`.

- [ ] **Step 2: FAIL. Step 3:** add `apps` to `SolutionDeployRequest`/`SolutionDeployResponse` + thread through router into `SolutionBundle.apps`; implement `_collect_apps`. For a disconnected deploy, if `npx vite` is available and the app is `standalone_v2`, optionally run `vite build` locally and send `dist_files` instead of `src_files` (fast-path); else send `src_files`. **Step 4: PASS** (run `test_dto_flags.py`). **Step 5: Commit.**

## Task 6: `BifrostProvider` + SDK v2 surface

**Files:** Create `client/src/lib/app-sdk/provider.tsx`; Modify the SDK hooks to read provider context when present (`client/src/lib/app-sdk/use-table.ts` etc. + `client/src/lib/esm-react-shim.ts` globalThis path stays for v1); Test `client/src/lib/app-sdk/provider.test.tsx`.

- [ ] **Step 1: Failing vitest** — `BifrostProvider` supplies `baseUrl`/`token`/`orgScope` via context; a `useBifrostContext()` hook reads it; rendering a child that calls it outside the provider throws a clear error.

```tsx
// provider.test.tsx
import { render, screen } from "@testing-library/react";
import { BifrostProvider, useBifrostContext } from "./provider";
function Probe() { const c = useBifrostContext(); return <span>{c.baseUrl}</span>; }
it("provides base url", () => {
  render(<BifrostProvider baseUrl="https://dev" token="t"><Probe/></BifrostProvider>);
  expect(screen.getByText("https://dev")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run — FAIL** (`./test.sh client unit src/lib/app-sdk/provider.test.tsx`).

- [ ] **Step 3: Implement** `provider.tsx`: a React context holding `{ baseUrl, token, orgScope, queryClient }`, a `BifrostProvider` that creates a `QueryClient` + authed fetch wrapper and provides them, and `useBifrostContext()`. Re-export from the SDK barrel so `import { BifrostProvider } from "bifrost"` works in a v2 app.

- [ ] **Step 4: Run — PASS. Step 5: Commit.**

## Task 7: `BifrostHeader` (optional platform chrome for v2)

**Files:** Create `client/src/components/solutions/BifrostHeader.tsx`; Test sibling `.test.tsx`.

- [ ] **Step 1: Failing vitest** — `BifrostHeader` renders a logout action and a back-to-Bifrost link; clicking logout calls the provider's logout; back navigates to the platform root.

- [ ] **Step 2: FAIL. Step 3:** implement using `useBifrostContext()` for session/logout; render the same header affordances the platform header exposes (logout, back-to-Bifrost, app title). **Step 4: PASS. Step 5: Commit.**

## Task 8: Render branch — v2 standalone mount in BundledAppShell + manifest surfaces app_model

**Files:** Modify `api/src/routers/app_code_files.py:537` (bundle-manifest returns `app_model`); Modify `client/src/components/jsx-app/BundledAppShell.tsx` (branch on `app_model`); Test `client/src/components/jsx-app/BundledAppShell.test.tsx` (render-branch unit) + the live e2e in Task 9-pre.

- [ ] **Step 1: Failing vitest** — given a manifest with `app_model: "standalone_v2"`, the shell renders the standalone container (a `data-testid="solution-v2-app-root"`) and does NOT use the inline `<BundledApp/>` path; with `inline_v1` (or absent) it uses the existing inline path (regression).

- [ ] **Step 2: Run — FAIL.**

- [ ] **Step 3: Implement** — bundle-manifest endpoint adds `app_model` (read from the `Application` row). In `BundledAppShell`, after fetching the manifest, branch: `inline_v1` → existing path unchanged; `standalone_v2` → mount the app's own `index.html`/entry in a full-page container that the app controls via its own `createRoot` (load the v2 entry which calls `createRoot` itself; the shell provides only the mount node + passes `baseUrl`/`token`/`orgScope` the app's `BifrostProvider` consumes — via a small bootstrap that reads them from the page). Keep the v1 branch byte-for-byte unchanged.

- [ ] **Step 4: Run — PASS** (v2 branch + v1 regression). Regenerate client types if the manifest contract type changed. **Step 5: Commit.**

## Task 9: Live e2e + npm run dev loop + verification gate + Codex

**Files:** Create `api/tests/e2e/platform/test_solution_v2_app_e2e.py`; doc the dev loop in the spec/README.

- [ ] **Step 1: Live e2e** — deploy a v2 Solution app (bundle with `dist_files` to skip the server vite build in test, OR stub the builder), then: `GET` the bundle-manifest → `app_model == "standalone_v2"`; the `dist/` is served from `_apps/{id}/`; (criterion 12) the app's `index.html` is fetchable and references its hashed entry. Assert a `_repo/` `inline_v1` app still serves via the inline path (regression).

- [ ] **Step 2: npm run dev loop** — add a scaffolded v2 app fixture (`index.html` + `src/main.tsx` with `createRoot` + `<BifrostProvider>` + `<BrowserRouter>`) and document/smoke-verify `npm run dev` boots it (vite dev server) against a live dev instance. Capture the documented command in the spec.

- [ ] **Step 3: Verification gate** — `cd api && pyright && ruff check .`; `cd client && npm run generate:types && npm run tsc && npm run lint`; `./test.sh all` green (parse JUnit); `./test.sh client unit`; relevant `./test.sh client e2e`.

- [ ] **Step 4: Codex gate** — `codex review --base <v2-plan-start-sha>`; triage via `superpowers:receiving-code-review`; fix confirmed issues with their own failing tests. Record reviewed scope + confirmed fixes + dismissed findings.

- [ ] **Step 5: Commit** any type regen + Codex-driven fixes. Criterion 12 demonstrable: a v2 app builds → dist → `_apps/`, runs standalone, has an `npm run dev` loop; v1 apps unaffected.

---

## Self-Review (done at write time)

- **Spec coverage:** discriminator (Task 1-2), full-page standalone render (Task 8), SDK real package + provider (Task 6), optional header (Task 7), server-side vite build always + prebuilt fast-path (Task 3, 5), deploy/reconcile/scope (Task 4), git-connected build-from-clone (reuses Task 3 via the existing `git_sync` → `SolutionDeployer` path — `apps` flow through the same bundle), src never under `_solutions/` (Task 3), migration (Task 1), backwards-compat regression (Task 8 v1 branch + Task 9 regression), `npm run dev` + live proof (Task 9).
- **Type consistency:** `app_model` value set `inline_v1`/`standalone_v2` used uniformly; `SolutionBundle.apps` dict shape `{id, slug, name, app_model, dependencies, src_files|dist_files, access_level}` consistent across Tasks 4/5; `BifrostProvider`/`useBifrostContext`/`BifrostHeader` names consistent across Tasks 6-8.
- **No placeholders:** each code step shows the code or the exact change; the one inherently-environmental piece (vite build subprocess) is isolated behind `_run_vite_build` so it's stubable and explicit.
