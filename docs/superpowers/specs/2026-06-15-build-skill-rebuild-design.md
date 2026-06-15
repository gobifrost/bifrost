# Spec: `bifrost:build` rebuild — solution/repo dispatcher + empirical Sonnet validation

**Date:** 2026-06-15
**Status:** design, awaiting user review
**Branch:** `solutions/connection-references` · worktree `solutions-success-criteria`
**Supersedes / executes:** `docs/plans/2026-06-09-build-skill-rebuild-plan.md` (the full hub-skill rebuild — WS-17). This spec adopts that plan's architecture and accuracy gates wholesale, and **augments** it with the three refinements decided in the 2026-06-15 brainstorm.

---

## 1. Why

The current `bifrost:build` skill (`.claude/skills/bifrost-build/SKILL.md`, 447 lines) is **pre-Solutions** and teaches a model that is now actively *wrong* in a solution workspace:

- `SKILL.md:58` (and `:261`) teach "mutate entities via `bifrost <entity> create | update | delete`" as the primary mechanism. In a **solution workspace** this is the OPPOSITE of the invariant: solution-managed entities are **deploy-owned / read-only**, and a live `bifrost <entity> create|update` **409s** against the always-on read-only guard (`api/src/services/database.py` `before_flush` + `solutions/guard.py`) that this branch hardened.
- The skill centers on `bifrost watch / sync / push / pull` + `bifrost git *` — legacy `_repo/` drift tooling that does not belong to the Solution paradigm.
- It references `bifrost export / import` (removed) and `bifrost api GET /api/llms.txt` (a doc dependency the rebuild kills).

This is a **correctness gap, not docs polish.** A model following the current skill in a solution workspace produces 409s and confusion.

The 2026-06-09 plan already designed the full fix (hub skill + curated reference files + machine-generated appendices + accuracy gates + Codex mirror). This spec executes that plan **with three augmentations** and adds the decisive proof step: **empirically validate the rebuilt skill by having fresh Sonnet subagents build real artifacts against the debug stack until they consistently succeed.**

## 2. Scope decisions (2026-06-15 brainstorm)

1. **Full 06-09 rebuild** — execute the whole hub-skill plan (reference files, `generated/*` appendices, three accuracy gates, `docs/llm.txt` removal, Codex sync). Not a narrow slice.
2. **Augmentation A — `bifrost.solution.yaml` dispatcher.** The hub `SKILL.md`'s first decision is the **mode split**, not a topic-keyed routing table: detect `bifrost.solution.yaml` (written by `bifrost solution init`, `commands/solution.py:61`) walking up from cwd → route to ONE of two entry docs. This is first because it flips the most load-bearing rule in the skill (read-only-deploy-owned vs. live-mutate).
3. **Augmentation B — `solutions.md` stays LIGHT.** v2 apps are standard React; the worked end-to-end path already lives in the `bifrost:migrate` skill. `solutions.md` is a lean primer that points at `/migrate` rather than restating it.
4. **Augmentation C — two-track validation loop.** The Sonnet validation loop runs for BOTH the solution flow AND the repo/global flow (the repo branch is equally susceptible to teaching something wrong). Same done bar for both.

**Out of scope (deferred, unchanged from 06-09 §5):** the served `/api/llms.txt` route and MCP `get_docs` stay (platform consumers); only the static `docs/llm.txt` file and the skill's "download docs" step are removed.

## 3. Distribution model (verified 2026-06-15)

There is **no project skill.** Everything is the plugin, shared by Claude and Codex from one source:

- **Single source of truth:** `.claude/skills/bifrost-build/` (real files: `SKILL.md`, `references/`, `generated/`).
- **Claude:** shipped via the existing top-level `skills/build → ../.claude/skills/bifrost-build` symlink. The plugin loader resolves the directory symlink transparently. **Verified:** the skill already ships sibling reference files (`platform-api.md` etc.) through this symlink today, and they read fine. Nested `references/` + `generated/` subdirs are just normal paths under the resolved target — no symlink issue.
- **Codex:** `plugins/bifrost/skills/bifrost-build/` is a **generated plain-file mirror** (Codex marketplace packaging of symlinks is unverified, so no symlinks there). `scripts/sync-codex-skills.sh` rsyncs the real files; CI Gate 3 (`diff -r`) fails red on drift. The Codex manifest declares `"skills": "./skills/"` relative to `plugins/bifrost/`.
- **`bifrost skill update` (`api/bifrost/skill.py`):** builds its public allowlist from the `skills/` symlinks, and **recurses into nested subdirs correctly** — verified: `_fetch_skill_files` keys every tarball file by full relpath under `.claude/skills/<skill>/`, and `_write_skill` does `out_path.parent.mkdir(parents=True, exist_ok=True)` before writing. So `references/solutions.md` and `generated/cli-reference.md` round-trip. Layout preserved; no `skill.py` change needed.

## 4. Architecture

One hub skill, file-level subskills (progressive disclosure inside the one plugin skill dir), machine-generated appendices. The 06-09 layout, with the entry-doc split made explicit:

```
.claude/skills/bifrost-build/
  SKILL.md          # HUB ~250 lines max: prereqs → detect bifrost.solution.yaml → route to ONE entry doc.
                    #   Global hard rules + access-tuple section only. No topic content.
  references/
    solutions.md    # ENTRY (solution mode) — LIGHT. init→scaffold-app→start→deploy lifecycle;
                    #   read-only / deploy-is-full-replace invariant stated LOUD; 7-export v2 SDK
                    #   surface; → /migrate for the worked v1→v2 path. Links into shared topic files
                    #   for depth; does NOT restate them.
    repo.md         # ENTRY (global-repo mode) — today's v1/global flow + MCP-only mode;
                    #   watch/sync/git as legacy-only. Links into the same shared topic files.
    tables.md       # SHARED ★ Python↔Web side-by-side (06-09 §3) + scope/solution cascade
    workflows-python.md  # SHARED: decorators, offline `bifrost run`, register/replace/remap, requirements
    web-sdk-v2.md   # SHARED: BifrostProvider, useWorkflow(path::fn), useWorkflowQuery/Mutation,
                    #   useTable/useInfiniteTable, BifrostHeader, scaffold anatomy, tokenless dev
    python-sdk.md   # SHARED: module-by-module prose (signatures live in generated/)
    entities.md     # SHARED: per-entity CLI verbs + semantics (docs/llm.txt salvage lands here)
    apps.md         # SHARED: design + resilience rules (merged from app-patterns.md), v2-first
    rest-api.md     # SHARED: `bifrost api` boundaries, executions, key endpoints
    mcp-mode.md     # SHARED (repo-only concept): MCP-only flow + verified tool names
    import-patterns.md   # kept (v1 reference)
    platform-api.md      # kept (v1/web reference)
  generated/        # machine-written, committed, CI-regenerated (Gate 1)
    cli-reference.md           # full recursive --help dump
    python-sdk-signatures.md   # inspect-derived signatures per module
    web-sdk-surface.md         # index.v2.ts export signatures
    openapi-digest.md          # method/path/operationId/params digest
```

### 4.1 Entry-doc split vs. shared topic files (Augmentation A, reconciled)

The 06-09 plan's primary structure was a topic-keyed routing table (app→web-sdk-v2, table→tables.md, …). We make the **mode** the first decision and the **topic** the second:

- **Mode-specific *behavior*** lives in the two entry docs. The clearest example: entity creation. In `repo.md`, `bifrost <entity> create|update` is the primary, correct mechanism. In `solutions.md`, it 409s — entities are deploy-owned; you author them in the workspace and `deploy`. That difference is not a topic nuance; it is a *correctness* boundary, so it lives at the top of each entry doc.
- **Mode-agnostic *reference*** (a table operation's signature, a decorator's shape, the v2 SDK export list) lives **once** in the shared topic files. Both entry docs link in. Facts live once → one accuracy gate per fact → no drift.
- Shared topic files carry a small contextual note where mode genuinely changes *usage* (e.g. tables scope/solution cascade, entity creation): "in a solution workspace this is deploy-owned — see solutions.md," without duplicating the flow.

**Hub routing (SKILL.md):** prereqs (BIFROST_* env, carried over) → detect `bifrost.solution.yaml` → solution? read `references/solutions.md` : read `references/repo.md`. Within each entry doc, link to the topic files by need (app → web-sdk-v2 + apps; workflow → workflows-python; table → tables.md; exact flag → generated/cli-reference.md; endpoint existence → generated/openapi-digest.md). Global hard rules in the hub: org+access tuple confirmed before scaffolding (keep the existing access-tuple section); never watch/push/sync/git in solution mode.

## 5. Accuracy gates (06-09 §4) + the mode-conditional augmentation

One CI job `skill-accuracy`, three gates:

- **Gate 1 — appendix freshness.** `scripts/skill-truth/generate.py` (+ `client/scripts/dump-app-sdk-surface.mjs`) regenerates `generated/*.md`; CI runs it then `git diff --exit-code`. Deterministic: sorted iteration, no timestamps, normalized widths. Reuses `api/src/services/mcp_server/tools/sdk.py` introspection (`_generate_module_docs`).
- **Gate 2 — claims linter.** `scripts/skill-truth/lint_claims.py` + pytest wrapper `api/tests/unit/test_skill_cli_claims.py`: extract every `bifrost …` invocation from fenced + inline code across `skills/**/*.md` (via symlink targets); validate command path against the real Click tree (`ENTITY_GROUPS` / `solution_group` / the hand-rolled dispatcher list in `cli.py`) and every `--flag` against the DTO-generated Click command. Runs in `./test.sh unit` (no DB) + CI.
  - **Augmentation C-lint — mode-conditional bans.** The 06-09 ban list was global (watch/push/pull/sync/git/export/import — still globally banned). We add a **solution-context ban**: live entity mutation (`bifrost <entity> create|update|delete` for solution-managed entity types) is flagged **when it appears in `solutions.md` or a solution-context fenced block**, and allowed in `repo.md`. The linter classifies a block's mode by its containing file (solutions.md vs repo.md) or an explicit ` ```bash solution ` / ` ```bash repo ` info-string marker. This is the lint encoding of the exact correctness gap §1 describes — it makes the regression impossible to reintroduce silently.
- **Gate 3 — Codex mirror equality.** `scripts/sync-codex-skills.sh` rsyncs `.claude/skills/bifrost-*` → `plugins/bifrost/skills/`; CI `diff -r` fails red on drift.

CI wiring: path-filtered to `skills/**, .claude/skills/**, plugins/bifrost/**, api/bifrost/**, client/src/lib/app-sdk/**, api/src/routers/**`; always-on for release tags.

## 6. Killing `docs/llm.txt` (06-09 §5)

1. Salvage per-entity prose into `references/entities.md`.
2. Delete `docs/llm.txt`; update `CLAUDE.md` + `AGENTS.md` pointers → "change a command → regenerate via `scripts/skill-truth/generate.py`; CI enforces."
3. New `SKILL.md` drops the "Download Platform Docs" step.
4. **Stays:** `/api/llms.txt` route + MCP `get_docs` (platform/MCP-only consumers). File a follow-up issue.

## 7. The Sonnet validation loop (Augmentation C — the centerpiece)

This is how "done" is *defined* for the skill. After the skill is written and Gates 1–3 are green, run a fresh-session validation loop with **two tracks**, both held to the same bar.

**Coverage mandate (not a representative slice).** The loop must drive **every feature of the web SDK and the Python SDK**, because the curated reference files (`tables.md`, `web-sdk-v2.md`, `python-sdk.md`, `workflows-python.md`) are hand-written prose that must be proven *empirically true*, not merely plausible. The skill working end-to-end is necessary but not sufficient — a reference doc can be wrong in a way a single happy-path build never hits. So the loop maintains an explicit **SDK-surface coverage checklist** derived from `generated/*` (the introspected signatures), and a reference file is not "verified" until every operation it documents has been *driven against the live stack* at least once:
- **Python SDK** (`api/bifrost/{tables,integrations,config,files,agents,forms,workflows,executions,knowledge,organizations,roles,users,ai,events}.py`): every public method exercised in a real workflow run — both happy path and the documented error/edge behavior where the doc makes a claim (e.g. `tables.update` → null-on-missing, `tables.count(where=…)` filtered count is Python-only).
- **Web SDK** (`client/src/lib/app-sdk/index.v2.ts`): every export driven from a real app under `solution start` / `npm run dev` — `useWorkflow`, `useWorkflowQuery`, `useWorkflowMutation`, `useTable`, `useInfiniteTable`, `tables.*` (get/insert/upsert/update/delete/query/count/subscribe), `BifrostProvider`/`BifrostHeader` (incl. theme), and the error classes (`TableAccessDeniedError`, `TableNotFoundError`) actually triggered.
- The **tables.md side-by-side** gets special attention — it documents the Python↔Web traps (same name, different object; batch spelling; kwargs vs options object; nested vs flat rows). Every cell in that table must be driven on *both* sides so the trap descriptions are confirmed real, not inferred.

Coverage gaps found mid-loop are themselves findings: an undocumented method, a doc claim no run exercised, a signature that doesn't match `generated/`. Each forces a doc fix and resets the streak (below).

**Mechanism.** Spawn Sonnet subagents (`subagent_type: general-purpose`, `model: sonnet`), each in a clean scratch dir (`/tmp/bifrost-build-validation-<track>-<n>`), pointed at the running debug stack (`./debug.sh status` for URL; `dev@gobifrost.com` / `password`; **port mode** for any browser drive — netbird can't drive Vite). Each subagent is given ONLY the rebuilt skill as guidance (no source-tree spelunking) and a from-scratch build task. Runs are **serialized against the single debug stack** (one stack; sequential drives) per the worktree's test-stack discipline.

**Track A — Solution build (read-only invariant in force):**
`bifrost solution init` → scaffold a real **Tailwind-styled** app (`solution scaffold-app`) → get an **agent + table + form/config** into the solution → `bifrost solution start` and drive every page in the browser → update an entity → `bifrost solution deploy`. The app must render **actually styled** (real Tailwind, not an unstyled stub).

> **Open question the loop must resolve (don't pre-assert in the skill):** what *is* the correct mechanism to get an agent/form/table/config into a solution? It is NOT live `bifrost <entity> create|update` against a deployed/solution-managed entity (that 409s) and it is NOT local YAML (workspace entities are API-only). The candidates are `bifrost solution capture` (author the entity in a scratch/global context, then capture it into the solution — the path `/migrate` uses) and/or a deploy-time manifest. Track A must pin down the single blessed mechanism by driving it, then `solutions.md` documents exactly that. If the answer turns out to differ by entity type, the scorecard's "entities created correctly" line captures it per type.

**Track B — Repo / global build (live mutation is correct here):**
The v1/global-workspace flow → author a workflow `.py` + create entities via live `bifrost <entity> create|update` (the *correct* mechanism in this mode) → execute the workflow → iterate; honor the watch/sync caveats. If cheap, include an MCP-only variant (repo-only concept). The SDK/global flow is the priority.

**Scorecard per run (both tracks):**
- App actually styled (real Tailwind, not unstyled) — Track A; app/UI correctness — Track B
- Entities created correctly (agent / table / form / config / workflow as applicable)
- Update worked
- Deploy clean (Track A) / execute clean (Track B)
- **Invariant respected** — Track A: no live `bifrost <entity> create` against a deployed solution, no watch/push/git; Track B: no forbidden global commands.
- Every misleading moment in the skill logged.

**Done bar (user decision):** stop a track after **~3 consecutive clean runs with no skill-doc edits in between.** Between runs, every logged misleading moment → a skill-doc fix → the consecutive-clean streak **resets to zero**. A track is done when it converges to a 3-run clean streak. Both tracks must reach the bar.

**Deliverable:** a validation log (`docs/plans/2026-06-15-build-skill-validation-log.md`) — per-run scorecards, the doc fixes each run triggered, the SDK-surface coverage checklist marked off, and the final clean streak as evidence.

## 8. Keeping the reference files current (operationalization)

The `generated/*` appendices stay honest cheaply (Gate 1 regenerates them; CI fails on diff). The **curated** reference files (`tables.md`, `solutions.md`, `web-sdk-v2.md`, `python-sdk.md`, `workflows-python.md`, `entities.md`, `apps.md`) are hand-written prose — they can silently rot as the SDK evolves. We need a durable, low-effort currency loop for them, separate from the one-time build. The model is **proven in-repo already**: the `bifrost-documentation` skill keeps the docs site fresh via a manifest (`screenshots.yaml`) that maps each output to `source_globs` + the `bifrost_sha` it was last captured at, plus a `diff` mode that re-acts only on entries whose source changed. We mirror that exactly.

**Manifest:** `.claude/skills/bifrost-build/references/sources.yaml` — one entry per curated reference file:
```yaml
- file: references/tables.md
  source_globs: ["api/bifrost/tables.py", "client/src/lib/app-sdk/tables.ts", "api/src/routers/tables.py"]
  verified_at_sha: <git sha the file's claims were last driven/verified against>
```

**Two enforcement layers, escalating by cost:**
1. **Cheap, always-on (CI) — staleness flag.** A pytest/CI check (`api/tests/unit/test_skill_reference_freshness.py`) compares, per entry, whether any `source_globs` path has commits newer than `verified_at_sha`. If so it **warns** (informational in CI, like the existing plugin-version drift report) — "tables.md documents tables.py, which changed in 3 commits since it was last verified." This is the low-effort signal; it never blocks a merge but makes rot visible.
2. **On-demand re-verification — a `diff`-mode skill run.** Operationalized as a documented mode of the build skill's own maintenance (and a candidate slash entrypoint, e.g. `/bifrost-build --verify-references` or a thin `bifrost-build-maintenance` companion): short-list reference files whose `source_globs` moved past `verified_at_sha`, **re-drive only those operations** against the debug stack (reusing the §7 coverage harness scoped to the changed surface), fix the prose, and bump `verified_at_sha`. This is the recurring, much-lighter version of the §7 loop — you never re-drive the whole SDK, only what changed.

**Why not make staleness a hard gate?** A source change is not always a doc change (rename, internal refactor, added private helper). A hard gate would force a full re-drive on every SDK touch — exactly the generation-heavy burden the user wants to avoid. Warn cheaply; re-verify deliberately. The `generated/*` appendices remain the *hard* gate for mechanical facts (signatures); the manifest is the *soft* gate for prose.

**Scope note:** this freshness manifest covers the build skill's reference files. If the same rot risk applies to `skills/migrate` (it documents the same v2 SDK surface), add its reference content to the manifest too — empowered per the user's "update existing skills as needed."

## 9. Existing-skill reconciliation (empowered scope)

The user empowered drastic plugin/skills changes and updates to project-specific skills. In-scope adjacent cleanups discovered during exploration (do them where they serve this work; don't gold-plate unrelated skills):

- **Codex mirror is split across two roots.** `plugins/bifrost/skills/` holds 4 public skills (build/copilot/migrate/setup, plain files); `.codex/skills/` holds a *different* 8 maintainer skills. The Gate 3 / `sync-codex-skills.sh` design (§5) must reconcile **both** Codex roots against `.claude/skills/` — not just `plugins/bifrost/skills/`. Determine the intended split (public-distributable vs maintainer) and make the sync script + `diff -r` gate enforce it for both. This is the real fix for the "two drifted copies" item in the RESUME doc.
- **`skills/` symlink inconsistency.** `skills/build|setup|copilot-cowork-package` are symlinks → `.claude/skills/bifrost-*`, but `skills/migrate` is a **real dir** (not a symlink). Normalize to the symlink pattern so `bifrost skill update`'s allowlist derivation is uniform, OR document why migrate differs. Verify against `skill.py`'s symlink-reading allowlist logic before changing.
- **`bifrost-documentation` skill.** No content collision (it's the docs-site refresher, not an SDK teacher), so no rewrite needed. But it is the *source pattern* for §8's freshness manifest — cross-reference it from `sources.yaml`'s design so the two currency mechanisms stay conceptually aligned, and if its `diff`-mode harness (`bootstrap-manifest.mjs`) is generic enough, reuse rather than reinvent.

## 10. Task breakdown

Follows 06-09 §7, with the validation loop expanded to two tracks + full-SDK coverage + the freshness manifest. Each task gates the next; tests/gates green before proceeding.

- **Task 0 — Ground-truth dumps.** `scripts/skill-truth/generate.py` (CLI walk + Python `inspect` + OpenAPI digest) + `dump-app-sdk-surface.mjs`; commit first `generated/*`. *Done:* double-run → zero diff. **Needs care.**
- **Task 1 — Claims linter (red), incl. mode-conditional bans.** *Done:* fails red against the CURRENT skill (flags `bifrost watch`/`export`/`git push`) AND flags a deliberately-planted `bifrost agents create` inside a solution-context block. **Needs care.**
- **Task 2 — CI job + Codex sync (both roots) + Gate 3.** *Done:* `skill-accuracy` red on a deliberate doc/flag edit, green after regen + sync; `sync-codex-skills.sh` + `diff -r` reconcile **both** `plugins/bifrost/skills/` and `.codex/skills/` (§9). Normalize the `skills/migrate` symlink inconsistency here.
- **Task 3 — Hub `SKILL.md` rewrite (dispatcher).** *Done:* ≤ ~250 lines; `bifrost.solution.yaml` detection routes correctly; linter green; every routing target exists.
- **Task 4 — `tables.md`** (the pain-point deliverable) + policies + scope/solution cascade. *Done:* every signature matches `generated/` verbatim.
- **Task 5 — `solutions.md` (LIGHT) + `repo.md` (entry) + `workflows-python.md`**; llm.txt salvage → `entities.md`, then delete `docs/llm.txt` + CLAUDE.md/AGENTS.md edits. *Done:* solutions.md points at /migrate, restates no shared facts; repo.md carries v1/global + links to mcp-mode.md.
- **Task 6 — `web-sdk-v2.md` + `apps.md`** (merge app-patterns, v2-first; platform-api.md / import-patterns.md retained as v1 refs).
- **Task 7 — `entities.md`, `mcp-mode.md`, `rest-api.md`, `python-sdk.md`.**
- **Task 8 — SDK-surface coverage harness + Track A loop (solution).** Build the coverage checklist from `generated/*`; iterate Track A to a 3-consecutive-clean streak with **full web+Python SDK coverage** marked off (§7 coverage mandate); log each run + fix.
- **Task 9 — Track B loop (repo/global)** to a 3-consecutive-clean streak; covers any SDK surface not reachable from the solution track (so the union of A+B drives the whole SDK); log each run + fix.
- **Task 10 — Reference-freshness manifest.** `references/sources.yaml` + `test_skill_reference_freshness.py` (cheap CI staleness warn) + the documented `diff`-mode re-verification path (§8). *Done:* manifest covers every curated reference file with a real `verified_at_sha`; the staleness check warns on a deliberately-staled entry.
- **Task 11 — Distribution check.** Plugin load (Claude + Codex, both roots), `bifrost skill update` round-trip from branch tarball (confirm nested `references/`+`generated/` materialize), version-bump dry run.
- **Task 12 — Plugin version bump.** `scripts/update-plugin-version.sh "$(scripts/compute-dev-version.sh)"` (bumps all manifests). (RESUME task 4 — pairs with any `skills/` change.)

## 11. Critical files

- `.claude/skills/bifrost-build/SKILL.md` (+ new `references/`, `generated/`, `references/sources.yaml`) — the artifact; symlinked from `skills/build`.
- `api/bifrost/commands/__init__.py` + `solution.py` + `workflows.py` — the Click tree the linter walks.
- `api/bifrost/tables.py` ↔ `client/src/lib/app-sdk/tables.ts` — the two sides of the tables pain point.
- `client/src/lib/app-sdk/index.v2.ts` — v2 SDK export surface (ground truth for web-sdk-v2.md / web-sdk-surface.md).
- `api/src/services/mcp_server/tools/sdk.py` — introspection generators the gate reuses.
- `api/bifrost/skill.py` — `bifrost skill update` fetch/write (verified recurses nested dirs).
- `scripts/{update-plugin-version,compute-dev-version}.sh` — version bump.
- `.github/workflows/ci.yml` — gate job + plugin-version guards.
- `skills/migrate/SKILL.md` — the worked v1→v2 path `solutions.md` points at (do not duplicate); `skills/migrate` is a real dir, not a symlink (§9 reconciliation).
- `.claude/skills/bifrost-documentation/SKILL.md` + `scripts/docs/bootstrap-manifest.mjs` — the proven manifest+diff freshness pattern §8 mirrors.
- `.codex/skills/` AND `plugins/bifrost/skills/` — the two Codex mirror roots Gate 3 must reconcile (§9).

## 12. Constraints

- Worktree only; never two concurrent `./test.sh`; full pre-completion verification before claiming done; no client specifics in the public repo.
- Solution-managed writes from deploy/sync/delete must use Core statements (the always-on read-only guard 500s on ORM-object mutation in prod but passes in isolated unit tests — install the guard in the test).
- Browser drives in the validation loop need **port mode** (`BIFROST_FORCE_PORT=1 ./debug.sh up`) — Chrome can't drive netbird-mode Vite.
