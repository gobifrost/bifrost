# Table document read service extraction

Executor: OpenCode `opencode-go/muse-spark-1.3-contributor`. Owner/reviewer:
Codex. Worktree:
`/home/jack/GitHub/bifrost/.claude/worktrees/sdk-engine-tables`, branch
`codex/sdk-engine-tables`, baseline `88744be92`. This is a parallel, bounded
preparation for the table read stage in
`docs/plans/2026-09-24-engine-sdk-local-transport.md`. It must not merge to
main or change the aggregate SDK worktree.

## Task and ownership

Extract the existing table document read behavior from
`api/src/routers/tables.py` into `api/shared/table_documents.py`. Move the
whole `DocumentRepository` and its query/filter helpers there. Add shared
service functions for single-document get, query, and count using the current
response DTOs. Move the generic read action check with its deny audit and
commit behavior into the shared module; update router imports for write
consumers. Make the three HTTP read handlers call the shared functions after
their existing `get_table_or_404` resolution. Preserve that router-owned
resolution and its Solution/app security gates. Do not add local transport,
SDK facade, dispatcher, or parent principal changes in this task; those are
the next integration step after this service extraction is reviewed.

Allowed files: `api/src/routers/tables.py`, new
`api/shared/table_documents.py`, direct import consumers
`api/src/services/solutions/zip_install.py`,
`api/tests/e2e/api-integration/test_tables.py`,
`api/tests/performance/test_table_document_id_pagination.py`, and focused
unit/E2E tests under `api/tests/`. If a different production file is needed,
report the blocker before editing it. No configuration, credentials,
dependencies, unrelated cleanup, commit, push, or primary checkout edits.

## Acceptance

- HTTP get/query/count retain exact status, DTO, policy, and pagination
  behavior. Query/count return an empty result/zero when no read policy grants
  access. Single get returns the existing 404 for a missing row and emits the
  same deny audit on policy rejection.
- Keep SQL filter details, stable ID tie-breaker, and `skip_count` semantics.
  Do not alter write policies or batch transactions.
- Remove the old repository implementation from the router and update real
  import consumers. No compatibility re-export solely for tests.
- Add small focused unit coverage for service orchestration; run affected
  existing table/policy tests and `./test.sh quality api` in this worktree.
  Report exact tests, failures, and broader suites not run. Stop and report
  an architectural gap rather than expanding into local dispatch.

OpenCode writes only in this worktree. Codex will review the diff and merge it
into the aggregate branch after the integration stage releases its own files.
