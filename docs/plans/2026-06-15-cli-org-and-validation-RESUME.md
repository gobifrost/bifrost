# RESUME — `--org` standard (DONE) + build-skill validation loop (Track A done, Track B at 1/3) + Solutions UX review (queued)

> **▶ OVERNIGHT RUN: the ordered goal-line is now `docs/plans/2026-06-15-OVERNIGHT-RUNBOOK.md`.**
> That file is the execution order (Phase 1 validation → 2 inbox UX → 3 SolutionDetail redesign →
> 4 GitHub-story UX review), with hard EXIT criteria and a decide-and-document policy. This RESUME
> doc is the detailed state behind Phases 1 + 4; the runbook is what to actually work down.

**Date:** 2026-06-15
**Branch:** `solutions/connection-references` · worktree `solutions-success-criteria` · part of draft **PR #347** — do NOT push / un-draft / merge without Jack's explicit say-so. **Nothing has been pushed all session.**
**Context note:** the prior session ended near 1M tokens. This file + the validation log are the handoff.
**Read first:** this file → `docs/plans/2026-06-15-build-skill-validation-log.md` (the per-run validation log) → `docs/superpowers/specs/2026-06-15-cli-org-standard.md` + `docs/superpowers/plans/2026-06-15-cli-org-standard.md` (the `--org` work, now complete).

---

## 1. DONE — Unified `--org` standard (workstream 3, Tasks 3–11) ✅

One org-targeting standard across all WRITE CLI commands. **All committed, green, NOT pushed.**

- `--org <id|name|none|global>` + `--global`; omit = caller's HOME org; `--organization`/`--scope` are permanent synonyms. The 3 wire states (in `api/bifrost/org_target.py`): HOME = omit `organization_id` (server uses caller org); GLOBAL = explicit `null`; ORG = the uuid.
- **Read commands (`list`/`get`) do NOT take `--org`/`--global`** — only write verbs (create/update/set/register) carry `org_option`. (This was a doc-precision fix found in Track-B BW1.)
- Server derives install/entity kind from `organization_id` (NULL == global). **Descriptor `scope` REMOVED** (`bifrost.solution.yaml` + `solution init` no longer carry it). `CONTRACT_VERSION` bumped 3→4 in both `api/shared/contract_version.py` + `api/bifrost/contract_version.py`.
- Server endpoints made HOME-default: `create_form`/`create_agent`(admin)/`create_source`/`register_workflow`/`create_solution`/`install_from_repo` all default an OMITTED `organization_id` to the caller's org (mirroring `set_config`/`create_table` which already did). `SolutionRepoPreviewRequest` gained `organization_id`.
- Commits (oldest→newest): `28d5912d` (resolver) `a705f8be` (claims) `b0b486a3` (configs+workflows) `49e6d68e` (tables/forms/agents/events) `743fb578` (solution deploy/pull/start/install) `f54a78ab` (drop descriptor scope + contract bump) `7eaf918a` (e2e) `d7657ff5` (skill docs) `cdb644b4` (ruff).
- Verified: pyright 0, ruff clean, 4640 unit pass (minus known-env + DB-pollution flakes), 5 e2e proving the 3-state semantics on a fresh DB.

## 2. DONE — SDK-example CI gate ✅ (Jack's "codify it like a release gate")

`api/scripts/skill-truth/lint_examples.py` + `api/tests/unit/test_skill_examples.py` (commit `3b0c4162`). Introspects the live SDK and flags, in reference code blocks: subscript-on-model (`doc["id"]`), nonexistent SDK method, `@workflow` with a `ctx` param, v2 SDK symbol from an internal `@/lib/app-sdk` path. `test_all_reference_examples_are_clean` is the gate; rides `test-unit`. **Example drift now fails CI, not a validation run.**

## 3. DONE — Generator: DTO-required → CLI-required ✅ (commit `a571040e`) — but see OPEN QUESTION

`build_cli_flags` (`api/bifrost/dto_flags.py`) now sets `required=True` when a DTO field has no default (`field.is_required()`). Create cmds get real required flags (FormCreate: name+form_schema; AgentCreate: name+system_prompt; TableCreate: name; ConfigCreate: key+value); Update cmds force none. `cli-reference.md`/`--help` show `[required]` (10→28). No contract bump. 2 lock-in tests in `test_dto_flags.py`.

> **⚠ OPEN QUESTION (settle FIRST next session — may invalidate part of `a571040e`'s doc note):** Track-B BW-batch-3 agent 2 claimed `forms create --name X --workflow <uuid>` (no `--form-schema`) gets **no** CLI "Missing option" and the server 422s instead, and that `--help` doesn't show `[required]`. This **contradicts** my verification (`is_required()`=True; the freshly-downloaded CLI's `forms create --help` showed `--form-schema [required]`). My own re-check was inconclusive (the scratch venv had **lost its login**, so it errored on auth before Click's required-validation fired). **REPRODUCE CLEANLY:** fresh `pip install http://localhost:37791/api/cli/download`, `bifrost login`, then `bifrost forms create --name x --workflow <a-real-registered-wf-uuid>` (omit `--form-schema`). If Click errors "Missing option '--form-schema'" → `a571040e` is correct, keep the entities.md note. If the server 422s instead → the generator's required flag isn't taking effect for forms (investigate why; maybe `forms create` wraps the DTO flags differently, or `--form-schema` is excluded) and SOFTEN the entities.md note to "server-validated, not CLI-validated." **Do not touch docs/code on this until reproduced.**

## 4. Build-skill validation loop — Track A DONE ✅, Track B at 1/3

Done-bar (Jack-confirmed): **3 concurrent Sonnet agents CLEAN against one doc state = "3 consecutive, no edits between."** A NEEDS-FIX resets the streak; fix, then a fresh batch of 3.

**Track A (solution build): DONE — W-batch 3 = 3/3 CLEAN.** Convergence W1 0/3 → W2 2/3 → W3 3/3. All structural flows green (Tailwind, manifest-entry workflow registration, capture→pull→deploy, read-only 409, `--org`, survival, execution).

**Track B (repo/global live-mutation): at 1/3** (B1 scout + BW1 0/3 + BW2 0/3 + BW3 1/3). Every structural flow green every agent (register/execute, live create, live update NO 409, full `--org` write coverage, push-then-register, `bifrost run`, SDK attr-access). The agents-update "stale response" claim was **empirically DEBUNKED** (server reloads + returns fresh state; reproduced by driving the CLI). **2 open Track-B doc items (NOT yet applied — context limit):**
  1. **Select-field `options` shape undocumented** — schema shows `{fields:[...]}` but not a select's `options`; a builder writes `["low","high"]` → 422 `Input should be a valid dictionary`; correct is `[{value,label}]`. Add a select-field example to `references/entities.md` (forms section) or `tables.md`.
  2. **The OPEN `--form-schema` question (§3 above)** — settle empirically, then either keep or soften the entities.md note.

### How to run a validation batch (the parallel workflow)
- Track-B script: `…/workflows/scripts/build-skill-validation-batch-trackb-wf_88e2424e-d12.js` (re-invoke with `Workflow({scriptPath})`). Track-A script: `…build-skill-validation-batch-wf_d057a9c2-6ee.js`.
- 3 Sonnet agents (`model:'sonnet'`), each a full build in `/tmp/bifrost-valbw-{i}` with unique slug/name suffixes (shared stack). They READ the worktree skill `.claude/skills/bifrost-build/SKILL.md` directly (NOT the `Skill` tool — installed plugin is STALE), return a structured verdict.
- **CRITICAL discipline:** an agent claim that contradicts code is NOT auto-true — REPRODUCE against the running system before fixing (the agents-update + maybe form-schema claims both show why). Two agents agreeing ≠ true.

### Per-fix loop chores (after ANY `references/*.md` edit)
1. Lint: in-container `lint_claims.py` + `lint_examples.py` over the changed files → **0 findings**. (Trap: `` `bifrost <banned>` `` or `--org` on a read-cmd as INLINE code trips the linter — write as prose. `push`/`watch`/`sync`/`pull`/`git`/`export`/`import` are GLOBAL_BANNED literals.)
2. If `cli-reference.md`/appendices stale: regenerate in-container → stdout → write host-side (`.claude/skills` is read-only in the container).
3. `./scripts/sync-codex-skills.sh` (CI Gate 3), bump `verified_at_sha` in `references/sources.yaml` for touched files.
4. Run `tests/unit/test_skill_{examples,cli_claims,appendix_fresh}.py` → green.
5. Commit per fix; message via `-F /tmp/msg.txt` (NOT `-m "...backticks..."` — bash command-substitutes backticked CLI commands → shell noise).

## 5. QUEUED — Solutions "GitHub story" UX review (Jack re-flagged 2026-06-15; never started)

Memory `project_solutions_github_story_review`. Jack asked for an **end-to-end EXPERIENCE review** (not a code audit) of the Solutions install/update/publish/DR story, to run AFTER connection-refs is merged into the **solutions branch (not main)**. The bar: a **fully-kitted Microsoft CSP app** (multiple shared modules, TWO integrations, in-depth setup) installs from scratch WITHOUT the platform being the reason it's hard. Questions: what does installing look like start→finish (does it spark joy)? how do updates / new-version signals work? how do you publish your own repo as a solution? does full-backup (encrypted secrets + table data) round-trip? do the CLI + API support a user's own DR? Deliverable: honest assessment of coherence + friction + what to fix. This is a **drive-it-don't-just-test** exercise (memory `feedback_drive_dont_just_test`). **It is NOT the same as the build-skill validation loop** — that's skill-doc accuracy; this is product UX. Jack noticed it "never happened" — keep it visible.

---

## Environment / gotchas (carry forward)

- **Debug stack:** `http://localhost:37791`, PORT mode (Chrome-drivable), `dev@gobifrost.com`/`password`. Serves THIS worktree's code; `/api/cli/download` serves the fresh CLI (confirmed it has the `required=` change). Re-check `./debug.sh status`; boot with `BIFROST_FORCE_PORT=1 ./debug.sh up`.
- **Test stack:** `bifrost-test-75bc0d9c`. Run tests in-container (the `./test.sh` api-exit flake): `docker compose -p bifrost-test-75bc0d9c -f docker-compose.test.yml --profile test run --rm --no-deps test-runner pytest <path> -v`.
- **DB-pollution trap (NEW lesson):** do NOT run the FULL unit suite against the test DB while validation agents are creating entities on the shared stack — duplicate names (`orphan`, etc.) cause false `UniqueViolationError` failures. Targeted suites are the trustworthy signal; for the full suite, reset the DB first: stop api/workers/pgbouncer → terminate conns → `DROP DATABASE bifrost_test; CREATE DATABASE bifrost_test TEMPLATE bifrost_test_template;` → FLUSHALL redis → start. Known-pre-existing env failures (NOT yours): `test_sdk_package`, `test_solution_app_build`, `test_platform_api_docs` (in-container fs/node/mount).
- **Chrome MCP can't drive localhost** on this host (site-permission denied every run) — validation agents fall back to curl + `bifrost workflows execute`. Not a skill/platform bug.
- **Tracked platform notes (for Jack, NOT skill bugs):** deploy summary line omits tables/forms; `scaffold-app` prints `bifrost deploy` not `bifrost solution deploy`; `tables list`/`apps list --json` are wrapped dicts vs bare arrays; no `solution add-workflow` command (new workflows need a hand-added `.bifrost/workflows.yaml` entry); `solution start` raw aiohttp OSError on port N or N+1 collision; `workflows register` 500s (should 409) on duplicate-name; `solution pull` overwrites `.bifrost/apps.yaml` repo_path to the slug.

## Start here (next session)
1. **Settle the OPEN `--form-schema` question (§3)** — reproduce with a fresh logged-in CLI; keep or soften the note accordingly.
2. **Apply the select-field `options` doc fix (§4 item 1).**
3. Run a fresh Track-B batch (script in §4) → drive to 3/3, OR (Jack's call) accept Track B as "validated+hardened" at high confidence and stop.
4. Surface the **Solutions UX review (§5)** to Jack as the next major piece — it's product work, distinct from the skill loop, and he wants it.
