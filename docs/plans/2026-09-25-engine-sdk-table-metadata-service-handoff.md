# Shared SDK table metadata operations

Owner/reviewer: Codex. Executor: OpenCode
`opencode-go/muse-spark-1.3-contributor`. Worktree:
`/home/jack/GitHub/bifrost/.claude/worktrees/sdk-engine-table-meta`.
Baseline: `edeb4e5aa`. Preparatory extraction for full engine-local SDK;
no main merge or PR until all fixed operations and tests pass.

## Scope

Extract the business operations behind `/api/sdk/tables/create`,
`/api/sdk/tables/list` (`api/src/routers/cli.py`) and
`DELETE /api/tables/{table_id}` (`api/src/routers/tables.py`) into a focused
`api/shared/sdk_table_metadata.py`. Keep HTTP handlers thin and preserve
their DTO/status/authorization behavior. Do **not** add local transport in
this stage; it will use the shared service after the aggregate import-channel
stage finishes.

Creation must reject any Solution execution context with the existing 404,
resolve org scope through the existing shared `resolve_sdk_scope` semantics,
check duplicate name in the exact org/loose scope (not the cascade), seed
`admin_bypass`, commit and refresh, preserve `created_by`, and return the
same `SDKTableInfo`. `app` sent by the facade remains ignored by the
existing request DTO; do not invent app scoping. List must preserve the
external vs engine sentinel repository trust, org/global cascade, sorted
name order, and the same DTO. Delete must preserve the HTTP superuser gate,
Solution-managed protection, repository org behavior, and 204/404.

The shared layer must not import either router. It may use the existing
`shared.table_resolution.TableResolutionContext` shape or a focused context
protocol. Handle `ScopeResolutionError`/service errors at the HTTP edge while
preserving exact historical status and detail. In particular, do not take
principal/org/Solution authority from future child frames.

Allowed production files: `api/shared/sdk_table_metadata.py`,
`api/src/routers/cli.py`, `api/src/routers/tables.py`, and, only if needed,
`api/shared/table_resolution.py`. Tests under `api/tests/unit/` and focused
table E2E. Report before modifying any other production file. Preserve
existing changes. Do not commit, push, merge, install packages, alter
configuration/credentials, or touch primary checkout.

## Verification

Add shared-service tests for exact-scope duplicate vs global name cascade,
Solution create rejection, org/provider/external scope, seeded policy,
sorted list, Solution-managed delete, absent delete, and HTTP adapter parity.
Run targeted existing table create/list/delete and Solution tests, one live
HTTP endpoint path, and `./test.sh quality api`. Use `./test.sh` only. Report
exact tests/results, skipped broader suites, and any blocker.

Progress log: `/tmp/bifrost-sdk-table-meta-opencode.jsonl`. Owner independently
reviews and verifies before committing/cherry-picking. Resume explicitly by
session ID.
