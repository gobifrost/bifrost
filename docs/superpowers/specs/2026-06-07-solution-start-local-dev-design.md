# `bifrost solution start` — first-class local dev for Solutions

Date: 2026-06-07
Worktree: `solutions-success-criteria` · Branch `worktree-solutions-success-criteria` · Draft PR #347
Tracks: shakeout decision **D2** (headline), **D4** (`--org` + install-scoped resolution),
and folds in **F8** (first-run button 404) and **F10** (local resolves global only).
Companion findings: `docs/plans/2026-06-07-solutions-shakeout-findings.md`.

## Problem

The Solution local-dev loop today is multi-step and partly misleading:

- Be logged in → ensure the dev stack is up → `cd apps/<slug>` → `npm install` → `npm run dev`.
- The running app's `useWorkflow("path::fn")` posts to the **shared dev API**'s
  `/api/workflows/execute`. The workflow only resolves if it has been **deployed/registered**
  to that instance — so a brand-new workspace's first button **404s** (F8).
- Even when it resolves, local dev sends no `appId`/`orgScope` (they come from the deployed
  platform's `window.__BIFROST_APP__` bootstrap, which is absent locally), so it can only ever
  hit the **global `_repo/` cascade** — you cannot exercise the install-scoped → org → global
  resolution a deployed Solution actually uses (F10/D4).

There is no single "run this and everything is wired" command. That is the gap D2 names,
benchmarked against `swa start` (Azure Static Web Apps CLI) and `firebase emulators:start`.

## The model (SWA CLI alignment)

The reference tools split **local development** from **deployment**:

| SWA CLI | Bifrost equivalent |
|---|---|
| `swa start` — runs the frontend dev server **and your local functions**, one origin; functions run **locally**, reloading on change; nothing is deployed | **`bifrost solution start`** (this spec) |
| `swa deploy` — pushes to the cloud, separately | **`bifrost solution deploy`** (exists) |

**Our "functions" are the workspace's `@workflow`-decorated Python functions.** They can live in
any folder the developer chooses (`functions/`, `modules/`, `shared/`, …) — there is **no fixed
`workflows/` directory** (per user direction). `bifrost solution start` runs those functions
**locally, in-process**, and proxies all other data-plane calls (tables, knowledge, configs,
ai) to the real dev API. **Nothing is registered or deployed to the platform by `start`** — the
platform is only mutated by `deploy`, where everything adopts the solution's scope
(`bifrost solution deploy --org …`).

This is the central correction over an earlier draft: `start` must **not** push or register
workflows to the shared instance. Local functions run locally; the platform stays untouched.

## What already exists (verified, reused — not rebuilt)

- **Local function host primitive.** `bifrost run <file>` (`api/bifrost/cli.py`) loads a
  workspace `.py` file in-process, puts the **solution root on `sys.path`** (via
  `find_solution_root`, `api/bifrost/solution_descriptor.py`) so arbitrary-layout imports
  (`from modules.x import y`) resolve, finds `@workflow` functions by the `_executable_metadata`
  attribute, and executes one locally — authenticating to the API **only** for the data-plane.
  This is criterion 15's offline dev loop and is exactly the local-functions engine `start`
  needs. `start` reuses this loader/exec path; it does not invent a second one.
- **Tokenless dev-server wiring.** The scaffold's `vite.config.ts`
  (`api/bifrost/commands/solution.py::_v2_scaffold_files`) already discovers the CLI's
  `BIFROST_API_URL` + `BIFROST_ACCESS_TOKEN` (env → walk-up `.env` → `bifrost auth token`
  keyring) and injects them via `define` **only for `serve`** (never `build`, so no token in a
  prod bundle). `start` sets the same env and relies on this.
- **The deployed resolver is canonical.** `/api/workflows/execute` already accepts `app_id`
  (`WorkflowExecutionRequest.app_id`, `api/src/models/contracts/executions.py:115`) and does
  install-scoped resolution: it maps `app_id` → `Application.solution_id` and calls
  `WorkflowRepository.resolve(ref, solution_scope=…)` (`api/src/routers/workflows.py:752-774`).
  The app's `appId` is the **manifest UUID** in `.bifrost/apps.yaml`, which deploy upserts as
  the server `Application.id` (`deploy.py:725`). So the app already knows its own `appId`
  locally. `BifrostProvider`/`useWorkflow` already send `app_id` (body) and `orgScope`
  (`X-Bifrost-Org` header). The only missing piece locally is **feeding the provider those two
  values** — which `start` does via injected Vite env vars (below).

## Architecture

`bifrost solution start` runs two things behind **one origin** the browser talks to:

```
        browser (the app)
              │  http://localhost:<port>
              ▼
   ┌─────────────────────────────┐
   │  local dev proxy (start)     │
   │                              │
   │  /api/workflows/execute ─────┼─►  LOCAL function host
   │     (path::fn refs)          │     (bifrost run engine, in-process)
   │                              │
   │  everything else  ───────────┼─►  real dev API  (tables, knowledge,
   │  (/api/...)                  │     configs, ai — data-plane)
   │                              │
   │  /  (and app assets) ────────┼─►  Vite dev server (npm run dev, HMR)
   └─────────────────────────────┘
```

- **Vite dev server** — `npm run dev` for the chosen app (HMR for TSX/CSS). Started as a child
  process; `start` owns its lifecycle.
- **Local function host** — when the app posts to `/api/workflows/execute` with a `path::fn`
  ref, the proxy resolves it against the **workspace's local files** (import the file, find the
  decorated function, execute via the `bifrost run` engine) and returns the result. UUID refs and
  any non-execute path fall through to the real dev API. The locally-executed function reaches
  the **data-plane** (tables, knowledge, ai) through the same authenticated `BifrostClient` that
  `bifrost run` uses, scoped to the resolved `--org` — so a local function calling
  `sdk.tables.get(...)` reads real dev-API data under the chosen org, exactly as the offline run
  loop already does (criterion 15).
- **Proxy/router** — a small local HTTP server that fronts both: serves the Vite app and
  intercepts `/api/workflows/execute` for local execution, proxying all other `/api/*` to the
  configured `BIFROST_API_URL` with the CLI's token. Single origin avoids CORS and matches the
  deployed shape (app and API same-origin).

### `appId` + org wiring (D4 / F10) — chosen approach

`start` injects two Vite env vars when launching the dev server:

- `VITE_BIFROST_APP_ID` = the chosen app's manifest UUID (its server `Application.id`).
- `VITE_BIFROST_ORG_ID` = the resolved `--org` (or the caller's own org).

The **scaffold's `main.tsx`** gains a local-dev fallback so the provider picks these up when the
platform bootstrap is absent:

```ts
const appId   = boot?.appId   ?? import.meta.env.VITE_BIFROST_APP_ID  ?? null;
const orgScope = boot?.orgScope ?? import.meta.env.VITE_BIFROST_ORG_ID ?? null;
```

Deployed apps are unaffected (they read `boot`). With these set, the local app sends `app_id` +
`X-Bifrost-Org`, so **the local function host can apply the same own-first → org → global
cascade the deployed resolver uses** — the resolution logic is shared, not special-cased
(dovetails with F2). New scaffolds get this for free; already-scaffolded apps need the two-line
`main.tsx` edit — `start` detects a stale `main.tsx` (one lacking the `VITE_BIFROST_APP_ID`
fallback) and prints the exact patch rather than silently failing to scope.

> Note on cascade scope locally: the **own-first** half (the install's own workflow) is served by
> the local function host (it IS the install's source). The **org → global fallback** half
> (`global_repo_access`) is a deployed-instance concept; locally, a `path::fn` not found among
> local files falls through to the real dev API, which applies the org/global cascade for that
> org. This gives a faithful preview of the full cascade under the chosen `--org` without
> registering anything.

## Command surface

```
bifrost solution start [--org <name|uuid>] [--app <slug>] [--port <n>]
```

Run from a Solution workspace root (a `bifrost.solution.yaml` present; reuse
`is_solution_workspace`). Steps:

1. **Auth** — `BifrostClient(require_auth=True)`; on failure, a clear "run `bifrost login`" hint.
2. **API reachable** — confirm the configured `BIFROST_API_URL` answers; if not, hint
   `./debug.sh up`. `start` does **not** boot Docker (that is `./debug.sh`'s job; SWA likewise
   assumes your backend/emulator is available).
3. **Resolve the app** — read `.bifrost/apps.yaml`; pick the `standalone_v2` app, or `--app
   <slug>`; error helpfully on zero/many. Capture its manifest UUID (`appId`).
4. **Resolve org** — `--org` via the existing `RefResolver` (name or UUID; superuser), default =
   caller's own org. → `orgScope`.
5. **Discover local functions** — scan the workspace (any folder layout) for files containing
   `@workflow`-decorated functions, building a `path::fn` → callable map for the local host.
6. **Stale-scaffold check** — if the app's `main.tsx` lacks the `VITE_BIFROST_APP_ID` fallback,
   print the two-line patch (don't rewrite the user's file silently).
7. **Launch** — `npm install` if `node_modules` is absent; start Vite (`npm run dev`) with
   `VITE_BIFROST_API_URL/TOKEN` (existing) + `VITE_BIFROST_APP_ID/ORG_ID` (new); start the local
   proxy/function host; print the single origin URL.
8. **Reload local functions on change** — watch the discovered function files; on change,
   re-import so the next call runs new code ("reload your local functions," SWA-style). App
   TSX/CSS is Vite HMR. **Ctrl-C** stops Vite + the proxy cleanly. Nothing is left on the
   platform because nothing was put there.

## Scaffold changes (folded in)

- **`main.tsx`** — the `VITE_BIFROST_APP_ID` / `VITE_BIFROST_ORG_ID` fallbacks (above).
- **F8 first-run** — ship a trivial matching local function so the scaffold's button works on
  first `start` with no platform round-trip. The scaffolded `App.tsx` ref and the shipped
  function file must agree on the `path::fn`. (Because functions can live anywhere, the scaffold
  picks one conventional location for its own sample, e.g. `functions/hello.py::main`, and the
  scaffolded `App.tsx` references exactly that.)

## Boundaries / non-goals (v1)

- `start` **never** deploys or registers to the platform. Platform mutation is `deploy` only,
  where entities adopt the solution scope (`bifrost solution deploy --org …`).
- `start` does **not** boot Docker; it targets the CLI's logged-in instance for the data-plane.
- The live-reload loop covers **functions (workflows) + app code**. Tables/configs/forms/agents
  are picked up by `deploy`, not by `start`'s reload loop. (Their *values/data* are read live
  from the dev API through the proxy; their *definitions* are a deploy concern.)
- No new platform endpoints. `start` is CLI-local: a proxy + the existing `bifrost run` engine.

## Testing strategy

- **Unit (CLI, `api/tests/unit/`):**
  - Local-function discovery across arbitrary folder layouts (a decorated fn in
    `functions/`, `modules/sub/`); `path::fn` → callable resolution; solution root on `sys.path`.
  - Proxy routing: `/api/workflows/execute` with a known local `path::fn` runs locally; a UUID
    ref and any other `/api/*` path proxy to the upstream (assert with a stub upstream).
  - `--org` resolution via `RefResolver` (name → UUID); default-org fallback.
  - App resolution from `.bifrost/apps.yaml` (zero/one/many; `--app` selection).
  - Stale-`main.tsx` detection + printed patch; fresh scaffold passes the check.
- **Scaffold round-trip:** a freshly scaffolded app's `main.tsx` contains the `VITE_BIFROST_*`
  fallbacks; the shipped sample function's `path::fn` equals the `App.tsx` ref (F8).
- **Client (vitest):** `main.tsx` bootstrap prefers `boot` over the Vite env, and falls back to
  `VITE_BIFROST_APP_ID/ORG_ID` when `boot` is absent — covering the deployed-vs-local boundary.
- **Manual drive (the shakeout bar):** from the scratch workspace, `bifrost solution start
  --org <org>` → open the URL → the sample button **works on first run** (F8 closed) → confirm
  the request carries `app_id` + `X-Bifrost-Org` (install-scoped path exercised, D4/F10) → edit
  the sample function, re-click, see new output (reload loop) → Ctrl-C leaves the platform clean.

## Open items deliberately deferred

- **F2 (centralize solution-first resolution).** This spec relies on the *existing* deployed
  resolver and a local host that mirrors own-first; it does not itself do the F2 consolidation.
  F2 remains its own carefully-scoped change (RESUME Tier 2) and would let local + deployed share
  one `OrgScopedRepository.resolve(name, *, solution_scope=)`. `start` is designed to sit on top
  of that once it lands, without rework.
- **`watch` refusing in a Solution workspace (D1/F4).** Separate, small; tracked in RESUME Tier 3.
