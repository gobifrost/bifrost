# Solutions — Resume Plan (handoff for a new session)

Date: 2026-06-05
Worktree: `/home/jack/GitHub/bifrost/.claude/worktrees/solutions-success-criteria`
Branch: `worktree-solutions-success-criteria`  •  Draft PR: **#347** (experimental — do NOT merge)
Spec: `docs/plans/2026-06-04-solutions-success-criteria.md` (18 criteria) + v2 app design doc.

---

# ▶ STATUS DASHBOARD — "how close are we?" (read this first)

**Short answer: feature-complete on the happy path, NOT through the correctness gate.**
The whole feature surface is built and the v2 dev loop is validated live. But the done-bar is
**two CONSECUTIVE Codex reviews with ZERO P1/P2** — and we have had **zero clean reviews in five
rounds**. Each round still finds real P1s, including holes opened by the previous round's fixes.

### The gate, round by round
| Review | Findings | Clean? |
|--------|----------|--------|
| #3 | 6 | ✗ |
| #4 | 8 | ✗ |
| #5 | 6 | ✗ |
| #6 | 4 P1 + 2 P2 | ✗ (5 fixed, 1 rejected) |
| #7 | **3 P1 + 3 P2** | ✗ ← **current; OPEN, not yet fixed** |
| #8 | — | needed: 1st of 2 clean |
| #9 | — | needed: 2nd of 2 clean |

**Realistic distance to done: ≥ 2 more fix-loops, possibly 3.** Burn-down is not converging to
zero yet (6→8→6→6→6). It will only converge once the *identity model* (per-install entity IDs +
scope-aware workflow-ref resolution) is solid — that cluster is what keeps regenerating findings.

### What is DONE (built + tested + validated live)
- v2 React app model end-to-end: scaffold → `npm install` (`bifrost` from `/api/sdk/download`) →
  `vite build` → `bifrost deploy` (server build) → same-document mount at `/apps/{slug}`. Proven live.
- Instance-served SDK (`BifrostProvider`/`useWorkflow`/`useTable`/`BifrostHeader`).
- Deploy: DB-first-then-S3 ordering, per-slug advisory lock, finalize-retry, full-replace reconcile.
- Read-only enforcement across UI/REST/MCP/role-junctions (+ S3 ORM-flush backstop).
- Portable export/import, vendored shared deps, offline `bifrost run`, headless operability.
- Git-connected one-writer install (auto-deploy on push, manual deploy refused).
- All of Codex #3/#4/#5 + the 5 verified #6 findings: fixed, committed, green.

### What is LEFT (open work blocking the gate)
**Review #7 — 3 P1 + 3 P2, ALL OPEN (not yet started). See "REVIEW #7 — OPEN FINDINGS" below.**
The dominant theme is the **entity-identity model**, which #6→#7 proved is not finished:
- **P1 — per-install ID remap is MISSING.** A byte-identical scaffold/export/git repo sends the
  same manifest UUIDs to a 2nd scope; the resolver makes a 2nd install, then the ownership guard
  aborts it. Criterion 9 (multi-install) cannot be met from a real repo without hand-editing UUIDs.
  *(This reopens #6's "P1-a" — my rejection was half-right: installs ARE independent, but the path
  to create the 2nd one has no ID-translation step. The remap is the missing piece.)*
- **P1 — v2 `useWorkflow("path::fn")` 404s** because the resolver excludes solution-managed rows;
  a deployed Solution's own workflow is unreachable from its own app. Tied to the ID model: app
  source can't hard-code per-install UUIDs, so path-refs MUST resolve within the app's solution scope.
- **P1 — org-deploy regression** (opened by this session's R6-P1-b fix): a `None` deployer org
  (provider/admin context) makes an org-scoped deploy match the GLOBAL install → can clobber it.
- **P2 ×3:** `delete_missing_prefix` bypasses the managed guard; v2 late-import-after-unmount mounts
  a stale app; dev token isn't read from the CLI credential store (device-code login → empty token).

After #7 is fully fixed + green → run review #8 (narrowed prompt `/tmp/codex_review7.txt` is a good
base). If #8 clean → that's clean #1; run #9 for clean #2.

### FOLLOW-UP (deferred, user-approved 2026-06-05 session 4) — DX, not blocking
- **manifest_generator should emit `path::fn` for form/agent→workflow cross-refs.** Today it writes
  the raw target UUID into `form.workflow_id`/`launch_workflow_id`/`data_provider_id` and agent
  `tool_ids`. The deploy-time remap (`_remap_bundle_ids`) translates those UUIDs correctly, so it
  WORKS — but a developer authoring/reading the manifest sees UUIDs for cross-links instead of
  readable `workflows/foo.py::main` refs. Making the generator emit path::fn (+ round-trip tests)
  would make authored manifests UUID-free for cross-links. User chose "follow-up, stay focused" to
  avoid widening the diff mid-gate. The runtime resolver already handles path::fn (R7-P1-c fix).

### USER DECISIONS (2026-06-05, session 3) — do NOT re-ask
- **Keep going autonomously.** The user wants the fix-loop continued without per-step approval;
  only surface a question if something genuinely needs their call. Update THIS doc each session so a
  fresh session can start cold.
- **Remap scheme is DECIDED: deterministic `uuid5(install_id, original_manifest_id)`.** This is the
  agreed approach for per-install entity identity (the "fresh phone numbers per customer" fix). It
  makes installs independent (criterion 9) AND keeps redeploys stable (same input → same ID, so an
  update doesn't scramble a customer's internal wiring). Do not re-litigate vs the `(solution_id,id)`
  keying alternative unless implementation reveals it can't work.

### Plain-English framing (for context, not jargon)
The remaining bugs are mostly ONE problem: when the same Solution installs into two customers, each
needs its OWN copy of every entity, but entity IDs (think: phone numbers) are baked into the bundle,
so two installs collide and the 2nd is refused. Fix = give each install fresh-but-deterministic IDs
at deploy time, and make the app→workflow reference resolve by name-path within the app's own install
(it can't hard-code an ID it won't know until install). R7-P1-b + R7-P1-c are two faces of this.

### Recommended sequencing for the next session
1. **Fix the identity cluster as ONE coherent change** (R7-P1-b remap + R7-P1-c scope-aware
   path-ref resolution) using the decided `uuid5(install_id, manifest_id)` scheme. They share a root
   cause; fixing piecemeal just regenerates review #8 findings. Touch points: the CLI/git-sync
   `_collect_*` collectors (where manifest IDs are read) + `deploy.py` upserts + the ownership guard
   + `WorkflowRepository.resolve` (must resolve `path::fn` within the caller's solution scope) +
   how `/api/workflows/execute` learns the caller's install scope.
2. Fix the org-deploy regression (R7-P1-a) — small, isolated; add the `None`-deployer test.
3. Fix the 3 P2s (R7-P2-d/e/f).
4. Full verify → review #8. (If clean → clean #1 of 2; run #9 for #2.)

---

## The loop (the bar to clear)
Fix findings → TDD each → local verify → **Codex spec review** → repeat until **two CONSECUTIVE
Codex reviews come back with ZERO P1/P2**. Only then is it worth human QA. (User directive: 2 clean
reviews; reuse `ManifestResolver`/indexers so future fields can't create deploy gaps.)

## How to run Codex review (the gate)
```bash
cd <worktree>
codex review - < /tmp/codex_review7.txt   # latest prompt (narrowed to the 2 high-risk areas)
```
Triage with `superpowers:receiving-code-review` — VERIFY each finding against the code before fixing
(use Explore subagents, one per finding — worked well in session 3). History: most findings real;
~2 of 14 in R4/R5 were holes in a prior fix; in #7, 2 of 6 were holes opened by #6's own fixes.
After codex exits, find the verdict: `grep -nE '^- \[P[123]\]' /tmp/codex_review7_out.txt` (the list
prints twice — dedupe). Kill any stale `until ! pgrep…` watcher bash procs; they are NOT the review.

## SESSION 3 END — START HERE (2026-06-05)

**State: clean, all pushed.** HEAD = 834e806b on `worktree-solutions-success-criteria` (PR #347).
Reviews #3 (6), #4 (8), #5 (6), **#6 (4 P1 + 2 P2)** all triaged + fixed/rejected + green.

**Review #6 outcome (commit 834e806b):** 5 findings verified REAL and fixed; 1 P1 REJECTED as a
design misread. Per-finding detail in the commit message. The rejected one (P1 "remap entity IDs
per install") was wrong because criterion 9 = two INDEPENDENT installs (distinct UUIDs), and the
ownership guard correctly forbids cross-install UUID reuse (criterion 10). The fixes:
- R6-P1-b: `_resolve_target_install` matches the deployer's OWN org (was: any org-scoped install).
- R6-P1-c (security): exclude `.env*` from `_collect_apps` + gate scaffold vite `define` on
  `command==="serve"` so `vite build` never bakes the access token into the public bundle.
- R6-P1-d: stage `sdk_src` in the PROD `api/Dockerfile` (was Dockerfile.dev only → prod 500s).
- R6-P2-e: scaffold/JSDoc use UUID or `path::function` ref, not bare names (param→`workflowRef`).
- R6-P2-f: `assert_not_solution_managed` guard in MCP `publish_app`/`push_files` before S3 writes.

**Codex review #7 is DONE — 3 P1 + 3 P2, all OPEN (none fixed yet).** Output →
`/tmp/codex_review7_out.txt` (prompt `/tmp/codex_review7.txt`, narrowed to deploy
identity/concurrency + v2 mount/dev lifecycle). Full findings in the next section. NEXT SESSION:
1. Don't re-run #7 — it's done. Start fixing per the dashboard's "Recommended sequencing."
2. Triage each with `superpowers:receiving-code-review` + an Explore subagent before fixing.
3. Fix loop: TDD each → `./test.sh <suite>` → commit → re-run Codex (#8) until **2 CONSECUTIVE clean**.

Tests green at session 3 end: 115 solution unit + 111 client app-sdk/jsx-app; ruff + pyright clean.
NOTE: 2 pre-existing `npm run tsc` errors (AppInfoDialog.tsx, AppReplacePathDialog.test.tsx — both
`app_model` on the generated app type) are `v1.d.ts` DRIFT, NOT from this work (confirmed by
stashing the one client change — they persist). Regen types against a running API to clear them.

## REVIEW #7 — OPEN FINDINGS (3 P1 + 3 P2) — verbatim, all unfixed

Triage status: read against the code with `superpowers:receiving-code-review` before fixing. The
two P1s in **bold** share a root cause (the per-install entity-identity model) and should be fixed
together. Task IDs below refer to the session TaskList.

- **[P1] (task R7-P1-b) Allocate install-local entity IDs before deploying another scope** —
  `api/bifrost/commands/solution.py:624-628`. Deploying the same workspace to a 2nd scope still
  sends the `.bifrost/*.yaml` UUIDs unchanged; after install #1 owns them, install #2 (created by
  the resolver) hits the ownership guard and aborts. No remap/translation in the CLI or git-sync
  collectors → a byte-identical scaffold/export/git repo can't satisfy multi-install (criterion 9)
  without manually regenerating all manifest IDs. *(Reopens #6 P1-a — see dashboard.)*
- **[P1] (task R7-P1-c) Resolve solution workflow path refs for v2 apps** —
  `api/src/repositories/workflows.py:110-113`. A v2 app's scaffolded `useWorkflow("workflows/foo.py::main")`
  posts that ref to `/api/workflows/execute`, but `resolve()` excludes solution-managed rows, so it
  only finds `_repo/` workflows and the deployed Solution workflow 404s. Path refs must resolve
  within the app/workflow's solution scope, not be filtered out.
- [P1] (task R7-P1-a) **Regression from this session's R6-P1-b fix** — Prevent org deploys without
  an org from matching global installs — `api/bifrost/commands/solution.py:532-533`. When
  `client.organization` is `None`/no `id`, `deployer_org_id` is `None`, and the org-scope predicate
  matches any same-slug GLOBAL install (its `organization_id` is also `None`). A provider/admin
  org-scoped `bifrost deploy` can full-replace the global install. Org-scope matching must require a
  non-null deployer org before comparing.
- [P2] (task R7-P2-d) Block `delete_missing_prefix` for managed app paths —
  `api/src/services/mcp_server/tools/apps.py:834-838`. The new guard only checks explicit `files`
  keys; `push_files(files={}, delete_missing_prefix="apps/managed")` still reaches the delete sweep
  and removes `_repo/` files under a managed app's `repo_path`. Needs the managed-prefix check before
  any S3 deletion.
- [P2] (task R7-P2-e) Guard late v2 imports after unmount —
  `client/src/components/jsx-app/StandaloneV2App.tsx:150-151`. Navigating away while the entry chunk
  is still downloading: cleanup deletes `window.__BIFROST_APP__`, but the dynamic import still
  executes later, sees no bootstrap, and falls back to `document.getElementById("root")` — mounting a
  stale app into the platform root after the shell unmounted.
- [P2] (task R7-P2-f) Read tokens from the CLI credential store for dev —
  `api/bifrost/commands/solution.py:166-168`. Scaffold promises `bifrost login` makes `npm run dev`
  authenticated, but default device-code login only writes `BIFROST_API_URL` to `.env` and stores
  tokens in keyring/`~/.bifrost/credentials.json`. The vite config reads `BIFROST_ACCESS_TOKEN` only
  from process env or a nearby `.env`, so the normal login path starts the app tokenless unless the
  dev used password-grant or manually exported the token.

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
