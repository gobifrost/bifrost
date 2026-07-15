---
name: migrate
description: Migrate a legacy Bifrost v1 (inline) app and its backing entities into a clean, installable v2 Solution. Use when the user wants to move an existing _repo app into Solutions, modernize a v1 inline app to standalone_v2, capture loose workflows/tables/configs into a Solution, or standardize/rename a messy workspace. Trigger phrases — "migrate this app to a solution", "move X into a solution", "v1 to v2", "/migrate", "convert my app to standalone v2", "capture these workflows into a solution".
---

# Bifrost Migrate (v1 → v2 Solution)

Move a legacy inline (`inline_v1`) app and its non-shared backing entities into a clean,
installable **Solution** (`standalone_v2`), with standardized folders and renamed workflows.

This skill is **judgment-heavy orchestration**, not a one-shot command. It drives existing
primitives (`bifrost solution migrate-app`, `bifrost solution scaffold-app`, `bifrost solution
start`, `bifrost solution swap-slugs`, `bifrost solution capture`) and makes the per-app calls a
deterministic command can't: which shadcn components to add, whether layout/theme translate, what
is safe to capture vs. irreducibly shared.

## The hard rule of why this exists (read first)

- **A v1 app CANNOT live in a Solution.** Deploy hard-rejects non-`standalone_v2`. Capture refuses
  inline_v1 apps (it would build an uninstallable bundle). So you do **not** "capture the app" —
  you **re-author it as v2** and capture its **backing tables/workflows/configs**.
- **The v1→v2 import surface gap is large.** v1 `import … from "bifrost"` injects ~40 shadcn UI
  components + React + react-router + lucide + `cn`/`format` at runtime. The v2 `bifrost` SDK
  exports only: `BifrostProvider`, `useBifrostContext`, `BifrostHeader`, `useWorkflow`, `useTable`,
  `useInfiniteTable`, `tables`. **No UI components, no React, no router.** Nearly every v1 import
  line must be rewritten — that's what `bifrost solution migrate-app` does (deterministically).

## Who runs what

| Action | Who | Why |
|---|---|---|
| `bifrost solution migrate-app`, `bifrost solution scaffold-app`, `solution capture --dry-run`/apply, `solution swap-slugs`, `bifrost api GET …`, `npx shadcn add`, file writes under the new app dir | **Agent** | non-interactive, scoped |
| `bifrost solution start` | **Agent** starts it; **user** drives the browser | long-running dev server; user confirms design |
| `bifrost watch` / `sync` / `push` / `git push` | **User** | broad blast radius, deploy cadence |

## Gather org preferences up front (AskUserQuestion)

Before touching code, collect the conventions — they shape every later step:

1. **Workflow rename convention.** Default offered: `{domain}_{verb}_{noun}` (e.g.
   `orders_sync_records`). Renames MUST rewrite every ref (this skill does that — see step 6).
2. **Folder layout.** Default: flat `workflows/` + flat `modules/` (NOT nested "feature" dirs).
   Standardize whatever mess `_repo` is in.
3. **Which app(s)** to migrate, and the **target Solution** (existing install id, or create and bind
   a new one with `bifrost solution create` / `init`).
4. **Confirm-in-browser or skip.** Offer to skip the browser-confirm loop once the user trusts the
   output.

## Environment

Work in the **debug stack** (`./debug.sh status` for the URL; `dev@gobifrost.com` / `password`),
never production. Install the API-matched CLI in a scratch venv (see repo `CLAUDE.md` §"Spinning up
the dev environment"). All `bifrost` calls below use that scratch CLI.

---

## Per-app migration flow

Do ONE app at a time, fully, before the next. Each step gates the following.

### 1. Read the v1 app + walk its dependencies

- Read the v1 app source under `_repo/{repo_path}/` (`bifrost api GET /api/files/...` or
  `bifrost pull` into a read-only fixture dir).
- Compute the **dependency closure + outside references** with the capture dry-run against the
  TARGET install, seeding the workflows/tables/configs the app uses:
  ```bash
  bifrost solution capture <solution_id> --workflow <wf> --table <tbl> --config <key> --dry-run
  ```
  The dry-run prints (a) the forward closure it pulls in and (b) **outside-reference warnings** —
  entities OUTSIDE your selection that still point at something inside it. **This is the crown
  jewel:** it answers "is anything else using this?". An outside-referenced entity is a candidate
  for the **shared-entity report** (step 9), not an automatic capture.

### Fast path — `bifrost solution migrate-app` does steps 2–5 deterministically

`migrate-app` (and `scaffold-app`) MUST run from inside a Solution workspace — so create and bind one
FIRST with `solution create` (or its `init` alias), then run `migrate-app` from that dir. The SOURCE argument is a real
filesystem path to the v1 app's source (pull it locally first with `bifrost pull`, or point at a
local checkout) — NOT a `_repo/...` URL.

```bash
mkdir <new-slug>-workspace && cd <new-slug>-workspace
bifrost solution create . --slug <solution-slug> --name "<Title>"
bifrost solution migrate-app /path/to/v1/apps/<old-slug> <new-slug> --title "<Title>" --api-url <debug-url>
```
This scaffolds the v2 app, ports the v1 `pages/`+`components/` (all source files, incl. `.ts`
helpers + `_layout.tsx`), runs the deterministic v2
import rewrite, installs the exact shadcn components the app uses (+ radix-ui, sonner, the combobox
recipe), and then **STOPS and prints a judgment checklist** — it never silently wires routes,
builds, or deploys. The checklist surfaces exactly what needs you: multi-route App.tsx wiring,
`// TODO(migrate)` unresolved imports, v1-only hooks with no v2 equivalent (`useUser`,
`useAppState`, `RequireRole`), workflow UUID-ref rewrites, the in-browser design check, and the
cutover order (swap-slugs → capture LAST). Do those, then jump to step 6's wiring + step 8.

The steps below explain what `migrate-app` automates (and are the manual fallback if an app's
shape is non-standard enough that you'd rather drive each step).

### 2. Scaffold the v2 app

```bash
bifrost solution scaffold-app <new-slug> --path apps/<new-slug> --api-url <debug-url>
```
Use a TEMPORARY slug (e.g. `<oldslug>-v2`); the live slug is swapped in at cutover (step 7). The
scaffold writes a working `standalone_v2` skeleton: `package.json` (depends on `bifrost` from the
instance), `vite.config.ts` (tokenless local dev), `main.tsx` (BifrostProvider + BrowserRouter +
basename, reads platform boot or dev env), `App.tsx` (imports `BifrostHeader`, `useWorkflow`).

### 3. Port the pages

Copy the v1 page/component TSX into the new app dir (flat `pages/` + `components/` is the v2
shape). Don't rewrite imports by hand yet — step 4 does it.

### 4 + 5. Rewrite imports + install shadcn (what `solution migrate-app` automates)

`solution migrate-app` performs this whole step for you — it's deterministic because the v1
`"bifrost"` surface is a fixed, known set, so every symbol has a known v2 home. It splits the
single `from "bifrost"` line by ORIGIN:
- shadcn UI (`Button`, `Card`, `Dialog`, `Table`, …) → `@/components/ui/<component>`
- React (`useState`, `Fragment`, …) → `react`; router (`Link`, `useNavigate`, …) → `react-router-dom`
- `cn`/`format` → `@/lib/utils`; `toast` → `sonner`; lucide icons → `lucide-react`
- **hooks STAY in `bifrost`**: `useWorkflowQuery` (READ — auto-runs, `{data,refresh}`),
  `useWorkflowMutation` (ACTION — `{mutate}`), `useWorkflow`, `useTable`/`useInfiniteTable`/`tables`
- JS globals (`Set`, `Map`) + bare `navigate` → dropped; unknowns → kept with a `// TODO(migrate)` marker
- **`src/components/ui/` is NEVER touched** (real shadcn source — rewriting it would corrupt it)

It then computes the exact shadcn-add list from those imports and installs it (the scaffold already
ships Tailwind v4 + `components.json` (radix-rhea) + `cn` + the `.dark` token layer, so `shadcn add`
works with no setup), plus `radix-ui` (the umbrella pkg the radix-rhea components import) and
`sonner` (toast). `Combobox`/`MultiCombobox` are shadcn RECIPES — the add-list includes their
`popover`+`command` primitives and `migrate-app` vendors the `combobox.tsx` wrapper.

**Do not** hand-write stub UI components or add a `@bifrost/ui` package — a stub/no-Tailwind app
renders UNSTYLED. Verify with `vite build` (step 8) AND a screenshot, not just a build.

(If you're driving the steps manually instead of `migrate-app`, the same logic lives in
`bifrost.migrate_v2` — but `migrate-app` is the supported entry point.)

### 6. Derive package.json + wire provider/theme/header/basename

- **package.json**: union of the scaffold's deps + everything the import scan surfaced (every
  `lucide-react`, `react-router-dom`, each shadcn dep, `clsx`, `tailwind-merge`, …). Don't ship
  deps nothing imports.
- **BifrostProvider + theme**: the scaffold already wraps the tree and passes `theme={theme}` from
  the platform boot. If the app is theme-aware (keys styles off the `dark` class / Tailwind
  `dark:`), add `supportsTheme` to `<BifrostProvider>` so `BifrostHeader` shows the light/dark
  toggle and recolors its own chrome. If the app has hardcoded light colors, omit `supportsTheme`.
- **BifrostHeader**: keep it in `App.tsx` for the familiar chrome (back-to-Bifrost, user menu).
- **basename**: the scaffold sets `basename` from the boot; v2 apps mount at `/apps/{slug}`.
- **brand icon**: port the tracked image in both scopes. Set the Solution-root-relative `logo:` in
  `bifrost.solution.yaml` for the catalog card, and set the app-relative `logo:` under the app's
  `.bifrost/apps.yaml` entry for `BifrostHeader`. See the Build skill's `references/solutions.md`.

### 7. Rename workflows to the org convention (ref-safe)

If the user chose a rename convention, rename each backing workflow's file/function to
`{domain}_{verb}_{noun}` and move it to flat `workflows/`. A rename changes the workflow's
`path::function_name` (and maybe `name`), which would break every ref — so rewrite them **at the
same time**:
- App TSX `useWorkflow*("old")` strings,
- `Form.workflow_id` / `launch_workflow_id` / `workflow_path`+`workflow_function_name`,
- sibling workflow source that launches it,
- AgentTool bindings (UUID FK refs survive automatically; only string refs need rewriting).

The backend `WorkflowRefRewriter` (`api/src/services/solutions/ref_rewriter.py`) does the rewrite;
the dependency walker already found the refs. Verify with a fresh capture `--dry-run` that nothing
dangles.

### 8. Build, run, confirm the design

First prove it compiles — `vite build` is the fastest, surest check that every import resolved
(any leftover `bifrost` import of a React/shadcn name fails here with "not exported by bifrost",
which is your remaining-rewrite to-do list):
```bash
cd apps/<new-slug> && npm install && npm run build   # must succeed before driving the UI
```
Then drive it:
```bash
bifrost solution start <new-slug>     # app Vite server + local @workflow functions, one origin
```
Open the printed URL; click every page. (Skip this loop only if the user opted out in the prefs.)
Iterate on layout/theme until it matches the v1 app.

### 9. Cutover by slug swap + capture the backing entities

- **Slug swap** (URLs/bookmarks survive — little to no downtime):
  ```bash
  bifrost solution swap-slugs <old-slug> <new-slug-v2>
  ```
  Atomic (one transaction, advisory lock both slugs): the v2 app takes the live slug, the v1 app
  parks under the temp slug.
- **Capture** the non-shared backing entities into the Solution (dry-run first, always):
  ```bash
  bifrost solution capture <solution_id> --workflow … --table … --config … --dry-run
  bifrost solution capture <solution_id> --workflow … --table … --config … [--include-imports]
  ```
  Use `--include-imports` only if the captured workflows import shared `modules/` you want bundled
  (it pulls the transitive import closure, never the whole `modules/` tree).

> **CAPTURE IS THE TERMINAL STEP — never `solution deploy` after capturing.** Deploy is
> full-replace: it deletes any solution-owned entity NOT present in the local workspace bundle.
> The workflows you just captured live in `_repo`/the DB, not your local v2 app workspace, so a
> deploy-after-capture WIPES them. Order: scaffold → port → build → **deploy the app** → swap →
> **capture last**. If you must redeploy app code after capturing, re-run capture afterward.

> **Workflow UUID refs:** v1 apps often call `useWorkflow("<uuid>")`. UUIDs are env-specific — in
> a fresh env they resolve to nothing. Rewrite them to portable `path::function` refs (the execute
> resolver accepts those in any env) as part of the import rewrite, so the migrated app's data
> loads after deploy.

---

## Shared-entity report (deliverable)

For each app cluster, the capture dry-run's **outside_references** name entities that more than one
app/workflow/form/agent uses. An entity that can't cleanly belong to ONE Solution (e.g. a workflow
shared by two unrelated apps) is **surfaced, not forced** in. Produce a short report:

- entity (kind + name),
- who inside the migrated Solution uses it,
- who OUTSIDE still references it,
- recommendation: capture into this Solution / leave loose as shared / split.

The canonical case: a workflow used by both "App A" and an unrelated "App B" — capturing it into
A's Solution would orphan B's reference across the scope boundary. Report it; let the human decide.

## Verification before declaring done

- The v2 app builds + runs under `bifrost solution start`, every page works.
- Capture `--dry-run` shows no dangling refs after rename (refs rewritten).
- The live slug now serves the v2 app (`bifrost api GET /api/applications/<old-slug>` → the v2 row).
- Both the Solution catalog record and application record carry the migrated logo when the v1 app
  had one; verify the two logo scopes separately after deploy.
- Backing entities are solution-owned (`bifrost api GET /api/solutions/<id>/entities`).
- A shared-entity report exists for anything irreducibly shared.

## After changing this skill

Bump the plugin version (`.claude-plugin/plugin.json`) per `[[reference_plugin_version_bump]]` —
run `update-plugin-version.sh "$(compute-dev-version.sh)"`.
