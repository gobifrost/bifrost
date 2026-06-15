# Build-Skill Validation Log

Empirical validation of the rebuilt `bifrost:build` skill (Tasks 11–12). Fresh
Sonnet subagents build real artifacts against the debug stack
(`http://localhost:37791`, port mode) following ONLY the skill. Done bar per
track: **3 consecutive clean runs with no skill-doc edits between them.** Any
misleading-moment fix resets the streak to 0.

## SDK-surface coverage target
- Python SDK: 71 public methods across 14 namespaces (`generated/python-sdk-signatures.md`)
- Web SDK: 22 exports (`generated/web-sdk-surface.md`)
- The union of Track A + Track B must exercise the surface; gaps logged with a reason.

### Python SDK namespace checklist (tick when a run drives it)
- [ ] agents (run)
- [ ] ai (complete/stream/get_model_info)
- [ ] config (get/set/list/delete)
- [ ] events (emit)
- [ ] executions (get/list/get_current_logs)
- [ ] files (list/read/write/delete/exists/get_signed_url)
- [ ] forms (get/list/...)
- [ ] integrations (get/...)
- [ ] knowledge (search/...)
- [ ] organizations (get/list/...)
- [ ] roles (get/list/...)
- [ ] tables (get/insert/update/delete/query)
- [ ] users (get/list/...)
- [ ] workflows (run/...)

### Web SDK export checklist (tick when a run drives it)
- [ ] BifrostProvider · useBifrostContext · BifrostHeader
- [ ] useWorkflow / useWorkflowQuery / useWorkflowMutation
- [ ] useTable / useInfiniteTable
- [ ] tables CRUD (get/insert/update/delete + error classes)
- [ ] (remaining exports per `generated/web-sdk-surface.md`)

## A1 skill-doc findings to apply during the loop (queued)
1. **capture→pull→deploy** is now the real flow — **DONE** (Task 7 rewrote solutions.md). Verify a run follows it cleanly.
2. **capture org-scope rule**: a global (`organization_id: null`) entity isn't capturable into an org-scoped install without re-stamp; same-org required. Document if a run trips on it.
3. **`solution start [APP_SLUG]`** positional needed with multiple apps — document if a run trips.
4. The "don't edit .bifrost/" vs "manually add a workflow UUID entry" contradiction — reconcile if a run trips (pull should now obviate manual edits).

---

## Track A — Solution build (read-only invariant in force)

Goal: `solution init` → scaffold a Tailwind-styled app → get an agent + table +
form/config into the solution → `solution start` + drive → update an entity →
`solution deploy`. Pin down the entities-into-a-solution open question.

| Run | Result | Styled | Entities | Update | Deploy | Invariant | Misleading moments → fix | Streak |
|-----|--------|--------|----------|--------|--------|-----------|--------------------------|--------|
| A1 | PARTIAL | Tailwind configured, sample uses inline styles | workflows round-trip; table/form/agent/config **captured then DELETED by next deploy** | yes (workflow) | workflows+app clean; **captured entities destroyed** | ✓ 409 on solution-managed update | see below — **blocked on platform bug** | 0 |
| A2 | INVALID (wrong skill) | yes | **all 4 round-trip + survive** ✓ | yes ✓ | clean ✓ | ✓ 409 | tested the STALE installed plugin, not the rebuilt worktree skill — see note | 0 |
| A3 | NEEDS-FIX (valid) | yes (manual Tailwind) | **all 4 round-trip + survive** ✓ | yes ✓ | clean ✓ | ✓ 409 | 4 real doc fixes (below) → applied, streak reset | 0 |
| A4 | NEEDS-FIX (valid) | yes (styling callout WORKED) | **all 4 round-trip + survive** ✓ | yes ✓ (.bifrost edit path) | clean ✓ | ✓ 409 | 3 fixes: pull `--org`, entities.md `.bifrost` contradiction, scaffold `src/` tree → applied | 0 |

### A4 — A3's styling fix verified clean; 3 new fixes applied (+ the scope-rule correction from the Jack exchange)

A4 confirmed A3's fix #3 landed ("the skill correctly documents that scaffold generates inline styles and says to replace with Tailwind"). Round-trip + 409 guard + read-only invariant + the `.bifrost` update path all ✓. Three new valid fixes, all applied (streak stays 0):
1. **solutions.md Path A** — `bifrost solution pull` needs the **same `--org`** as deploy when the install is in a non-default org; without it pull resolves the WRONG install, downloads stale state, and deploy keeps 409-ing. → Added `--org` to the pull/deploy examples + a "`--org` must match across deploy and pull" note (and the `--solution <id>` escape hatch). VERIFIED against `pull_cmd`'s `_resolve_target_install(slug, scope, deployer_org_id)`.
2. **entities.md `.bifrost/` is export-only** (lines 5 + 315) flatly contradicted solutions.md's update path. → Scoped both to the global `_repo` workspace with an explicit Solution-workspace carve-out pointing to solutions.md.
3. **solutions.md scaffold file tree** listed `main.tsx`/`App.tsx` at the app root; they're under `apps/<app>/src/`. → Corrected to show config-at-root, source-under-`src/`. VERIFIED against the scaffold's file-writing dict.

Plus the **capture scope-rule correction** (from the Jack exchange, committed separately `…e7bbf2f`-prior): capture **re-stamps** a different-scope entity to the install's scope (global→org migration), only cross-tenant is refused; the candidate-list-vs-capture-by-id wrinkle is documented. (Earlier A3 over-generalized "global isn't capturable".)

Claims lint 0, mirror synced, verified_at_sha bumped. **Platform candidate-vs-action note (for the platform side, NOT a skill bug):** `/capture/candidates` hides global entities from an org install, but `capture()` accepts+re-stamps them by id — the list under-reports what the action allows.

### A3 — first VALID run (read the worktree skill directly). Platform fix re-confirmed; 4 doc fixes applied.

Followed `.claude/skills/bifrost-build/SKILL.md` → `references/solutions.md`. Round-trip + 409 guard + read-only invariant all ✓ again. Browser blocked by Chrome localhost permission (ENVIRONMENT, not skill) — verified the app via curl + grepping the deployed bundle for Tailwind classes. Four legitimate doc fixes, all applied this commit (streak → 0):
1. **solutions.md Path A** said author "in a scratch or **global** context" — wrong: global (org-null) entities are NOT capture candidates for an org-scoped install. → Rewrote to require same-org authoring (`--organization <uuid>`), with the candidate-pool rule spelled out.
2. **solutions.md "manifest is machine-managed"** misled — direct `.bifrost/*.yaml` **content** edits ARE the update path for an already-owned entity (live update 409s). → Added an "Updating an already-owned entity" section: edit the field + redeploy; never hand-add/remove UUID keys.
3. **solutions.md scaffold step** didn't warn the scaffold emits inline styles. → Added a callout: Tailwind is wired; replace the inline styles with classes.
4. **entities.md `solution start`** showed `start my-app --org <ref>` without noting `my-app` is a positional app-slug. → Changed to `start [APP_SLUG]` with a clarifying comment.

Linter trap handled: the mode-conditional ban correctly flags any live-mutation verb (`bifrost forms create`, `bifrost agents update`) in a solution-context doc, so the fixes describe those as forbidden/`_repo`-side in prose rather than as literal commands. Claims lint 0, appendices fresh, mirror synced.

### A2 — platform fix VALIDATED LIVE, but tested the wrong skill copy

A2 invoked the `Skill` tool for `bifrost:build`, which resolves to the **stale installed plugin** (`~/.claude/remote/plugins/*/skills/bifrostbuild/` — old flat structure: `app-patterns.md`, no dispatcher), NOT this worktree's rebuilt dispatcher skill. So its "misleading moments" (no Solutions section, llms.txt empty, etc.) describe the OLD skill and are moot.

**What A2 DID prove (the valuable part) — the platform fix works end-to-end against the live debug stack:**
- table + form + agent + config **all captured → pulled → deployed and SURVIVED** (the exact bug A1 found is fixed).
- deploy **409-blocked** post-capture/pre-pull naming all 4 entities ("Run `bifrost solution pull`, then deploy"); `bifrost solution pull` cleared the queue and unblocked the deploy.
- update round-tripped; read-only invariant (409 on live solution-managed `tables/forms/agents update`) holds.
- One platform note: a fresh debug stack needed the `20260615_pending_captures` migration applied (restart init+api) — expected for a new migration on a live stack (memory `project_debug_stack_migration_apply`), not a code bug.

**Distribution gap surfaced:** the rebuilt skill is correct in the worktree (`.claude/skills/bifrost-build/` + `plugins/bifrost/skills/bifrost-build/`) but is NOT what the `Skill` tool loads — that's the installed plugin, still stale. Validation must point the subagent at the worktree skill FILES directly (read `.claude/skills/bifrost-build/SKILL.md`), which is what A3+ do. Installing the rebuilt plugin is a release-flow step, not part of this branch's diff.

### A1 — pivotal finding (verified at code level)

**The entities-into-a-solution mechanism is broken at the PLATFORM level, not the skill level.**
- `bifrost solution capture` is a pure server call (`POST /api/solutions/{id}/capture`, `commands/solution.py:1581+`) — it sets `solution_id`/`is_solution_managed` on the DB record but does **NOT** write `.bifrost/{tables,forms,agents}.yaml`.
- `bifrost solution deploy` is manifest-driven full-replace. So the next deploy **deletes** any captured table/form/agent that isn't in the on-disk manifest. Reproduced twice with a table; confirmed in source.
- **Workflows are the only entity that round-trips** — and only because you manually add a UUID-keyed entry to `.bifrost/workflows.yaml` (deploy does not auto-scan `functions/`).

**Consequence:** the skill cannot be edited into "consistently produces a good solution *with entities*" because no working capture→deploy round-trip exists for table/form/agent/config. This is a release-blocker-class platform gap, escalated to the user (scope decision).

**Genuine skill-doc findings (fixable independent of the bug):** capture requires entities be in the SAME org as the install (global `organization_id: null` not capturable) — undocumented; `solution start [APP_SLUG]` positional needed with multiple apps; capture-by-UUID more reliable than by-name; adding a 2nd workflow needs a manual `.bifrost/workflows.yaml` UUID entry (contradicts the "don't edit .bifrost" guidance — needs reconciling).

**Status: Track A BLOCKED pending user decision on the platform bug.**

---

## Track B — Repo/global build (live mutation correct)

Goal: author workflow `.py` + entities via live CLI create/update → execute →
iterate. Cover SDK surface Track A didn't reach.

| Run | Result | UI/exec | Entities | Update | Execute | Invariant | Misleading moments → fix | Streak |
|-----|--------|---------|----------|--------|---------|-----------|--------------------------|--------|
| _pending_ | | | | | | | | |
