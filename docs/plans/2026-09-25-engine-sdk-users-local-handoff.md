# User SDK engine-local transport

Executor: OpenCode. Reviewer: Codex. Work only in this worktree. Do not
commit, push, merge, or touch the primary checkout.

Implement local transport for all five fixed `bifrost.users` methods:
`list`, `create`, `get`, `update`, `delete`. External SDK callers keep HTTP.
Both paths must use `api/shared/sdk_users.py`; the worker parent owns short
pooled DB sessions, and the child opens no DB connection or API HTTP. No
local failure falls back to HTTP.

Match HTTP `CurrentSuperuser` with token-equivalent authority: ordinary
workflow engine tokens are superusers even when the initiating user is not;
supervised service tokens are not. Derive actor ID/email and capability in
the parent, never from child frames. Preserve DTO validation, list filters,
ordering, pagination, invite URL/status creation, audit, role transitions,
self/system protection, errors/statuses, return types, and commits.

Correct the known `users.list(org_id=...)` mismatch as part of this joint
SDK/transport stage: the Python SDK currently sends an ignored `org_id`
query parameter while the HTTP handler accepts `scope`. Make the HTTP SDK
send the intended `scope` filter, and make local use the same filter. This
changes the broken external SDK filter to the documented behavior. Add a
regression test proving both paths filter to the requested organization,
including a no-match case. Do not add a compatibility alias/fallback.

Keep edits to user SDK, local transport/dispatcher, tests, and this handoff.
Do not touch other identity, files, tables, artifacts, AI, or import
transport. Run focused `./test.sh` unit/E2E tests including a real worker
execution with fixed-operation API HTTP disabled and `./test.sh quality api`.
Diagnose failures without blind reruns, retries, skips, or timeout increases;
report exact commands/results.
