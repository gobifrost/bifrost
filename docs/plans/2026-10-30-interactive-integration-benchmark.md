# Interactive Integration Benchmark

Status: deployed/current private portal import diagnostics completed with
40 correct, traced actions each. Reviewing app result delivery is the urgent
repair; hosting confirmation, soak and recovery work remain in the plan.
The V2 browser/fake-integration fixture remains planned.

## Immediate Priority: App Result Delivery

Workflows serve as APIs for apps, so verify the complete app-facing result
path before accepting a platform latency improvement. Historical fixes
`390026147` (#483, July 14) and `dccd6f0e3` (#621, August 22) wake synchronous
callers immediately after the authoritative completion transaction commits.
That ordering survives the RabbitMQ retirement. V2 apps switched to async
submission and WebSocket/GET result delivery in `082db6c5c` (#487). Their
terminal execution notification still follows log acknowledgement/recovery,
completion-event emission and derived aggregates. This leaves an observable
post-commit dependency on bookkeeping despite the sync fix.

Move the existing terminal notification after commit and sync-result wakeup,
before follow-up bookkeeping. Preserve lease fencing, result/SDK-write/log
durability, payload shape and existing transports. Verify blocked follow-up
work cannot delay either async success or failure. One zero-job integration
test should exercise the canonical consumer with real PostgreSQL, Redis,
API WebSocket and result GET, holding metrics open while checking the result.
Do not add a second execution path, client polling or detached work.

This post-commit delivery gap is separate from the original 14.86 seconds
between durable worker timestamps minus engine duration. Fixing it does not
by itself explain that earlier interval or the 33-second outlier. Continue
recording import, engine, completion commit and app delivery independently.

The hosting benchmark needs an interactive, non-AI integration workload as well
as SDK-heavy throughput and agent mixes. Recent user-reported Halo portal
observations are preserved in the [hosting ledger](../benchmarks/2026-10-30-hosting-baselines.md).
Their separate runs are not an additive trace or a controlled current-runtime
baseline. The goal is to reduce complete action-to-result latency while
recording CPU, memory, concurrency and hosting-cost tradeoffs.

The user reports that outside-engine time was nearly eliminated before the
RabbitMQ migration and subsequent runtime changes. Treat the current large
intervals as a regression investigation. Locate the last attributable
known-good receipt and compare the same import-heavy workload across that
boundary and the later execution/import/auth changes. Keep the fast SDK-only
case as a negative control. Accepting the idle-worker memory baseline does not
accept this latency regression or justify replacing the known-good baseline.

First establish the current reproducible case, then narrow the regression
range with frozen revisions. Preserve each revision's supported execution,
SDK, schema and topology contract; do not pair a historical worker with current
authorization/dispatch behavior and attribute a compatibility failure to
performance. Record any instrumentation or topology differences explicitly.
Prioritize identifying the regression before choosing a preload optimization.

Authorized production site-read replays identified the deployed build as
`1.4.2-dev.1793214616` / `c2a116ca4`, with registry digest matching the running
API and worker. They retained about 1.94–2.20 seconds outside the engine;
the original 33-second detail case did not repeat. Selected worker logs show
about 1.82–2.01 seconds before workflow-loaded and 24 preceding module HTTP
responses. Preserve those live observations separately from the local
October 30 runtime. An exact-production-base diagnostic image must keep that
build's dependencies and authorization/schema behavior, adding only the same
opt-in trace, before making a controlled local comparison.

That exact-base local diagnostic is now complete: all 40 actions succeeded
with correct output and correlated traces. Graph worker-wall medians were
321.2 and 317.6 ms, versus 266.8 and 267.6 ms on the newer local runtime. It
does not reproduce the live seconds-long loading interval. A live overlap
group increased site residuals to about 3.0–3.2 seconds and contact residual to
5.2 seconds, but actual starts clustered rather than reproducing the original
arrival offsets. The original 33-second case remains unreproduced.

The larger captured portal graph is now also measured, with the production
Markdown dependency matched locally. Its two graph passes have worker-wall
p50/p95 697.95/837.59 and 686.49/739.99 ms on deployed `c2`, versus
538.68/648.36 and 534.72/590.97 ms on the newer runtime. Every execution in
both 40-action diagnostics succeeded with correct output and one valid trace;
cleanup was verified. These are import-only wrappers, not original workflow
body, Halo, browser or hosting-capacity measurements. Preserve both private
runs (`import-portal-prod-c2-r2`, `import-portal-current-r1`) and the earlier
missing-Markdown failure. The local improvement does not reproduce the
seconds-long production loading spans or establish a causal commit.

The pre-remote `a00bb09e` runtime supports the existing isolated deployment:
its API hosts control and creates its own image's worker Jobs, which inject a
worker-local SDK Unix socket into each child. Compare it on its own schema and
native dependencies with the same fixture and trace. Record the older worker's
memory/process cost as well as latency. An `a00` versus later `c2` difference
only brackets several changes; an adjacent post-change arm is needed before
assigning causality to remote launch.

The pre-migration [worker-memory report](../runbooks/worker-memory-reduction-results.md)
was introduced at `2399fbb049` on September 18, before worker-local SDK HTTP
(`e2be9c4c`, September 26) and RabbitMQ retirement (`73506378`, October 23).
It records 100 successful sequential synchronous jobs per workload and image,
checking both Redis result receipt and durable status. The baseline SDK case
reported end-to-end p50/p95 of 68.6/89.8 ms; the slimmer image reported
89.1/201.8 ms. These are historical whole-path observations for a small
organizations SDK call, not paired outside-engine spans or Halo measurements.
The referenced raw `/tmp/bifrost-769-clean-*` artifacts are no longer present;
the report remains evidence, with that provenance limitation.

After reproducing the current import case, compare supported revisions around
worker-local SDK HTTP, child source isolation (`0423c883`, October 17),
durable acceptance (`720c7554`), unified dispatch and
RabbitMQ retirement (`53728257`, `73506378`), API-controlled remote launch
(`f2fcd2fa`), runtime authority reads (`98fdc4a6`), and claims/refresh behavior
(`68139d0a`, `22fde0c0`). This is a candidate sequence from source history,
not a causal attribution. Do not include commits outside the measured runtime's
ancestry in a linear bisect, or run incompatible schemas as performance cases.

## Verified Timing Boundaries

The current shared execution path loads workflow source and executes its import
graph in `api/src/services/execution/worker.py` before entering
`engine.execute`. The engine starts `duration_ms` after this loading; the child
envelope in `simple_worker.py` preserves that engine duration. Loading failures
and exceptions can use broader elapsed time, so this field is not a uniform
child-wall-time measure for every outcome.

Existing durable timestamps support `created_at → started_at` queue and
`started_at → completed_at` worker-wall intervals when those fields are
present. They do not isolate dependency loading. The completion timestamp is
assigned before log staging and transaction commit; notification delivery
follows commit. Neither that timestamp nor engine duration measures browser
result availability.

The V2 `useWorkflow` path submits over REST, subscribes over the shared execution
WebSocket and reconciles results over REST. A terminal WebSocket notification
does not contain the result. The two-second polling interval is a failure
fallback; it must not be assumed to explain every two-second delay. Measure
actual transport events before assigning that delay to polling.

Workflow templates already preload the base execution engine, `bifrost`,
`httpx` and `pydantic`. Workspace modules remain fresh per execution and load
through the authenticated SDK API. The SDK selects an injected worker-local
Unix socket when present; the default thin runner uses network HTTP to the
API. The frozen current-runtime diagnostic logs already show network entry
source reads at `http://api:8000/api/sdk/modules/...`. Do not infer Unix-socket
transport from the helper name `engine_request_sync`. A warm worker therefore
does not imply that each new child inherits a warm workflow import graph.

Source history distinguishes two changes. `0423c883` moved an injected-socket
child's source reads ahead of direct Redis access, including warm sources.
`f2fcd2fa` retained that behavior for socket children and added a post-fork
marker for thin children without a socket; those children use authenticated
network source requests. It did not add another resolver request to each
existing socket import. Each distinct virtual fullname, including namespace
parents, resolves once per child; that response already contains source.
Installed dependencies resolve normally before the virtual finder. Entry
source loading is a separate request. Measure route counts and walls before
attributing an import-graph penalty to either boundary. Direct child access
to Redis or infrastructure credentials is not an acceptable optimization.

## Reproducible Workload

The first implemented slice is `m-import`, with a reduced-seed pipeline check
in `m-import-smoke`. It uses separate workflow entry files with the same small
body and output: an SDK/stdlib-only control and a synthetic namespace/package
graph loaded at module scope before engine entry. The graph intentionally
adds installed `requests`; it constructs a request object and makes no
external call. Six synthetic files, their source bytes/hash and the installed
library version are retained. Both passes use fresh execution children and
ten sequential requests per class. This is a small import diagnostic, not the
complete portal dependency graph, a capacity test or a Halo reproduction.

Run the existing harness with an explicit frozen API image and a new output
directory: `./test.sh bench run --profile m-import-smoke --backend none
--api-image <trace-image> --out <new-directory>`. Each execution must have the
expected successful output and exactly one valid correlated worker trace.
Save raw records and pod/UID logs before settling or reading final counters;
missing trace/output evidence invalidates the diagnostic. A bounded result GET
after terminal success verifies output outside the recorded durable spans.
The existing class runner now records the explicitly selected image rather
than silently deploying its default. Preserve both images and all attempts.

The initial reduced-seed smoke completed 40 correct, uniquely identified and
fully traced executions; see the [ledger](../benchmarks/2026-10-30-hosting-baselines.md).
It identifies five extra resolver calls for missing optional `requests`
dependencies, but does not reproduce the live multi-second residual. Retain
this negative result. The full-M catalog repetition also completed 40 correct,
fully traced actions, with graph worker-wall medians of 266.8 and 267.6 ms and
no seconds-long reproduction. Full-catalog and historical comparisons remain
separate observations; do not promote these diagnostics into a hosting claim.

Extend the existing benchmark with one synthetic V2 interactive fixture. Use
synthetic organizations and data, a small shared authorization/mapping module,
a lazy endpoint facade and a fresh mock client per call. Fetch declared-size
site/contact payloads from the existing in-cluster fake service, with declared
delays and bounded contact-read concurrency. Keep the HTTP-library and
workflow/shared-module versions, file counts and source bytes in the result.
The fixture is representative by shape, not a claim of identical Halo behavior.
Keep a minimal stdlib/Bifrost-only control and a same-body workflow with the
synthetic shared import graph. The supplied fast workflows have observed
outside-engine intervals of about 0.13–0.19 seconds, so a multi-second platform
constant is not a supported explanation. Compare import-only controls before
adding external-read work, then retain both shallow-list and detail-hydration
request shapes. Measure all variants on the same frozen runtime and template
state; the live examples alone are not a matched A/B.

Drive it through the actual V2 SDK and shared execution stream in one targeted
browser path. Reuse existing durable outcome reconciliation, request IDs,
resource sampling and fault gates. Record fixture build resources separately
from runtime resources. Do not introduce a second execution/status transport.

Each request needs correlated evidence for:

- Browser action, POST start/response, subscription readiness, terminal event,
  result-fetch completion and rendered result.
- Server admission and durable execution ID, dispatch/queue, child admission
  and bootstrap, entry-source fetch, import/compile execution and engine entry.
- Workflow-local authorization/mapping, fake external reads, engine completion
  and durable completion.

Use component-local monotonic durations and correlate by execution ID. Browser,
worker and database timestamps are different clock domains: do not subtract
unrelated monotonic clocks or infer a missing span by adding overlapping
durations. Retain raw spans, failures, sample counts and uncertainty. Log module
identifiers, counts and timings without source contents, credentials or caller
data. Measure instrumentation overhead and keep it identical in comparisons.

The initial child import diagnostic uses one opt-in structured event on the
existing container log stream, captured immediately by the benchmark. It adds
no telemetry HTTP request, credential refresh or background emitter to the
timed execution path. Require exactly one event correlated to each execution;
missing or duplicate trace evidence invalidates that diagnostic observation.
Record emission overhead separately from the child spans. This diagnostic
alone does not measure browser transport or parent-side queue/fork work.

## Controlled Comparisons

Keep distinct groups for fresh templates, steady templates with new execution
children and a clearly labeled same-process warm-import diagnostic. Disable
workflow result caching for measured actions. Record worker/template identity,
source/image provenance and deployment state; preserve all repetitions and
failed attempts. Report complete browser latency alongside each internal
duration, without redefining existing duration fields silently.

Compare the existing preload set against bounded stable-library candidates
only after the baseline identifies their contribution. Record p50/p95 and
uncertainty, successful throughput, CPU per correct action, template/private
memory, child peak, copy-on-write growth and safe worker concurrency. Preserve
the accepted approximately 277 MiB idle-worker baseline as a comparator.

Do not preload mutable tenant/workflow modules, sessions, credentials or caller
context into a shared template. Preserve source freshness, Solution isolation,
fresh role checks and refreshed integration tokens. Avoid making the worker's
entire generated endpoint catalog eager merely to improve one action.

Any later authorized live-provider check must identify its execution IDs,
runtime/workflow versions, payload shape and network conditions. Actual Halo
latency and variability remain separate from the reproducible platform
baseline. No production credentials, customer source or customer records enter
the public benchmark fixture.
