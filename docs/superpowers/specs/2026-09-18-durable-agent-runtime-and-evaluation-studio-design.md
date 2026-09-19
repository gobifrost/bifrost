# Durable Agent Runtime and Evaluation Studio

## Status

Approved direction from the September 2026 Bifrost ticket-agent review. This
document defines the product and architecture. A separate implementation plan
will break it into backend-only commits suitable for an overnight agent, then
end with an Astra UI handoff.

## Problem

Bifrost already records AgentRuns, executes agents through RabbitMQ, exposes
workflow tools, records steps and usage, supports cancellation, and represents
delegated runs. The active autonomous-agent loop is nevertheless tied to one
worker process:

- run context is read from Redis;
- model and tool progress is buffered until the run finishes;
- a worker claims only `queued` rows;
- an expired `running` row is terminalized rather than resumed;
- delegated agents execute inline under a hardcoded ten-minute timeout;
- caller-provided output schemas are prompt guidance, not enforced contracts;
- AgentRun completion does not produce a durable, filterable platform event.

This prevents reliable long-running ticket ownership, durable delegation,
parallel specialist work, and trustworthy prompt regression testing. The
platform also lacks a native place to evaluate an unpublished candidate Agent
configuration against coherent synthetic tool behavior.

## Outcomes

The work delivers three connected capabilities:

1. **Durable Agent Runtime.** Another worker can claim an unfinished AgentRun
   after its lease expires and continue from the last committed model/tool
   boundary.
2. **Durable delegation, fan-out, and timers.** Delegation is an ordinary
   system-tool call backed by independent child AgentRuns. A parent can wait
   durably for one child, all children in a fan-out, or a scheduled wake-up.
3. **Agent Evaluation Studio backend.** A user can create an unpublished
   candidate Agent snapshot, attach published tools only for that test
   session, generate coherent synthetic cases with a Test Designer agent, run
   baseline-versus-candidate suites, and inspect every execution through a
   common journal/debug API.

The first delivery is backend and CLI only. UI implementation is explicitly
reserved for a later Astra pass using the established Bifrost design system.

## Non-goals

- Human approval wait states.
- Geographic multi-region scheduling.
- A2A protocol adapters.
- Arbitrary replay or fork from historical checkpoints.
- Resuming in the middle of a provider request, generated token, or arbitrary
  Python workflow function.
- Real external tool execution from Studio v1.
- Replacing Bifrost workflows or the underlying model/tool runner.

## Core invariants

1. PostgreSQL is authoritative for every admitted AgentRun. Redis and RabbitMQ
   transport work and updates but never define whether a run can continue.
2. One worker owns a live AgentRun lease at a time. An expired lease makes the
   same run claimable; it does not create a replacement run.
3. A committed model response or tool result is never repeated after restart.
4. An external side effect whose outcome is unknown is reconciled before it is
   retried.
5. Agent configuration is snapshotted at enqueue. A resumed run does not
   silently adopt a changed prompt, model, tool set, delegation set, or budget.
6. Output contracts belong to the invocation. Agents do not declare mandatory
   input or output schemas.
7. Delegation and fan-out happen only when the model calls the corresponding
   system tool.
8. Studio candidate overrides and simulated tools cannot mutate the published
   Agent or invoke real external tools.
9. Completion events are at-least-once. Every consumer is idempotent.

## Durable AgentRun model

### Run lifecycle

The public lifecycle uses familiar language:

- `queued`
- `running`
- `waiting_child`
- `waiting_children`
- `sleeping`
- `completed`
- `failed`
- `cancelled`
- `timeout`
- `budget_exceeded`
- `contract_failed`
- `recovery_required`

`waiting_child`, `waiting_children`, and `sleeping` are durable inactive states
and consume no worker. `recovery_required` means a tool may have produced an
external effect but the engine lacks an authoritative result; automatic replay
is prohibited until reconciliation resolves it.

### AgentRun additions

The existing `AgentRun` remains the aggregate root. Add or formalize:

- `root_run_id`: root of a delegation tree; a top-level run stores its own ID
  and every descendant copies it;
- `execution_snapshot`: immutable prompt, model/profile settings, tool and
  delegated-agent references, system-tool grants, and resolved limits;
- `checkpoint_sequence`: latest committed checkpoint number;
- `lease_owner`, `lease_expires_at`, and `last_progress_at`;
- `attempt`: worker-claim attempt count, not model-turn count;
- `wake_at`: populated only while sleeping;
- `correlation`: bounded JSON metadata used by coordinators and event filters;
- `completion_event_pending_at`, `completion_event_emitted_at`, publication
  attempt count, and last publication error for at-least-once delivery.

The existing `input` and `output_schema` columns remain the durable invocation
input and caller-requested result contract. Enqueue must stop depending on
Redis for their recovery.

### Checkpoints and journal

Create append-only checkpoint and tool-invocation records rather than placing
an ever-growing transcript in the AgentRun row.

An AgentRun checkpoint contains the runtime state required for the next safe
step:

- normalized conversation/model items;
- active agent within the run;
- remaining and consumed budgets;
- pending model response or tool calls;
- pending join or timer reference;
- provider continuation identifiers when available;
- runtime format version.

The execution journal records model requests/responses, tool calls/results,
delegation, waits, resumes, lease recovery, validation, and completion in a
stable sequence. It powers both restart recovery and the debugger.

A durable tool invocation records:

- provider tool-call ID and engine operation ID;
- tool identity and snapshotted schema/version;
- arguments;
- `planned`, `running`, `completed`, `failed`, or `uncertain` state;
- result or error;
- idempotency key;
- start and completion timestamps.

The current `AgentRunStep` API remains compatible. It may be backed by the new
journal or projected from it, but existing run-detail clients must continue to
work during migration.

### Claim and resume

The queue message is a nudge containing the run ID. A consumer atomically
claims either:

- a `queued` run without a lease; or
- a resumable `running` run whose lease expired.

It increments `attempt`, loads the immutable execution snapshot and latest
checkpoint, establishes a lease, and proceeds from the next unfinished
boundary. Meaningful progress renews the lease. A scheduler requeues expired
leases; it does not terminalize them solely because their original worker
disappeared.

Resume behavior is deterministic at committed boundaries:

| Last durable state | Resume action |
|---|---|
| Before model request | Issue model request |
| Model response committed | Process its tool calls/final output |
| Tool planned, not started | Execute tool |
| Tool completed | Feed stored result to model |
| Tool running when lease expired | Reconcile or mark uncertain |
| Waiting on children | Remain inactive until join condition is satisfied |
| Sleeping | Remain inactive until `wake_at` |
| Final result committed | Finalize and publish completion event |

Provider calls may be repeated if the process dies before their response is
committed. That can duplicate provider cost but cannot duplicate a committed
tool side effect.

### Side-effect safety

The runtime generates one operation ID for each tool invocation and makes it
available to workflow tools. Reusable integration modules should use that ID
as an idempotency key or reconciliation key where possible.

If a process disappears while a write is in flight:

1. mark the invocation `uncertain` when the lease is reclaimed;
2. invoke a tool-specific reconciliation hook when one exists;
3. persist a recovered result when the external effect is found;
4. execute the tool only when reconciliation proves it did not occur;
5. otherwise move the run to `recovery_required` with actionable evidence.

Workflow authors do not implement checkpoints or leases. Workflow/tool owners
only provide idempotency or reconciliation behavior for side-effecting
operations that require automatic replay safety. Existing tools without such a
hook remain usable but fail closed when their outcome is uncertain.

## Execution limits

Keep existing public names where possible:

- `max_iterations` remains the model-turn limit;
- `max_token_budget` remains the run token budget;
- `max_run_timeout` remains the optional active-run safety limit and becomes
  writable through normal Agent contracts;
- `agents.run(timeout=...)` remains the caller wait duration and never cancels
  the AgentRun;
- workflow `timeout_seconds` remains scoped to a workflow/tool execution.

`max_run_timeout=0` means disabled, consistent with workflow timeouts. Remove
the hardcoded delegated-agent timeout. Inactive `waiting_*` and `sleeping`
periods do not consume active runtime.

Leases are operational crash-detection settings, not user-facing Agent limits.

## Invocation-owned output contracts

An Agent has no required input schema or permanent output schema. A workflow or
calling agent may pass `output_schema` for a particular invocation.

When supplied:

1. the runtime requests structured output;
2. the runtime parses and validates against the exact caller schema;
3. one bounded correction turn is attempted when the remaining iteration and
   token budgets permit it; otherwise validation fails immediately;
4. valid output completes normally;
5. invalid output is preserved and the run becomes `contract_failed`.

The same behavior applies to child-agent calls. A delegation task may include
an optional output schema chosen by the parent for that child invocation.

## Delegation and fan-out

### Single delegation

Existing `delegate_to_<agent>` tools remain visible to the model. Internally,
each call creates a queued child AgentRun rather than running a nested executor
inline. The parent checkpoints the pending tool call and enters
`waiting_child`. Child completion satisfies the pending tool result and makes
the same parent AgentRun claimable again.

Authorization, organization scope, depth limits, cancellation, and run-tree
visibility remain enforced.

### Fan-out system tool

Add a normal model-callable system tool, `delegate_agents`, whose request
contains:

- a bounded list of child tasks;
- target delegated agent for each task;
- task instruction and optional invocation-specific output schema;
- join configuration;
- failure policy.

V1 supports `join.mode = "all"`. The data model must leave room for `any` and
`quorum` without requiring a migration, but those modes are not enabled until
their cancellation and partial-result semantics are separately tested.

The engine creates all authorized children, persists one join record and its
members, checkpoints the parent, and enters `waiting_children`. Child events
update the join idempotently. When all children finish, the parent is queued
and receives one ordered tool result containing each child's run ID, status,
validated output, and error.

Fan-out has platform and root-run limits for child count, concurrency, depth,
and aggregate token usage. Child runs keep their own Agent limits. Parent
cancellation cascades to unfinished descendants.

## Durable timers

Add a normal system tool for a model-requested wake-up. It accepts a future
time and a bounded reason, checkpoints the tool call, sets `wake_at`, and moves
the run to `sleeping`. The scheduler makes the same AgentRun claimable when due
and the tool returns a deterministic “timer fired” result.

Ticket appointments may continue to dispatch new runs through the ticket
coordinator. The timer primitive exists for an already-running agent that must
continue the same logical run later; it is not a replacement for Halo
appointments or event schedules.

## Completion events

Expose status-specific built-in topics consistent with workflow language:

- `agent.completed`
- `agent.failed`
- `agent.cancelled`
- `agent.timed_out`

Add `workflow.completed` to complement the existing `workflow.failed` topic.
Other non-success Agent statuses map to `agent.failed` with their exact status
in the payload unless a dedicated public topic is justified later.

Event payloads are bounded and contain identifiers, status, timestamps,
correlation metadata, and contract validity—not the full run output. Consumers
fetch the authoritative run.

Event publication is at-least-once. A terminal AgentRun remains discoverable
as needing publication until the built-in event is emitted. A scanner retries
unpublished completion events, and subscriber filters select Agent ID,
correlation type, ticket ID, root run, or status as needed. Duplicate events
are safe because coordinator workflows use run/correlation IDs as idempotency
keys.

## Debugger backend

The durable journal is the debugger source of truth. Backend and CLI surfaces
must expose:

- the run and descendant tree;
- the immutable execution snapshot;
- ordered model, tool, delegation, timer, recovery, and completion events;
- tool arguments, results, durations, and uncertain/reconciled state;
- checkpoints and worker claim/lease history;
- token, iteration, latency, and cost totals;
- output-schema validation attempts and failures;
- completion-event publication and deliveries.

V1 debugger operations are inspection, cancellation, and safe continuation of
a run already made resumable by the engine. Historical fork/replay is deferred.
Large payloads are paginated and sensitive tool fields follow existing log
redaction rules.

## Agent Evaluation Studio backend

### Candidate Agent snapshots

A candidate is an immutable, persisted test-run snapshot derived from a
published Agent plus optional overrides:

- prompt;
- model/profile and model settings;
- published workflow tools attached only for this candidate;
- delegated agents;
- system-tool grants;
- budgets and caller-provided output schema.

Creating or running a candidate does not create a second published Agent and
does not update the real Agent's relationship tables. Every test result keeps
the exact candidate snapshot and base-Agent revision/hash. Promotion is a
separate explicit diff applied through the normal Agent update/deploy path.

### Suites, cases, and executions

Persist versioned Agent test suites and cases. A case contains:

- initial invocation input;
- simulator initial state;
- synthetic tool behavior;
- exact, predicate, and semantic assertions;
- expected and forbidden tool calls;
- optional output schema;
- repetition and scoring policy;
- provenance, including generated-from run IDs when applicable.

Suite execution is user-initiated durable platform work and therefore uses the
existing `PlatformJob` infrastructure rather than a feature-specific worker,
job table, polling loop, or WebSocket channel. The PlatformJob fans out bounded
case repetitions into AgentRuns using the same runtime in simulation mode and
aggregates their results.

Results record pass/fail by assertion, malformed output, tool differences,
tokens, turns, latency, model, cost, and links to the common debugger journal.
CLI and future GUI consume the same APIs.

### Stateful synthetic tool simulator

Studio v1 never invokes real workflow tools. It presents the candidate with
the real published tool schemas while routing calls to a deterministic
simulator.

Each case owns coherent mutable fixture state and a set of handlers. Handlers
match tool identity and argument predicates, return data, and may mutate that
state. Consequently a synthetic `create_ticket` can allocate a ticket ID and
insert a ticket into state, and a later `get_ticket` for that ID returns the
created ticket. Unhandled calls fail closed and become visible test failures.

The simulator records the same tool journal shape as production execution so
the model loop, contracts, delegation, fan-out, budgets, debugger, and scoring
exercise the real runtime rather than an imitation loop.

### Test Designer agent

A dedicated Test Designer agent generates **proposed** cases from authorized
sources:

- selected Agent prompt/configuration;
- published tool schemas;
- existing suites;
- explicitly selected historical AgentRuns and tool results;
- user requirements and examples.

It produces initial state, stateful handlers, assertions, expected/forbidden
behavior, and provenance. Historical values may inspire realistic shapes but
must pass existing authorization and secret/redaction boundaries. Generated
cases remain drafts until accepted. Once accepted, fixtures are frozen and
reproducible; the evaluated agent never defines its own passing criteria.

An exploratory generative simulator may be considered later, but its output
cannot be a regression gate until captured and approved as a fixed case.

### Assertion model

Support:

1. exact assertions for tool identity, counts, ordering, arguments, and output;
2. predicate assertions over tool arguments, simulator state, and final output;
3. semantic rubric assertions evaluated by a separately configured judge;
4. budget assertions for turns, tokens, latency, and estimated cost.

Exact and predicate failures are authoritative. Semantic scoring records judge
identity, prompt version, reasoning-independent evidence, and score; suites may
require repetitions or thresholds to reduce variance.

### CLI surface

The CLI must support listing suites, generating draft cases, validating cases,
creating candidate snapshots, running suites, watching/polling the shared
PlatformJob, comparing baseline and candidate, and exporting machine-readable
JSON/JUnit results. Exact command names are selected during implementation to
match existing CLI conventions and DTO parity rules.

## Studio UX requirements for the Astra handoff

The backend does not implement client UI. It must nevertheless return the data
needed for an evidence-first interface.

- **R1 — Candidate clarity:** the base Agent and all candidate overrides must
  be visible as one coherent context; users must never wonder whether an edit
  is live. This protects limited working memory by removing cross-screen state
  reconstruction. **Citations:** Cowan (2001, 2010) for working-memory capacity.
- **R2 — Immediate state:** queued, running, waiting, recovered, passed, and
  failed states must be visually classifiable before detailed trace reading.
  **Citations:** Lindgaard et al. (2006) for rapid first impressions.
- **R3 — Stable language:** GUI and CLI must use the same run, suite, case,
  candidate, checkpoint, tool, and completion vocabulary. **Citations:** Alter
  and Oppenheimer (2009) for processing fluency.
- **R4 — Evidence before judgment:** comparisons lead with assertion deltas,
  tool-call changes, and measured cost/latency; model-written summaries are
  supporting material. **Citations:** Seckler et al. (2015) for perceptual trust.
- **R5 — Explicit promotion:** applying candidate changes is a distinct,
  reviewable action showing the exact production diff. Test success never
  silently publishes configuration. **Citations:** Hertwig and Erev (2009) for
  decisions grounded in experienced evidence.

Astra owns the visual hierarchy, interaction details, responsive states,
accessibility, and final design-system implementation after backend APIs and
generated client types are stable.

## Security and access

- Candidate snapshots may reference only Agents, profiles, workflows, MCP
  connections, and other resources the initiating user can access.
- Simulation mode provides no execution path to real workflow, MCP, or system
  tools except the simulator and engine-owned delegation/timer primitives
  explicitly enabled for the case.
- Historical-run fixture generation requires access to every source run and
  applies log redaction before the Test Designer sees material.
- Organization and Solution scope are preserved on suites, candidates,
  PlatformJobs, and AgentRuns.
- Test results cannot elevate a published Agent's tool grants.
- Tool and output payloads retain existing size limits and secret handling.

## Compatibility and migration

- Existing SDK signatures and `agents.run(timeout=...)` remain compatible.
- Existing AgentRun history remains readable; old runs simply lack resumable
  checkpoints and immutable configuration snapshots.
- Existing single-delegation tool names remain stable.
- Existing run-step APIs continue serving old clients during journal migration.
- Existing workflow timeouts and execution infrastructure are unchanged.
- The uncommitted `bifrost-tests-poc` worktree is not a dependency and must not
  be modified or copied wholesale. Its generic Tests surface and ephemeral
  source-override ideas may be reconciled after its ownership and state are
  reviewed.

## Testing strategy

### Runtime unit and integration tests

- Atomic claim permits one worker and rejects concurrent owners.
- Expired lease reclaims the same AgentRun and increments `attempt`.
- Every model/tool crash boundary resumes at the correct next operation.
- A committed tool result is never executed twice.
- An uncertain side effect reconciles or enters `recovery_required`.
- Configuration changes after enqueue do not affect a resumed snapshot.
- Caller output schema succeeds, corrects once, or ends `contract_failed`.
- Caller wait timeout leaves the AgentRun active.
- Cancellation cascades through child runs and joins.
- Sleeping runs wake once and resume the same run.

### Delegation and fan-out tests

- Single delegation uses an independently queued child AgentRun.
- Parent consumes no worker while waiting.
- Duplicate child-completion events satisfy a join only once.
- `all` join preserves requested child ordering and mixed outcomes.
- Fan-out authorization, maximum children, depth, and aggregate budgets fail
  closed.
- Container restart during parent, child, and join completion resumes safely.

### Event tests

- Every success path emits `agent.completed` once or more but is consumed once.
- Failure, cancellation, timeout, and workflow completion topics use consistent
  names and bounded payloads.
- Subscription filters prevent unrelated AgentRuns from invoking the ticket
  coordinator.
- Publication recovery emits an event missed during process failure.

### Studio tests

- Candidate overrides never mutate the base Agent or its relationships.
- Simulation rejects an unhandled tool and cannot reach a real dispatcher.
- Stateful `create` followed by `get`, `update`, and `list` observes coherent
  fixture state.
- The Test Designer produces drafts with provenance and cannot auto-approve.
- Baseline and candidate use identical accepted cases and repetition policy.
- Exact/predicate/semantic/budget assertions report independently.
- PlatformJob cancellation stops undispatched repetitions and cancels active
  test AgentRuns.
- CLI JSON/JUnit output represents the same stored result as the API.

Focused tests run after every commit. Before handoff, the implementing agent
runs affected unit and E2E suites, API quality checks, migration checks, DTO/CLI
parity checks, and `./test.sh pre-pr` when the complete candidate is practical.
Any unavailable or failing suite is recorded exactly rather than waived.

## Delivery boundaries

### Backend agent owns

- Database migrations and ORM/contracts.
- Claim, lease, checkpoint, journal, resume, and recovery services.
- Durable delegation, fan-out/all join, and timers.
- Output-schema enforcement and completion events.
- Debugger APIs and CLI.
- Evaluation Studio persistence, simulation, Test Designer integration,
  PlatformJobs, APIs, SDK/CLI, generated schemas, and backend tests.
- Documentation and a final evidence-backed handoff.

### Backend agent does not own

- React components, routes, navigation, visual design, screenshots, or
  Playwright coverage for the new Studio/debugger.
- Reworking or deleting the existing dirty `bifrost-tests-poc` worktree.
- Production deployment or migration execution.

### Final handoff

The backend agent ends with a copy-ready prompt for the reviewing agent. That
prompt must identify commits, migrations, changed contracts, test evidence,
known gaps, and risky areas; request validation and repair before continuation;
and explicitly direct the reviewer to delegate all Studio/debugger UI design
and implementation to an Astra agent using Bifrost's design system and the UX
requirements in this specification.

## Acceptance criteria

1. Kill an autonomous-agent worker after a committed tool result; another
   worker claims the same AgentRun and continues without repeating the tool.
2. Kill a worker while an external tool is uncertain; the run reconciles or
   stops in `recovery_required` without blind replay.
3. A model calls `delegate_agents` with multiple tasks; independent child runs
   execute concurrently, the parent consumes no worker while waiting, and the
   same parent resumes with an ordered joined result.
4. A sleeping run resumes once at its requested time.
5. Caller-provided output schemas are enforced for direct and delegated calls.
6. Filtered `agent.completed` events restart only the intended coordinator and
   duplicate delivery causes no duplicate work.
7. A Test Designer-generated case is reviewed and accepted, then produces
   coherent synthetic create/read/update behavior across repeated baseline and
   candidate runs.
8. Candidate prompt/model/tool overrides never modify the published Agent.
9. Debugger APIs reconstruct the full parent/child, model/tool, wait/recovery,
   validation, and completion timeline from durable records.
10. Backend and CLI are complete and verified without new client implementation;
    the final handoff explicitly assigns UI work to Astra.
