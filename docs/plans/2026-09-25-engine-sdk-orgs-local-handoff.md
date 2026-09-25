# Organization SDK engine-local transport

Executor: OpenCode. Reviewer: Codex. Work only in this worktree. Do not
commit, push, merge, or touch the primary checkout.

Implement local dispatch for the five fixed `bifrost.organizations` methods:
`create`, `get`, `list`, `update`, `delete`. External SDK callers keep HTTP.
Both paths must call the existing `api/shared/sdk_organizations.py` service.
The parent creates short pooled DB sessions; the child never opens a DB
connection or calls API HTTP. No local failure falls back to HTTP.

HTTP uses `CurrentSuperuser`. Match its token-equivalent authority: an
ordinary workflow engine token has superuser authority even when the
initiating user is not a platform admin, while a supervised service token
does not. Do not use `LocalDispatchPrincipal.is_platform_admin` as the
SDK-token authority and do not trust child actor/org/capability claims.
Preserve DTO validation, return types, status/error behavior, cache
invalidation, audit actor, provider-org protection and transaction behavior.
Test both token kinds and creation/update/deletion side effects.

Keep edits to organization SDK, local transport/dispatcher, tests and this
handoff. Do not touch users, roles, files, tables, artifacts, AI, or import
transport. Run focused `./test.sh` unit/E2E tests including a real worker
execution with fixed-operation API HTTP disabled, and `./test.sh quality api`.
Diagnose failures without blind reruns, retries, skips, or timeout increases;
report exact commands/results.
