# Interactive Integration Benchmark

Status: source investigation and measurement plan; not implemented or measured.

The hosting benchmark needs an interactive, non-AI integration workload as well
as SDK-heavy throughput and agent mixes. Recent user-reported Halo portal
observations are preserved in the [hosting ledger](../benchmarks/2026-10-30-hosting-baselines.md).
Their separate runs are not an additive trace or a controlled current-runtime
baseline. The goal is to reduce complete action-to-result latency while
recording CPU, memory, concurrency and hosting-cost tradeoffs.

## Verified Timing Boundaries

The current shared execution path loads workflow source and executes its import
graph in `api/src/services/execution/worker.py` before entering
`engine.execute`. The engine starts `duration_ms` after this loading; the child
envelope in `simple_worker.py` preserves that engine duration. Loading failures
and exceptions can use broader elapsed time, so this field is not a uniform
child-wall-time measure for every outcome.

Existing durable timestamps support `created_at → started_at` queue and
`started_at → completed_at` worker-wall intervals. They do not isolate
dependency loading. Completion is written before log staging and notification
delivery, so durable completion is also not browser result availability.

The V2 `useWorkflow` path submits over REST, subscribes over the shared execution
WebSocket and reconciles results over REST. A terminal WebSocket notification
does not contain the result. The two-second polling interval is a failure
fallback; it must not be assumed to explain every two-second delay. Measure
actual transport events before assigning that delay to polling.

Workflow templates already preload the base execution engine, `bifrost`,
`httpx` and `pydantic`. Workspace modules remain fresh per execution and load
through the authenticated worker-local SDK API. A warm worker therefore does
not imply that each new child inherits a warm workflow import graph.

## Reproducible Workload

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
