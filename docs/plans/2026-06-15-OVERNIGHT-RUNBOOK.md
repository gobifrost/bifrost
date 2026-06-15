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
- NOTE: if Phase 3 collapses Contents into one list anyway, do the sticky fix as part of the new Contents list rather than twice. **`[DECIDED-OVERNIGHT]` allowed:** fold 2a into Phase 3 if cheaper — document the choice.
- **STATE:** ☐ not started

### 2b. Name-only datatables → real columns
- `SolutionEntitySummary` is just `{id, name}`. Add per-type columns to the contract (`api/src/models/contracts/solutions.py`) + the `/entities` endpoint, then surface them: workflows → path/function; apps → slug/model; tables → row count; forms → linked workflow. `npm run generate:types` after. Backend test + vitest.
- **STATE:** ☐ not started

### 2c. Verify the two real bug candidates (then fix if confirmed)
- **Org-change re-stamp:** does `PATCH /api/solutions/{id}` (org change) re-stamp `organization_id` on owned entities, or strand them until next deploy? Check `solutions.py` update endpoint + `_upsert_*` in `services/solutions/deploy.py`. Reproduce live. If broken → fix + test. If fine → document "verified correct."
- **Provider-org/admin access to an org-scoped install's app:** test live as `dev@gobifrost.com` (provider) against an RTM-org install — app mount + workflow exec. Bypass = `is_platform_admin OR is_provider_org`. If broken → fix + test; else document.
- **STATE:** ☐ not started

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

> Update this section at the end of each phase so a glance shows where the night landed.

- **Phase 1 (validation):** ☐ pending
- **Phase 2 (inbox UX):** ☐ pending
- **Phase 3 (redesign):** ☐ pending
- **Phase 4 (UX review):** ☐ pending
- **`[DECIDED-OVERNIGHT]` calls made:** _(list, with one-line WHY each)_
- **`[BLOCKED-ON-JACK]` items:** _(list, with options)_
- **Commits added overnight:** _(range)_
- **Anything pushed?** NO (policy) — confirm at close.
