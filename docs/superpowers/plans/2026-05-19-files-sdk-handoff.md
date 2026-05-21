# Files SDK — handoff for a fresh session

> **What this is:** a single-screen pickup point for the file-policies work (#170).
> Read this first; it tells you where you are, what's left, and which docs to load.

## TL;DR

- **PR A (engine extraction) is open and CI-green**: <https://github.com/jackmusick/bifrost/pull/264>. Branch: `170-file-policies`.
- **PR B (file-policies impl) is unwritten code, fully planned** in `2026-05-19-file-policies.md`.
- **Hard gate:** PR A must merge before Task 2 of Plan B can start. Plan B Task 1 is just the "is PR A merged?" check.

## What's done

| Item | State | Where |
|---|---|---|
| Spec for file policies | ✅ committed | `docs/superpowers/specs/2026-05-01-file-policies-design.md` |
| Plan A — engine extraction | ✅ executed | `docs/superpowers/plans/2026-05-19-policy-engine-extraction.md` |
| Plan B — file-policies build | ✅ written, **not started** | `docs/superpowers/plans/2026-05-19-file-policies.md` |
| PR A: domain-agnostic engine | ✅ open, all CI green | [PR #264](https://github.com/jackmusick/bifrost/pull/264) |
| PR B (file policies) | ⏳ not started | — |

## What PR A actually shipped

Refactored `api/shared/policies/` so the engine is domain-agnostic, with table-specific code in a new module:

- `api/shared/policies/{ast,resolver,binding,evaluate,compile,probe,subscription,functions}.py` — domain-agnostic
- `api/shared/table_policies.py` — `RowResolver`, `TableBinding`, `compile_read_filter`, `make_seed_admin_bypass`, `TablePolicies = PolicyDocument`
- AST validator now accepts `{row: ...}` and `{file: ...}` references via a `_KNOWN_NAMESPACES` allowlist; unknown single-key dicts (e.g. `{has_role: "x"}`) raise with a clear error
- Engine isolation test (`test_engine_does_not_import_domain_code`) statically forbids the engine from importing `src.models.orm`, `shared.table_policies`, or `shared.file_policies`
- All consumers (`routers/tables.py`, `routers/websocket.py`, `routers/cli.py`, `services/manifest_import.py`, `services/mcp_server/tools/tables.py`, `routers/export_import.py`, `test_admin_bypass_seed_migration.py`) migrated to the new imports/signatures

**Verification at completion:** 5107 tests passed, 50 skipped, 0 failed in full `./test.sh all`. Pyright + ruff clean.

## What PR B will ship (the full file-policies build)

From the spec / Plan B, the seven sub-projects:

| # | Sub-project | Tasks | Notes |
|---|---|---|---|
| A | Engine wiring | 2–4 | `FileResolver` + longest-prefix lookup + DB-backed `evaluate_file_action` |
| B | Storage + migration | 5–7 | `FilePolicy` ORM + `file_index` sidecar columns + dual-write on every write path |
| C | REST relax + batch signed-URL | 8–11 | `/api/files/*` from `CurrentSuperuser` → `Context` with policy enforcement; new `POST /api/files/signed-urls`; CRUD + validate endpoints |
| D | CLI / MCP / Manifest | 12–14 | `bifrost files policies …`; DTO parity; ManifestFilePolicy round-trip |
| E | Web SDK + `useFiles` hook | 15–17 | `client/src/lib/app-sdk/files.ts`; Playwright E2E |
| F | Admin UI | 18–21 | Tree-view file browser; Monaco rule editor; effective-access tester |
| G | Subscriptions | 22–24 | `files:{location}:{prefix}` websocket channel; per-recipient Creator filter; folder-rename as a UI primitive |

**Parallelism in Plan B:** Tasks 1–7 serial. After Task 7 lands, sub-projects C/D/E can run in parallel (three agents). After Task 15, F starts. G is last. Plan §"Parallelism playbook for agent teams" has the exact split.

## How to resume in a fresh session

1. **Read this file first** (you're here).
2. **Check PR A status:**
   ```bash
   gh pr view 264 --json state,mergeable,statusCheckRollup -q '.state, .mergeable'
   ```
   - If `MERGED`: proceed to step 3.
   - If `OPEN` and CI green: ask the user whether to merge. Do not self-merge without explicit consent (see project memory `feedback_explicit_merge_consent`).
   - If anything red: investigate.
3. **Read the spec** to refresh context: `docs/superpowers/specs/2026-05-01-file-policies-design.md` (553 lines, fully self-contained).
4. **Open Plan B** and start at Task 1 (a 4-step "is PR A merged?" preflight):
   `docs/superpowers/plans/2026-05-19-file-policies.md`
5. **Use the `superpowers:subagent-driven-development` skill** to execute Plan B task-by-task with two-stage review (spec compliance + code quality). The plan is written for that workflow.

## Worktree state

- Branch: `170-file-policies` at `/home/jack/GitHub/bifrost/.worktrees/170-file-policies`
- Rebased on `main` as of 2026-05-19; 9 commits ahead (8 engine refactor + 1 plan docs)
- Test stack: was up via `./test.sh stack up` (compose project `bifrost-test-9e2076d6`). May have timed out by now — `./test.sh stack status` to check.
- Dev stack: down (not needed for Plan B Tasks 1–6 since those are backend-only)

## Known quirks the next session should be aware of

- **Pyright "could not be resolved" diagnostics from the harness are false alarms.** They appear when pyright is invoked with the wrong cwd. Always verify with `cd api && pyright <path>` before treating one as real. (See conversation history — happened ~10 times during PR A execution.)
- **`./test.sh` can fail to boot the api container if a recent commit broke imports.** When you see "service `api` is not running", restart with `docker compose -p <project> -f docker-compose.test.yml restart api`. The Plan B implementer hit this during Task 6 when consumers were temporarily broken; same pattern will recur during Plan B's transitional commits.
- **`make_seed_admin_bypass` for files needs the file action vocab `[read, write, delete, list]`**, NOT tables' `[read, create, update, delete]`. The spec is clear about this; Plan B Task 2 codifies it.
- **Pre-sidecar files are admin-only by design.** No retroactive backfill of `created_by`. Plan B Task 7 enumerates every write path that must populate the sidecar — if you find a path the plan misses, add it and update the plan.

## If a PR A change request comes back

If PR #264 picks up review comments before merge, address them on the same branch (`170-file-policies`). The plan files and spec live on the same branch, so amending PR A is straightforward.

## Decision log (things I'd ask about if I were a fresh session)

- **Why a tables-narrowed `Policy` in `contracts/policies.py` vs. just using the wide one?** The existing test `test_policy_actions_limited_to_known_set` (predating this work) asserts `Policy(actions=["query"])` raises — i.e., handlers using `Policy` from contracts expect strict action vocab enforcement. Keeping the narrow class preserved behavior while letting the shared engine accept any string. The shim is explicitly temporary; the comment in `contracts/policies.py` points new code at `shared.table_policies`.
- **Why a `_KNOWN_NAMESPACES` allowlist in the AST validator instead of a fully open one?** The original validator (pre-refactor) rejected `{has_role: "support"}` because `has_role` wasn't a known operator. The refactor's "single-key dict ⇒ domain reference" branch silently accepted it. Adding `{"row", "file"}` as the allowlist re-tightens to the original behavior while staying domain-agnostic at the engine layer. Adding a new domain (e.g. `runs`) is a one-line change here.
- **Why pass `RowResolver()` explicitly everywhere instead of defaulting in `evaluate_action`?** The plan deliberately requires the resolver kwarg so the engine has no implicit domain. The cost (a few extra `resolver=RowResolver()` at handler call sites) is small; the payoff (domain-agnostic engine, file policies can plug in trivially) is large.
