# Agent Evaluation Studio — backend

Reproducible synthetic evaluation for Agents: unpublished candidate
snapshots, coherent stateful tool simulation, generated test cases, and
baseline-versus-candidate comparison — all executed through the durable
agent runtime and orchestrated by one canonical PlatformJob per suite
execution. No client UI is implemented; the REST API, CLI, debugger links,
and generated contracts are the complete v1 surface.

Spec: `docs/superpowers/specs/2026-09-18-durable-agent-runtime-and-evaluation-studio-design.md`.
Plan: `docs/superpowers/plans/2026-09-18-agent-evaluation-studio-backend.md`.

## Concepts (stable language, spec R3)

Suite, case, candidate, execution, result, assertion, comparison,
simulation session, Test Designer, checkpoint, tool, completion — the API,
CLI, docs, and (future) UI use these words identically.

- **Suite**: versioned collection of cases targeting one baseline Agent.
  `draft` suites are editable; `published` suites are immutable.
- **Case**: one accepted, frozen, versioned test: invocation input,
  simulator fixture, assertions, expected/forbidden tools, output schema,
  repetitions, scoring policy, provenance.
- **Candidate**: immutable evaluation-only snapshot (base Agent revision
  plus prompt/model/tool/delegate/limit overlays). Never a published Agent,
  never attached to live relationships, never runnable in production.
- **Execution**: projection over one `agent.evaluation_suite` PlatformJob:
  suite/candidate versions, counters, terminal status.
- **Result**: per-case, per-repetition outcome with assertion results,
  baseline/candidate comparison, usage, simulator hash, and debugger run
  links — summaries, never duplicated journals.

## Synthetic-only guarantees

Studio v1 never invokes real workflow, MCP, or system tools:

- The simulator (`services/agent_evaluations/simulator.py`) has no import
  path to real integration/workflow/MCP dispatch (asserted by
  `test_simulator_has_no_real_execution_import_path`). Unknown or
  disallowed tools fail closed as visible `SyntheticToolError`s.
- Every call is validated against the snapshotted published tool schema
  before simulation; schema-valid results or controlled synthetic errors
  are the only outcomes.
- `SimulatorToolRouter` carries a proof hook: any bypass to a real
  executor raises. E2E asserts `real_calls == 0` after full engine runs.
- Production `enqueue_agent_run` rejects the reserved
  `evaluation_synthetic` trigger type; only `admit_synthetic_run` writes
  synthetic rows, and synthetic correlation never carries production
  trigger keys (`ticket_id`, `production_trigger`, …).
- Timers never wall-clock wait: `validate_synthetic_timer` enforces
  `MAX_SYNTHETIC_TIMER_SECONDS` (300s default) and the engine resolves
  admitted timers with a deterministic wake result.

## Candidate isolation

- Candidates freeze base Agent revision + overlays + content hash at
  creation (`services/agent_evaluations/candidates.py`). Later Agent/tool
  edits cannot change a snapshot (tested: byte-stable hashes).
- Creation resolves every referenced tool/delegate against objects the
  caller could attach normally; cross-tenant creation is denied; inactive
  or inaccessible tools are rejected.
- The base Agent row and its `agent_tools` / `agent_delegations`
  relationships are never touched (tested: attribute-level equality).
- Promotion is out of scope: test success never publishes configuration.
  Applying candidate changes is a separate explicit diff through the
  normal Agent update path (spec R5).

## PlatformJob lifecycle

One suite execution is one `agent.evaluation_suite` job (payload v1:
execution ID only; `jobs/platform/agent_evaluation.py`):

1. Handler loads the execution, plans case × repetition × side items in
   suite order, dispatches a bounded batch (concurrency ceiling 4, max 16),
   reports progress, then raises `PlatformJobDeferred` — the scheduler
   slot is released while synthetic runs execute.
2. Agent terminal events apply through `apply_terminal_event`
   (idempotent per side; terminal results freeze; candidate executions
   wait for both sides via `expects_candidate`).
3. `reconcile_agent_evaluation_jobs` (every 60s, registered in
   `scheduler/main.py` beside the summary-backfill reconciler) heals
   event-before-wait and lost-notification races, dispatches follow-up
   batches, and finishes deferred jobs through the shared service.
4. Cancellation marks the execution cancelled and cancels only unfinished
   synthetic AgentRuns; the shared PlatformJob cancel API is also
   requested.

Status, retry, notifications, and the `platform_job_updated` WebSocket
event all use the shared contract. Enqueue returns `202
PlatformJobAccepted` with `Location: /api/platform-jobs/{job_id}` plus
`X-Evaluation-Execution-Id`; repeated enqueues reuse the active execution
(`reused: true`). The CLI may poll the shared status endpoint with a
client-side deadline that states server work continues past expiry; the
browser must not add polling.

## Test Designer review loop

- The designer runs as an ephemeral evaluation-only snapshot (no mutable
  system Agent row) with a caller-owned JSON output contract
  (`schemas/test_designer_output.json`) and a checked-in prompt.
- Historical runs are inspiration, never replay: `redact_history` omits
  runs the caller cannot access and redacts secret-bearing values before
  the designer sees them.
- Output validation fails closed with a repair-oriented problem list
  (unknown tools/assertions, unresolved entity IDs, incoherent chains).
- Proposals deduplicate by stable signature across coverage labels
  (`success`, `failure`, `safety`, `edge`).
- Drafts persist as `accepted=False`, `enabled=False` rows and never run.
  Acceptance is explicit, reviewable, and content-idempotent (double
  accept of identical content returns 409); it freezes a new accepted
  version and reruns never regenerate it.

## Assertion semantics

Exact and predicate assertions are authoritative; budget assertions gate
on measured usage; `llm_judge` is explicitly nondeterministic, stores
judge identity/prompt version/rationale/usage, and core suites work
without it. Every outcome carries a stable code, redacted
expected/actual, and evidence journal sequence IDs. Comparison reports
regressions, improvements, unchanged failures, trajectory deltas, output
deltas, and usage deltas with a deterministic verdict — prose never gates.

## Quotas and retention

Bounds live in `services/agent_evaluations/quotas.py` (env-overridable):
fixture bytes per case (256 KiB), case versions per suite (200), designer
proposals per request (10), active executions per org (5), repetitions per
case (10), synthetic timer cap (300s), simulator records per run (2000),
evidence bytes (4 MiB). Enforcement fails closed at the API boundary and
inside the simulator.

Retention: all tables cascade from suite/candidate/agent/org deletes;
there is deliberately **no** suite/case/candidate DELETE endpoint, so
published fixtures and result evidence cannot be silently deleted through
the API. Simulation sessions and tool records inherit case/run lifetimes.
Draft rows are editable only while their suite is a draft; accepted
versions are append-only.

## CLI flows

`bifrost agent-tests` (see the generated CLI reference):
`suites-list/get/create/publish`, `cases-list/create/export`,
`candidates-create/get`, `designer-drafts/accept`, `run [--wait]`,
`status`, `results`, `compare`, `cancel`. `@file.json`/`@file.yaml`
load fixtures/assertions/overlays; Agent/tool/delegate refs resolve
through `RefResolver`; `--json` output is automation-safe and `compare`
summarizes regressions, failures, usage deltas, and linked run IDs.

## Astra UI handoff

Backend and CLI are complete with no client implementation. The UI pass
owns visual hierarchy, responsive states, accessibility, and design-system
work, and must honor the evidence-first requirements:

- **R1 candidate clarity**: base Agent + all overrides visible as one
  context (`GET /candidates/{id}` returns both).
- **R2 immediate state**: execution/result statuses are enumerable
  (`queued/running/waiting/succeeded/failed/cancelled`,
  `pending/running/passed/failed/error`).
- **R3 stable language**: reuse the vocabulary above verbatim.
- **R4 evidence before judgment**: lead with assertion deltas, tool
  trajectories, and cost/latency (`compare` output); judge prose is
  supporting material.
- **R5 explicit promotion**: never publish from test success; promotion
  is a distinct diff review through normal Agent update.

Regenerate web types against a running stack before UI work:
`cd client && npm run generate:types` (requires `./debug.sh` up).
OpenAPI digest: `.claude/skills/bifrost-build/generated/openapi-digest.md`.
