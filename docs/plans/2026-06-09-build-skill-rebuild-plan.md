# Plan: Rebuild `bifrost:build` — Hub Skill + Curated Subskills + Deterministic Accuracy Gate

Date: 2026-06-09 · Status: execution-ready (refs verified against tree)
Mandate: WS-17 in `docs/plans/2026-06-09-platform-50ft-action-plan.md`
Modeled on: `docs/plans/2026-06-09-worker-memory-slimming-plan.md`

**Branch-base decision (load-bearing):** the Solutions-era CLI (`bifrost solution init/scaffold-app/start/deploy/install`, export/import removed) exists only on `worktree-solutions-success-criteria` (PR #347), not `main`. The skill teaches that workflow, so **the rebuild worktree must branch from `worktree-solutions-success-criteria`** (or land after #347 merges). All paths below are relative to that worktree root unless marked `[main]`.

---

## 0. Verified inventory

### 0.1 Current skill state

| Artifact | Location | Size | Notes |
|---|---|---|---|
| Build skill (real files) | `.claude/skills/bifrost-build/{SKILL.md,app-patterns.md,import-patterns.md,platform-api.md}` | 447 + 608 + 150 + 1582 lines | SKILL.md is pre-Solutions |
| Claude plugin alias | `skills/build -> ../.claude/skills/bifrost-build` (symlink; same for `setup`, `copilot-cowork-package`) | — | The symlink set in `skills/` is the **public allowlist** consumed by both the Claude plugin loader AND `bifrost skill update` (`api/bifrost/skill.py:201-240` reads these symlinks from the GitHub tarball) |
| Codex plugin copy | `plugins/bifrost/skills/bifrost-build/` — **plain-file copies, already drifted** (`SKILL.md` 33,970 B vs 33,563 B; `diff -q` DIFFER) | — | Manual copy, no sync check |
| Plugin manifests | `.claude-plugin/plugin.json`, `.codex-plugin/plugin.json`, `plugins/bifrost/.codex-plugin/plugin.json` (all 0.9.2-dev.84), two marketplace.json files | — | Versions via `scripts/update-plugin-version.sh` + `compute-dev-version.sh`; CI informational drift report (`ci.yml:215-230`) + hard tag-time guard (`ci.yml:454-460`) |
| `docs/llm.txt` | 313 lines / 18,327 B CLI/MCP reference prose | — | Referenced by `CLAUDE.md:183` + `AGENTS.md:184`; origin branch `feat/llms-txt-and-design-workflow` dies with WS-17 |
| Served `llms.txt` (distinct!) | `api/src/routers/docs.py:11` → `generate_llms_txt()` (`api/src/services/llms_txt.py`, template + **runtime introspection** via `mcp_server/tools/sdk.py` `_generate_module_docs`); MCP `get_docs`; tested by `test_docs_router.py` | — | The introspection helpers are reusable for the accuracy gate |

### 0.2 Spot-check: where the current skill is wrong (verified against worktree source)

1. **Primary workflow is the dead one.** `SKILL.md:14-21, 110-120, 259-299` center SDK-first mode on `bifrost watch/sync/push/pull` + `bifrost git *`. Success criteria §3.10: "watch does not belong to the Solution paradigm"; WS-5 schedules removal.
2. **Mandates the llm.txt dependency being killed.** `SKILL.md:34-46` ("Step 1: Download Platform Docs… `bifrost api GET /api/llms.txt`").
3. **Stale export claim.** `SKILL.md:57`: "`.bifrost/*.yaml` is what `bifrost sync`/`bifrost export` produce" — export/import are deleted in the worktree.
4. **Wrong hook guidance for v2 apps.** `SKILL.md:336`: "Always use UUIDs, never names — `useWorkflowQuery('uuid')`". The v2 SDK's `useWorkflow(workflowRef)` (`client/src/lib/app-sdk/use-workflow.ts:45`) takes **portable `path::fn` refs**, and the `solution start` identity chain depends on them.
5. **Whole command groups missing** (all verified present): `bifrost solution {init,scaffold-app,deploy,install,start}` + top-level `deploy` (`cli.py:640-647`), `files {read,write,list,delete,exists,search}`, `claims`, `configs set`, `workflows remap` (`commands/workflows.py:412`), `apps replace` (`commands/apps.py:386`), `auth token` (load-bearing for v2 tokenless dev), `bifrost skill {list,update,remove}`.
6. **The tables pain point has no doc at all.** `SKILL.md:356` → `platform-api.md` documents only the **web** surface; the Python surface is documented nowhere; the prose never says they differ.
7. **App-build flow is v1-only.** `SKILL.md:373-407` has no `standalone_v2` model (Vite scaffold, `npm run dev`, `solution start`, dist-only deploy).

Still accurate and kept: MCP tool naming section, app resilience rules, drag-and-drop warning, `bifrost api` boundaries, `platform-api.md` (current for web/v1: `useTable`/`tables.*` correct), `app-patterns.md`, `import-patterns.md`, and the `PLATFORM_EXPORT_NAMES` drift test (`api/tests/unit/test_platform_names_match_runtime.py`) — the in-repo precedent the gate generalizes.

### 0.3 Surfaces and their ground-truth sources

| Surface | Ground truth | Deterministic dump mechanism |
|---|---|---|
| CLI | `api/bifrost/cli.py:568-660` (hand-rolled dispatcher: login logout auth run git sync push pull watch api migrate-imports skill solution deploy) + Click groups `commands/__init__.py:37-49` (orgs roles workflows forms agents apps claims integrations configs tables events files requirements) + `solution_group`. workflows has 13 verbs incl. register/execute/remap/grant-role/revoke-role/list-orphaned/replace; events uses hyphenated verbs; flags are DTO-generated (`dto_flags.py`) | Import Click groups, walk `click.Context.get_help()` recursively; hand-rolled: capture `print_help()` text. No server needed. |
| REST API | FastAPI OpenAPI (typegen precedent: `client/package.json:19`) | Offline `from src.main import app; app.openapi()` in test-runner (verify DB-free import; fallback: running debug stack). Digest = sorted method/path/operationId + param names. |
| Python SDK | `api/bifrost/{tables,integrations,config,files,agents,forms,workflows,executions,knowledge,organizations,roles,users,ai,events}.py` | `inspect.signature` walk — **reuse `_generate_module_docs` from `mcp_server/tools/sdk.py`** |
| Web SDK (v2) | `client/src/lib/app-sdk/index.v2.ts`: BifrostProvider, useBifrostContext, BifrostHeader, useWorkflow, useTable, useInfiniteTable, tables, error classes | TS dump script (`tsc --emitDeclarationOnly` or compiler API) |
| Web SDK (v1) | `platform_names.py` ↔ `app-code-runtime.ts` | Already drift-locked by `test_platform_names_match_runtime.py` — keep |

### 0.4 The Solutions-era workflow to teach

- **Two tiers:** `_repo/` = shared library (unchanged); Solutions = installable one-writer read-only deployable units.
- **Lifecycle:** `bifrost solution init` → `bifrost solution scaffold-app <slug>` (normal Vite project; `bifrost` resolves from `<api>/api/sdk/download`; tokenless dev via env/.env/`bifrost auth token` — `commands/solution.py:187-270`) → **`bifrost solution start`** (local function host + Vite + data-plane proxy; registers nothing) → offline `bifrost run <file>` → `bifrost solution deploy [--org]` / `bifrost solution install <zip>`.
- **Entities are API-only:** CLI/MCP, never local YAML.
- **EXCLUDE:** watch/push/pull/sync, `bifrost git *` (mention only as "legacy `_repo/` drift tooling, do not use"), export/import (deleted).

---

## 1. Architecture

**One hub skill, file-level subskills, generated appendices.** Subskills are reference files inside the one plugin skill dir (progressive disclosure), NOT separate plugin skills (trigger-matching pollution across two harnesses; complicates `bifrost skill update` allowlist).

```
.claude/skills/bifrost-build/
  SKILL.md                    # HUB: ~250 lines max. Routing + plot + hard rules only.
  references/
    cli.md                    # curated CLI guide (who-runs-what, ref resolution, @file syntax)
    solutions.md              # lifecycle, descriptor, _repo/ vs solution, one-writer
    workflows-python.md       # decorators, offline bifrost run, register/replace/remap, requirements
    tables.md                 # ★ the side-by-side Python↔Web doc (§3)
    python-sdk.md             # workflow-side SDK module guide
    web-sdk-v2.md             # BifrostProvider, useWorkflow, useTable/useInfiniteTable, BifrostHeader, scaffold anatomy
    apps.md                   # design + resilience rules (merged from app-patterns.md), v2-first
    entities.md               # forms/agents/events/integrations/configs/orgs/roles/claims via CLI+MCP
    rest-api.md               # bifrost api boundaries, executions, key endpoints
    mcp-mode.md               # MCP-only mode (verified tool names)
    import-patterns.md        # kept (v1); platform-api.md kept as the v1/web reference
  generated/                  # ★ machine-written, committed, CI-regenerated
    cli-reference.md          # full recursive --help dump
    python-sdk-signatures.md  # inspect-derived signatures per module
    web-sdk-surface.md        # index.v2.ts export signatures
    openapi-digest.md         # method/path/operationId/params digest
```

**Hub routing table:** app → web-sdk-v2 + apps; workflow → workflows-python; table from app → tables.md (web column); from workflow → tables.md (Python column); deployable client unit → solutions.md; form/agent/entity → entities.md; exact flag → generated/cli-reference.md; endpoint existence → generated/openapi-digest.md. Global hard rules: never watch/push/sync/git; entities via CLI/MCP only; org+access tuple confirmed before scaffolding (keep existing access-tuple section).

**Layout note (project-rule reconciliation):** `skills/<name>` symlinks → `.claude/skills/bifrost-*` real files is load-bearing — `bifrost skill update` (`api/bifrost/skill.py:207-213`) builds its public allowlist from those symlinks. Keep this shape; changing it requires a lockstep skill.py change for zero benefit.

**Codex parity:** replace hand-copied `plugins/bifrost/skills/` with a generated mirror: `scripts/sync-codex-skills.sh` (rsync from the three public skill dirs) + CI `diff -r` equality check (red on drift). No symlinks inside `plugins/bifrost/` (Codex marketplace packaging of symlinks unverified).

---

## 2. Content map

| File | Teaches | Ground-truth inputs |
|---|---|---|
| `SKILL.md` | Plot: scaffold → solution start → bifrost run → deploy; routing; prereqs (BIFROST_* env, carried over); hard rules | success-criteria §2/§3, 50ft WS-5/WS-17 |
| `solutions.md` | init/scaffold-app/start/deploy/install; `bifrost.solution.yaml`; install scoping; global-repo-access flag; one-writer (git-connected refuses deploy); table data preserved on redeploy | `commands/solution.py`, success-criteria §3.2-3.10, solution-start design |
| `workflows-python.md` | decorators (`api/bifrost/decorators.py`); no fixed workflows/ dir; offline `bifrost run <file> -w <name> --org`; register/execute/replace/remap/list-orphaned (keep UUID-preservation guidance — correct); requirements | `cli.py:1164`, `commands/workflows.py`, `commands/requirements.py` |
| `tables.md` | §3 side-by-side + policies + scope/solution cascade (`tables.py:24-42` `_scope_query`; web `setDefaultAppScope` `tables.ts:44`) | both SDK sources + server DSL `api/src/routers/tables.py:219-290` |
| `python-sdk.md` | module-by-module prose; signatures live in generated/ | `api/bifrost/*.py` |
| `web-sdk-v2.md` | v2 scaffold anatomy (`_v2_scaffold_files`); BifrostProvider; `useWorkflow(ref)` with **portable path::fn refs**; useTable/useInfiniteTable; BifrostHeader; tokenless dev; when v1 still applies | `index.v2.ts` + v2 spec |
| `entities.md` | per-entity CLI verbs + semantics — **port the good prose from docs/llm.txt here** (its salvage value lands here before deletion) | `docs/llm.txt`, `commands/*.py` |
| `mcp-mode.md` | MCP-only flow; verified tool names (create_form, create_app, register_workflow, execute_workflow, get_execution, patch_content, replace_content, validate_app, …) | tools modules |
| `generated/*` | appendices, regenerated by the gate | §4 |

---

## 3. The tables side-by-side (the named pain point — from source)

| Operation | Python SDK (workflow-side, all `async`) | Web SDK (app-side) | Trap |
|---|---|---|---|
| Create table | `tables.create(name, description=None, table_schema=None, scope=None)` (`tables.py:94`) | **none** (DDL is Python/CLI-only) | — |
| **Delete** | `tables.delete(table_id)` — **deletes the TABLE** (`tables.py:179`); row delete = `tables.delete_document(table, doc_id, scope=None)` (`:390`) | `tables.delete(table, id \| ids[])` — **deletes ROW(S)**, never the table (`tables.ts:259`) | ★ same name, different object destroyed |
| Insert | `tables.insert(table, data: dict, scope=None)` single-only; batch = `insert_batch(table, documents)` (`:211/:424`) | `tables.insert(table, data \| Array<{data, id?}>)` — array form IS the batch (`tables.ts:176`) | batch spelling differs |
| Upsert | `tables.upsert(table, id, data, scope=None, created_by=None, updated_by=None)` positional id (`:260`); batch `upsert_batch` | `tables.upsert(table, {id, data} \| array)` (`tables.ts:202`) | arg shape differs |
| Update | `tables.update(table, doc_id, data, scope=None)` merge-patch (`:345`) | `tables.update(table, id, data)` → `null` if missing (`tables.ts:238`) | parity |
| Get | `tables.get(table, doc_id, scope=None)` (`:312`) | `tables.get(table, id)` → `null`, throws `TableAccessDeniedError` on 403 (`tables.ts:163`) | error model differs |
| Query | `tables.query(table, where=None, order_by=None, order_dir="asc", limit=100, offset=0, scope=None)` → `DocumentList` with nested `.documents[].data` (`:590`) | `tables.query(table, q: Partial<DocumentQuery>)` — **single options object** (`tables.ts:285`); `useTable` rows **flattened** (`use-table.ts:48-58`) | kwargs vs options object; nested vs flat |
| Count | `tables.count(table, where=None, scope=None)` (`:643`) | `tables.count(table)` — **no `where`** (`tables.ts:301`) | filtered count is Python-only |
| Filter ops | docstring teaches `in_` | TS teaches `in`, `neq`/`ne` | server accepts both (`routers/tables.py:267`) — docs must say so |
| Live updates | none | `tables.subscribe`, `useTable`, `useInfiniteTable`; `contains/starts_with/ends_with/has_key` query-only, not subscribable (`use-table.ts:70-79`) | — |

---

## 4. Accuracy-gate design

**Mechanism: generated-appendix regeneration check + CLI-claims linter.** Front-matter assertion blocks rejected — they duplicate facts into a second hand-maintained place (the disease being cured).

**Gate 1 — appendix freshness.** `scripts/skill-truth/generate.py` (+ `client/scripts/dump-app-sdk-surface.mjs`) regenerates all `generated/*.md`; CI runs it then `git diff --exit-code` on the dir. Determinism: sorted iteration, no timestamps, normalized widths. Reuses `mcp_server/tools/sdk.py` introspection.

**Gate 2 — curated-claims linter.** `scripts/skill-truth/lint_claims.py` + pytest wrapper `api/tests/unit/test_skill_cli_claims.py`: extract every `bifrost …` invocation from fenced blocks + inline code across `skills/**/*.md` (via symlink targets); validate command path against `ENTITY_GROUPS`/`solution_group`/dispatcher list (`cli.py:597-650`) and every `--flag` against the real Click command (DTO-generated flags introspected); **banned commands** (watch, push, pull, sync, git, export, import) fail outright. Hand-rolled commands validate against a small allowlist beside their `handle_*` parsers. This catches the actual failure mode — plausible-but-wrong flags in prose. Runs in `./test.sh unit` (no DB) + CI.

**Gate 3 — Codex mirror equality.** `diff -r` public `.claude/skills/bifrost-*` ↔ `plugins/bifrost/skills/`; fixed by `scripts/sync-codex-skills.sh`.

CI wiring: one `skill-accuracy` job, path-filtered to `skills/**, .claude/skills/**, plugins/bifrost/**, api/bifrost/**, client/src/lib/app-sdk/**, api/src/routers/**`, always-on for release tags. OpenAPI digest runs in the test-runner if offline `app.openapi()` proves DB-free (executor verifies; fallback: piggyback the stack-booting job).

---

## 5. Killing `docs/llm.txt`

1. Salvage per-entity prose into `references/entities.md` (Task 5).
2. Delete `docs/llm.txt`; update `CLAUDE.md:183` + `AGENTS.md:184` → "change a command → regenerate via `scripts/skill-truth/generate.py`; CI enforces."
3. New SKILL.md drops the "Download Platform Docs" step.
4. **Out of scope:** the served `/api/llms.txt` route + MCP `get_docs` stay (MCP-only/platform consumers). File a follow-up issue.

---

## 6. Distribution

- **Claude:** content in `.claude/skills/bifrost-build/`; `skills/build` symlink unchanged; version flow unchanged (tag-time `update-plugin-version.sh`, hard guard `ci.yml:454-460`).
- **Codex:** `sync-codex-skills.sh` regenerates `plugins/bifrost/skills/`; Gate 3 enforces; `.codex-plugin/plugin.json` rides the same bump.
- **`bifrost skill update`:** unaffected (layout preserved). Verify `_write_skill` (`skill.py:290`) round-trips nested `references/`+`generated/` dirs.

---

## 7. Task breakdown (fresh worktree off `worktree-solutions-success-criteria`)

- **Task 0 — Ground-truth dumps.** `scripts/skill-truth/generate.py` (CLI walk + Python introspection + OpenAPI digest) + `dump-app-sdk-surface.mjs`; commit first `generated/*`. *Done:* double-run produces zero diff; dumps sanity-checked against §0.3 verb map. **Needs care.**
- **Task 1 — Claims linter (red).** *Done:* fails red against the CURRENT skill (must flag `bifrost watch`, `bifrost export`, `bifrost git push`). **Needs care.**
- **Task 2 — CI job + Codex sync + Gate 3.** *Done:* `skill-accuracy` red on a deliberate doc edit, green after regen.
- **Task 3 — Hub SKILL.md rewrite.** *Done:* ≤ ~250 lines; linter green; every routing target exists.
- **Task 4 — `tables.md`** from §3 + policies + cascade. *Done:* every signature matches `generated/` verbatim; linter green. **The pain-point deliverable.**
- **Task 5 — `solutions.md` + `workflows-python.md`** + llm.txt salvage into `entities.md`, then delete it + CLAUDE.md/AGENTS.md edits.
- **Task 6 — `web-sdk-v2.md` + `apps.md`** (merge app-patterns, v2-first; platform-api.md/import-patterns.md retained as v1 refs).
- **Task 7 — `entities.md`, `mcp-mode.md`, `rest-api.md`, `python-sdk.md`.**
- **Task 8 — End-to-end shakeout.** Fresh session with ONLY the new skill: toy solution (scaffold → start → run → deploy) against `./debug.sh up`; log every misleading moment; fix; re-run gates. *Done:* full lifecycle without consulting source; all gates green.
- **Task 9 — Distribution check.** Plugin load (Claude + Codex), `bifrost skill update` round-trip from branch tarball, version-bump dry run.

## Critical files

- `.claude/skills/bifrost-build/SKILL.md` (artifact being replaced; symlinked from `skills/build`)
- `api/bifrost/commands/__init__.py` + `solution.py` + `workflows.py` (the Click tree the gate walks)
- `api/bifrost/tables.py` ↔ `client/src/lib/app-sdk/tables.ts` (the two sides of the pain point)
- `api/src/services/mcp_server/tools/sdk.py` (introspection generators the gate reuses)
- `.github/workflows/ci.yml` (gate job + plugin-version guards at 215-230 / 454-460)
