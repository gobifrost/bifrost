# Shared workflow execute and scheduled cancel service

Executor: OpenCode. Reviewer: Codex. Work only in this worktree. Do not
commit, push, merge, or touch the primary checkout. Read AGENTS.md, the
aggregate SDK transport plan, and the current workflow/execution tests.
The reviewer will merge the completed execution-read service before this
stage begins.

Extract the business orchestration of `POST /api/workflows/execute` and
`POST /api/workflows/executions/{execution_id}/cancel` from
`api/src/routers/workflows.py` into `api/shared/sdk_workflow_execution.py`
(or one equivalently scoped shared module). Keep the HTTP handlers thin and
preserve their request/response DTOs, statuses and error details. This stage
does not wire a local transport; a later local dispatcher will call the
same service.

Preserve all execution forms, not just the SDK's async path: inline code,
data providers, transient/sync, cache hit, normal queue dispatch, scheduled
execution with `scheduled_at`/`delay_seconds`, Solution scope and inbound
policy, lookup and role checks, org override, `run_as`, creator/audit fields,
status/result visibility, and WebSocket publication ordering. Scheduled
cancel must retain its status-guarded UPDATE race and 404/403/409 precedence.
Pass a trusted server context/principal to the shared service; never build
one from user-supplied request fields. Keep DB and queue transaction order
identical and remove route-only helpers that become dead after extraction.

Add targeted unit tests for the shared service decisions and E2E tests for
normal enqueue, scheduled enqueue/cancel, Solution denial, cross-org/run_as
denial, and HTTP error behavior. Run relevant existing workflow tests,
`./test.sh quality api`, and explain any warning/failure. No blind retries,
skips, or timeout inflation. Keep the stage limited to shared service,
workflow router, and focused tests; no SDK child/dispatcher changes.
