# Solutions — Resume Plan (handoff for a new session)

Date: 2026-06-05
Worktree: `/home/jack/GitHub/bifrost/.claude/worktrees/solutions-success-criteria`
Branch: `worktree-solutions-success-criteria`  •  Draft PR: **#347** (experimental — do NOT merge)
Spec: `docs/plans/2026-06-04-solutions-success-criteria.md` (see §8 "REAL STATUS")

## The loop (the bar to clear)
Fix findings → TDD each → local verify → **full Codex spec review** → repeat until
**two CONSECUTIVE full Codex reviews come back with ZERO P1/P2 spec findings**. Only then is
it worth human QA. (User directive: 2 clean reviews; reuse `ManifestResolver`/indexers so future
fields can't create deploy gaps — done for forms/agents.)

## How to run Codex review (the gate)
```bash
cd <worktree>
codex review - < /tmp/codex_review3.txt   # prompt file already written; or rebuild from the one below
```
Triage with `superpowers:receiving-code-review` — VERIFY each finding against the code before fixing
(several past findings were right; some I over-asserted in tests). The prompt that found the deep
issues is saved at `/tmp/codex_spec_review_prompt.txt` (+ `/tmp/codex_review3.txt` with the "what's
fixed" context). Re-create it if /tmp is cleared — it's a "full adversarial spec review, file:line +
severity, hunt for issues the fixes introduced."

## SESSION 2 END — START HERE (2026-06-05)

**State: clean, all pushed.** HEAD = 5d678cb0+ on `worktree-solutions-success-criteria` (PR #347).
ALL findings from Codex reviews #3 (6), #4 (8), #5 (6) are FIXED + committed + pushed + green.

**Codex review #6 is RUNNING in the background.** Output → `/tmp/codex_review6_out.txt`
(prompt `/tmp/codex_review6.txt`). FIRST THING NEXT SESSION:
1. Check it's done: `pgrep -f 'codex review -'` (empty = done). Read the final `^codex$` block
   in `/tmp/codex_review6_out.txt` (`grep -n '^codex$' …` → read from there).
2. Triage with `superpowers:receiving-code-review` — VERIFY each finding against the code before
   fixing (history: most findings real, ~2 of 14 were holes in a prior fix, a few I over-asserted).
3. Fix loop: TDD each → `./test.sh <suite>` → commit → re-run Codex until **2 CONSECUTIVE clean**.
   If #6 is clean, that's review 1 of 2 — run one more clean pass to clear the bar.

**If #6 is again non-trivial**, the open question to put to the user: keep reviewing the WHOLE
surface each pass, or narrow to the two high-risk areas only (deploy atomicity/concurrency +
v2 mount lifecycle)? The user leans toward a second pass being valuable; "2 clean" may take 2-3 more.

**Test command for the full solution suite** (paste verbatim — all green as of session end):
`./test.sh tests/unit/test_solution_*.py tests/unit/test_sdk_package.py tests/unit/test_solution_scaffold_app.py tests/unit/repositories/test_org_scoped_solution_visibility.py` (unit)
+ `tests/e2e/platform/test_solution_*.py` (e2e) + `cd client && npx vitest run src/lib/app-sdk/ src/components/jsx-app/`.
Quality: `cd api && ruff check . && pyright` (pyright shows ~1 known false positive: aiobotocore + a
few host-only `src.services.sdk_package` import errors — both resolve in-container). `cd client && npx tsc --noEmit && npx eslint …`.

**Live debug stack** was UP port-mode at `http://localhost:37791` (login dev@gobifrost.com/password).
Scratch CLI at `/tmp/bifrost-cli-sol/.venv/bin/bifrost` (re-`bifrost login --url http://localhost:37791
--email dev@gobifrost.com --password password` if token stale). `/api/sdk/download` serves a working
`bifrost` npm tarball LIVE — verified. `bifrost solution scaffold-app <slug>` generates a deployable v2 app.

## PAUSE DEBRIEF (2026-06-05, session 2) — read this first

**What's solid:** all of Codex review #3 (6) and #4 (8) are fixed, committed, pushed, green
(104 unit + 13 e2e + 110 client). The v2 dev experience is **validated live** on the port-mode
debug stack at `http://localhost:37791`: scaffolded a v2 app → `npm install` resolved `bifrost`
from `/api/sdk/download` → `vite build` succeeded → `bifrost deploy` server-built the dist → it
serves at `/apps/{slug}` with the entry in the manifest. Tokenless dev loop proven (vite walks up
to the solution-root `.env` `bifrost login` wrote). That's the core "done" substance.

**Codex review #5 (1 P1 + 5 P2) — ALL FIXED + COMMITTED.** R5-P1 (v2 remount cache-bust),
R5-P2 node_modules-skip, R5-P2 tokenless-dev, R5-P2 slug-advisory-lock (pg_advisory_xact_lock),
R5-P2 finalize-retry. The finalize fix is what Codex actually asked for ("no queued retry"):
each idempotent finalize step retries with backoff (3x); a blip is absorbed and the deploy
completes normally; only a sustained outage raises `SolutionFinalizeIncomplete` → router 502 /
git-sync logs-and-heals-next-sync. (Earlier 502-on-any-failure was backed out per user — it made
good deploys look failed.) git_sync.py:162 now handles it. Tests cover transient-retry + sustained-raise.

**The real struggle (the meta-problem):** each Codex pass finds NEW real depth — #3→6, #4→8, #5→6.
This is NOT whack-a-mole on my fixes (only ~2 of 14 R4/R5 findings were holes in a prior fix); the
rest are genuinely new surfaces (concurrency/TOCTOU, ES-module caching, dev-loop papercuts, partial-
failure). The bar is "two CONSECUTIVE clean reviews" and we have not had ONE clean review yet. The
honest read: the v2-app-model + deploy surface is broad enough that a single reviewer keeps finding
a fresh corner. **NOW: all R3+R4+R5 findings fixed — running Codex review #6** (`/tmp/codex_review6.txt`,
output `/tmp/codex_review6_out.txt`). If #6 is again non-trivial, consider (a) a focused adversarial
sweep on JUST the two riskiest areas (deploy atomicity/concurrency, v2 mount lifecycle) rather than
the whole surface each pass, or (b) accept "2 clean" may take 2-3 more passes. Don't claim done until 2 clean.

## RAISED DONE-BAR (user, 2026-06-05)
"Done" requires the FULL E2E dev experience validated (a dev builds+runs a v2 app with no
papercuts — no pasting tokens, no dropped assets), manual + automated testing, nothing left
crappy, AND two consecutive clean Codex reviews. Green unit tests alone ≠ done.

## Codex review #4 → ALL 8 fixed + committed + pushed (1 P1 + 7 P2)
- R4-P1 atomic deploy: compile dists to memory INSIDE deploy() (pre-commit); only cheap
  uploads/python-write deferred to finalize_s3. Build failure now rolls back, no DB-ahead-of-S3.
- R4 slug guard: covers full visible set (org install vs global app; global install vs org app).
- R4 admin slug: get_by_slug_global disambiguates by active org (no MultipleResultsFound 500).
- R4 role DELETE: refuse deleting a role bound to any solution-managed entity (cascade bypass).
- R4 BifrostHeader: shipped in the SDK (self-contained ./bifrost-header, lucide external peer).
- R4 unmount: window.__BIFROST_APP__.registerUnmount(teardown); shell calls it on cleanup.
- R4 binary assets: _collect_apps carries non-text as base64 bin_files → decoded into builder.
- R4 full-page: AppRouter renders standalone_v2 without AppLayout chrome.
Verify after these: 104 unit + 13 e2e + 110 client green; ruff/pyright/tsc/eslint clean. Pushed.

## REMAINING (the real "done")
- **Codex review #5** — confirm the 8 R4 fixes are clean (need 2 consecutive zero-P1/P2).
- **E2E validation (task #16)**: build a real v2 app scaffold (`bifrost solution init` /
  `apps create --model standalone_v2`) — package.json (bifrost from instance), vite.config,
  index.html, src/main.tsx reading window.__BIFROST_APP__ + registerUnmount, src/App.tsx. Local
  dev token from the CLI login `.env` (VITE_BIFROST_* from BIFROST_ACCESS_TOKEN) — NO pasting.
  Drive on a port-mode debug stack: deploy → open /apps/{slug} → navigate sub-route (URL updates)
  → refresh works → useWorkflow round-trips → logout. Document the loop. Manual + Playwright.

## IN-FLIGHT right now (updated 2026-06-05, later session)
- **Codex review #3 returned 4 P1 + 2 P2 — ALL fixed + committed + pushed.** See "DONE — review-3 fixes" below.
- **Codex review #4 is running** → output at `/tmp/codex_review4_out.txt` (prompt at `/tmp/codex_review4.txt`).
  Read its final `^codex$` block first thing. It re-verifies the review-3 fixes + hunts new issues.
- Tree is CLEAN, branch pushed (HEAD past 5bef15b9). Full solution suite green:
  98 unit + 12 e2e + 107 client tests; ruff/pyright/tsc/eslint clean (only the known
  aiobotocore host-pyright false positive remains).

## DONE — review-3 fixes (each committed with tests)
- **P2-e** CLI `_collect_workflows` passes the full workflow body (endpoint/timeout/category/tags).
- **P1-c** S3 deferred until after DB commit: `deploy()` returns `DeployResult.finalize_s3`; router
  commits THEN finalizes; git-sync commits+finalizes inside its lock. A failed commit changes no code.
- **P1-d** deploy syncs manifest roles → Form/Agent/App/WorkflowRole junctions (`_resolve_roles` +
  `_sync_entity_roles`); role_names resolve to install org; redeploy full-replaces. CLI `_collect_apps`
  now carries roles. (The indexers do NOT handle roles — the false docstring was corrected.)
- **P2-f** deploy refuses an app slug colliding with another visible app at the install's org scope
  (cross-org same-slug still allowed). Stops the slug resolver raising MultipleResultsFound.
- **P1-a** `bifrost` SDK served from the instance: new `/api/sdk/download` (esbuild tarball, React-only
  external peer dep, version-stamped) + `api/src/services/sdk_package/`. Added `useWorkflow`. `_materialize`
  vendors the tarball as a `file:` dep. SDK source COPYied into the api image; `sdk_src/` gitignored.
  Dropped unused react-query/react-router from the SDK. See `project_solutions_v2_sdk_shape` memory.
- **P1-b/G7** v2 apps mount SAME-DOCUMENT at /apps/{slug} (`StandaloneV2App.tsx`), not iframe — injects
  `window.__BIFROST_APP__` (token/baseUrl/orgScope/basename) + dynamic-imports the entry; real URL tracks
  app routes so deep-links work. bundle-manifest surfaces the v2 entry+css.

## (historical) IN-FLIGHT — review #3 (superseded)
- Tree is CLEAN (an incomplete P2-j edit was reverted — see below).

## DONE this session (all committed, each with a test)
Original Codex spec review found 7 (G1–G7); a re-review found 10 more (batch 2). Fixed:
- **G1** deployed entities visible in `list()` (filter moved to name-cascade get(), opt-in via
  `exclude_solution_managed`/`include_solution_managed` flags).
- **G2** forms + agents deploy — **via canonical FormIndexer/AgentIndexer** (full content, gap-proof).
- **G3** per-install app identity — migration `20260605_app_identity` (partial unique indexes).
- **G4** git-sync `read_workspace_bundle` bundles apps + forms + agents.
- **G5** ambiguous org-scoped deploy refused (`_resolve_target_install`).
- **G6** cross-solution `sys.modules` eviction in `_clear_workspace_modules`.
- **batch2 P1-a** role-centric endpoints `/api/roles/{id}/{workflows,apps,forms,agents}` (assign,
  remove, bulk-unassign — 12 endpoints) now guard managed entities.
- **batch2 P1-b** app-by-slug open path passes `include_solution_managed=True`.
- **batch2 P1-c** deploy marks apps published (`published_snapshot`+`published_at`).
- **batch2 P1-d** bundle-manifest returns `app_model` for `standalone_v2` BEFORE the v1 build.
- **batch2 P1-e** **DB-first deploy** — all DB upserts/reconcile run before ANY S3 write; app builds +
  stale-dist deletes deferred to an S3 phase that runs only after DB succeeds.
- **batch2 P1-f** form fields read from `form_schema.fields` (was wrong key; old test passed falsely).
- **batch2 P1-g** only `standalone_v2` apps are vite-built (inline_v1 would fail a Vite build).
- **batch2 P2-h** agent tool/delegation/knowledge bindings deploy (via indexer).
- **batch2 P2-i** full workflow metadata deployed (endpoint/timeout/category/tags, full-replace).

## REMAINING (do these next, then re-run Codex until 2 clean)
1. **P2-j — binary app assets dropped.** `_collect_apps` (api/bifrost/commands/solution.py:167)
   only collects `_APP_SRC_SUFFIXES` (text); png/woff/etc. are skipped → server build emits a dist
   with missing assets. **Half-done edit was reverted** — redo cleanly:
   - In `_collect_apps`: `import base64` (top of file); collect non-text files into a `bin_files`
     dict as `{rel: base64(bytes)}`; add `"bin_files": bin_files` to each entry. Skip `.DS_Store`.
   - Thread `bin_files` through: `SolutionDeployRequest.apps` already free-form dict;
     `_run_app_builds` (deploy.py) must decode `bin_files` (base64→bytes) and merge into `src_bytes`
     before `builder.build(...)`. Also collect them in `git_sync._collect_apps` reuse path.
   - Test: an app dir with a `.png` deploys and the bytes reach the builder.
2. **G7 (P2) — v2 app route.** The v2 iframe loads `/api/applications/{id}/dist/index.html`, so the
   inner app's `window.location` is the dist URL and `<BrowserRouter basename='/apps/{slug}'>`
   won't match → deep-links differ from a normal app (criterion 12 / spec §2). This is a DESIGN
   FORK — needs a decision: (a) serve dist as a document AT `/apps/{slug}` (nginx/backend route work
   so window.location matches), (b) scaffold uses a pathname-independent router, or (c) accept
   iframe-at-dist + document the limitation. Recommend deciding with the user. Also: the
   BundledAppShell v2 branch (client/src/components/jsx-app/BundledAppShell.tsx) builds the iframe
   src from the dist route — align with whatever serving decision is made. The vite `--base` is set
   to `/api/applications/{id}/dist/` in app_build.py `_run_vite_build`.
3. Anything Codex #3 (and subsequent reviews) surface.

## KEY FILES
- `api/src/services/solutions/deploy.py` — the deployer (DB-first phases; `_upsert_*`, `_run_app_builds`,
  `_delete_stale_app_dist`, `_reconcile_*`). Forms/agents delegate to indexers.
- `api/src/services/solutions/git_sync.py` — connected auto-pull bundle (`read_workspace_bundle`).
- `api/src/repositories/org_scoped.py` — `get()`/`can_access()`/`_apply_cascade_scope()` +
  `include_solution_managed`/`exclude_solution_managed` flags (the visibility model — read carefully).
- `api/src/routers/roles.py` — the 12 now-guarded role-junction endpoints.
- `api/src/routers/app_code_files.py` — bundle-manifest v2 short-circuit; dist serve route; S3 file guards.
- `api/bifrost/commands/solution.py` — CLI deploy + `_collect_*` (P2-j lives here).
- `api/src/services/file_storage/indexers/{form,agent}.py` — canonical content indexers deploy reuses.
- `api/src/services/manifest_import.py` — `_form_content_from_manifest`, `_agent_content_from_manifest`.

## VERIFY / TEST commands (from worktree root)
```bash
./test.sh stack up        # boot once
# the solution suite (all should pass):
./test.sh tests/unit/test_solution_guard.py tests/unit/test_solution_deploy_reconcile.py \
  tests/unit/test_solution_app_deploy.py tests/unit/test_solution_form_agent_deploy.py \
  tests/unit/test_solution_git_sync.py tests/unit/test_solution_module_isolation.py \
  tests/unit/test_solution_resolve_install.py tests/unit/test_mcp_solution_managed.py \
  tests/unit/repositories/test_org_scoped_solution_visibility.py \
  tests/unit/test_contracts_parity.py \
  tests/e2e/platform/test_solution_readonly.py tests/e2e/platform/test_solution_readonly_full.py \
  tests/e2e/platform/test_solution_v2_app_e2e.py tests/e2e/platform/test_solution_deploy_execution.py \
  tests/e2e/platform/test_solution_table_e2e.py tests/e2e/platform/test_solution_git_connected_e2e.py
cd api && ruff check . && pyright    # pyright host shows ~40 false reportMissingImports (aiobotocore etc.) — filter those
```
Migrations: a new one landed (`20260605_app_identity`). `./test.sh stack reset` applies it to the
test template. For the debug stack: restart bifrost-init then api (see CLAUDE.md).

## LIVE DEBUG (for hands-on / browser)
- Debug stack was UP in **port mode** at `http://localhost:37791` (login dev@gobifrost.com/password).
  Netbird mode can't be browser-driven (Vite HMR hang) — to force port mode, the user's
  `~/.config/bifrost/debug.env` sets NETBIRD_SETUP_KEY; temporarily move it aside, `./debug.sh up`,
  then restore it. (It was restored at end of session; re-do if you need the browser.)
- Scratch CLI: `/tmp/bifrost-cli-sol/.venv/bin/bifrost` (API-matched, logged in). Sample solution
  workspace at `/tmp/bifrost-cli-sol/v2sol/`. Manual Playwright harnesses:
  `cd client && TEST_BASE_URL=http://localhost:<port> npx playwright test -c playwright.manual.config.ts`
- Local git-connected testing pattern: a bare repo INSIDE the api container
  (`git init --bare /tmp/livegitrepo`), connected install `git_repo_url=file:///tmp/livegitrepo`,
  then `POST /api/solutions/{id}/sync`. Set `git config --system --add safe.directory '*'` in the
  container first (else "dubious ownership" — a `file://`-local-only quirk, not a prod concern).

## DONE-BAR REMINDER
Do NOT claim "done" again until two consecutive full Codex reviews are clean (zero P1/P2). I've been
wrong about "done" three times — each review found real depth. Trust the gate, not optimism.
