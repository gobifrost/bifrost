# Agent Debugger Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose one authorized, cursor-paginated debugging surface for live, resumed, delegated, and synthetic AgentRuns through REST and CLI.

**Architecture:** The durable runtime journal is the canonical timeline. Read services project tree, timeline, snapshot, checkpoint, tool invocation, join, timer, contract, usage, and terminal-event information without exposing credentials. Existing AgentRun detail endpoints remain compatible.

**Tech Stack:** FastAPI, Pydantic contracts, SQLAlchemy, Click CLI, existing authorization and agent-run WebSocket notification paths, pytest.

---

## Task 1: Define stable debugger contracts

**Files:**
- Create: `api/src/models/contracts/agent_debugger.py`
- Modify: contract export modules
- Create: `api/tests/unit/models/contracts/test_agent_debugger_contracts.py`

- [ ] Start with failing serialization tests for `AgentRunTree`, `AgentRunTreeNode`, `AgentTimelinePage`, `AgentTimelineEntry`, `AgentRunSnapshotView`, and `AgentCheckpointSummary`.
- [ ] Timeline entries expose sequence, kind, run/root/parent IDs, attempt, timestamp, duration/tokens when applicable, safe summary, redacted detail, and related operation/child/join IDs.
- [ ] Snapshot view exposes immutable configuration identity and hashes, effective limits, grants, correlation, lifecycle/lease/wake state, and current checkpoint metadata. It must not return decrypted credentials, stored authorization tokens, or unredacted secret-bearing tool arguments.
- [ ] Add `next_cursor`; cursor is opaque and stable for `(run_id, sequence)` pagination.
- [ ] Run and commit: `feat: define agent debugger contracts`

## Task 2: Build authorized read projections

**Files:**
- Create: `api/src/services/agent_runtime/debugger.py`
- Create: `api/tests/unit/services/agent_runtime/test_debugger.py`

- [ ] Reuse the exact visibility/tenant checks used by current AgentRun detail routes. A user who cannot read a child directly cannot reveal it through a root tree.
- [ ] Implement batched tree loading with no recursive N+1 query. Detect corrupt parent cycles and return a bounded diagnostic node rather than recursing forever.
- [ ] Implement cursor timeline queries over the journal with optional `kind`, `run_id`, `attempt`, and `after_sequence` filters.
- [ ] Project checkpoint summaries, tool reconciliation state, join progress, timer wake, output-contract validation, and completion-event publication status.
- [ ] Apply central redaction plus a response allowlist. Test tenant isolation, secret redaction, pagination without gaps/duplicates, and trees containing failed/cancelled children.
- [ ] Run and commit: `feat: add agent debugger read projections`

## Task 3: Add debugger REST endpoints

**Files:**
- Modify: existing AgentRun router (do not create a second top-level execution API)
- Modify: `api/src/models/contracts/agent_runs.py` only for shared links/summary fields
- Create: `api/tests/e2e/api/test_agent_run_debugger.py`

- [ ] Add:

```text
GET /api/agent-runs/{run_id}/tree
GET /api/agent-runs/{run_id}/timeline?cursor=&limit=&kind=&attempt=
GET /api/agent-runs/{run_id}/snapshot
GET /api/agent-runs/{run_id}/checkpoints?cursor=&limit=
```

- [ ] Return 404 rather than leaking cross-tenant existence. Validate `limit` with a conservative maximum.
- [ ] Keep existing detail/steps/children responses unchanged and link new data only through additive fields where needed.
- [ ] Reuse the current agent-run update channel for live refresh; emit a bounded `journal_appended` notification containing run ID and latest sequence, not full sensitive journal content.
- [ ] Add OpenAPI contract tests and commit: `feat: expose agent debugger api`

## Task 4: Add CLI inspection commands

**Files:**
- Modify: `api/bifrost/commands/agents.py`
- Modify: `api/bifrost/client.py` only if a shared helper is needed
- Create: `api/tests/unit/bifrost/commands/test_agents_debug.py`

- [ ] Add commands under the existing group:

```text
bifrost agents run-tree RUN_ID
bifrost agents run-timeline RUN_ID [--kind KIND] [--attempt N] [--follow]
bifrost agents run-snapshot RUN_ID
bifrost agents run-checkpoints RUN_ID
```

- [ ] Default output works with global output formatting. `--follow` uses bounded short polling and exits on terminal state or Ctrl-C; it never changes or cancels the run.
- [ ] Show lease/wait state, child/join progress, contract error, and uncertain tool state clearly in human output; JSON output preserves the API contract.
- [ ] Test URL construction, pagination, terminal exit, interrupted follow, and HTTP errors.
- [ ] Run and commit: `feat: add agent debugger cli`

## Task 5: Debugger verification and documentation

**Files:**
- Modify: `docs/architecture/durable-agent-runtime.md`
- Create: `docs/reference/agent-debugger-api.md` if the repository’s reference structure supports it

- [ ] Document endpoints, cursors, visibility, redaction, journal kinds, CLI examples, and why the API cannot replay/fork in v1.
- [ ] Run all debugger tests plus existing AgentRun router/CLI tests and contract-version checks.
- [ ] Verify no endpoint returns `caller_context`, credentials, complete execution snapshots, or raw secret-bearing values.
- [ ] Commit: `docs: document agent debugger backend`

