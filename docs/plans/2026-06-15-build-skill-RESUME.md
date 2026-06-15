# Build-Skill Rebuild + Capture Round-Trip — RESUME (fresh-session handoff)

**Date:** 2026-06-15
**Branch:** `solutions/connection-references` · worktree `solutions-success-criteria` · part of draft **PR #347** (do NOT push/un-draft/merge without Jack's explicit say-so).
**Read first:** this file, then the two specs + the plan + the validation log linked below.

---

## The arc in one paragraph

We rebuilt the `bifrost:build` skill (Tasks 0–10, all DONE + reviewed + committed). The empirical validation loop (Task 11) then discovered that the **Solution capture→deploy round-trip was broken at the platform level** — fixed and BUILT (Tasks 1–7 of `2026-06-15-solution-capture-roundtrip.md`, committed, all green). The validation loop then ran **Track A runs A2–A6**, each confirming the fix works live and producing skill-doc fixes (all applied; see `2026-06-15-build-skill-validation-log.md`). **The loop is now PAUSED** (run A7 stopped) for a Jack-requested cross-cutting CLI change: **the unified `--org` standard** (spec + plan written, see below). Build that, THEN resume the validation loop against the corrected docs.

## CURRENT PRIORITY — unified `--org` CLI standard (spec+plan DONE, build NOT started)

**Spec:** `docs/superpowers/specs/2026-06-15-cli-org-standard.md` · **Plan:** `docs/superpowers/plans/2026-06-15-cli-org-standard.md` (11 TDD tasks). Locked decisions: one `--org <id|name|none|global>` + `--global` everywhere; omit = caller's HOME org; `--organization`/`--scope` are permanent synonyms (additive CLI); **remove `scope` from `bifrost.solution.yaml` + `solution init`** (install kind = deploy-time choice; server already derives it from `organization_id`). `solution install` omit→home. Contract bump 3→4 (descriptor/`SolutionCreate` change is breaking). **Start at Task 1.** This SUPERSEDES the `--org`/scope sections in `references/solutions.md` written during A4–A6 — Task 10 rewrites them.

## THEN — resume the validation loop (Track A to 3-clean, then Track B)

Track A streak is at 0 (A6 was clean-scorecard with 1 ordering fix applied). After the `--org` build lands + docs are rewritten, resume per the validation log: fresh Sonnet runs reading the WORKTREE skill files directly (NOT the `Skill` tool — installed plugin is stale), to 3 consecutive clean runs, then Track B (repo/global). Dispatch template + done-bar in `2026-06-15-build-skill-validation-log.md`.

## DONE — capture round-trip fix (committed, not pushed) — 2026-06-15

`pending_captures` queue table closes the round-trip. Tasks 1–7 of `docs/superpowers/plans/2026-06-15-solution-capture-roundtrip.md` all complete + green:
- **T1** `pending_captures` ORM + migration `20260615_pending_captures` (FK→solutions, unique on solution+type+id).
- **T2** capture enqueues one row per table/form/agent/config/event/claim (Core upsert, idempotent); router threads `captured_by`.
- **T3** deploy guard: pure `unpulled_blockers` helper + wired into `deploy_solution` → **409** when a pending entity is absent from the manifest; `force=True` bypasses; absent + no pending row = genuine delete (unchanged).
- **T4** `POST /{id}/pull/ack` server-authoritative clear; `PullAck*` DTOs in contracts.
- **T5** `bifrost solution pull` CLI — `POST /export?mode=shareable` → unzip only `.bifrost/*.yaml` → ack/clear. Agent-runnable.
- **T6** e2e `test_capture_roundtrip.py`: capture→409(+entities survive)→export/ack→deploy→genuine-delete. PASSES on a fresh clone.
- **T7** `solutions.md` rewritten with the real flow; appendices regenerated (new `solution pull` cmd + `pull/ack`); mirror synced; sources.yaml bumped.

Verified: pyright 0, ruff clean, 112 solutions/contract/DTO/MCP unit tests pass, generate.py --check fresh, claims lint 0, mirror in sync. Commits `…772ac58b` and earlier (see git log).

---

## DONE — build-skill rebuild (committed, not pushed)

Plan: `docs/superpowers/plans/2026-06-15-build-skill-rebuild.md`. All 11 build tasks complete, each spec- + quality-reviewed by subagents:

- **Tasks 0–1** — `api/scripts/skill-truth/generate.py` deterministic generators → `generated/{cli-reference,python-sdk-signatures,openapi-digest,web-sdk-surface}.md`; `dump-app-sdk-surface.mjs` (dependency-free). Freshness test `test_skill_appendix_fresh.py`.
- **Task 2** — `api/scripts/skill-truth/lint_claims.py` claims linter with **mode-conditional bans** (live entity mutation banned in solution-context, allowed in repo-context) + `test_skill_cli_claims.py`. This is the CI encoding of the read-only correctness gap.
- **Task 3** — `scripts/sync-codex-skills.sh` reconciles BOTH Codex mirror roots (`plugins/bifrost/skills/` public + `.codex/skills/` maintainer); normalized `skills/migrate` real-dir → symlink (also fixed a latent `bifrost skill update` allowlist bug). Guard test `test_codex_mirror_sync.py` (host-run, skips in-container).
- **Task 4** — CI Gate 3 (Codex mirror diff) added as a step in the existing `lint` job (Gates 1–2 already ride `test-unit`). NOTE: ci.yml `paths-ignore` skips skill-only PRs (documented tradeoff).
- **Task 5** — `SKILL.md` rewritten as a thin **dispatcher** (detect `bifrost.solution.yaml` → `references/solutions.md` vs `references/repo.md`); preserves access-tuple + MCP-naming.
- **Task 6** — `references/tables.md` Python↔Web side-by-side (the named pain point); signatures verified verbatim against `generated/`.
- **Task 7** — `references/solutions.md` (LIGHT, → `bifrost:migrate`) + `references/repo.md` (v1/global flow, live mutation correct here).
- **Task 8** — 7 shared refs (`web-sdk-v2, workflows-python, python-sdk, entities, apps, rest-api, mcp-mode`); moved `import-patterns.md`+`platform-api.md` under `references/`; killed `docs/llm.txt` (salvaged into `entities.md`); repointed CLAUDE.md/AGENTS.md. **apps.md got a real v1/v2 correctness fix** (it was teaching v1 `from "bifrost"` imports for Button/lazy/Suspense/useUser/etc. as if v2 — corrected to the real v2 sources; sections on `useUser`/`RequireRole`/`useAppState` rewritten as "v1-only, here's the v2 way").
- **Task 9** — `references/sources.yaml` freshness manifest + `test_skill_reference_freshness.py` (SOFT staleness warn, mirrors the bifrost-documentation manifest+diff pattern).
- **Task 10** — distribution verified (`bifrost skill update` round-trips nested `references/`+`generated/`; symlink + both Codex mirrors carry nested content); plugin version bumped to **0.9.2-dev.587** (all 3 manifests).

All reference docs lint 0; appendix + claims + mirror + freshness tests green; mirrors in sync.

---

## NEXT — fix the capture round-trip (designed, NOT built)

**Spec (APPROVED in design, awaiting Jack's spec review):** `docs/superpowers/specs/2026-06-15-solution-capture-roundtrip-design.md`.
**Plan (ready to execute):** `docs/superpowers/plans/2026-06-15-solution-capture-roundtrip.md` — 7 bite-sized TDD tasks. **A new session can start at Task 1 directly** (subagent-driven, same cadence as the build-skill rebuild). Key shortcut baked in: `bifrost solution pull` reuses the EXISTING `POST /export` endpoint (which already live-rebuilds a `.bifrost/`-complete bundle), so the only new server code is the queue table + enqueue + deploy guard + a clear-queue endpoint.

**The fix in brief — a `pending_captures` queue table:**
1. New `pending_captures` table (the ONLY schema change — no columns on the entity tables). Row per captured-but-unpulled entity: `(solution_id, entity_type, entity_id, captured_at, captured_by)`, unique on `(solution_id, entity_type, entity_id)`.
2. `POST /capture` (UI + CLI) inserts a queue row per captured entity.
3. New `bifrost solution pull` CLI command: regenerates **only** `.bifrost/*.yaml` from server state (reusing `manifest_generator.py` serializers), never touches `apps/`/`functions/`; server clears the materialized queue rows. **Agent-runnable** (low blast radius).
4. Deploy guard (server, before reconcile): **409 BLOCK** if any `pending_captures` row's entity is absent from the incoming manifest ("run pull first"). An absent entity with NO queue row = **genuine delete** → deleted as today. This is the safe distinction — deletion only touches entities source has demonstrably seen.

**Key decisions already made (don't re-litigate):**
- Queue TABLE, not a per-entity flag (Jack's call — more scalable, no migration on the guarded entity tables).
- Deploy **blocks, never silently deletes** on an un-pulled capture.
- `pull` writes only `.bifrost/`, leaves dev source untouched; agent may run it.
- Capture stays in BOTH UI and CLI; we do NOT auto-pull inside capture (uniform UI/CLI boundary).

**Watch-outs for the build (in the spec §3.5/§5):** config capture has a `solution_id`/`solution_config_schema`/`origin_solution_id` quirk — verify it actually enqueues + round-trips or scope configs explicitly. Dangling queue rows (entity hard-deleted) must be ignored, not block. Solution-managed writes MUST use Core statements + install the read-only guard in tests (memory `project_solution_managed_guard_deploy_core`).

**To start:** this spec is ready for `writing-plans`. Build it TDD per the build-skill rebuild cadence (subagent-driven, spec+quality review per task). Restart the test-stack API container after deploy-path changes.

---

## THEN — Sonnet validation loop (now Tasks 8–9 of the SAME plan)

**The validation loop is folded into the capture-roundtrip plan as Tasks 8–9** — so the one plan file (`2026-06-15-solution-capture-roundtrip.md`) runs end-to-end: fix the platform bug (Tasks 1–7) → Track A loop (Task 8) → Track B loop + closeout (Task 9). No separate doc to stitch in.

- **Task 8 (Track A, solution):** fresh Sonnet from scratch, capture→pull→deploy (now working), to a 3-consecutive-clean streak. Applies the queued A1 skill-doc findings.
- **Task 9 (Track B, repo/global) + closeout:** live entity mutation flow to 3 clean; union of A+B covers the 71 Python methods + 22 web exports; full pre-completion verification + all skill-accuracy gates green.

Log: `docs/plans/2026-06-15-build-skill-validation-log.md` (run A1 recorded, was blocked on the now-fixed bug). Done bar both tracks: **3 consecutive clean runs, no skill-doc edits between** (a fix resets the streak). This satisfies Tasks 11–12 of the original build-skill rebuild plan.

---

## Environment / gotchas (carry into the new session)

- **Debug stack:** port mode, `http://localhost:37791` (`dev@gobifrost.com`/`password`). Re-check `./debug.sh status`; Chrome needs port mode (netbird can't drive Vite). Boot with `BIFROST_FORCE_PORT=1 ./debug.sh up` if down.
- **Test-runner quirk:** `./test.sh` has an api-container-exit flake in this worktree. Working pattern used all session:
  `docker compose -p bifrost-test-75bc0d9c -f docker-compose.test.yml --profile test run --rm --no-deps test-runner pytest tests/unit/<file>.py -v`
- **In-container paths:** `/app` = `api/`; repo root mounted such that `.claude/skills` = `/.claude/skills`. `api/scripts/`, `.claude/`, `.codex/`, `plugins/` are NOT all mounted in the test-runner — tooling/scripts that need them run on the HOST (that's why the mirror + freshness tests skip-in-container).
- **`bifrost.*`/`src.*` imports** only resolve in-container (or `api/` with PYTHONPATH). `.claude/skills` is READ-ONLY in the container — regenerate appendices by running the generator in-container to stdout, write host-side.
- **After any `references/*.md` or SKILL.md edit:** re-run `./scripts/sync-codex-skills.sh` (or CI Gate 3 fails) and re-lint with `lint_claims.py` (0 findings required).
- **Claims-linter placeholder trap:** `` `bifrost <entity> create` `` in backticks trips "unknown command" — write entity placeholders as plain prose or use a concrete entity (`bifrost forms create`).

## Commits this session (newest first, all on the branch)
- `aebaa81d` validate: Track A run 1 + blocked-on-platform-bug log
- `fcad8af2` chore(plugin): bump version 0.9.2-dev.587
- `69e0e4b8` feat: reference-freshness manifest
- `a14a0224`/`4af03ca2` feat: shared refs + kill llm.txt (+ apps.md v1/v2 fix)
- `a9c58aeb` feat: solutions.md + repo.md
- `0b54b76c` feat: tables.md · `2955c1b8` feat: hub SKILL.md dispatcher
- `b05b132c` ci: Gate 3 · `8596e1c8` feat: Codex sync + migrate symlink
- `930864dc` feat: claims linter · `2deb325a` feat: sdk/openapi generators · `93b917dc` feat: cli generator
- `da3dde3d` plan · `46509f3c`/`ea12485e` spec (build-skill rebuild)
- (capture round-trip spec `2026-06-15-solution-capture-roundtrip-design.md` committed next)
