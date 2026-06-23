# Handoff — Execute the Named Policy Rules + Solution-Scoped Files plans

Paste the block below into a fresh Claude Code session (run from the worktree
`/home/jack/GitHub/bifrost/.claude/worktrees/files-sdk-policies`). It is self-contained.

---

## COPY FROM HERE

You are taking over execution of two fully-designed, twice-Codex-reviewed implementation plans in the Bifrost worktree at `/home/jack/GitHub/bifrost/.claude/worktrees/files-sdk-policies` (branch `codex/files-sdk-policies`). All design is done. Your job is to **execute both plans to completion via subagent-driven development**, then have Codex do a final whole-branch review, then stop and report.

### Invoke this skill first
Use `superpowers:subagent-driven-development`. Execute continuously — do NOT pause between tasks for check-ins. Only stop for: a genuine BLOCKED status you can't resolve, a design ambiguity that prevents progress, or all tasks complete.

### What you're building (one paragraph)
Reusable **named policy rules**: file & table policies can contain `{"$ref": "name"}` entries that resolve (live, hard-fail-on-missing) to a shared org/global/**solution**-scoped `PolicyRule` entity, with rename-cascade, delete-guard, two read-only `admin_bypass` built-ins (file+table domains), audit, and REST/CLI/MCP/manifest/UI surfaces. Then **solution-scoped files**: files belonging to an installed Solution, isolated under `solutions/{install_id}/path`, resolved own-solution→org→global, exported/imported with the bundle (encrypted sidecars), **orphaned-to-org on uninstall** (never deleted), surfaced on the Solution Contents list, exercised by a full real-solution e2e.

### Execute these two plans IN ORDER (named-rules first — solution-files depends on it)
1. `docs/superpowers/plans/2026-06-22-named-policy-rules.md` (13 tasks)
2. `docs/superpowers/plans/2026-06-22-solution-scoped-files.md` (14 tasks)

Total **27 sequential tasks**. They are strictly ordered (each builds on prior types/migrations) — dispatch one implementer subagent per task, review (spec + quality) after each, fix-loop on Critical/Important, then the next. Do NOT use the Workflow tool — there is no parallelism here; subagent-per-task is correct.

### Supporting specs (read for context; the plans cite them)
- `docs/superpowers/specs/2026-06-22-named-policy-rules-design.md` — incl. a "Codex pre-implementation review — corrections" section.
- `docs/superpowers/specs/2026-06-22-solutions-files-open-decisions.md` — the decided D/O items (note: O3 = orphan-not-sweep, D3 = encrypted sidecar, both REVISED).

### Binding global constraints (both plans carry their own — these are the cross-cutting ones)
- **Worktree only.** Build on the committed tip; never touch the primary checkout.
- **Resolver choke point.** Every policy load *for evaluation* routes through the domain's resolving loader (`load_resolved_table_policies` / the file `is_allowed` path). Refs resolve BEFORE `preresolve_for_policies` / `compile_read_filter` / `evaluate_*`. A raw `TablePolicies.model_validate`/`FilePolicies.model_validate` on an eval path is a bug.
- **Core writes for solution-managed rows** (`solution_id`-bearing PolicyRule / FileMetadata / FilePolicy). The `before_flush` guard catches ORM update/delete but **NOT inserts** — tests assert the Core path directly, don't rely on the guard tripping.
- **Hard-fail on missing/mismatched `$ref`** (raise; enforcement sites catch→deny+log, save/import/deploy→structured 422 / fail-closed).
- **No mirror-delete of files** (update/install). **Uninstall orphans files to the org**, never deletes.
- **Three parallel surfaces** (REST/CLI/MCP) stay in sync; run DTO-parity + contract-version tripwire + `python api/scripts/skill-truth/generate.py` after DTO/CLI/MCP changes.
- **Tests via `./test.sh`** (Dockerized). JUnit at `/tmp/bifrost-<project>/test-results.xml`. Boot the test stack once: `./test.sh stack up`.

### Pre-flight before Task 1 (do these in order)
1. `git -C /home/jack/GitHub/bifrost/.claude/worktrees/files-sdk-policies log --oneline -5` — confirm HEAD is `0fb0d3974` (or later) and tree is clean. The Files-SDK foundation + all design docs are already committed.
2. **Ledger:** there is NO `progress.md` in `.superpowers/sdd/` yet — this is a fresh execution, start at Task 1. (Ignore the stale `task-5/7/9/10-report.md` files there — they're from a *previous, unrelated* SDD run, not this work. Create a new `progress.md` and append one line per completed task.)
3. Boot the test stack: `cd /home/jack/GitHub/bifrost/.claude/worktrees/files-sdk-policies && ./test.sh stack up`. Boot the debug stack only if a task needs the browser/live API: `./debug.sh status || ./debug.sh up`.
4. Do the skill's **pre-flight plan scan** for conflicts; batch any to the user once. The plans were heavily reviewed, so expect it clean.

### Decisions already locked (do NOT re-litigate — implement as written)
- PolicyRule: individual-rule refs (not bundles); live resolution; org→global→**solution** cascade; explicit `domain` column ('file'|'table'); two read-only `admin_bypass` built-ins; hard-fail on missing ref; server-side Core-update rename cascade; no migration of existing inline rows; admin-gated writes.
- Solution files: freeform `solutions` location, `scope=install_id`; presign scope server-resolved (3 failure-mode tests); policy cascade own-solution→org→global; content does NOT cascade (only policy does); bundle file bytes **encrypted** into `secrets.enc` (full-mode+password), index per file; uninstall **orphans** (re-stamp `solution_id→NULL` before the Solution delete + S3 move as a non-cascading background job querying `origin_solution_id`); Files row on Solution Contents needs backend (`SolutionEntities.files`) + a scope-param-aware `FilesExplorer`.

### Model selection for subagents (per the SDD skill)
- Implementers on tasks whose plan text contains the full code → cheapest tier (transcription+tests). Multi-file/integration tasks → standard. The final whole-branch review → most capable.
- Always specify the model explicitly when dispatching.

### When all 27 tasks are complete
1. Run the final whole-branch review via `superpowers:requesting-code-review` (most capable model), package with `scripts/review-package "$(git merge-base main HEAD)" HEAD`.
2. Then run an INDEPENDENT Codex final review (the user explicitly wants this): `codex review --base main` from the worktree (it may run 1-4 min; background it and read the output file). Triage findings via `superpowers:receiving-code-review` — verify each against the code, fix confirmed Critical/Important with ONE fix subagent carrying the full list, discard noise.
3. Verification sweep: `cd api && pyright && ruff check .`; `cd client && npm run generate:types && npm run tsc && npm run lint`; `./test.sh all`; `./test.sh client unit`. Confirm the real-solution e2e (`test_solution_files_e2e.py`) passes.
4. Then use `superpowers:finishing-a-development-branch` to present integration options. **Do NOT merge without explicit user consent** — offer, don't execute.

### Reporting
Report only: blockers needing the user, the final review outcome, and completion. Keep a `progress.md` ledger line per task. Don't narrate every task to the user.

## COPY TO HERE

---

## For the human (not part of the paste)

- **Why no Workflow tool:** the 27 tasks are strictly sequential (ORM → contracts → repo → resolver → loader → service → router → CLI/MCP/manifest → UI → e2e). A workflow's value is parallel fan-out; there's none here. Subagent-per-task with review gates is the right model and gives per-task quality control.
- **Design provenance:** spec → 2× Codex pre-implementation reviews (17 findings folded round 1, 8 round 2 — all verified against real code). The plans embed every correction inline with `Codex R2/Cn` markers.
- **The seam (C1):** named-rules now lands `PolicyRule.solution_id` + a solution-aware resolver signature so the solution-files plan can pass a real `solution_id`. If you ever split the two plans across people, named-rules MUST finish first.
- **Foundation commit:** the 77-file Files-SDK base is committed at `bd5c090fd` ("checkpoint: Files Web SDK + file policies foundation").
