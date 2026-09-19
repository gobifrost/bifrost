# Durable Agent Runtime — operations

The durable agent runtime lets any worker claim an unfinished AgentRun after
its lease expires and continue the **same run** from the last committed
model/tool boundary. PostgreSQL is authoritative; Redis and RabbitMQ move
work and updates but never decide whether a run can continue.

## Lifecycle

Public statuses: `queued`, `running`, `waiting_child`, `waiting_children`,
`sleeping`, `completed`, `failed`, `cancelled`, `timeout`, `budget_exceeded`,
`contract_failed`, `recovery_required`.

- `waiting_child`, `waiting_children`, and `sleeping` are durable inactive
  states that consume no worker.
- `recovery_required` means a tool may have produced an external effect but
  the engine lacks an authoritative result; automatic replay is prohibited
  until a human or hook reconciles it.
- Terminal states (`completed`, `failed`, `cancelled`, `timeout`,
  `budget_exceeded`, `contract_failed`) never transition. Enforced in
  `agent_runtime/types.py:TRANSITIONS` and tested in
  `test_run_store.py`.

## Leases (crash detection, not user limits)

- Claim: `queued` without a lease, or `running` with an expired (or legacy
  absent) lease. Exactly one worker wins (`FOR UPDATE` fencing); the attempt
  counter increments per claim, not per model turn.
- Lease TTL: `AGENT_RUN_LEASE_TTL_SECONDS` (120s) in
  `jobs/consumers/agent_run.py`; heartbeat every
  `AGENT_RUN_LEASE_HEARTBEAT_SECONDS` (30s). Renewals and all writes require
  the current lease token — stale workers cannot checkpoint, journal, or
  terminalize (`LeaseMismatchError`).
- Losing a lease mid-attempt stops the attempt without terminalizing; the new
  owner continues the same run.
- Tune via environment only if crash detection lags real outages: shorter TTL
  reclaims faster but risks duplicate nudges under GC pauses (safe:
  at-least-once nudges, fenced claims).

## Checkpoints, journal, and versions

- Message codec version: `CHECKPOINT_MESSAGE_FORMAT_VERSION` in
  `agent_runtime/checkpoint_codec.py` (currently 1). Unknown versions raise
  `CheckpointDecodeError` — the run fails loudly, never restarts silently.
- A checkpoint stores the normalized Pydantic AI message history plus engine
  bookkeeping; the journal records model/tool/delegation/timer/recovery/
  validation/completion events in stable sequence and backs the debugger.
- `AgentRunStep` rows are projected from checkpoints for existing clients.
- Checkpoint commit latency is logged per boundary (`checkpoint_committed`,
  debug, no content).

## Uncertain side effects

Each tool call gets a deterministic engine operation ID
(`agent-tool:{run_id}:{tool_call_id}` uuid5), exposed as `operation_id` on
`AgentWorkflowCaller` and `MCPContext`. Reusable integration modules should
use it as an idempotency or reconciliation key.

Resume table:

| Last durable state | Resume action |
|---|---|
| Before model request | Issue model request |
| Model response committed | Execute pending calls, then model request |
| Tool planned, not started | Execute tool |
| Tool completed | Replay stored result, never re-execute |
| Tool running at lease expiry | Reconcile or `recovery_required` |
| Waiting on children | Stay inactive until the join completes |
| Sleeping | Stay inactive until `wake_at` |
| Final result committed | Finalize and publish completion event |

- `search_knowledge` is registered safe-retry (read-only). Side-effecting
  tools without a `RECONCILIATION_REGISTRY` hook fail closed with
  `uncertain_tool_no_hook` logged and the run parked in `recovery_required`.
- Provider calls may repeat when the process died before the response was
  committed (duplicated provider cost, never a duplicated side effect).

## Output contracts

Contracts belong to the invocation (`output_schema` at enqueue/delegation),
never to the Agent entity. The engine validates final JSON with its own JSON
Schema validator, allows exactly one correction turn when iteration/token
budget remains, else finishes `contract_failed` with the raw output
preserved (`contract_valid`/`contract_errors` on the run and API response).

## Delegation, fan-out, timers

- `delegate_to_<agent>` admits one independent child (`root_run_id` copied,
  child config snapshotted) and parks the parent in `waiting_child`. Child
  terminalization wakes the parent exactly once; the resumed parent consumes
  the result via `DeferredToolResults`.
- `delegate_agents` admits N children plus one `all`-join record and parks in
  `waiting_children`. Each completion updates its member idempotently; the
  last terminal child stores the ordered aggregate (requested position order,
  mixed outcomes kept) and wakes the parent once. `any`/`quorum` modes are
  rejected in v1 (stored mode column is extensible).
- Bounds live in `agent_runtime/settings.py` (`BIFROST_MAX_FANOUT_CHILDREN`
  10, `BIFROST_MAX_FANOUT_ACTIVE_CHILDREN` 20, `BIFROST_MAX_DELEGATION_DEPTH`
  5) and are journaled with each join. Cycles, unauthorized delegates, and
  over-limit requests fail closed; one suspension per model turn.
- `sleep_until` (`wake_at` or bounded `seconds` + reason, 30-day cap) parks in
  `sleeping`. The scheduler promotes due rows on database time with a fenced
  update, journals one fired entry, and republishes once. Sleeping time never
  counts toward `max_run_timeout`; cancellation still applies (waiting and
  sleeping rows cancel immediately with cascade to descendants).
- Parent cancellation cascades: queued/waiting descendants terminalize at
  once, running ones get the Redis cancel flag.

## Completion events (at-least-once)

Topics: `agent.completed`, `agent.failed` (also carries `budget_exceeded`),
`agent.cancelled`, `agent.timeout`, `agent.contract_failed`, and
`workflow.completed` beside `workflow.failed`. Payloads carry IDs, status,
timestamps, correlation, counters, and contract validity — bounded output for
success, structured error otherwise. Subscribe with `filter_expression` on
`run.agent_id`, `run.status`, `run.root_run_id`, or `correlation.*`; no new
topic syntax was added.

Terminalization marks `completion_event_pending_at` in the same transaction.
The `agent_completion_events` scheduler (every 60s) emits through
`emit_event` and stamps success, or records attempts/errors for retry.
Duplicate delivery is safe (coordinators key on run/correlation IDs). If no
event source exists for a topic, emission is a no-op success, matching
existing built-in topic semantics. Watch `agent_completion_outbox`
(max lag) for delivery health.

## Limits

- `max_iterations` (model turns), `max_token_budget` (run tokens),
  `max_run_timeout` (active seconds, `0` = disabled, exposed on Agent
  create/update/read). Budgets apply cumulatively across attempts from the
  immutable enqueue snapshot; usage ledgers are seeded from stored counters
  on resume.
- `agents.run(timeout=...)` is caller wait only. Workflow `timeout_seconds`
  scopes one tool execution. No hardcoded delegation timeout remains.
- Active time excludes `waiting_*`/`sleeping` (journal-derived); the
  scheduler still enforces total safety age on expired leases.

## Debugger (journal-backed inspection)

The journal is the debugger source of truth. REST (`tree`, `timeline`,
`snapshot`, `checkpoints` on the existing AgentRun router) and the CLI
(`bifrost agents run-tree/run-timeline/run-snapshot/run-checkpoints`)
project tree, ordered model/tool/delegation/timer/recovery/validation/
completion events, tool reconciliation state, join progress, checkpoints,
usage, and completion-event publication — never `caller_context`,
credentials, tokens, or unredacted secrets (per-kind allowlist, default
deny, plus key-name redaction).

Reads reuse the exact AgentRun detail visibility/tenant checks
(cross-tenant reads 404); tree loading is one batched query with bounded
cycle diagnostics; timeline/checkpoint pages use opaque cursors stable
for `(run_id, sequence)`. Journal appends emit a bounded
`journal_appended` hint (run ID + latest sequence) on the existing
`agent-run:{run_id}` channel. V1 is inspection-only: no replay/fork
from historical checkpoints. Full contract:
`docs/reference/agent-debugger-api.md`.

## Upgrade / rollback

Migrations (all forward-only, additive):

- `20260918_durable_agent_runtime` — lease/snapshot/checkpoint/journal/
  invocation/join tables and columns; backfills `root_run_id=id`,
  `attempt=0`, `checkpoint_sequence=0`.
- `20260918_agent_output_contract` — `contract_valid`, `contract_errors`.

Old queued rows without snapshots fail closed with a recovery reason
("predates durable execution snapshots; re-enqueue"); in-flight legacy Redis
contexts are honored once during rolling upgrades. Rollback: new columns and
tables are unused by old code paths except the consumer's snapshot gate —
downgrade the migrations to restore pre-runtime behavior. The `v1.d.ts`
client types are regenerated from the running API after contract changes.
