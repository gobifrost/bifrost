# Agent run SDK shared service

Executor: OpenCode. Reviewer: Codex. Work only in this worktree. Do not
commit, push, merge, or touch the primary checkout.

Extract business behavior for the fixed Python SDK agent operations
`agents.enqueue` and `agents.get_run` from
`api/src/routers/agent_runs.py` into shared application service code under
`api/shared/`, used by both HTTP handlers and a future engine-local parent
dispatcher. `agents.run` composes enqueue + wait; `agents.wait` polls
get_run, so these two server operations cover the facade. Keep the HTTP
handlers thin. Reuse existing `enqueue_agent_run`, run visibility rules,
AI usage, response DTO construction, and Solution active guard.

Preserve exact name lookup, paused response/status, inactive Solution 409,
run queue payload and actor attribution, org scope, run visibility 404,
steps/usage/totals response, error precedence and transaction behavior.
Service parameters must be trusted explicit principal, session and
validated request data, never FastAPI Request or child claims. The future
local dispatcher will provide token-equivalent workflow-engine or service
identity. Do not change `/execute`, agent definitions, backfill, summaries,
or unrelated run endpoints. Do not add local transport yet.

Add focused service tests for enqueue success/paused/inactive Solution and
get-run visible/hidden/missing/usage. Preserve HTTP tests. Run relevant
`./test.sh` unit/E2E tests and `./test.sh quality api`; report exact
commands/results. Diagnose failures without blind reruns, retries, skips,
or timeout increases.
