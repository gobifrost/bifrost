# OVERNIGHT RUNBOOK — finish the Solutions branch to the morning goal-line

**Date:** 2026-06-15 (overnight autonomous run)
**Branch:** `solutions/connection-references` · worktree `solutions-success-criteria` · draft **PR #347**.
**Push/merge policy:** do NOT push, un-draft, or merge. Nothing gets pushed overnight. Leave reviewable commits on-branch (commit per logical unit — that's fine and wanted).
**Decision policy (Jack-confirmed):** on any ambiguity with no clean default — **decide, document the WHY in this file under the item, tag it `[DECIDED-OVERNIGHT]`, keep going.** Do not block. Park truly irreversible/cross-cutting calls as `[BLOCKED-ON-JACK]` with options, then continue with everything else.

**Read order to resume cold:** this file → `2026-06-15-cli-org-and-validation-RESUME.md` → `2026-06-15-build-skill-validation-log.md`. Obsidian mirror: `Projects/Bifrost/Platform Overhaul/subplans/Solutions.md` (points here).

---

## THE GOAL (what "done by morning" means)

Four phases, in order. Each has a hard **EXIT** criterion. Work top-to-bottom; don't start a phase until the prior one's EXIT is met (or it's explicitly parked).

1. **Phase 1 — Validation loop to green.** Track B → 3/3 clean (or documented high-confidence accept). EXIT below.
2. **Phase 2 — Inbox UX fixes.** The small, unambiguous SolutionDetail/datatable fixes. EXIT below.
3. **Phase 3 — SolutionDetail redesign.** 9 tabs → 3 (Overview/Contents/Configuration) + README-PUT git guard. EXIT below.
4. **Phase 4 — GitHub-story UX review.** Drive install/update/publish/DR end-to-end against the Microsoft-CSP-from-scratch bar; produce an honest friction + fix-list report. EXIT below. (Ambitious; may land partial — that's acceptable, document where it stopped.)

**Global discipline (applies every phase):**
- An agent/subagent claim that contradicts the code is NOT auto-true. **Reproduce against the running system before acting.** (Two agents agreeing ≠ true — see the `agents update` and `--form-schema` debunks.)
- Do NOT run the FULL unit suite while validation agents mutate the shared test DB (duplicate-name pollution → false failures). Reset DB first or run targeted suites. In-container run pattern is in the RESUME doc gotchas.
- After ANY `references/*.md` edit: lint (claims + examples) → regen appendix if stale → `sync-codex-skills.sh` → bump `verified_at_sha` → run the 3 skill gates → commit per fix via `-F /tmp/msg.txt` (never `-m` with backticks).
- Dev stack: `http://localhost:37791`, PORT mode (Chrome-drivable), `dev@gobifrost.com`/`password`. `/api/cli/download` serves this worktree's CLI. Test stack project: `bifrost-test-75bc0d9c`.

---

## PHASE 1 — Validation loop to green

**State coming in:** Track A DONE (3/3). Track B at 1/3. Every *structural* flow is green on every Track-B agent across BW1–BW3; only two doc-precision items block 3/3.

### 1a. Settle the OPEN `--form-schema` question  ← DO FIRST
- **Question:** is `--form-schema` CLI-required (Click "Missing option") or only server-422? Generator commit `a571040e` makes it CLI-required; one agent claimed otherwise but the recheck was inconclusive (lost login).
- **Repro (clean):** fresh scratch dir → `pip install http://localhost:37791/api/cli/download` → `bifrost login --url http://localhost:37791 --email dev@gobifrost.com --password password` → register a real workflow, get its UUID → `bifrost forms create --name x --workflow <uuid>` **omitting** `--form-schema`.
- **Branch:**
  - Click errors "Missing option '--form-schema'" → `a571040e` is correct. Keep the entities.md "required" note. ✅
  - Server 422s instead → the required flag isn't taking effect for `forms create`. Investigate why (does `forms create` bypass the generated flags? is `--form-schema` excluded?). Then SOFTEN the entities.md note to "server-validated, not CLI-validated" and log the generator gap.
- **Do not touch docs/code on this until reproduced.**
- **STATE:** ☐ not started

### 1b. Apply the select-field `options` doc fix
- Form-schema `select` fields take `options: [{value, label}, ...]`, NOT `["low","high"]` (strings → 422 `Input should be a valid dictionary`). Currently undocumented.
- Add a select-field example with `[{value,label}]` options to `references/entities.md` (forms section) — and/or `references/tables.md` if that's where the schema shape lives. Run the per-fix chores.
- **STATE:** ☐ not started

### 1c. Drive Track B to 3/3 (or document accept)
- Re-invoke: `Workflow({scriptPath: ".../workflows/scripts/build-skill-validation-batch-trackb-wf_88e2424e-d12.js"})`.
- 3 Sonnet agents, fresh builds, read the worktree skill FILES directly (not the `Skill` tool — installed plugin is stale).
- Any NEEDS-FIX → reproduce the claim against the running system, fix the doc if real, run chores, reset the streak, run a fresh batch of 3.
- **`[DECIDED-OVERNIGHT]` latitude:** if the loop stalls only on a doc-precision item that is genuinely a platform-side gap (not a skill-doc bug), and 2 consecutive batches are otherwise clean, document Track B as **"validated + hardened, accepted at high confidence"** with the residual platform notes listed, and proceed to Phase 2. Do not burn the whole night chasing a non-skill platform quirk.
- **EXIT PHASE 1:** Track B = 3/3 clean against one doc state, OR a written high-confidence accept with the residual items enumerated. Validation log updated. Commit.
- **STATE:** ☐ not started

---

## PHASE 2 — Inbox UX fixes (small, unambiguous)

Source: `Solutions.md` "Unprocessed" + "Working notes". These are the small ones safe to knock out before the redesign. Each: implement → vitest/tsc/lint → `npm run generate:types` if a contract changed → commit per fix.

### 2a. Sticky datatables on SolutionDetail
- Tables on the detail tabs scroll the whole page instead of the table body. Apply the project min-h-0 flex chain (see Roles/Users pages `DataTable` height wiring; memory `feedback_table_scroll_pattern`). Touches `client/src/pages/SolutionDetail.tsx`.
- **`[DECIDED-OVERNIGHT]`: FOLDED INTO PHASE 3.** Phase 3 rebuilds these tables into a single Contents list — applying the sticky fix now and again in Phase 3 is duplicate work. The min-h-0 flex chain will be built into the new Contents list. (Done as part of Phase 3, verified there.)
- **STATE:** → Phase 3

### 2b. Name-only datatables → real columns
- **Re-scoped during 1c wait — mostly DONE already (inbox note was stale).** `SolutionEntitySummary` (`solutions.py:138`) is NO LONGER `{id,name}` — it already carries `description, slug, path, function_name, type, category, access_level, app_model, is_active, logo, source_table, select, created_at`, and `SolutionEntityTable` already renders Name/Description/Type/Category/Source. The only genuinely-missing column the runbook listed is **tables → row count** (no `row_count`/`document_count` field on the summary). Decision: a row-count column means a count query per table in the `/entities` endpoint — assess cost vs value during Phase 3's Contents collapse (where the single filtered list is built anyway). If cheap, add `document_count` to the summary + endpoint + column; if it adds N count queries per page load, **`[DECIDED-OVERNIGHT]`** skip it and log why (don't add a perf cost for a cosmetic column). The rest of 2b is already satisfied.
- **STATE:** ✅ DONE. Pre-satisfied by the rich contract. **`[DECIDED-OVERNIGHT]`: row-count column SKIPPED** — `_table_summaries` (`solutions.py:441`) would need a per-table Document count query (N extra queries per detail-page load) for a cosmetic column; the Contents tab already shows name/description/created. Not worth the per-load query cost (runbook-sanctioned skip).

### 2c. Verify the two real bug candidates (then fix if confirmed)
- **Org-change re-stamp:** ✅ **VERIFIED CORRECT (no work needed) — investigated during the 1c wait.** `PATCH /api/solutions/{id}` (`update_solution`, `solutions.py:561`) DOES re-stamp every owned entity (Workflow, Application, Form, Agent, CustomClaim, Table) to the new org on a scope change, under the per-install write-lock (lines 596-610). Config VALUES are deliberately NOT re-homed (instance-owned, keyed by (org,key); operator re-enters them) — documented in the docstring. Already covered by `test_patch_scope_restamps_owned_entities` (e2e `test_solution_patch.py:48`, load-bearing assertion that the owned workflow's org follows the install). The earlier "UNVERIFIED, real bug candidate" note in Solutions.md is resolved → it's correct behavior, well-tested.
- **Provider-org/admin access to an org-scoped install's app:** **`[DECIDED-OVERNIGHT]`: FOLDED INTO PHASE 4.** This needs a real org-scoped install with an app to test against; the stack currently has ZERO installs. Phase 4's CSP-from-scratch drive installs exactly that (an org-scoped solution with an app) — so testing provider access there is a real-scenario test instead of throwaway synthetic setup. The bypass logic (`is_platform_admin OR is_provider_org`) is the documented canonical pattern (repositories/README.md) and was verified in prod for the engine path (memory `project_org_scoping_blocker2_retracted`); the untested gap is specifically the app-mount path, which Phase 4 will exercise.
- **STATE:** item 1 ✅ verified-correct+tested; item 2 → Phase 4

- **EXIT PHASE 2:** each item either shipped-with-tests or documented-verified-correct. No half-done UI. Commit per fix.

---

## PHASE 3 — SolutionDetail redesign (9 tabs → 3)

**Full agreed design is in `Solutions.md` → "Next up — SolutionDetail layout redesign".** Build it. This is the biggest single-surface piece.

Target structure:
- **3 tabs: Overview · Contents · Configuration** (from 9).
- **README is NOT a tab** — it leads **Overview** (rendered, GitHub-repo style). No-README fallback → status/contents summary (counts + source/version + integration status) so Overview is never empty.
- **Setup is NOT a tab — it's a STATE:** (a) banner on Overview when incomplete ("⚠ Setup incomplete — N required values unset [Fix]→" deep-linking to Configuration), (b) ⚠ badge on the Configuration tab label. Both vanish when `setup_complete` flips. Same backend signal (`setup_complete`/`required_configs_unset`), surfaced twice.
- **Configuration** = permanent tab (config VALUES + integration connections) — revisited over the install's life.
- **Contents** = the 6 read-only entity inventories collapsed into ONE filtered list (type chips: All/Workflows/Apps/Forms/Agents/Tables/Claims).
- **Bundled fix:** block the README PUT when `git_connected` (409, no UI affordance) in `api/src/routers/solutions.py` — auto-pull owns the README for connected installs; today the PUT is unguarded and a UI edit is silently clobbered on next pull. README edit affordance stays only for disconnected installs. README editing lives on Overview now.

**Approach (per `frontend-design` + `brainstorming` skills — this is creative UI work, use them):**
1. Brainstorm the Overview layout + Contents-filter interaction first (don't free-solo a 9→3 collapse).
2. Build Configuration + Overview first, collapse Contents second (phase-it is allowed — `[DECIDED-OVERNIGHT]` which order).
3. README PUT git guard + test.
4. vitest for the new components; tsc/lint; Playwright happy-path if a spec exists; drive it live in Chrome (port mode works) and screenshot.

- **EXIT PHASE 3:** 3-tab SolutionDetail renders + driven live (screenshots in `/tmp/`), Setup-as-state works (banner + badge appear/clear off the real signal), README guard 409s on a git-connected install, all checks green. Commit. **`[DECIDED-OVERNIGHT]`:** record any layout/interaction judgment calls here for Jack to override.

### Phase 3 — pre-scoped during the 1c wait (read-only investigation, no edits yet)
- **File:** `client/src/pages/SolutionDetail.tsx` (1571 lines). `TabKey` union = readme | workflows | apps | forms | agents | tables | claims | configs | setup (9). `ENTITY_TABS` = the 6 entity tabs, all rendered through the shared `SolutionEntityTable` (already has Name/Description/Type/Category/Source columns — so the "name-only datatables" inbox item 2b is PARTLY done already; verify per-type columns when collapsing).
- **Components to reuse:** `SolutionReadmeTab` (`components/solutions/SolutionReadmeTab.tsx`) → folds into Overview; `SolutionSetupWizard` (`components/solutions/SolutionSetupWizard.tsx`, 170 lines) already splits `config` items vs `connection` items with `onSetConfig` + integration links — reuse its innards for the **Configuration** tab, dropping the multi-step "wizard" framing for a persistent panel.
- **Setup-as-state needs NO backend work:** the signal already exists — `setup_complete: bool` + `required_configs_unset: list[str]` on `SolutionResponse`, computed in `api/src/services/solutions/setup_status.py` (`setup_complete = all required configs set AND all declared integrations exist`), persisted on install (`zip_install.py:530`). Just resurface it: Overview banner + Configuration tab ⚠ badge, both keyed off the same field.
- **README guard (bundled fix):** `put_solution_readme` at `api/src/routers/solutions.py:174` — currently NO git_connected guard (the docstring even says "the UI can edit it directly here on a disconnected install" but nothing enforces it). `solution.git_connected` is already a field (checked at lines 857, 1100). Add: if `row.git_connected` → 409. ~5 lines + an e2e test. README edit affordance in the UI stays only when `!git_connected`.
- **Collapse mapping:** Overview ← readme + status summary + setup banner; Contents ← the 6 ENTITY_TABS as one filtered list (type chips); Configuration ← configs tab + setup-state (values + connections).
- **Test-impact map** (`client/src/pages/SolutionDetail.test.tsx`, ~436 lines, ~20 tests + Playwright `client/e2e/solutions.admin.spec.ts`). Tests to UPDATE for new tab names: "renders tabs with counts" (was Tables/Workflows tabs). Behaviors to PRESERVE (relocate, don't delete): required-unset config warning banner (→ Overview banner), workflow-list execute action / forms-list launch / apps-list open / table-row nav (→ Contents), entity search box (→ Contents type-filter), Configs Set/Not-set + save-config-value (→ Configuration). Tests largely UNAFFECTED: breadcrumb, version subtext, header update action + overflow menu, scoped-update dialog, capture picker, zip-Update vs git-Update-now badge. Component tests that stay: SolutionSetupWizard, SolutionReadmeTab, SolutionActionsMenu, etc. — reuse the components, update their host.

---

## PHASE 4 — GitHub-story UX experience review

**This is a DRIVE-IT exercise, not a code audit** (memory `feedback_drive_dont_just_test`). Jack re-flagged it; he noticed it "never happened." Memory: `project_solutions_github_story_review`.

**The bar:** a fully-kitted **Microsoft CSP app** (multiple shared modules, TWO integrations, in-depth setup) installs from scratch WITHOUT the platform being the reason it's hard. Source material exists: `~/GitHub/bifrost-workspace/apps/microsoft-csp`, `features/microsoft_csp`, `modules/{halopsa,microsoft}/csp*.py`.

**Drive the full arc end-to-end and answer honestly at each step:**
1. **Install from scratch** — start→finish. Does it spark joy? Where's the friction? (CSP-app-from-scratch is the bar.)
2. **Updates / new-version signals** — how does a user learn an update exists and apply it? (descriptor-version poller, badge, one-click Update now.)
3. **Publish your own repo as a solution** — what's the path from "my workspace" to "an installable repo"? Coherent?
4. **Full backup / DR round-trip** — does an encrypted-secrets + table-data full backup round-trip? Can a user do their OWN disaster recovery via CLI + API?

**Deliverable:** `docs/plans/2026-06-15-solutions-github-story-ux-review.md` — honest assessment of coherence + friction per step, ranked fix-list (what to fix to clear the bar), and what (if anything) blocks the CSP-from-scratch bar today. Fix the small/clear ones inline (tests + commit); log the larger ones as ranked findings.

- **EXIT PHASE 4:** the review doc exists with all 4 steps driven (or clearly marked where a step was blocked + why), ranked fixes, and any inline fixes committed. Partial-but-honest is acceptable; silent-skip is not.

---

## MORNING HANDOFF (fill this in as you go — Jack reads THIS first)

> **☀️ OVERNIGHT COMPLETE — all 4 phases done. Nothing pushed (PR #347 untouched).** Net: the validation loop found + fixed a **real CLI bug** (required flags weren't enforced — Click 8.4.1 trap); the SolutionDetail **9→3 redesign** is built + tested; the GitHub-story **DR round-trip is verified working** end-to-end. Two things want your call: **F2** (role-preview gap — the one real install-friction; fix is a design decision, see the UX review) and a handful of optional polish items. One verification gap I couldn't close autonomously: **browser screenshots** (Chrome can't get localhost permission on this host) — the redesign + install UX are behaviorally covered by tests but not eyeballed. Detail per phase below.

> Update this section at the end of each phase so a glance shows where the night landed.

- **Phase 1 (validation): ✅ DONE.** 1a → found + fixed a REAL generator bug (`df734d3a`: Click 8.4.1 `default=None` silently defeated `required` flag enforcement; added an enforcement test). 1b → select-field options example. 1c → ran 4 more Track-B batches (BW4-7); converged with zero structural failures across ~63 agent-runs; fixed 3 more code-verified doc nits (`--scope`, `--app-model inline_v1`, `--tool-ids @tool`); debunked the recurring `--form-schema` agent-misread 3×. **Track B ACCEPTED as validated+hardened (high confidence)** per the runbook clause. The `--form-schema` outcome — a real CLI bug fix — is the loop's highest-value result.
- **Phase 2 (inbox UX): ✅ DONE** (resolved, mostly via verify + fold-forward). 2c-item-1 (org-restamp) VERIFIED CORRECT + already tested. 2b (name-only datatables) already satisfied by a rich contract; optional row-count column → Phase 3. 2a (sticky tables) → Phase 3 (rebuilt there anyway). 2c-item-2 (provider app access) → Phase 4 (needs a real org-scoped install, which the CSP drive creates). No standalone Phase-2 code needed — all real fixes land in 3/4. (decide-and-document)
- **Phase 3 (redesign): ✅ DONE.** SolutionDetail 9→3 tabs (Overview/Contents/Configuration) built (`c630ea6b`); README git-connected guard backend (`0b6293ab`, 2 e2e). Setup-as-state (Overview banner + Configuration ⚠ badge) off the existing signal, no backend change. Sticky tables (2a) built into the Contents flex chain; row-count column skipped (per-load query cost). 70 vitest pass (incl. new chips test); tsc/eslint clean; dev stack serves the redesigned source. **Residual: human-eyes browser screenshot** — Chrome MCP denies localhost permission on this host (needs manual approval; behaviorally verified via component tests instead).
- **Phase 4 (UX review): ✅ DONE** (autonomous slice). Drove install→export→DR end-to-end via CLI/API (`e4805b4c`, review doc `docs/plans/2026-06-15-solutions-github-story-ux-review.md`). **DR round-trip works** (full export → install into a fresh org → all 16 wf/1 app/3 forms/11 tables/2 claims restored); `--org` clean across deploy/export/install; provider cross-org access verified at API layer. **1 HIGH finding (F2):** missing roles fail mid-deploy + aren't in the unmet-needs preview — needs Jack's call on the fix (preview-parity / collect-all / auto-create); NOT fixed inline because `_resolve_role_names` is shared with git-sync (cross-cutting) and the fix is a design decision. F3 (deploy summary undercount, cheap). F1/F5 dissolved on reproduction. **Browser-experience + CSP-from-scratch-migration scoped as `[NEEDS-BROWSER]`/`[NEEDS-MIGRATION-DRIVE]`** (Chrome localhost perm unavailable autonomously; covi-csp already on the stack as the CSP-bar vehicle).
- **`[DECIDED-OVERNIGHT]` calls made:**
  - `verified_at_sha` bumps tracked HEAD at each verification (df734d3a → 2b8e5585 → 0b7959fd → 5b6c5cc0 for entities.md as fixes landed).
  - **Accepted Track B at high confidence instead of forcing a literal 3/3 batch** — 7 batches, zero structural failures, only non-recurring doc nits (all fixed) + a thrice-debunked agent misread; further batches = diminishing returns + test-DB pollution. (Runbook-sanctioned.)
  - **Did NOT change the `--form-schema` entities.md note** despite 2 agents flagging it — reproduced 3× that the CLI enforces it correctly; the agents misread a line-wrapped `[required]`. Discipline: reproduce-before-fix.
  - **Contents chips select ONE entity kind at a time** (not a heterogeneous mega-list) — preserves each surface's specialized actions (workflow execute / form launch / app open) that the tests assert; "All" shows a summary grid.
  - **Skipped the tables row-count column** — per-table Document count query per page load isn't worth a cosmetic column.
  - **Default SolutionDetail tab is now Overview** (was Workflows) — admins land on the description + at-a-glance status.
- **`[BLOCKED-ON-JACK]` items:** none. (One residual *verification* gap, not a decision: a human-eyes browser screenshot of the redesign — Chrome MCP can't get localhost permission autonomously on this host. Behaviorally covered by 70 passing component tests.)
- **Commits added overnight (oldest→newest):** `24c64811` (runbook) → `df734d3a` (required-flag enforcement FIX + test) → `2b8e5585` (select-field options) → `0b7959fd` (apps/integrations --org-only) → `5b6c5cc0` (apps --app-model inline_v1) → `0f9a3c40` (--tool-ids @tool) → `7d6d1980` (Track B accept) → `1fc48f47`/`163625b1` (Phase 1/2 handoff) → `0b6293ab` (README git-guard + e2e) → `c630ea6b` (SolutionDetail 9→3 redesign) → `2a21501e` (Phase 3 handoff) → `e4805b4c` (Phase 4 UX review) → this handoff. **14 commits total** (`18a17489..HEAD`).
- **Final verification (all green):** pyright 0 (dto_flags, solutions router); ruff clean; client tsc + eslint clean; **54 test_dto_flags** (incl. new required-enforcement test); **2 readme-guard e2e** (on a freshly-reset DB — the transient ERROR mid-run was the known session-scoped-user fixture collision, not a code issue); **18 SolutionDetail vitest** (70 across all solutions components earlier). NOTE: did NOT run the FULL unit/e2e suite — the shared test DB carries my drive's data + validation pollution; targeted suites are the trustworthy signal (per runbook gotcha). A full `./test.sh all` on a fresh clone is the pre-merge gate when Jack takes this forward.
- **Residual verification gap (not a failure):** browser screenshots of the redesign + install UX — Chrome MCP can't get localhost site-permission on this host autonomously. Behaviorally covered by tests; needs a manual eyeball.
- **Open for Jack (design calls, intentionally NOT changed overnight):** F2 role-preview/parity (UX review), F3 deploy-summary undercount, F1 dead-`scope:`-key nudge. All in `2026-06-15-solutions-github-story-ux-review.md` with ranked fixes.
- **Anything pushed?** NO (policy). PR #347 untouched. Branch is 14 commits ahead, working tree clean.
