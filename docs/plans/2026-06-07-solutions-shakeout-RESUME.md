# Solutions shakeout — RESUME (start a new session here)

Date: 2026-06-07
Worktree: `/home/jack/GitHub/bifrost/.claude/worktrees/solutions-success-criteria`
Branch: `worktree-solutions-success-criteria` · Draft PR **#347** (experimental — do NOT merge)
Pushed: yes (origin up to date as of this writing).

## TL;DR
The Solutions **configs-ownership + management UI + non-destructive uninstall** feature is
BUILT, tested green, and verified live (see `2026-06-06-solutions-configs-and-management-ui-design.md`,
`2026-06-06-solutions-orphan-and-reattach-design.md`, and the plan
`2026-06-06-solutions-configs-and-management-ui.md`). Then the user asked for an end-to-end
**experience shakeout** ("drive it as a new user; find the friction"). Pass 1 is done and
found 10 things (`2026-06-07-solutions-shakeout-findings.md`). The user made decisions (D1-D4
below) that turn several findings into real work — plus there are scenarios NOT yet driven.
**This session: do the prioritized work below, and finish driving the un-driven scenarios.**

## What is DONE and green (don't redo)
- Configs as a solution-owned entity (declarations deploy/reconcile/remap; values
  instance-owned, never in the bundle; orphaned values invisible to runtime + list).
- `GET /solutions/{id}/entities`; zip install (preview→scope→config-values→atomic deploy
  under write-lock) + `bifrost solution install`; PATCH install-local fields; DELETE that
  ORPHANS tables(+docs)+config values with provenance, reattach-on-reinstall, orphan
  visibility fix, "Show orphaned" toggle on Tables/Config.
- Admin-only Solutions list + RoleDetail-style detail view; `SolutionManagedBadge`
  (admin-only, links to owner) across Forms/Workflows/Fleet/Applications; app cards enlarged.
- Verified: 104 solution unit + 26 e2e + 69 client vitest + Playwright admin e2e; ruff/
  pyright/tsc/eslint clean. Detail view + list page + orphan toggle confirmed in a live
  browser (port-mode debug stack).

## Live environment for driving (set up, reuse it)
- Debug stack UP, **port mode**, http://localhost:37791 (login dev@gobifrost.com/password).
  It mounts THIS worktree's api/src, shared, bifrost (branch code, not main). Netbird mode
  can't drive a browser (Vite HMR hang) — stay in port mode.
- **Migrations on the debug DB:** a new migration does NOT auto-apply. Restart
  `bifrost-debug-75bc0d9c-init-1` (runs alembic) then `-api-1`. (See memory
  `project_debug_stack_migration_apply`.) This bit me once: the detail view showed
  "Failed to get solution entities" purely because `solution_config_schema` wasn't on the
  debug DB until I restarted init.
- Scratch CLI (new-user simulation): `/tmp/bifrost-shakeout/.venv/bin/bifrost` (API-matched,
  logged in). Sample solution workspace + scaffolded v2 app at `/tmp/bifrost-shakeout/mysol/`
  (apps/dashboard `npm install`ed). `npm run dev` from there serves at :5173 and WORKS
  (authed, calls the API) — the placeholder workflow ref 404s (F8).
- Screenshots: write throwaway `client/manual-verify/*.manual.ts`, run with
  `cd client && TEST_BASE_URL=<url> npx playwright test -c playwright.manual.config.ts <name>`,
  save PNGs to `/home/jack/Sync/Screenshots/`, then DELETE the script (don't commit it).

## USER DECISIONS (do NOT re-ask)
- **D1:** `watch` should NOT become solution-aware. It should REFUSE/WARN in a Solution
  workspace (a `bifrost.solution.yaml` present) with: "Solutions are local-development-first
  — use `npm run dev`; watch is for _repo/ development."
- **D2 (headline):** build a first-class local dev experience like `firebase emulators:start`
  / `swa start` — ONE command that spins up the dev environment so the builder launches their
  web client and it can IMMEDIATELY call local workflows + the API. The scaffold works but
  there's no "run this and it's all wired" command.
- **D3:** the unstyled standalone `BifrostHeader` (F9) is HIGH PRIORITY ("a huge miss").
- **D4:** the local dev command takes `--org`/`--organization-id` like `bifrost run` does
  (superuser), and local dev resolves install-scoped-first then falls back per that org
  (own-first → org → global-if-global_repo_access) — the SAME cascade the deployed resolver
  uses. Ties to F2: the centralized resolver should serve BOTH deployed and local-dev paths.

## THE WORK (prioritized) — for this session

### Tier 1 — DX-critical (the user cares most)
1. **D2: local dev command (`firebase/swa`-style).** Design + build a `bifrost solution dev`
   (name TBD) that: boots/uses the dev stack, runs the app's `npm run dev`, wires token+org
   so the web client calls local workflows + the API with ZERO manual steps. Takes
   `--org` (D4). This likely subsumes/depends on F8 + F10. THINK about what "local workflows"
   means — does it run the workspace's `workflows/*.py` against the local API, and how does
   the dev server's `useWorkflow` reach them? Brainstorm this one (it's a real feature, not a
   patch) before building.
2. **D3/F9: BifrostHeader self-contained styling.** The SDK's BifrostHeader must look
   intentional standalone (`npm run dev`) AND keep working deployed. Fix in the SDK source
   (`api/src/services/sdk_package/` — the served `bifrost` tarball source) + rebuild; verify
   by re-running the scratch app's `npm run dev` and screenshotting.
3. **D4/F10/F2: install-scoped resolution under a chosen org, locally.** Local dev should
   send an app_id/org so `useWorkflow` resolves own-first-then-cascade. Best done by FIRST
   doing F2 (centralize the resolver) so local + deployed share it.

### Tier 2 — architecture/scalability (my list, do carefully)
4. **F2: centralize solution-first resolution.** Add ONE
   `OrgScopedRepository.resolve(name, *, solution_scope=)` (own-first → org→global cascade)
   and route tables/workflows/configs/forms/agents through it; document in
   `repositories/README.md`. Touches canonical code shared by the whole branch — consider
   doing it as its own commit/sub-PR with heavy tests. Currently: tables resolve inline in
   the ROUTER, workflows in the repo (path-refs only), configs not at all.
5. **F3: kill manifest field-list duplication.** Collector uses
   `ManifestSolutionConfigSchema.model_validate(body)`, upsert uses `.model_dump()`; add a
   field-parity test (like `test_dto_flags.py`) across ORM↔manifest↔collector. Also document/
   guard the hand-enumerated cross-ref remap field lists (`_FORM_WORKFLOW_REF_FIELDS` etc.).

### Tier 3 — quick PLATFORM polish
6. **F1:** add `solution` to `bifrost --help` (the hand-maintained command listing) + examples.
7. **D1/F4:** `watch` refuses/warns in a Solution workspace.
8. **F8:** scaffold either ships a matching trivial `workflows/your_workflow.py::main` (so the
   button works on first run) OR makes the 404 self-explanatory in App.tsx. (May be folded
   into D2.)

### Tier 4 — needs a user decision before building
9. **F6 [Q]:** no MCP path to create/deploy/list Solutions (only blocked-from-mutating). Ask:
   intended (admin op, out of MCP scope) or a gap to fill?
10. **F10 [Q]:** resolved by D4 — local dev gets `--org` + install-scoped resolution. (Closed.)

## Scenarios NOT yet driven (finish the shakeout)
Pass 1 covered CLI discoverability, the architecture audits, and the local-dev loop
(scaffold→install→dev server). STILL TO DRIVE as a new/existing user:
- **export/import round-trip** of a Solution (`bifrost export --portable` → `bifrost import`)
  — does it round-trip cleanly? are env-specific fields scrubbed? does the imported solution
  install + work?
- **delete → reattach with REAL data through the UI** — install a solution with a table, add
  documents, delete from the Solutions UI (see the non-destructive copy), confirm orphan via
  "Show orphaned", reinstall, confirm data reattaches. (Backend is unit/e2e-tested; the UI
  path + copy is not yet eyeballed.)
- **production-content coexistence** — move a real prod slice (e.g. an existing app/workflow
  set) into a Solution and confirm it coexists with _repo/ content without collision.
- **UI new-user walk-through** — click every Solutions surface as a first-timer: install
  dialog (drag a real zip → preview → scope → config values), edit dialog (change scope/
  settings), delete dialog, the configs tab value-entry, the badge→solution navigation, the
  ?from back-nav. Screenshot each; note anything sloppy/inconsistent vs. the rest of the app.
- **existing-user workflow impact** — does anything about the badge/read-only changes or the
  list-page edits disrupt a normal (non-solution) user's Forms/Workflows/Tables/Apps flow?
- **LLM-discoverability** — is it obvious to an LLM (via `--help`, `docs/llm.txt`, MCP tool
  descriptions) what the solution commands do and how to build a solution? (F1 is one hit;
  check llm.txt has nothing about solutions, and whether it should.)

## How to run the suites (all currently green)
Backend: `./test.sh stack reset` then the solution unit set
(`tests/unit/test_solution_*.py tests/unit/test_orphan_*.py tests/unit/test_org_scoping_enforcement.py
tests/unit/test_dto_flags.py`) + e2e (`tests/e2e/platform/test_solution_*.py
tests/e2e/platform/test_tables_include_orphaned.py`). Client: `./test.sh client unit` on the
solutions/pages/services files; Playwright `./test.sh client e2e e2e/solutions.admin.spec.ts`.
Quality: `cd api && ruff check . && pyright` (filter ~40 known host `reportMissingImports`
false positives — they resolve in-container); `cd client && npx tsc --noEmit && npx eslint`.

## Process note
The user wants this DRIVEN end-to-end, not just unit-tested. Position: "I'm a new user — what
friction do I hit? what's not in the UI? what feels sloppy vs. similar dev tooling? as an
existing user, how is my workflow affected?" Experience bugs/sloppiness → PLATFORM fix list.
Non-scalable code → CODE fix list. Produce findings, let the user triage big items (esp. F2).
Build via the subagent + two-stage-review loop (see `2026-06-06-...-ui.md` plan header).
