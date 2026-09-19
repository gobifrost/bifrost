# Agent debugger API

Backend and CLI inspection surface for live, resumed, delegated, and
synthetic `AgentRun`s. The durable execution journal is the canonical
timeline; every endpoint below projects it without exposing credentials.

V1 is inspection-only (plus the pre-existing cancel/rerun operations).
There is no replay or fork from historical checkpoints — see
[Why v1 cannot replay or fork](#why-v1-cannot-replay-or-fork).

## Endpoints

All routes live on the existing AgentRun router (no second top-level
execution API). Existing detail/steps/children responses are unchanged;
`GET /api/agent-runs/{run_id}` additionally carries additive
`debug_links` (tree/timeline/snapshot/checkpoints URLs).

```text
GET /api/agent-runs/{run_id}/tree
GET /api/agent-runs/{run_id}/timeline?cursor=&limit=&kind=&attempt=&include_descendants=
GET /api/agent-runs/{run_id}/snapshot
GET /api/agent-runs/{run_id}/checkpoints?cursor=&limit=
```

- `GET .../tree` — delegation tree rooted at the run's root run.
  Single batched query (no N+1). A corrupt parent cycle yields a bounded
  `diagnostic` node instead of recursing forever. Depth is capped at 25
  (`truncated: true` when capped).
- `GET .../timeline` — ordered journal events. `limit` defaults to 50,
  maximum 200. Optional `kind`, `attempt`, and `run_id`-scoped
  `include_descendants` merge of visible descendant runs.
- `GET .../snapshot` — immutable execution-snapshot identity (prompt
  hash, model, tool/delegate references, limits) plus live
  lifecycle/lease/wake/usage/contract/completion-event state.
- `GET .../checkpoints` — checkpoint summaries oldest-first (metadata
  only; state payloads stay server-side). `limit` defaults to 50,
  maximum 200.

## Cursors

`timeline` and `checkpoints` pages return an opaque `next_cursor`.
Cursors are stable for `(run_id, sequence)` pagination: pass
`next_cursor` back as `?cursor=` to resume without gaps or duplicates.
A cursor is bound to the run it was issued for and is rejected (`422`)
on any other run; malformed cursors are also `422`.

## Visibility

Every reader reuses the exact visibility/tenant checks of the AgentRun
detail routes (`agent_run_visibility_conditions`). A user who cannot
read a child run directly cannot reveal it through a root tree or a
descendant timeline. Missing or invisible runs return `404` — never a
cross-tenant existence leak.

## Redaction

Journal payloads pass through a per-kind response allowlist (default
deny: unknown keys are dropped) plus key-name redaction of
secret-bearing values (`[REDACTED]`). Snapshots expose configuration
identity and hashes only. No debugger response contains
`caller_context`, decrypted credentials, stored authorization tokens,
lease tokens, or complete execution snapshots. The system prompt is
identified by SHA-256 hash, not content.

## Journal kinds

`model_request`, `model_response`, `model_error`, `tool_call`,
`tool_result`, `tool_error`, `delegation`, `wait`, `resume`,
`lease_recovery`, `validation`, `completion`, `checkpoint`,
`cancellation`, `timer`.

Timeline entries carry `sequence`, `kind`, run/root/parent IDs,
`attempt`, timestamp, duration/tokens when applicable, a one-line
`summary`, the redacted `detail`, and related
`operation_id`/`child_run_id`/`join_id`. Tool entries are annotated
with the durable invocation state (`planned`/`running`/`completed`/
`failed`/`uncertain`) plus reconciliation evidence when present; wait
and delegation entries carry fan-out join progress.

## Live refresh

Journal appends broadcast a bounded `journal_appended` notification
(run ID + latest sequence only, never journal content) on the existing
`agent-run:{run_id}` channel. Clients refresh through the read API.

## CLI

```text
bifrost agents run-tree RUN_ID
bifrost agents run-timeline RUN_ID [--kind KIND] [--attempt N] [--follow]
bifrost agents run-snapshot RUN_ID
bifrost agents run-checkpoints RUN_ID
```

Default output is human-readable; `--json` preserves the API contract.
`run-timeline --follow` polls with a bounded budget (default 2s
interval, 60 polls), exits on terminal state or Ctrl-C, and never
mutates or cancels the run. Lease/wait state, child/join progress,
contract errors, and uncertain tool state are shown inline in human
output.

## Why v1 cannot replay or fork

Checkpoints capture the state needed for the engine to take the *next*
safe step, not a general time machine: provider continuation
identifiers may be absent, external side effects cannot be unwound, and
an `uncertain` tool invocation must reconcile before any retry.
Arbitrary replay/fork would therefore risk duplicating real-world
effects. V1 exposes inspection, cancellation, and safe continuation of
runs the engine already made resumable; historical fork/replay is a
deferred, separately-designed capability.
