# Agent Platform Implementation Notes

Evidence log for architectural contradictions and cross-cutting hazards found
during backend implementation. Both entries below were found during final
verification, root-caused, and fixed in-tree (see commits).

## 1. RabbitMQ publisher singleton deadlocks across pytest event loops (FIXED)

**Symptom.** `./test.sh all` hung forever in
`test_agent_workflow_tool_execution.py` after `test_agent_fanout_join.py`
passed. Coroutine-stack capture showed the workflow test stuck in
`enqueue_workflow_execution → publish_message → ensure_publish_topology →
aio_pika declare_exchange → connection.ready → Lock.wait`, with the
`workflow-executions` topology lock orphaned in the held state.

**Root cause.** `RabbitMQConnection` is a process-wide singleton whose pools
bind to the first asyncio loop that touches them, but pytest-asyncio runs
each test on a fresh function-scoped loop. The fan-out tests called
`notify_parent_of_completion` outside any publish mock, so a REAL
`publish_message("agent-runs")` built the singleton pools on that test's
loop. The next test's loop reused the stale pooled connection (whose
ready-future belongs to the dead loop) and blocked forever; the
per-queue topology lock stayed held, poisoning every later publish.

**Fix** (`5e4fd37e2`).
- `RabbitMQConnection.init_pools()` records the building loop and rebuilds
  (`reset_pools()`) when the running loop differs. Single-loop worker and
  scheduler processes never trigger the rebuild. Unit test:
  `test_init_pools_rebuilds_on_loop_change`.
- The five new durable e2e files that execute runs in-process gained a
  file-level autouse fixture mocking `src.jobs.rabbitmq.publish_message`,
  so queue nudges never reach the shared stack worker mid-suite.

**Residual risk.** The same singleton pattern exists for any other
loop-bound client held process-wide. `reset_db_state` (DB) and manual
`reset_pools` call sites (`test_builtin_events.py`,
`test_workflow_scheduling_flow.py`) are the established workarounds. Any
future e2e test that publishes in-process on a pytest loop must mock the
transport or reset the pools at the loop boundary.

## 2. Pydantic AI imports break the worker import-time closure (FIXED)

**Symptom.** `test_worker_app_closure_has_no_heavyweights` failed with
`{'mcp', 'uvicorn', 'starlette'}` after the runtime work landed.

**Root cause.** In pinned `pydantic-ai==2.35.3`, even a bare
`import pydantic_ai` eagerly pulls `mcp`/`uvicorn`/`starlette`. The durable
work added top-level `from src.services.agent_runtime import ...` imports to
the agent-run consumer; importing that package runs its `__init__`, which
re-exported Pydantic AI-backed helpers (`toolset`, `budgets`,
`observed_model`, …) eagerly — dragging the whole chain into the worker
entry closure.

**Fix** (`554d6a7ab`).
- `agent_runtime/__init__.py` now imports only loop-safe helpers eagerly
  (`errors`, `types`, `settings`) and resolves everything else through PEP
  562 `__getattr__`, with a `TYPE_CHECKING` mirror so pyright still sees
  real types (keeps `quality api` green).
- The consumer imports `resume` (checkpoint codec) lazily inside
  `process_message`, mirroring the existing executor pattern.

**Rule for future work.** No module reachable from `src.worker.app` at
import time may import `pydantic_ai` (or anything that does) at module
level. Defer such imports into function bodies, as the consumer already
does for `AutonomousAgentExecutor` and `AgentExecutor`.

## Review continuation — 2026-09-19 (verification in progress)

The requested branch was reconstructed at `670d22c2d` from
`codex/durable-agent-runtime-design` into the isolated worktree
`.worktrees/durable-agent-platform-backend`. Review covered all 34 commits
following the approved design commit `7558ce1fe`, including the runtime,
debugger, Studio, documentation, and cross-cutting follow-up commits.

The review found behavioral gaps despite the original focused tests passing.
Repairs and regression coverage address lease expiry and stale ORM state,
checkpoint flush ordering with production `autoflush=False`, recovery queue
eligibility, child/timer wake races, cancellation, immutable model settings,
debugger scope/redaction/pagination, persistent synthetic dispatch, Studio
resource authorization, frozen evaluation inputs, and queued Test Designer
work. Verification is still in progress; this section is not a completion claim.

### Completion-event contract correction

The approved design specifies `agent.timed_out` and metadata-only completion
payloads. The original implementation instead used `agent.timeout`, exposed
output, and added `agent.contract_failed`. The reviewed correction restores
`agent.timed_out`, maps contract/budget failures to `agent.failed`, and excludes
output and validation values. Correlation retains bounded scalar metadata and
filters sensitive keys.

Synthetic completions use the separate `agent.evaluation.*` topic namespace.
This closes an isolation gap: publishing synthetic runs to `agent.completed`
could invoke an existing production automation subscription. Exact topic
matching keeps those subscriptions separate. Synthetic run IDs remain available
through debugger/result APIs. Outbox processing locks one row through emission,
reconciles parent wakes before marking delivery, and preserves at-least-once
semantics after a crash.

### Known baseline failures receive repairs

- The enqueue API test now owns an explicit isolated model profile. Admission
  deliberately resolves the model at enqueue, so a missing implicit primary
  assignment is not an acceptable fixture. The test waits for its queued run
  before deleting referenced configuration to prevent teardown deadlocks.
- Packaged CLI verification checks the required command groups, including
  `agent-tests`, instead of asserting the obsolete total group count.
- LLM E2E fixtures now restore previous model assignments and remove their own
  profiles/connections. An ordered 83-test reproduction covering enqueue, CLI,
  chat, model selection, media, and MCP solution tests passed. The full suite
  remains necessary to establish whether other polluters exist.

### Comparison evidence

Comparison previously indexed assertions by type/label alone. Two `tool_called`
assertions could overwrite one another and hide a regression. A reproducing
regression test failed with an incorrect `unchanged` verdict; comparison now
retains every occurrence in the frozen assertion order.

### Verification ledger

- Initial focused backend selection: 357 passed.
- Completion/debugger review selection: 51 passed, 2 failed (outbox attempt count
  and enqueue teardown race; repaired and subject to subsequent verification).
- Ordered fixture-isolation reproduction: 83 passed.
- Expanded focused selection: 374 passed, 8 failed. Failures are being resolved;
  several exposed tests still using superseded draft/model contracts.
- Dockerized API static review: pyright reported 0 errors; Ruff passed. The
  repository `quality api` command will be repeated after the full run releases
  the test-stack lock.
- Full backend gate: running. No full-suite or UI success is claimed yet.

Astra UI work starts only after backend verification. Contract changes required
by UI work must be recorded here and kept to the smallest reviewed correction.

### Additional reviewed boundaries

- Evaluation cancellation first commits a durable admission fence. A cancelled
  execution with no completion timestamp remains eligible for crash recovery of
  descendant cancellation. The reconciler also observes cancellation through
  the canonical PlatformJob API.
- Evaluation evidence uses root wall duration and separately reports provider
  duration. Tool evidence carries the run ID and journal sequence, since child
  runs have independent sequence spaces. Deferred child/timer control flow must
  not produce a failed-tool journal event.
- Synthetic clocks and record limits survive router reconstruction; invocation
  identity includes the run ID so child/provider call IDs cannot collide.
- Caller authorization is retained in a server-owned snapshot distinct from
  arbitrary caller context. Preexisting caller-bearing durable runs lacking
  that snapshot fail closed and require re-enqueue; historical APIs remain
  readable. Migration `20260919_runtime_caller_auth` follows
  `20260919_eval_hardening` and is the sole Alembic head.
- Generated OpenAPI client types and CLI/MCP skill truth were refreshed from the
  reviewed backend. No handwritten UI implementation has started.

### Final backend regression dispositions (verification pending)

The first complete review run produced 8,555 passes and 14 failures. The
failures led to fixes rather than waivers:

- Studio admission flushes a new AgentRun before inserting rows that reference
  it. New database fixtures now respect the same foreign-key ordering.
- Usage fixtures reference a real or global organization. AgentRun response
  fixtures supply persisted defaults rather than constructing incomplete ORM
  objects and relying on insert-time defaults without inserting them.
- Designer row locks explicitly lock `AgentRun`, excluding its nullable joined
  Agent relationship. The lock refreshes previously loaded identity-map state.
- Studio cancellation uses the canonical Redis signal client. Its tests cover
  both the dedicated execution route and shared PlatformJob cancellation.
- Completed evaluations remain eligible for reconciliation while their shared
  jobs are waiting. This repairs completion-before-deferral and a crash between
  the two completion commits. Invalid persisted evidence produces an explicit
  result error and failed job, rather than an indefinite wait or empty success.
- Simulator failures preserve committed clock/state progress. JSON results use
  stable key ordering across JSONB replay. Engine timers lock the shared fixture
  clock and persist invocation identity so replay cannot advance time twice.
- Engine delegation/timer calls appear in assertion evidence, deduplicated by
  run and provider-call ID; all attempt journal references remain available.
- Model-setting, gateway, SDK-update, and Studio fixtures now clean up their
  own durable resources. Studio uses the existing deterministic model server,
  avoiding provider retries that occupied consumer slots after tests ended.
- MCP file-push discovery excludes independent V2 apps with no repository path.
  A legitimate standalone app can coexist with an unmanaged workspace push;
  solution-managed repository paths remain protected.
- Candidate overlay scope checks have explicit authorization-tripwire entries:
  references must belong to the selected candidate tenant or global scope, and
  live caller role access is checked independently.

The combined focused selection reached 506 passes with one remaining fixture
snapshot expectation mismatch. That expectation was corrected to the resolved
OpenAI chat-completions configuration. Studio API tests, `./test.sh quality api`,
and a fresh `./test.sh all` are running sequentially against frozen backend code.
The full run is the remaining backend gate before the Astra UI phase.

The Studio rerun passed all 7 tests and API quality passed. The next full run
was interrupted after 134 passes and one chat/workflow failure: fixture cleanup
had initialized a process-cached Redis signal client on a function-scoped test
event loop. Cleanup now uses canonical HTTP cancellation routes in the API
process instead of directly invoking runtime transport helpers. The exposing
ordered selection passed all 39 tests:

```bash
./test.sh tests/e2e/api/test_agent_evaluation_api.py tests/e2e/api/test_agent_workflow_tool_execution.py tests/e2e/api-integration/test_mcp_gateway.py tests/unit/test_ai_model_service.py tests/unit/test_chat_model_profiles.py
```

API quality and the full backend gate are running again after that fixture fix.

### User follow-up: testing workflow and cache evidence

The user requested CLI-first testing, followed by a UI for testing; selected
historical runs should inform synthetic tool response shapes and failures. The
existing Designer route currently omits tool calls/results from selected run
history, despite the approved input contract. Correct that authorization- and
redaction-preserving projection before claiming historical inspiration works.
Accepted regression fixtures remain frozen; varied draft cases can model
different responses and failures without changing a running regression's gate.

Add a dedicated `testing` model assignment beside summarization for the Test
Designer, while preserving the evaluated candidate's frozen model choice.
Expose measured cache-read/write/input tokens and an explicitly defined cache
hit fraction in evaluation evidence. Existing runtime usage already records
cache counters, OpenRouter session affinity, and cache-prefix diagnostics;
aggregate evidence currently drops the cache counters.

Live OpenRouter testing is authorized using the personal 1Password vault.
Credentials must remain out of logs, source, and command arguments. After the
feature is implemented, compare its actual guarantees with current agent
frameworks, including OpenChamber/OpenCode caching behavior and Pydantic AI.
The latest model preference allows cheaper models for implementation while
retaining Astra for design. A reusable personal OpenCode delegation skill is
being developed separately from this repository.

Live cache smoke: two sequential requests through this worktree's
`create_agent_model` and `agent_model_settings`, using OpenRouter
`openai/gpt-4.1-mini` with the same synthetic 3,594-token input and stable
session ID, both returned the expected response. Request 1 reported zero
cache reads; request 2 reported 3,456 cached input tokens (96.16%). Both
reported zero cache writes. This is a provider-adapter smoke, not a claim
about end-to-end durable recovery or typical production cache performance.
The initial smoke script called the current Pydantic AI `usage` property
as a method; that script error was corrected before the two measured calls.
No credential values were logged or stored in repository files.

The next broad run was stopped after 2,581 passes and one model-profile merge
test failure. The test relied on whether another profile existed before its
fixture: creating a chat-enabled source could also assign `chat_default`.
The exposing condition was reproduced explicitly; the test now owns both
`primary` and `chat_default` assignments and verifies both move in the merge.

A live OpenRouter timer run exposed a strict-provider schema defect: the
unused `wake_at` property could not be null, so the model supplied an empty
timestamp alongside `seconds` and received repeated validation errors.
The schema now permits null for the unused alternative; the timer parser
continues rejecting two actual values. The added schema regression failed
before the fix. Timer, helper, and model-profile checks then passed 25 tests.

The corrected live run entered `sleeping` and its worker was restarted, but
it did not wake within the smoke's original deadline. Due timers shared the
five-minute workflow cleanup cadence. A bounded 15-second recovery task is
being added to the existing scheduler and execution queue, with publish-loss
recovery coverage. Review also found cleanup confusing total elapsed age
with the durable active timeout; a regression covers a day-old sleeping run
with a one-minute active budget. These changes remain under verification.

The live scheduler logs also exposed the primary timer-wake failure:
`wake_run(commit=False)` left an unflushed wake journal entry, so production
`autoflush=False` sessions allocated the same sequence for the fired entry.
The wake transaction now flushes before allocating that next sequence. The
production-session regression failed before the fix and passed afterward.
The broad focused follow-up passed all 197 tests, including scheduler recovery,
history authorization/redaction, testing-model assignment, cache evidence,
Studio routes, and DTO/CLI compatibility tripwires.

A fresh live OpenRouter run then traversed queued → running → sleeping →
running → completed, with the debug worker restarted during its 45-second
sleep. It retained run ID `0b1ec1e1-c9d5-4c40-8829-cdb8d1782185`, completed on
attempt 2, and recorded exactly one tool-call step. The older stuck run also
completed automatically after the fix, without manual re-enqueue. API quality
and the final full backend gate remain to be completed before UI work.

The standalone API-matched CLI failed in a clean scratch virtualenv because
its package omitted the `packaging` dependency used at startup. The package
now declares it, with a dependency regression test. Rebuilding the debug
server's CLI artifact and installing that artifact in the scratch environment
proved login, tool registration, Agent creation, suite/candidate creation, and
queued Designer invocation work without host-installed dependencies.

A live Designer exercise exposed two prompt/validation gaps. The prompt
referred to an assertion catalog absent from its input. A later draft invented
`when`/`then` fixture rule fields; validation accepted them and simulation
silently returned `{"ok": true}` even for nonmatching arguments. Unsupported
rule fields and malformed match/mutation containers now fail at save time;
the Designer receives the actual deterministic assertion contract and fixture
syntax. These are bounded corrections to the existing v1 contract, not a new
mock execution engine. The first request also mistakenly asked for an
unsupported `output_contains` assertion; that smoke input error is separate
from the product defects. Follow-up verification is required after the broad
backend run. No generated draft has been promoted automatically.

Live accepted-case execution (`68930c22-6645-4410-bc4d-d591884e9a47`) exercised
both nested mock responses and an unhandled synthetic tool failure. The
successful case passed on both sides. The failure case correctly exposed a
candidate regression: it omitted the required tool call. No real tools ran.
This deliberately varied candidate is not expected to pass every assertion.

Further review found suite mutations could race publication; they now
serialize on the refreshed suite row. Draft edits advance the version used
for optimistic concurrency, and acceptance revalidates stored fixtures and
retains historical provenance. Malformed immutable Designer output now gets
a persisted materialization error instead of retrying forever and starving
the scheduler's bounded scan. These repairs have targeted coverage pending.

The broad run exposed a stale exact schedule inventory test. Live diagnostics
confirmed the three newly registered Agent Platform schedules; its expected
set now includes recovery, completion events, and evaluation reconciliation.
The run continues to discover any additional failures before focused repairs
are verified.

Live usage inspection reproduced summary contamination: runtime checkpoints
1 and 4 consumed 724 tokens, while the automatic summary appended another
898 tokens with the old default sequence 1. Evaluation evidence included all
three rows in cache/cost/model-call totals but used the runtime scalar for
`tokens`. Summaries now reserve AIUsage sequence 0; positive sequences remain
runtime calls. Evaluation aggregates exclude summary rows and use their
observed input+output total consistently. Summaries still roll into Agent
spend, as required. The unreleased debug results created before this repair
retain their original frozen evidence; new executions verify the correction.

The CLI's shared Click dispatcher also failed to format operational
`ClickException`s, exposing a traceback for an expected failed evaluation.
It now formats these with the same nonzero exit behavior as usage errors;
a regression covers the actual dispatch boundary.

Fresh baseline-only live execution `cfe856ea-46e0-4fd5-84cf-f5d19ccd12bc`
completed through the shared PlatformJob with both cases passing. The
successful nested response consumed 682 input + 40 output = 722 runtime
tokens; the synthetic failure consumed 663 + 26 = 689. Each recorded two
model calls and zero cache reads/writes. These short runs contrast with the
separate stable long-prefix smoke's 96.16% cache-hit fraction; no universal
cache effectiveness claim is made. Both cases asserted zero real tool calls.

The completed broad backend run recorded 8,611 passes and 18 failures. All
failures have concrete dispositions: one stale scheduler inventory check;
six model-selection failures caused by an integration fixture leaking its
`Connection pressure` profile and first-profile assignments; and eleven
Solution dependency-preview failures caused by independent V2 apps without
repository source paths. The pressure module followed by the first-profile
unit test reproduced the leak (2 passed, 1 failed). A new independent-V2
preview regression reproduced the source-path ValueError alone. Repository
dependency discovery now limits its app universe to repository-backed apps;
it does not invent source paths for independent deployments. The fixture
cleanup repair and final focused gates are still pending.

OpenCode Go (`opencode-go/muse-spark-1.3-contributor`, session
`ses_f4571fe4fffeaXhSDBgu57jpbO`) implemented the bounded pressure-fixture
cleanup. Root reviewed the exact diff: function-scoped fixtures remove only
their profile/connection, restore previous model assignments, and fail visibly
on Agent cleanup errors. The concurrent-run behavior and test deadlines are
unchanged. The source-path reproduction failed before its fix as expected.
The final focused run now includes these reproductions in exposing order,
all affected model-selection tests, and the final Designer/Studio/CLI checks.

API quality passed with zero errors/warnings and clean Ruff checks. The
final focused batch passed 259 tests and found two issues in new coverage:
the Designer catalog used a tuple instead of its documented list shape, and
a monetary assertion compared decimal formatting rather than numeric value.
The catalog now returns a detached list; the cost assertion compares Decimal
values. The affected Designer/evidence tests rerun before a fresh complete
backend gate. No backend source writes are planned during that gate.

Latest backend verification commands (all through the repository harness):

```bash
./test.sh quality api
./test.sh tests/e2e/platform/test_agent_connection_pressure.py tests/unit/test_ai_model_service.py tests/unit/test_chat_model_profiles.py tests/unit/test_model_selection.py tests/unit/services/test_media_generation.py tests/unit/test_solution_dependency_walker.py tests/unit/services/agent_evaluations tests/unit/test_run_summarizer.py tests/unit/test_cli_packaging.py tests/unit/cli/test_cli_base.py tests/e2e/api/test_agent_evaluation_api.py tests/e2e/api/test_agent_evaluation_authorization.py tests/e2e/api/test_agent_evaluation_draft_mutations.py tests/e2e/api/test_agent_designer_history.py tests/e2e/platform/test_scheduler_diagnostics.py tests/unit/test_contract_version.py tests/unit/test_dto_flags.py
./test.sh tests/unit/services/agent_evaluations/test_designer.py tests/unit/services/agent_evaluations/test_evidence.py
./test.sh all
```

The first focused command reported 259 passed / 2 failed; the two causes were
fixed, and the affected Designer/evidence modules then passed all 6 tests.
The fresh full gate is currently running against the corrected source. Its
JUnit artifact must be read from
`/tmp/bifrost-bifrost-test-69c4f3b1/test-results.xml`, not the stale global host
path. Browser and client verification remain pending until Astra's UI phase.

The next full run reported 8,639 passed / 6 failed. The scheduler inventory
and independent-V2 dependency-preview regressions passed. The six remaining
failures came from a different integration fixture: with the pressure leak
removed, CLI Agent CRUD seeded a `CLI Agents Default` profile and deliberately
left it behind. CLI CRUD followed by the first-profile unit test reproduced
this second leak (1 passed, 1 failed). That test now uses the existing
`llm_config_cleanup` fixture, which owns its temporary bootstrap as well as
restoring prior assignments. A source review found no other unowned default
profile bootstrap; all profile-producing integration modules are now being
run together before the affected model tests to verify the combined order.

The combined profile-fixture order passed all 67 tests, including every
previously failing model-selection check. Exact command:

```bash
./test.sh tests/e2e/api-integration/test_mcp_gateway.py tests/e2e/api/test_agent_evaluation_api.py tests/e2e/api/test_agent_evaluation_authorization.py tests/e2e/api/test_agent_run_enqueue.py tests/e2e/platform/test_agent_connection_pressure.py tests/e2e/platform/test_agent_indexer.py tests/e2e/platform/test_cli_agents.py tests/e2e/test_ai_model_settings_api.py tests/unit/services/test_media_generation.py tests/unit/test_ai_model_service.py tests/unit/test_chat_model_profiles.py tests/unit/test_model_selection.py
```

A fresh `./test.sh quality api` followed by `./test.sh all` is running with
the final fixture correction. There are no planned backend source changes
during this run; UI work still waits for a clean full backend result.

The corrected full backend gate completed: `./test.sh quality api` passed
with zero errors/warnings and clean Ruff checks; `./test.sh all` passed all
8,645 tests, with zero failures, errors, or skips, in 35m29s. The suite emitted
2,333 warnings; passing does not imply warning-free execution. JUnit retained
at `/tmp/durable-backend-full-final5.xml`. Backend source stayed frozen during
this run. Astra UI implementation and its browser/client gates are next;
the overall feature is not complete.

### UI contract correction: Designer materialization notification

Astra identified a concrete completed-before-materialized ordering: the browser
could consume the model-completion event before draft persistence, then receive
no further hint to refetch. Reconciliation now claims the current Designer row
under an AgentRun-only lock, commits each materialization, and publishes the
existing AgentRun update for success, zero deduplicated drafts, and terminal
validation/published-destination errors. No DTO, endpoint, or transport changed.
Rollback preserves the pending marker; commit failures reset the session;
pubsub failure does not undo committed drafts. Error logging uses a stable ID
instead of accessing an expired row after rollback.

The pre-fix regression produced three missing-notification failures and exposed
the rollback logging error. The initial repair test caught and corrected a
PostgreSQL lock targeting a nullable joined relationship. The focused 48-test
batch then passed 47; its remaining published-suite test lacked its setup
assignment, which was fixed. All seven new notification regressions now pass,
including separate-session visibility, repeat-sweep idempotence, rollback,
commit failure, and pubsub failure. Existing reconciliation, completion-race,
and Designer coverage passed in the focused batch. Exact commands:

```bash
./test.sh tests/unit/jobs/platform/test_agent_evaluation_designer_notifications.py tests/unit/jobs/platform/test_agent_evaluation.py tests/unit/jobs/platform/test_agent_evaluation_completion_race.py tests/unit/services/agent_evaluations/test_test_designer.py
./test.sh tests/unit/jobs/platform/test_agent_evaluation_designer_notifications.py
./test.sh quality api
```

API quality passed (zero Pyright errors/warnings; Ruff clean). Full backend
8,645-pass evidence precedes this small correction; the full suite was not
rerun for it. Astra UI implementation continues, with browser gates pending.

### Astra UI implementation and verification in progress

Implemented integrated Agent debugger and Evaluation Studio routes using generated
contracts, existing query patterns, AgentRun events, and PlatformJob notification
updates. Original run detail/actions remain available. Studio distinguishes live
baseline from immutable candidate, keeps Designer drafts inspectable, freezes
accepted versions, compares assertion/tool/usage evidence, and requires an explicit
current-production diff before applying overlays. Added the dedicated Testing
assignment beside Summarization. Runtime counters exclude automatic summaries;
missing comparison measurements render as not recorded.

The deterministic live Designer browser journey passed selected historical-run
review, queued generation, post-materialization notification refresh, draft
inspection, and frozen acceptance. Temporary AI assignments were restored and
the disposable provider removed. Desktop/mobile captures were visually inspected.
The first full Vitest gate passed 519 files / 3,115 tests; subsequent regression
coverage exposed and repaired pending assertion presentation. The final expanded
unit gate and full browser verification are ongoing. Broad browser failures have
concrete fixes in client tests for semantic selectors and paginated organization
lookup; they are not waived. Final evidence is maintained in
`/tmp/durable-astra-ui-result.md`. No UI completion claim yet.

Astra verification handoff: final full Vitest passed 519 files / 3,121 tests;
TypeScript and lint passed (one preexisting seed-review-pack console warning).
The full browser gate ran 169 tests: 162 passed, seven failed. All client-owned
selector/pagination failures now have green focused reproductions, including the
final Files/Table policy check (3/3). The debugger live journey passes. Designer
selected-history → queued generation → materialized drafts → frozen acceptance
passed with a deterministic provider, and screenshots were visually inspected.

One parent-owned blocker remains: both synthetic AgentRuns completed at
19:10:44 UTC, while their suite execution remained running until the 19:11:25
reconciliation tick. `apply_synthetic_terminal` has no production caller; the
60-second sweep is the only completion projection. A manual visual run likewise
took 58.7 seconds to become succeeded. No browser timeout was increased and no
polling or backend edit was added. Reproduction and proposed smallest correction
are in `/tmp/durable-ui-contract-gaps.md`; complete UI evidence and changed files
are in `/tmp/durable-astra-ui-result.md`. Parent must repair/resolve this runtime
boundary, then the focused Studio journey and full browser gate must pass before
feature completion. No commits were made by Astra.

### UI-discovered backend correction: prompt synthetic completion projection

Astra's browser reproduction proved that both synthetic roots were terminal while
Studio's authoritative execution remained running until the 60-second sweep.
`apply_synthetic_terminal` existed but had no production caller. The consumer now
invokes it after its processing scope, using a fresh committed-state read and
closing that session before projection. Only terminal synthetic roots with valid
execution/case/side/repetition correlation qualify. Production runs, Designer
runs, delegated children, and nonterminal waits are excluded. Failures preserve
the committed run and leave the existing reconciliation recovery intact. No
endpoint, DTO, scheduler interval, browser timeout, or polling contract changed.

OpenCode `opencode-go/muse-spark-1.3-contributor` contributed only the consumer and
its new sibling tests (session `ses_f44e5de21ffeUMCDl8wCTrCXlF`); parent review
added database integration coverage, strict nonnegative integer repetition
validation, and committed-fixture cleanup. Initial focused failures exposed a
missing parent FK fixture and in-place JSON edits that SQLAlchemy did not persist;
both fixtures were corrected. Additional validation tests reproduced truncation
of fractional repetition values and acceptance of negative values; the helper
now rejects these rather than coercing them. No retries or skips were added.

The database integration test proves that a terminal consumer delivery completes
the evaluation/shared job without calling reconciliation, missing persisted
evidence fails explicitly, and duplicate delivery does not increment completion
twice. Final focused and browser verification results are recorded below when
complete. The full 8,645-test backend gate predates these two UI-discovered
notification/projection corrections; their affected surfaces receive focused
verification and the full browser gate exercises the resulting live path.

Final backend correction verification:

- `./test.sh tests/unit/services/test_agent_run_evaluation_completion.py tests/unit/services/test_agent_run_consumer.py tests/unit/jobs/platform/test_agent_evaluation.py tests/unit/jobs/platform/test_agent_evaluation_completion_race.py tests/unit/test_import_hygiene.py`: **73 passed**, zero failures/errors/skips. Retained JUnit: `/tmp/durable-terminal-bridge-focused-verified.xml`.
- `./test.sh quality api`: **0 errors, 0 warnings**, Ruff all checks passed. Log: `/tmp/durable-terminal-bridge-quality-final.log`.
- `git diff --check`: passed. UI browser verification resumes after this reviewed correction.
