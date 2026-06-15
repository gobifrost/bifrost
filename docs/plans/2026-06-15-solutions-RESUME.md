# Solutions — RESUME (fresh-session handoff)

**Date:** 2026-06-15
**Branch:** `solutions/connection-references` · worktree `solutions-success-criteria` · **draft PR #347, NOT pushed** (do not push/un-draft/merge without Jack's explicit say-so).
**Read first for state:** `docs/plans/2026-06-14-solutions-platform-impact-audit.md` (audit + fixes) and `docs/superpowers/specs/2026-06-15-solutions-knowledge-decision.md` (knowledge/storage scoping). Obsidian tracker: `Projects/Bifrost/Platform Overhaul/subplans/Solutions.md` (morning summary at top + "Next up" sections).

## What's DONE (committed on branch, not pushed)
- **Adversarial audit + 9 fixes** — incl. H1 (git-sync deletion sweep wiped solution-managed entities via Core, bypassing the guard — VERIFIED data loss, fixed centrally). F1/F2 (`bifrost run` + `solution start` now carry solution_id; verified live). U1, M-MCP (5 legacy MCP tools), M-ROLE, UX surfacing.
- **`/qa` skill** — adversarial four-lens review, in `gocovi/skills` (Covi-managed, pushed, synced to both runtimes). Separate from the bifrost plugin.
- **Event/schedule TRIGGERS — V1 coverage, fully built.** `solution_id` on event_sources/event_subscriptions; capture/export/install wiring; `_upsert_events` deploy + FK remap + reconcile; events-router read-only guard (PATCH/DELETE → 409); SDK/CLI deploy carry `events`. TDD: 4 unit + 1 e2e, full verification green, desloppify clean.
- **Knowledge + storage SCOPED OUT of V1** (decision records written). Knowledge V1 = nothing to build (namespace binding already travels; operator populates; install-preview note warns). Knowledge corpus-carry = V2, gated on embedding portability (re-index on restore unless same model). Storage = post-V1 (files have no tracked record).

## REMAINING TASKS (priority order)

### 1. Decide PR #347 (Jack's call) — the only thing truly waiting
17 commits this session, all green, draft, not pushed. Push / un-draft / merge is Jack's decision.

### 2. SolutionDetail layout redesign (designed, not built)
9 flat tabs → **3: Overview · Contents · Configuration.** README is NOT a tab — it leads the **Overview** (rendered, GitHub-style; no-README fallback = status/contents summary). **Setup is a STATE not a tab** — demote to (a) Overview banner when incomplete ("⚠ Setup incomplete — N unset [Fix]→") and (b) ⚠ badge on the Configuration tab label; both vanish when `setup_complete` flips. **Configuration** = permanent tab (config values + connections — revisited over the install's life). **Contents** = the 6 entity inventories collapsed into one type-filtered list. **Bundled fix: block the README PUT when `git_connected`** (409, no UI affordance — auto-pull owns it; Jack: "no UX for this should be allowed"). Touches `client/src/pages/SolutionDetail.tsx` + readme PUT guard in `api/src/routers/solutions.py` + tests. Full design in the Solutions subplan "Next up — SolutionDetail layout redesign".

### 3. Build skill rework — Solutions-aware + router/reference-guides (designed, not built)
**Correctness gap, not docs polish:** the build skill teaches "mutate entities live via `bifrost <entity> create|update`" (`bifrost-build/SKILL.md:58,261`) — the OPPOSITE of the solution invariant (entities are deploy-owned/read-only; live mutation 409s against the guard we hardened). Redesign (Jack): thin top-level `SKILL.md` **dispatcher** → `bifrost.solution.yaml` present ⇒ read `reference/build/solutions.md`, else `reference/build/repo.md`; branch the 33KB monolith into per-topic reference guides (CLI/API/app-patterns). **`solutions.md` deliberately LIGHT** (v2 = standard React; cover only init/start/deploy + the read-only constraint + reference the `migrate` skill as the worked path). **Collapse the two drifted copies** (`plugins/bifrost/skills/bifrost-build/` and `.claude/skills/bifrost-build/` via the `skills/build` symlink). **Brainstorm the skill design first.** Repo-native (committed to bifrost), so: edit + manual plugin version bump.

### 4. Plugin manifest version bump (chore)
Manifest at `0.9.2-dev.451`; `scripts/compute-dev-version.sh` says `0.9.2-dev.571` (~120 commits stale, pre-existing — NOT caused by this session). `scripts/update-plugin-version.sh "$(scripts/compute-dev-version.sh)"`. Pairs with task 3 (and any `skills/` change). Durable fix: automate in pre-push/CI.

### 5. (V2 / later, gated on brainstorms) Knowledge corpus-carry · File storage
See `docs/superpowers/specs/2026-06-15-solutions-knowledge-decision.md` and `…/2026-06-14-solutions-v1-coverage-design.md`. Both need a brainstorm before any build (knowledge: embedding portability; storage: tracked-file-record design). Not V1.

## Constraints / gotchas (carry into the new session)
- Worktree only; never 2 concurrent `./test.sh`; full pre-completion verification before claiming done; no client specifics in the public repo.
- Test/debug stacks already migrated to `20260614_solution_triggers`. The test-stack **API container** must be restarted after deploy-path code changes (it's long-running; the test-runner reads source fresh but the HTTP API doesn't).
- Solution-managed writes from deploy/sync/delete MUST use Core statements (the always-on read-only guard 500s on ORM-object mutation in prod but passes in isolated unit tests — install the guard in the test, see memory `project_solution_managed_guard_deploy_core`).
- `./test.sh` passthrough: `-k` must be a separate arg, not inside the path string.
