# Solutions — Resume Plan (handoff for a new session)

Date: 2026-06-05
Worktree: `/home/jack/GitHub/bifrost/.claude/worktrees/solutions-success-criteria`
Branch: `worktree-solutions-success-criteria`  •  Draft PR: **#347** (experimental — do NOT merge)
Spec: `docs/plans/2026-06-04-solutions-success-criteria.md` (18 criteria) + v2 app design doc.

---

# ▶ STATUS DASHBOARD — "how close are we?" (read this first)

**STATUS (end of session 4, 2026-06-06): cleanup complete; branch is GREEN and ready
for HUMAN review. The Codex two-clean gate was ABANDONED (see "DECISION" below) —
ignore the round-by-round table; it's history.**

## ▶▶ WHAT TO DO NEXT (read this, not the gate table)

**UPDATE (2026-06-07): the configs feature + Solutions management UI + non-destructive
uninstall are now BUILT, tested, and verified (incl. live browser).** Specs:
`docs/superpowers/specs/2026-06-06-solutions-configs-and-management-ui-design.md` +
`2026-06-06-solutions-orphan-and-reattach-design.md`; plan:
`docs/superpowers/plans/2026-06-06-solutions-configs-and-management-ui.md`. What landed
(162 ahead of main): configs as a solution-owned entity (declarations deploy/reconcile;
values instance-owned, never in the bundle); `GET /solutions/{id}/entities`; drag-and-drop
**zip install** (preview→scope→config-values→atomic deploy under the write-lock) + `bifrost
solution install`; **PATCH** install-local fields (scope re-stamps owned entities); **DELETE**
that ORPHANS tables (+documents) and config values instead of destroying them, with
provenance, reattach-on-reinstall, an orphan-visibility fix (orphans excluded from the org
name cascade), and a "Show orphaned" toggle on Tables/Config; admin-only Solutions list +
RoleDetail-style detail view; `SolutionManagedBadge` (admin-only, links to owner) across
Forms/Workflows/Fleet/Applications; app cards enlarged. Green: 104 solution unit + 26 e2e +
69 client vitest + Playwright admin e2e; ruff/pyright/tsc/eslint clean.

STILL worth doing:
1. **Human review** the new auth/concurrency code (highest risk): the
   `X-Bifrost-App` table gate, the per-install `write_lock.py`, install-scoped resolution,
   the uuid5 remap, the v2 mount races — AND the new zip-install atomicity
   (`api/src/services/solutions/zip_install.py`) + the orphan/reattach logic
   (`deploy.py` reattach + reconcile-protection; `org_scoped.py` orphan exclusion).
2. The original CLI-only "done" was correct against the 18 criteria but had no management
   UI; this session added that layer (it was net-new scope, not in the original criteria).

## WHAT'S LEFT vs DONE
- **DONE + verified green** (173 solution unit + 106 e2e + 116 client vitest;
  ruff/pyright/tsc clean): the whole owns-its-entity model for workflows/apps/forms/
  agents/tables (uuid5 identity, solution-scoped uniqueness, solution-first
  resolution, deploy of all metadata incl access_level/agent-limits/MCP-grants,
  read-only enforcement, write-lock, v2 app model), merged with main, audited,
  dead-code removed.
- **NOT built:** configs as a solution-owned entity + the install management view
  (item 2 above). Everything else is review + the small UI gap.

## (history) The Codex gate, round by round — ABANDONED after #16
Kept for context only. The bar was "two consecutive clean Codex reviews"; it never
converged (a broad reviewer always finds one more P2, and my own fixes spawned the
next round's). Of ~20 findings, ~5 were NORMAL-USE and all are fixed. See DECISION.

### The gate, round by round
| Review | Findings | Clean? |
|--------|----------|--------|
| #3 | 6 | ✗ |
| #4 | 8 | ✗ |
| #5 | 6 | ✗ |
| #6 | 4 P1 + 2 P2 | ✗ (5 fixed, 1 rejected) |
| #7 | 3 P1 + 3 P2 | ✗ → ALL 6 FIXED (session 4) |
| #8 | 1 P1 + 1 P2 | ✗ → BOTH FIXED (session 4) |
| #9 | 2 P2 | ✗ → BOTH FIXED (session 4) |
| #10 | 1 P2 | ✗ → FIXED (session 4) |
| #11 | 2 P2 | ✗ → BOTH FIXED (session 4) |
| #12 | 2 P2 | ✗ → BOTH FIXED (session 4) |
| #13 | 4 P2 + 1 P3 | ✗ → ALL FIXED (session 4) |
| #14 | 2 P2 (NORMAL-USE) | ✗ → BOTH FIXED (session 4) |
| #15 | 3 P2 + 1 P3 (all NORMAL-USE) | ✗ → ALL FIXED (session 4) |
| #16 | 1 P1 + 1 P2 + 1 P3 | ✗ → **ALL FIXED (session 4)** |
| #17 | (killed) | gate abandoned — replaced by whole-branch audit + human review |

### ✅ SESSION 4 FINAL STATE (2026-06-06) — branch ready for HUMAN review
Done this session after abandoning the Codex gate:
1. **Merged main** (was 7 behind; resolved v1.d.ts + a multiple-alembic-heads merge
   migration `20260606_merge_sol_brand`). Now 127 ahead, working tree clean.
2. **Whole-branch multi-agent audit** (7 reviewers / 149 prod files): every slice
   "Solid" or "Acceptable", ZERO complexity findings — core is not slop. Found
   3 bugs + 9 dead-code + 4 inconsistency + 18 nits.
3. **Fixed the real ones:** table-name solution-scoped uniqueness (the owns-its-table
   bug; migration `20260606_table_name_sol_scope` + in-bundle-dup→409), `bifrost auth
   token` expiry-refresh, removed 8 dead-code items + the dead simple_worker eviction
   block (and fixed its test to verify the REAL hash-check isolation, not the fiction).
   The audit's 9th dead-code item (client index.v2.ts) was a FALSE positive (it's the
   baked SDK source) — left it.
4. **Wrote the configs/install-view design note** (`2026-06-06-solutions-own-configs-
   and-install-view.md`) — the evolved "Solutions OWN their entities" model for a fresh
   session to build (configs schema-owned + value-supplied-in-an-install-view).
**Verified green:** 173 solution unit + 106 e2e + 116 client vitest; ruff/pyright/tsc
clean. **NEXT: human review of the new auth/concurrency code; then the configs feature
per the design note. Known non-blocker:** read-only-UI gap on Forms/Workflows/agents
list pages (badge present on Applications only) — noted in the design note.

### ▶▶ DECISION (session 4, mid): TWO-CLEAN BAR ABANDONED → WHOLE-BRANCH AUDIT
The Codex two-clean loop was stopped after #16. Rationale the user and I agreed on:
the loop was grinding on **production-fine edges** (sub-second nav races, same-install
concurrent-deploy, lock fencing) and — worse — my OWN fixes kept spawning the next
round's findings (#13 from #12's lock code; #15's incomplete-tier fixes; **#16's P1 was
a cross-tenant hole I introduced in #15's table fix**). A capable adversarial reviewer
always finds *one more* defensible P2 on a surface this broad, so "two consecutive
clean" may never converge. Of ~20 findings across the loop, **~5 would bite a real user**
(#8 workflow-collision, #11 inline_v1, #13 MCP read-only bypass, #15 table-404, #16
cross-tenant table) — ALL FIXED. The rest was hardening.
**NEW done-bar: (a) a whole-branch quality audit (in progress, workflow
`solutions-branch-audit`) with the real BUG/SLOP findings fixed, then (b) HUMAN review of
the branch — especially the new auth/concurrency code (X-Bifrost-App gate, write_lock,
install-scoped resolution, uuid5 remap).** Working tree CLEAN, all tests green, nothing
half-done. Session footprint: ~700 lines net logic across ~35 files (12 code commits).

### ⚠️ STOPPING-RULE REASSESSMENT (session 4, user-aware) — IMPORTANT
**The finding source has shifted from "pre-existing gaps" to "defects in the fixes I
just wrote."** #13: 3/4 were edges in the #12 concurrency code. #15: 2/4 were my
incomplete prior fixes (workflow-fixed-but-not-table). **#16 P1 was a CROSS-TENANT
SECURITY HOLE I introduced in the #15 table fix** (the X-Bifrost-App lookup had no
org gate — fixed). When fixes themselves spawn P1/P2, "two consecutive clean" may
not converge. **User decision: run #17 as VERIFICATION-ONLY (add NO new mechanisms),
then reassess.** If #17 again finds only my-own-new-code issues → switch the bar to
"core correctness verified (zero design-level P1 across 16 rounds) + a HUMAN
security/concurrency review of the NEW code (X-Bifrost-App gate, write-lock,
install-scoped resolution, uuid5 remap)" rather than chasing Codex to two-clean.

### SESSION 4 cont. — review #16 closed (1 P1 + 1 P2 + 1 P3)
Commit 4509891d:
- **[P1 SECURITY] cross-tenant table access**: my #15 X-Bifrost-App table lookup
  trusted the client header and resolved a table by the app's solution_id with NO
  org gate — a foreign app UUID could reach another tenant's install table by name.
  Now gated to the caller's org scope (own-or-global for non-superusers).
- **[P2] manifest generator not _repo/-scoped**: `generate_manifest(db)` (workspace
  regen) serialized solution-managed entities into `.bifrost/`. Now solution_id
  IS NULL for the no-solution_id (workspace) case.
- **[P3] app description dropped** by the collector → cleared on deploy. Now carried.

### SESSION 4 cont. — review #15 closed (4 NORMAL-USE findings; 1 was a regression I introduced)
Commit 6650adfb. The broad sweep again found NORMAL-USE issues, incl a real regression:
- **[P2 REGRESSION] v2 app useTable("name") 404'd**: the table analogue of the #8
  useWorkflow fix that I never extended to tables. The name cascade excludes
  solution-managed tables and the table SDK sent no install id, so an app couldn't
  use the table it deployed. Fix: v2 provider sends `X-Bifrost-App`;
  get_execution_context → ExecutionContext.app_id; get_table_or_404 resolves the
  app's solution_id and finds the install's table by name.
- **[P2] Incomplete #14 workspace scoping**: #14 scoped only delete_workflows_for_file;
  reindex/deactivation/rename/register/replace still mutated by Workflow.path alone.
  All now filter solution_id IS NULL (reindex.py, deactivation.py, file_ops.py,
  workflow_orphan.py, mcp_server/tools/workflow.py).
- **[P2] Agent deploy dropped max_iterations/max_token_budget/mcp_connection_ids**:
  now stamped + the MCP-grant junction full-replaced on deploy.
- **[P3] Bad manifest access_level → 500**: validated against the enum → 409.
PATTERN NOTE: two of these (#14-incomplete, table-not-extended-from-#8) were MY
incomplete prior fixes — the "fix one tier, miss the parallel tier" trap. When
fixing a tier-scoping or identity issue, sweep ALL parallel surfaces (workflow AND
table AND form AND agent; lookup AND delete AND rename AND register).

### SESSION 4 cont. — review #14 closed (2 NORMAL-USE P2s; severity tagging added)
The #14 prompt asked Codex to tag findings NORMAL-USE vs HOSTILE-LOAD; both were
NORMAL-USE (worth fixing), and both in areas the concurrency rounds didn't touch.
Commit a5289658:
- **Workspace indexer not _repo/-scoped**: the uniqueness migration lets a _repo/
  and a solution workflow share (path, function_name), but WorkflowIndexer queried
  by path alone → MultipleResultsFound on save, or a _repo/ file delete deactivating
  the solution workflow. Scoped index_python_file / data_provider / deactivate /
  github_sync prefetch to solution_id IS NULL.
- **Form/agent access_level dropped**: deploy stamped only org+solution; the indexer
  preserves access_level, so the manifest's value was ignored + unchangeable on a
  read-only entity. Deploy now applies it from the manifest.

### ⚠️ HONEST READ ON THE TREND (session 4, 2026-06-06) — read before more rounds
**Zero P1 for SIX rounds (#8 was the last).** The core design (identity remap, install-scoped
resolution, isolation, read-only) is solid; every round since is peripheral hardening.
**But the bar may be the wrong stopping rule for a surface this broad.** A capable adversarial
reviewer told "find P1/P2" will almost always surface *a* defensible P2 — and this feature is
genuinely race-prone (per-install deploy + dynamic ES imports + a shared global + Redis locks +
a reused React component). #13 even ticked UP (4 P2), mostly because the #12 concurrency-lock fix
introduced its own edges (fencing token, watchdog resilience, lost-trigger) — i.e. fixes can spawn
next-round findings.
**Honest severity recount of the ~15 findings:** only ~3–4 would bite NORMAL use —
#8-P1 (two solution apps → wrong workflow; scaffold default makes it common), #11 (inline_v1 →
broken app; reachable by omission), #13 (MCP bulk-delete read-only bypass). The rest are
**correct-under-hostile-concurrency/timing** (v2 A→B sub-second nav races; concurrent deploys to
the SAME install — rare for an occasional operator action). All real *as code defects*; most
low-probability in practice.
**OPEN DECISION for the user:** keep the strict 2-clean bar (may chase marginal P2s a few more
rounds) vs. switch to **"zero findings that hit normal use + remaining documented"** (functionally
reached ~#11). User leaning: hold the bar for now, but flag each round whether new findings are
normal-use or hostile-load. Don't lower the bar unilaterally — it's the recorded team criterion.

### SESSION 4 cont. — review #13 closed (4 P2 + 1 P3 — concurrency-code edges + 1 pre-existing)
Commit 52c9d2b5. 3 of 4 P2s were edges in the #12 lock code (reviewer scrutinizes NEW code hardest):
- **write_lock fencing**: constant lock value → a stale holder's watchdog/release could touch a
  SUCCESSOR's lock. Now per-holder UUID token + compare-by-token Lua (register_script) for
  renew/release.
- **write_lock renewal resilience**: watchdog exited on the first transient redis error → renewal
  silently stopped. Now logs + keeps retrying.
- **git-sync lost newer commits**: a trigger arriving while the lock was held was dropped. Now sets a
  pending-rerun flag; the holder re-checks after finalize and re-syncs. Extracted `_run_sync_once`.
- **MCP bulk-delete read-only gap** (PRE-EXISTING on main, in-scope because the branch creates managed
  agents/forms): `update_agent`/`update_form` issue Core bulk deletes that bypass the ORM-flush
  backstop, and the agent executor commits even after an error_result. Added `is_solution_managed`
  guard returning the locked message BEFORE any mutation.
- **[P3] deploy conflict → 409**: router caught only lock+finalize errors; `SolutionDeployConflict`
  surfaced as 500. Now mapped to 409.
Files: write_lock.py, git_sync.py, solutions.py (router), mcp_server/tools/{agents,forms}.py.

### SESSION 4 cont. — review #12 closed (deploy concurrency, 2 P2s, both real)
Broad sweep found two one-writer/atomicity gaps (commits 6f8fce99 + the clone-to-thread follow-up):
- **#12 P2 manual-deploy lock**: the deploy router held NO per-install lock across
  DB commit + S3 finalize (the app-slug pg advisory lock is txn-scoped, releases
  at commit). Two concurrent deploys could interleave finalize → DB from B,
  artifacts from A.
- **#12 P2 git-sync lock TTL**: fixed 300s, no renewal; a long clone+build+finalize
  could outlast it, letting a 2nd sync interleave.
Fix: new `solution_write_lock` (write_lock.py) — Redis SET-NX with a watchdog that
RENEWS the TTL while held; crashed holder self-heals in one TTL. Manual deploy
wraps deploy+commit+finalize (concurrent → 409); git_sync uses the SAME lock
(shared namespace, manual+connected can't race). Follow-up: `GitRepo.clone_from`
now runs via `asyncio.to_thread` so a slow clone can't block the loop and starve
the watchdog (the vite build already ran off-loop). Solution unit + e2e green.

### SESSION 4 cont. — review #11 closed (BROAD sweep, 2 backend P2s, both real)
The #11 prompt was widened to a full-surface sweep (not just v2 lifecycle); it
found two BACKEND P2s (commit fe797366):
- **#11 P2 sibling-install resolution**: a scoped path-ref caller fell back to a
  SIBLING install's workflow when its own install lacked the path (and the
  `len(rows)==1` shortcut bypassed scope). Restructured: scoped caller →
  own-install → _repo/ → None, NEVER a sibling.
- **#11 P2 inline_v1 apps**: deploy created a published-but-sourceless Application
  for an inline_v1 app (the omitted-app_model default). Now REJECTED at deploy
  (Solution apps are standalone_v2 by design). User chose reject-over-persist.
Note: the broad sweep finding backend issues (after v2-lifecycle rounds) is why
widening the prompt mattered. Solution unit + e2e green; ruff/pyright clean.

### SESSION 4 cont. — review #10 closed (1 P2, verified real)
- **#10 P2 stale v2 mount across navigation** (commit 82fee1b8): BundledAppShell
  reused across app routes rendered StandaloneV2App with the NEW appId + the
  PREVIOUS app's entry/baseUrl during the next manifest fetch (the nonce registry
  couldn't catch it — self-consistent but wrong identity). Fix: reset v2Mount
  DURING RENDER on appId change (prevAppId pattern, no effect → no cascading
  render) + `key={appId}` on BundledAppShell at AppRouter.
**Convergence is clear: 3P1+3P2 → 1P1+1P2 → 2P2 → 1P2, all in the v2-lifecycle
corner; no P1 for 3 rounds.** Client 116 vitest + tsc + eslint clean.

### SESSION 4 cont. — review #9 closed (lifecycle P2s, both verified real via subagents)
- **#9 P2 worker ordering** (commit b72ed6a7): the persistent fork path cleared
  workspace modules BEFORE the Solution context was set, so cross-install
  sys.modules eviction ran blind (a prior install's same-name module could
  survive). Moved eviction into `_execute_async` AFTER `set_solution_context`;
  removed the premature clear from `template_process`.
- **#9 P2 v2 A→B race** (same commit): fast app→app nav could mount app A into
  app B's node via the shared global bootstrap. Added per-mount registry
  `window.__BIFROST_APPS__` keyed by the entry URL's `m` nonce; scaffold reads
  its own nonce from `import.meta.url`. Legacy `__BIFROST_APP__` kept for older
  hosts.
Green: 141 solution unit + 14 e2e + 115 client vitest; ruff/pyright/tsc/eslint clean.

### SESSION 4 cont. — review #8 closed (install-scope cluster)
Codex #8 reset the counter with two real findings on the session's own identity work
(verified with Explore subagents before fixing — both real):
- **#8 P1** (commit 579d57a3): a v2 app's `path::fn` ref could resolve a SIBLING
  install's workflow (two installs in one org both shipping workflows/main.py::main;
  resolver had only org_id → non-deterministic first row). Fixed per the user's
  intended install-namespaced model: (1) DB uniqueness — ONE install per (slug,
  scope) [migration 20260605_solution_unique_scope + Solution.__table_args__]; (2)
  `WorkflowRepository.resolve(..., solution_scope=)` resolves the caller's OWN
  install first, then global _repo/; (3) threaded `app_id` on the execute request →
  `Application.solution_id` → solution_scope; client injects appId into the bootstrap
  → BifrostProvider → useWorkflow sends app_id. Regenerated v1.d.ts. Proven by an
  e2e: two installs sharing the path each run THEIR OWN workflow via app_id.
- **#8 P2** (same commit): same-bundle-object redeploy double-remapped. `_remap_bundle_ids`
  → `_remapped_bundle` returns a NEW bundle; caller's bundle never mutated.
Green: 171 solution unit + 14 e2e + 114 client vitest; ruff/pyright/tsc/eslint clean.
**NEXT: read `/tmp/codex_review9_out.txt` (prompt `/tmp/codex_review9.txt`). Clean → clean
#1; run #10 for #2. Findings → triage w/ `superpowers:receiving-code-review` + Explore subagents.**
USER DESIGN NOTE (session 4): a Solution is NOT meant to install twice on one scope;
solution code is namespaced `_solutions/{install_id}/` and resolves own-namespace-first
then global _repo/ (if global_repo_access). The #8 P1 fix codifies this.

### SESSION 4 (2026-06-05) — review #7 fully closed
The user stress-tested the identity design (composite-key vs uuid5; cross-refs; events calling
solution workflows) and confirmed: **uuid5 remap at install-time only** (source repo keeps
author-time ids), **cross-refs resolve by path/name** within solution scope, **events calling a
solution workflow/agent already work** (direct FK join, no resolver, no exclusion — unaffected).
Commits on `worktree-solutions-success-criteria` (HEAD 65753cf6):
- `dfd6800f` R7-P1-bc: `solution_entity_id` uuid5 remap + `_remap_bundle_ids` (deep-copy, idempotent,
  cross-ref translation: form/agent→workflow, agent→agent, form_schema data_provider_id) +
  `WorkflowRepository._resolve_by_path_ref` includes solution rows & prefers the caller's own-org
  solution row (global caller prefers _repo/). Removed dead `exclude_solution_managed`.
- `141fc83d` R7-P2-d (delete_missing_prefix managed guard) + R7-P2-e (StandaloneV2App tombstone).
- `70eaa160` R7-P1-a (None deployer org ≠ global install match).
- `c42bfcef` R7-P2-f (`bifrost auth token` + vite execFileSync fallback to credential store).
- `65753cf6` e2e tests updated for the remap (look up by `solution_entity_id`, assertions unchanged).
Green: 166 solution unit + 13 e2e + 17 jsx-app vitest; ruff + pyright + tsc + eslint clean.
**NEXT: read `/tmp/codex_review8_out.txt` (prompt `/tmp/codex_review8.txt`). If clean → clean #1,
run #9 for #2. If findings → triage with `superpowers:receiving-code-review` + Explore subagents.**

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
