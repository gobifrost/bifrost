# Solutions — v2 Standalone App Model

Status: design approved (architecture + components + testing), pending written-spec review
Date: 2026-06-04
Implements: success-criteria criterion 12 ("App artifact: a Solution's React app builds to
`dist/` and is served from `_apps/`; the Solution surface contains no app `src/`. The app runs
like a normal React app, with an `npm run dev` local loop.")
Sub-spec of: `docs/superpowers/plans/2026-06-04-solutions.md` Sub-plan 6.

> Companion decision: criterion 15 (offline dev loop) builds on the v2 SDK/provider here — the
> `npm run dev` loop talks to a live dev instance through `BifrostProvider`. Sub-plan 7 reuses
> this model.

---

## 1. Problem & the v1 incompatibility

Today every Bifrost app renders **inline** inside the platform's React tree
(`client/src/components/jsx-app/BundledAppShell.tsx` — `<BundledApp/>` as a child component). It
inherits the host's context providers (auth, React Query, theme), returns a **bare `<Routes>`**
(the host owns the single `<BrowserRouter>`), and gets the `bifrost` SDK from
`globalThis.__bifrost_platform` injected before the dynamic import. This is intentional (the
bundler synthesizes the entry, esbuild externalizes React/Router, BundledAppShell mounts it
inline).

"Runs like a normal React app with `npm run dev`" is structurally incompatible with that inline
model: a normal app owns its `createRoot`, its router, and imports the SDK as real code. **A
replace is therefore not backwards compatible** — flipping the single render path to a standalone
root would break every existing app at once (double router, lost context, no SDK).

**Decision: add a v2 model ALONGSIDE v1.** v1 (`inline_v1`) stays exactly as-is; v2
(`standalone_v2`) is the new normal-React model. Backwards compatibility by construction.

## 2. Architecture (approved)

- **Discriminator:** `Application.app_model: Literal['inline_v1', 'standalone_v2']`, default
  `'inline_v1'`. Carried through `ManifestApp` and the bundle manifest. Decoupled from
  solution-management (a v2 app may live in `_repo/`; a v1 app may be solution-managed).
- **Render (v2):** full-page standalone. The app owns `createRoot` at the document root and its
  own `<BrowserRouter basename='/apps/{slug}'>`. No platform shell chrome wraps it.
- **Platform chrome as a library, not a shell:** the SDK ships an **optional `<BifrostHeader>`**
  component (logout, back-to-Bifrost, and the affordances the current platform header exposes).
  Authors compose it if they want the platform header; it is not imposed.
- **SDK/auth (v2):** the app imports the SDK as a **real package** —
  `import { BifrostProvider, useWorkflow, BifrostHeader } from 'bifrost'` — and wraps its root in
  `<BifrostProvider baseUrl token orgScope>`, which owns the authed client, a React Query client,
  and org scope. No `globalThis` proxy. Identical in `npm run dev` (resolves from `node_modules`,
  talks to a live dev instance) and when deployed.
- **Build:** one canonical **server-side Vite build** —
  `SolutionAppBuilder.build(app_id, src_tree) -> _apps/{app_id}/dist/` (index.html + hashed
  assets). Always used for git-connected installs (build from the clone). For disconnected
  installs, `bifrost deploy` MAY run `vite build` locally and ship `dist/` in the bundle, and the
  platform skips its build (fast-path). App `src/` is **transient build input** (in the deploy
  bundle or the git clone) and is **never persisted under `_solutions/`** — §3.6 holds.
- **Serve:** the platform serves the static `dist/` from `_apps/{app_id}/`. The `/apps/{slug}`
  route branches on `app_model`: `inline_v1` → existing `BundledAppShell` inline fetch;
  `standalone_v2` → serve the app's own `index.html` (which boots `createRoot`).

## 3. Components & data flow

### 3.1 `bifrost` web SDK — v2 surface (`client/src/lib/app-sdk/` + published package)
- `BifrostProvider({ baseUrl, token, orgScope, children })` — establishes an authed fetch client +
  a React Query client + org scope via React context. One provider at the root; everything below
  uses ordinary hooks.
- Existing hooks (`useWorkflow`, `useWorkflowQuery`, table hooks, etc.) read from the provider
  context instead of `globalThis.__bifrost_platform` when running under a v2 provider. (v1 path
  unchanged — it still uses the global.)
- `BifrostHeader({ ... })` — optional; renders logout / back-to-Bifrost / current header items,
  driven by the provider's session.
- Packaging: a versioned package the v2 app depends on. `npm run dev` resolves it from
  `node_modules`; the token comes from the dev login (env/`.env`), pointed at a live dev instance.

### 3.2 v2 app scaffold (on disk — a normal Vite project)
```
my-app/
  index.html
  vite.config.ts
  package.json            # depends on "bifrost"
  src/main.tsx            # createRoot + <BifrostProvider><BrowserRouter basename><App/>
  src/App.tsx             # the app's own routes/components
```
`bifrost solution init` (or `bifrost apps create --model standalone_v2`) scaffolds this.

### 3.3 Server-side build service — `api/src/services/solutions/app_build.py`
- `SolutionAppBuilder.build(app_id, src_files: dict[str,bytes], dependencies) -> dist_files` runs
  `vite build` (Node toolchain on the platform) and returns/uploads `dist/` to
  `_apps/{app_id}/dist/`.
- If the bundle already carries a valid `dist/` (disconnected fast-path), skip the build and
  upload the shipped `dist/` directly.
- Never writes app `src/` to `_solutions/`.

### 3.4 Deploy wiring — `SolutionDeployer`
- `SolutionBundle.apps: list[dict]` — each `{id, slug, name, app_model, dependencies,
  src_files | dist_files, access_level}`.
- `_upsert_apps`: upsert the `Application` row stamped with `solution_id` + inherited org scope +
  `app_model`; for `standalone_v2`, run `SolutionAppBuilder.build` (or accept shipped `dist`).
  Ownership guard mirrors workflows/tables (no hijack of a `_repo/` or other-install app).
- `_reconcile_deletions`: also sweep `Application` rows under this `solution_id` absent from the
  bundle (and their `_apps/{id}/` artifacts).
- Git `sync` builds v2 apps from the clone's `src/`.

### 3.5 Render branch — `BundledAppShell` / `/apps/{slug}` route
- The bundle-manifest endpoint surfaces `app_model`. The shell renders `inline_v1` via the
  existing path; for `standalone_v2` it serves/loads the app's own `index.html` from
  `_apps/{id}/dist` so the app boots its own `createRoot`.

## 4. Testing strategy

- **SDK unit (vitest):** `BifrostProvider` context; hooks read provider (not global);
  `<BifrostHeader>` actions. Sibling `*.test.tsx`.
- **Build service (pytest e2e):** `build()` turns minimal `src/` into `_apps/{id}/dist/`; accepts a
  prebuilt `dist/` and skips; `src/` not persisted under `_solutions/`.
- **Deploy wiring (pytest e2e):** v2 app deploy stamps `solution_id` + `app_model=standalone_v2`;
  scoped reconcile; git `sync` builds from clone.
- **Render branch (vitest):** shell picks inline vs standalone on `app_model`; v1 unchanged.
- **Live e2e (criterion 12):** deploy a v2 app → serves its own `index.html` from `_apps/`, mounts
  standalone, a `useWorkflow` call through `BifrostProvider` round-trips against the stack; the
  `npm run dev` loop documented + smoke-verified.
- **Backwards-compat regression:** an `inline_v1` app still renders inline and runs.

## 5. Migration

- One alembic migration: add `app_model` to `applications` (`String(20)`, default `'inline_v1'`,
  `server_default='inline_v1'`). All existing apps remain v1.

## 6. Out of scope (this spec)

- Converting existing v1 apps to v2 (they stay inline; conversion is a separate, opt-in effort).
- iframe isolation for v2 (full-page same-document `createRoot` is sufficient; revisit only if CSS/JS
  isolation between platform and app proves necessary).
- The offline dev loop's data-plane edge cases (criterion 15, Sub-plan 7) — this spec provides the
  SDK/provider it builds on.
