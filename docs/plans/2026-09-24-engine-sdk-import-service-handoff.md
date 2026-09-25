# Engine SDK import source service extraction

Owner/reviewer: Codex. Executor: OpenCode `opencode-go/muse-spark-1.3-contributor`.
Worktree: `/home/jack/GitHub/bifrost/.claude/worktrees/sdk-engine-imports`.
Baseline: `45aeadc77`. This is preparatory extraction for the complete engine
SDK local transport plan in the aggregate worktree. No main merge until all
fixed operations are complete and verified.

## Task

Extract the source-domain operations in `api/src/routers/sdk_modules.py` into
`api/shared/sdk_modules.py`, so the HTTP router and a later parent-local import
dispatcher can invoke the same logic. Move import-name resolution, including
metadata cache hydration, scoped module/package/namespace probes,
Solution-before-repo ordering, cache writes, and per-key singleflight. Move
direct source fetch path validation and Solution-path access rules. Preserve
Redis keys, positive/negative TTLs, statuses, and response DTOs.

The shared service takes an already-authoritative `ModuleSourceScope` with
`solution_id` and `global_repo_access`. It must not accept `Request`, a JWT,
raw child frame, or child-selected security scope. Keep HTTP authentication,
system-user `engine_execution_id` proof, signed-claim-over-query override,
human admin diagnostic query parsing, and HTTP exception mapping in the
router. Requirements fetch remains router-local. Do not yet modify the child
synchronous import code or add local dispatch; that is a separate stage.

Allowed production files: `api/shared/sdk_modules.py` and
`api/src/routers/sdk_modules.py`. Tests: focused existing/new files under
`api/tests/unit/routers/`, `api/tests/unit/sdk/`, and relevant import-source
tests. Do not edit other production files without reporting a blocker first.
Do not commit, push, merge, install packages, change configuration or
credentials, or edit the primary checkout. Preserve unrelated work.

## Acceptance

Both router operations call the new shared service. It has no router import.
Engine system callers without a bearer token or execution-id claim are
rejected. Signed Solution claims override query parameters. Direct source
fetch forbids sibling Solution paths and bare repo paths when sealed. Resolver
preserves cache scope and namespace shadowing. Add or retarget tests for
these rules, metadata rehydration, and singleflight. Run focused unit and
live endpoint tests through `./test.sh`, plus `./test.sh quality api`.
Report exact results, skipped broader suites, changed files, and blockers.

Progress log: `/tmp/bifrost-sdk-import-extract-opencode.jsonl`.
The owner records the OpenCode session ID on completion and reviews the diff,
independently runs focused checks, then integrates the reviewed commit into
the aggregate branch. Resume explicitly with `--session`, never `--continue`.
