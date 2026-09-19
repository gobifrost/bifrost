# Durable Agent Runtime Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make each AgentRun resumable under the same Bifrost run ID, add durable child joins and timers, enforce caller-owned output contracts, and publish filterable terminal events.

**Architecture:** Persist an immutable execution snapshot, append-only journal/checkpoints, leases, and tool invocation records in PostgreSQL. A Rabbit message only wakes a consumer. The existing Pydantic AI executor resumes from serialized model messages; `CallDeferred`/`DeferredToolRequests` is the suspension seam for child runs and timers. A fresh Pydantic invocation ID after resume is an implementation detail recorded in the journal, not a new Bifrost AgentRun.

**Tech Stack:** SQLAlchemy 2, PostgreSQL JSONB, Alembic, Pydantic AI 2.35.3, FastAPI, RabbitMQ, existing event delivery system, pytest.

---

## Task 1: Pin the resumable Pydantic contract with tests

**Files:**
- Create: `api/src/services/agent_runtime/checkpoint_codec.py`
- Create: `api/tests/unit/services/agent_runtime/test_checkpoint_codec.py`
- Modify: `api/src/services/execution/autonomous_agent_executor.py`

- [ ] Write failing tests proving `ModelMessagesTypeAdapter` round-trips requests, responses, tool calls, and tool returns without changing tool-call IDs.
- [ ] Write a fake-model test in which a tool raises `CallDeferred`, the first Pydantic invocation returns `DeferredToolRequests`, and a second invocation receives the original messages plus `DeferredToolResults` and finishes.
- [ ] Assert the two Pydantic invocation IDs differ while the test’s Bifrost run ID remains one value.
- [ ] Implement a versioned codec with `encode_messages(messages) -> dict` and `decode_messages(payload) -> list[ModelMessage]`. Reject unknown format versions with a typed recovery error; never silently start over.
- [ ] Keep this adapter isolated so a future Pydantic upgrade changes one module.
- [ ] Run:

```bash
./test.sh api/tests/unit/services/agent_runtime/test_checkpoint_codec.py api/tests/unit/services/test_autonomous_agent_executor.py
```

- [ ] Commit: `test: pin resumable pydantic agent contract`

## Task 2: Add the durable persistence model

**Files:**
- Modify: `api/src/models/orm/agent_runs.py`
- Modify: `api/src/models/orm/__init__.py`
- Create: `api/alembic/versions/20260918_durable_agent_runtime.py`
- Create: `api/tests/unit/models/test_durable_agent_run_models.py`

- [ ] Write model tests for defaults, uniqueness, indexes, parent/root relationships, and JSON round-trips.
- [ ] Add nullable/backfillable AgentRun columns: `root_run_id`, `execution_snapshot`, `caller_context`, `checkpoint_sequence`, `lease_owner`, `lease_token`, `lease_expires_at`, `last_progress_at`, `attempt`, `wake_at`, `correlation`, `completion_event_pending_at`, `completion_event_emitted_at`, `completion_event_attempts`, and `completion_event_last_error`.
- [ ] Add append-only `AgentRunCheckpoint` with `(run_id, sequence)` uniqueness, runtime format version, serialized state, created timestamp, and the worker attempt/lease token that committed it.
- [ ] Add append-only `AgentRunJournalEntry` with `(run_id, sequence)` uniqueness, stable event kind, bounded JSON data, provider invocation ID, checkpoint sequence, and timestamp. Keep `AgentRunStep` as the compatibility projection.
- [ ] Add `AgentToolInvocation` keyed by engine operation ID with run/tool/tool-call identity, snapshotted schema/version, arguments, result/error, state, idempotency key, reconciliation metadata, and timestamps. Enforce one operation per `(run_id, provider_tool_call_id)`.
- [ ] Add `AgentRunJoin` and `AgentRunJoinMember`; v1 join mode is constrained to `all`, and each parent/tool-call pair is unique.
- [ ] Write a forward-only additive migration. Backfill `root_run_id=id`, `attempt=0`, and `checkpoint_sequence=0` for current rows before enforcing non-null where appropriate. Do not rewrite historical transcripts.
- [ ] Confirm exactly one Alembic head. If main has advanced, make a merge revision rather than changing someone else’s migration.
- [ ] Run model and migration tests, then commit: `feat: add durable agent runtime persistence`

## Task 3: Centralize lifecycle, lease, and checkpoint transactions

**Files:**
- Create: `api/src/services/agent_runtime/run_store.py`
- Create: `api/src/services/agent_runtime/types.py`
- Create: `api/tests/unit/services/agent_runtime/test_run_store.py`

- [ ] Define the lifecycle constants and explicit transition map from the approved spec. Terminal states may not transition; waiting states may only wake through their corresponding child/timer transaction.
- [ ] Test atomic claims with two database sessions: only one may claim a queued or expired resumable run using row locking/fencing.
- [ ] Test that lease renewals and writes require the current lease token; stale workers cannot checkpoint, journal, or terminalize.
- [ ] Implement `claim_run`, `renew_lease`, `append_journal`, `commit_checkpoint`, `transition_waiting`, `wake_run`, and `finish_run` as narrow transactional operations.
- [ ] `commit_checkpoint` must atomically append the checkpoint/journal boundary, advance `checkpoint_sequence`, project compatible `AgentRunStep` rows, and update counters/last progress.
- [ ] Never place raw credentials in journal content. Reuse existing redaction utilities before persistence.
- [ ] Run and commit: `feat: add fenced agent run state store`

## Task 4: Persist immutable enqueue snapshots and remove Redis authority

**Files:**
- Create: `api/src/services/agent_runtime/execution_snapshot.py`
- Modify: `api/src/services/execution/agent_run_service.py`
- Modify: `api/src/jobs/consumers/agent_run.py`
- Modify: `api/tests/unit/services/test_agent_run_service.py`
- Modify: `api/tests/unit/services/test_agent_run_consumer.py`

- [ ] Write tests showing an admitted run executes after Redis context deletion and after the Agent’s prompt/tools/model are edited.
- [ ] Snapshot resolved prompt, model/profile settings, tools and their schemas/versions, delegated-agent grants, system-tool grants, roles/authorization facts, and effective limits at enqueue.
- [ ] Persist invocation input, output schema, caller context, correlation, and snapshot in the same transaction that admits the AgentRun.
- [ ] Publish only `run_id` as the queue payload. Redis may still support synchronous waiter notification but may not carry required execution state.
- [ ] If Rabbit publication fails after commit, keep the run queued and record/publish a recoverable delivery error; do not mark admitted work terminal solely because a transport nudge failed.
- [ ] Preserve existing enqueue and synchronous HTTP/SDK response shapes.
- [ ] Run and commit: `feat: make postgres authoritative for agent enqueue`

## Task 5: Checkpoint model and tool boundaries during execution

**Files:**
- Modify: `api/src/services/agent_runtime/observed_model.py`
- Modify: `api/src/services/agent_runtime/toolset.py`
- Modify: `api/src/services/execution/autonomous_agent_executor.py`
- Modify: `api/tests/unit/services/test_autonomous_agent_executor.py`
- Create: `api/tests/unit/services/agent_runtime/test_durable_tool_execution.py`

- [ ] Replace end-of-run buffering with durable callback writes after each completed model response and tool result. Preserve observable step ordering.
- [ ] Before invoking a tool, persist a `planned` invocation with deterministic operation/idempotency ID; mark `running` immediately before dispatch and persist result/error before feeding it back to the model.
- [ ] Expose operation ID to workflow/system tools through execution context without requiring workflow authors to manage leases/checkpoints.
- [ ] On reclaim, completed tool results are reused. Planned-not-started calls execute. A call left `running` invokes a registered reconciliation hook; absent proof, move to `recovery_required`.
- [ ] Add a small reconciliation registry keyed by snapshotted tool identity. Existing read-only tools may declare safe retry; side-effecting tools without a hook fail closed after an uncertain boundary.
- [ ] Persist AI usage at each model boundary with an idempotent uniqueness key so restart cannot double-count committed usage.
- [ ] Run and commit: `feat: checkpoint agent model and tool boundaries`

## Task 6: Claim, heartbeat, and resume the same AgentRun

**Files:**
- Modify: `api/src/jobs/consumers/agent_run.py`
- Modify: `api/src/jobs/schedulers/execution_cleanup.py`
- Modify: `api/src/services/execution/autonomous_agent_executor.py`
- Modify: `api/tests/unit/services/test_agent_run_consumer.py`
- Modify: `api/tests/unit/jobs/schedulers/test_execution_cleanup.py`
- Create: `api/tests/e2e/api/test_agent_run_restart_recovery.py`

- [ ] Consumer claims with `run_store.claim_run`, starts a bounded heartbeat task, reconstructs execution from snapshot/latest checkpoint, and records the new Pydantic invocation ID.
- [ ] Resume uses decoded message history and any durable deferred results. It never makes a replacement AgentRun.
- [ ] The cleanup scheduler requeues expired `running` leases and due `sleeping` rows; it does not fail them merely because the original container vanished.
- [ ] Wall-clock accounting excludes `waiting_*`/`sleeping`; active execution across attempts counts toward `max_run_timeout`.
- [ ] Failure-injection E2E tests kill execution after committed model response, after planned tool, after completed tool, and while an external write is uncertain. Assert the table from the approved spec exactly.
- [ ] Run and commit: `feat: resume agent runs after worker loss`

## Task 7: Enforce invocation-owned output schemas

**Files:**
- Create: `api/src/services/agent_runtime/output_contract.py`
- Modify: `api/src/services/execution/autonomous_agent_executor.py`
- Modify: `api/src/models/contracts/agent_runs.py`
- Create: `api/tests/unit/services/agent_runtime/test_output_contract.py`

- [ ] Validate `output_schema` as JSON Schema at admission and validate final JSON against the exact stored schema.
- [ ] Prefer Pydantic AI structured output where it can represent the schema, but retain an engine-side JSON Schema validator as authority.
- [ ] On invalid output, permit exactly one correction turn only when iteration/token budget remains. Otherwise store raw invalid output plus validation errors and finish `contract_failed`.
- [ ] Apply the same contract path to delegated runs. No Agent entity gains a permanent input/output contract.
- [ ] Add response fields needed to distinguish validation failure without breaking current clients.
- [ ] Run and commit: `feat: enforce caller owned agent output contracts`

## Task 8: Make delegation an asynchronous durable system tool

**Files:**
- Create: `api/src/services/agent_runtime/delegation.py`
- Modify: `api/src/services/execution/agent_helpers.py`
- Modify: `api/src/services/execution/autonomous_agent_executor.py`
- Modify: `api/tests/e2e/api/test_agent_delegation_lifecycle.py`
- Modify: `api/tests/e2e/api/test_agent_run_children.py`

- [ ] Replace inline nested execution and `DELEGATION_TIMEOUT_SECONDS=600` with a deferred system tool that transactionally creates an independently queued child.
- [ ] Child input is the parent-selected task plus minimal durable locators (for example ticket ID), optional child output schema, and bounded correlation—not the parent transcript by default.
- [ ] Snapshot the child’s own configuration at child admission; copy `root_run_id`, set `parent_run_id`, and preserve tenant/caller authorization.
- [ ] Parent transitions to `waiting_child` without occupying a consumer. Child terminalization atomically stores the deferred result, wakes the parent once, and republishes its run ID.
- [ ] Make the wake path race-safe when a child finishes before the parent’s waiting transaction commits.
- [ ] Retain existing child listing APIs and ensure cancellation/terminal failure propagates a useful tool result rather than hanging the parent.
- [ ] Run and commit: `feat: add durable asynchronous agent delegation`

## Task 9: Add model-initiated fan-out and all-join

**Files:**
- Modify: `api/src/services/agent_runtime/delegation.py`
- Modify: `api/src/services/execution/agent_helpers.py`
- Create: `api/tests/e2e/api/test_agent_fanout_join.py`

- [ ] Expose `delegate_agents` like any other system tool. Arguments are a bounded list of child requests, each with delegated agent, task/locators, and optional output schema.
- [ ] Admit all children plus one `all` join in a single transaction, then suspend parent as `waiting_children`.
- [ ] Every child completion idempotently updates its member. The last terminal child stores a deterministic ordered aggregate and wakes the parent exactly once.
- [ ] Define bounded fan-out and nesting limits in central runtime settings; snapshot resolved values. Reject cycles and unauthorized delegates.
- [ ] Do not implement `any` or `quorum` in v1; make the stored mode extensible.
- [ ] Run and commit: `feat: add durable agent fanout joins`

## Task 10: Add durable timers

**Files:**
- Create: `api/src/services/agent_runtime/timers.py`
- Modify: `api/src/services/execution/agent_helpers.py`
- Modify: `api/src/jobs/schedulers/execution_cleanup.py`
- Create: `api/tests/e2e/api/test_agent_run_timers.py`

- [ ] Add a system tool that accepts an absolute wake time or bounded duration and a reason; it persists `wake_at`, checkpoints, and moves the run to `sleeping` via deferred-tool suspension.
- [ ] Due promotion uses database time and a fenced update, stores one deferred result, and republishes once. Early/stale schedule scans are no-ops.
- [ ] Sleeping time does not consume active runtime. Cancellation and total safety-age policies still apply.
- [ ] Run and commit: `feat: add durable agent timers`

## Task 11: Publish terminal AgentRun and workflow events reliably

**Files:**
- Modify: `api/src/services/events/builtins.py`
- Modify: `api/src/services/events/registry.py`
- Modify: `api/src/services/agent_runtime/run_store.py`
- Create: `api/src/jobs/schedulers/agent_completion_events.py`
- Modify: `api/src/jobs/consumers/workflow_execution.py`
- Create: `api/tests/unit/services/events/test_agent_completion_events.py`

- [ ] Register `agent.completed`, `agent.failed`, `agent.cancelled`, `agent.timeout`, `agent.contract_failed`, and `workflow.completed` beside existing workflow failure topics.
- [ ] Canonical Agent payload includes run/agent/root/parent IDs, status, correlation, counters, timestamps, output for success, and structured error for failure. Existing subscription `filter_expression` filters payload/correlation; no new topic syntax.
- [ ] Terminalization marks completion-event pending in the same database transaction. A scheduler outbox pass emits through existing `emit_event`, records success, and retries failures at least once. Duplicate delivery is permitted; lost terminal events are not.
- [ ] Do not publish before the terminal transaction commits.
- [ ] Run and commit: `feat: emit durable agent completion events`

## Task 12: Normalize limits and compatibility

**Files:**
- Modify: Agent ORM/contracts/router/service files owning `max_iterations`, `max_token_budget`, and `max_run_timeout`
- Modify: generated SDK/CLI contracts only through repository generators
- Modify: `api/src/services/execution/autonomous_agent_executor.py`
- Create: `api/tests/unit/services/agent_runtime/test_execution_limits.py`

- [ ] Expose `max_run_timeout` through Agent create/update/read contracts. `0` means disabled.
- [ ] Preserve `max_iterations`, `max_token_budget`, workflow `timeout_seconds`, and `agents.run(timeout=...)` names and meanings.
- [ ] Remove hardcoded child timeout. Parent and child each use their snapshotted limits; waiting time is excluded.
- [ ] Add compatibility tests for existing statuses/endpoints and ensure old queued rows without snapshots fail with a clear migration/recovery reason rather than guessing live configuration.
- [ ] Regenerate required truth/client artifacts, run DTO/contract-version guards, and commit: `fix: normalize durable agent execution limits`

## Task 13: Runtime verification and documentation

**Files:**
- Create: `docs/architecture/durable-agent-runtime.md`
- Modify: relevant operations/deployment docs
- Modify: `docs/superpowers/plans/2026-09-18-agent-platform-implementation-notes.md` if needed

- [ ] Document lifecycle, lease settings, checkpoint versioning, uncertain-side-effect handling, output contracts, delegation/fan-out/timers, event topics, and upgrade/rollback behavior.
- [ ] Add metrics/logs for claim attempts, lease recovery, checkpoint latency, waiting age, contract failures, uncertain tools, and completion-event lag without logging prompts/secrets.
- [ ] Run all runtime tests together, then the applicable backend suite. Inspect `git diff --check` and Alembic heads.
- [ ] Commit: `docs: document durable agent runtime operations`
