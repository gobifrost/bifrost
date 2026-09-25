# Workflow execute and scheduled cancel SDK local transport

Executor: OpenCode. Reviewer: Codex. Work only in this worktree; no commit,
push, main merge, or primary checkout edit. Read AGENTS.md and the aggregate
SDK transport plan. The reviewer will merge the shared workflow execution
service before this stage starts.

Migrate `bifrost.workflows.execute` and `workflows.cancel` to named parent-
local operations; external SDK callers retain HTTP. `workflows.get` delegates
to `executions.get` and is covered by execution reads. The dispatcher must
call `api/shared/sdk_workflow_execution.py`, also used by the HTTP routes.
Do not copy workflow resolution, Solution policy, schedule, `run_as`, queue,
or cancellation business rules into the dispatcher. Preserve `execute`'s
fire-and-forget execution ID, `scheduled_at`/`delay_seconds` validation,
org override and Solution target semantics, and `cancel`'s 404/403/409
behavior and guarded update race. Preserve the SDK's existing exception and
model mapping.

Derive the execution actor, org, effective Solution caller, and capability
from the parent dispatch context. Reject forged child actor/org/Solution
claims exactly as HTTP auth would; a requested override still needs the
shared service's authorization check. A local operation cannot retry over
HTTP after enqueue or cancellation. Use the parent's pooled DB session and
preserve the service's commit/queue ordering; never open a child DB connection.

Test HTTP/local parity for normal enqueue, scheduled enqueue/cancel,
duplicate/late cancel, cross-org and `run_as` denial, sealed/inactive Solution
denial, malformed arguments, external HTTP unchanged, and one real fork with
fixed-operation HTTP disabled. Assert durable scheduled row and queue status.
Run focused `./test.sh` and `./test.sh quality api`; diagnose failures
without retries, skips, or timeout inflation.
