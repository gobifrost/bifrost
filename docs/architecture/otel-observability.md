# OTel observability — traces + metrics v1

Ship platform traces and metrics to operator-owned backends over OTLP/HTTP.
Instrumentation lives here; collectors, storage, dashboards, and alerts live
in the operator's repo.

This document specifies a future implementation. Delivering this design does
not enable telemetry or authorize implementation or deployment. Original
proposal: Thomas Bray (MTG-Thomas), PR #1372.

## Goal

An operator who sets one env var on the API and execution-worker deployments
gets W3C-distributed traces (request → run) and a minimal metric set in
their own Tempo/Mimir (or equivalent), with zero behavior change when the
env var is absent.

## Non-goals

- Log export. Plain-text logs keep flowing to the operator's Loki; v1 only
  injects trace context into log records for correlation.
- Dashboards, alerts, collectors, retention. Operator repo owns those.
- Prometheus text exposition. OTLP is the proposed transport; the run
  metrics below do not implement the scheduled-backlog gauge deferred in
  `docs/superpowers/specs/2026-04-23-deferred-executions-design.md`.
- Scheduler-internal and worker-pool spans, tail sampling, browser tracing.

## Decisions

1. **Distro over hand-rolled setup.** `opentelemetry-distro` +
   `opentelemetry-exporter-otlp-proto-http` plus instrumentations for
   FastAPI, httpx, logging, and system metrics. Database and Redis clients
   are not instrumented (see "Span budget" below). No custom exporter,
   sampler, or propagator code.
2. **Default off, env-only config.** A shared setup module is called at each
   process boundary described below,
   only when `OTEL_EXPORTER_OTLP_ENDPOINT` is set;
   otherwise the globals stay no-op. All tuning uses spec env vars
   (`OTEL_SERVICE_NAME`, `OTEL_RESOURCE_ATTRIBUTES`,
   `OTEL_TRACES_SAMPLER`/`OTEL_TRACES_SAMPLER_ARG`,
   `OTEL_METRIC_EXPORT_INTERVAL`); no new Bifrost settings keys.
3. **Health endpoints excluded** via
   `OTEL_PYTHON_FASTAPI_EXCLUDED_URLS` (`/health/*`, `/ready`) so K8s
   probes don't become spans.
4. **Two hand-written span kinds, both run boundaries**: workflow-run and
   agent-run server spans (`trace.get_tracer(__name__)` per module,
   `start_as_current_span`). Attributes are IDs only
   (`bifrost.execution.id`, `bifrost.workflow.id`, `handler`) — never user content, keys, or tokens,
   with the export privacy boundary below. `log_safe` escapes control
   characters and truncates; it is not secret redaction.
5. **Propagation rides the dispatch payload.** `stage_dispatch` stores a
   `traceparent` entry (W3C format) in `WorkDispatch.message` at enqueue;
   `PoolClaim.message` carries it through claim, release/requeue and remote
   dispatch. The execution child extracts and attaches it after fork,
   detaching context when the run ends. Validate incoming context, do not
   propagate baggage, and start a root span when context is absent/invalid.
   No side-channel, no schema migration — the
   message dict already carries arbitrary keys.
6. **Sampling: `parentbased_always_on` default**, env-overridable.
   Self-hosted volumes are small; operators with volume tune down.
7. **Metrics are minimal**: runtime/system metrics plus three manual
   instruments — run started count, run duration histogram, run failed
   count (workflow + agent dimensions). `PeriodicExportingMetricReader`
   on the same OTLP endpoint.
8. **Privacy applies before export to every span and event**, including
   automatic instrumentation, as specified below. Instrumentation is
   enabled only when its emitted fields meet that contract.

## Process lifecycle

The API runs `app_lifespan` in `api/src/main.py`. Worker containers instead
enter `api/src/worker/main.py` and start `remote.launcher`; they do not run
FastAPI startup. The launcher uses independent workflow and agent fork
templates. API-only initialization and context extraction are insufficient.

- API: install instrumentation before serving requests (FastAPI middleware
  must be registered before the middleware stack starts). Initialize shared
  providers/readers before accepting traced work; stop accepting work and
  drain existing work before bounded provider shutdown in lifespan cleanup.
  Scheduling hosted in the API uses that process's providers; no separate
  scheduler telemetry process is introduced.
- Launcher: initialize its own providers/readers in launcher startup, not by
  importing API lifespan. Export runtime metrics once per owning process;
  do not duplicate launcher/system metrics in every run child.
- Templates: keep preloaded fork templates free of exporter threads/readers
  and network clients. Each workflow/agent execution child initializes its
  own providers and permitted instrumentation after fork, before attaching
  the dispatch context and starting its run span. Never rely on inherited
  exporter locks, threads, connections or a parent's current context.
- Child exit: end the run span and record the result/duration, then perform
  bounded flush/shutdown before the normal child exit path. The workflow
  template uses `os._exit`, so `atexit` alone is insufficient. Agent children
  need the same explicit lifecycle. Cancellation/error paths also detach
  context and attempt bounded flush within the existing termination grace;
  SIGKILL may lose telemetry and must not delay recovery or change run state.
  For persistent service children, detach and flush at run boundaries without
  shutting down a provider still needed by that process; shut it down on exit.
- Launcher shutdown: preserve existing drain and child termination ordering,
  then bound its own flush/shutdown within the remaining shutdown deadline.
  An unavailable collector must not prevent startup, change execution results,
  extend cancellation deadlines or retain a child indefinitely. Use SDK
  export timeouts and bounded buffers; verify failure isolation explicitly.

The shared module owns idempotent setup and cleanup per process. Endpoint
absence leaves providers no-op, adds no telemetry threads/network traffic,
and leaves the existing logging configuration intact. Give API, launcher
and run children distinct resource identities under the operator's service
name convention; exclude run IDs from metric labels. Version-specific SDK
fork behavior must be verified against the locked dependencies during
implementation rather than assumed from context propagation.

## Export privacy boundary

Audit all enabled automatic instrumentation against an explicit field
allowlist before enabling export. Manual run spans permit only `run.id`,
`org.id` and the fixed handler name; automatic spans permit reviewed protocol,
route-template, status and timing fields. IDs must not become metric labels.
Operator resource attributes must also be documented as exported data.

- Database and Redis clients are not instrumented. If they ever are, the
  same rule holds: no SQL text, bind values, result content, Redis
  arguments, keys or values.
- FastAPI/httpx: suppress raw URL paths/query strings, credentials, request
  and response bodies and headers. Use route templates or reviewed fixed
  operation names, never user-supplied URLs as span names/attributes.
  Implemented by dropping `http.target`, `http.url`, `url.full`, `url.path`,
  `url.query` and any `http.*.header.*` attribute at export; `http.route`
  (the template) is retained.
- Exceptions: suppress messages, stack/source text and exception events that
  may carry user data; report fixed error categories and status only. Check
  automatic error status descriptions as well as attributes and events.
- Logging: inject trace/span IDs for correlation only; do not export logs,
  replace the formatter or treat `log_safe` as a privacy filter.

Use supported instrumentor configuration/hooks or omit instrumentation whose
output cannot meet this boundary. No raw content may leave the process for
collector-side redaction. Sampling does not replace sanitization. A locked
instrumentor version's defaults are not evidence of safety: test the complete
exported span names, attributes, events, status descriptions, resource fields
and metric labels, including automatic spans. If an export path cannot be
made safe with the supported SDK, stop that implementation for review rather
than silently relaxing this contract.

## Data flow

```
request (auto span) → enqueue: persist traceparent in WorkDispatch.message
  → claim/remote dispatch → fork child setup → extract → run span
  (child of request span) → bounded flush before normal child exit
  → spans + metrics → OTLP/HTTP → operator collector → Tempo/Mimir
logs (unchanged path) → Loki, now carrying trace_id/span_id
```

## Implementation status (v1)

- `src/core/telemetry.py` initializes providers only when
  `OTEL_EXPORTER_OTLP_ENDPOINT` is set; otherwise every helper is a no-op.
  The API and launcher get distinct service identities
  (`bifrost-api`/`bifrost-worker`/`bifrost-run`) and
  `bifrost.process.role`; `OTEL_TRACES_SAMPLER`/`_ARG` are honoured, and
  `OTEL_PYTHON_FASTAPI_EXCLUDED_URLS` (anchored regexes) overrides the
  health-probe defaults.
- The FastAPI app is instrumented with `instrument_app` at construction:
  the class-patching `.instrument()` form only covers apps created after
  the call, which would leave the API with no request spans. It emits one
  server span per request: the ASGI per-message `send`/`receive` spans are
  excluded. Each request starts from an empty trace context. Otherwise
  asyncio can carry the previous request's context into the next request
  on the same keep-alive connection (cpython#140947), and the instrumentor
  would nest it under that request and ignore its `traceparent`.
- `stage_dispatch` wraps the publish in a `PRODUCER` span (`<family>
  publish`, `messaging.system=postgresql`, `messaging.operation=publish`,
  `messaging.destination.name=<family>`) and injects `traceparent` while
  that span is current. `PoolWorker._run_claim` opens a `CONSUMER` span
  (`<handler> process`, `messaging.operation=process`,
  `messaging.message.id=<dispatch_id>`) parented to the extracted context,
  and the `run.<handler>` span is its child. One trace therefore reads
  request → publish → process → run. Covered by
  `api/tests/unit/test_otel_messaging.py`.
- Each template process resolves its children's telemetry set-up once,
  before it forks (`prepare_child_telemetry`): it loads the SDK and HTTPX
  instrumentor modules, builds the run resource and runs the instrumentor's
  dependency check. Children inherit that copy-on-write. Left to each
  child, the imports and package-metadata reads cost roughly 45-100 ms per
  run; prepared, child set-up measured about 6 ms. Without preparation a
  child stays untraced.
- Execution children re-initialize tracing after `os.fork` and export
  nothing themselves. `init_telemetry_in_child` discards the inherited
  providers (fork carries memory but no threads, so an inherited batch
  queue would never drain) and installs a provider whose only processor is
  a bounded in-memory buffer (512 spans), plus the HTTP-client
  instrumentor. There is no metric reader, so runtime metrics stay with the
  owning process. Spans are scrubbed as they end.
- The workflow consumer stamps the active dispatch span's `traceparent`
  into the execution context it routes, so local and remote children
  receive it with their work item. The child wraps module load and
  execution in a `workflow.run` span (`bifrost.execution.id`,
  `bifrost.workflow.id`) parented on that context, and marks it `ERROR`
  with the exception type only when the run reports failure. Its outbound
  HTTP and engine SDK calls nest beneath it. A child's first run starts at
  its telemetry set-up, just after fork, and opens with a
  `workflow.startup` span covering the time before the work began.
- A remote launcher wraps the fork command in a `workflow.launch` span
  parented on the same context: receipt, any wait for the template, the
  fork and the hand-off to the child. A failure is marked `ERROR` with its
  type only. Locally the pool forks inside `run.workflow.execute`, so no
  separate span is needed there.
- The child returns its finished spans with the run result
  (`_otel_spans`). The process that reads the result (the pool, or the
  remote launcher before anything crosses the route socket) strips them
  and hands them to its own long-lived `BatchSpanProcessor`. A one-shot
  child therefore opens no collector connection and never waits on an exit
  flush, so a slow or unreachable collector cannot hold a run process.
  Agent children return their spans the same way. Covered by
  `api/tests/unit/test_telemetry_fork.py`, the pool and launcher result
  tests, and the consumer routing test.
  One trace therefore reads request → publish → process →
  `run.workflow.execute` → (`workflow.launch`) → `workflow.run`
  (`workflow.startup`, then outbound calls), with no unattributed time
  between the dispatch and the run.
- `ScrubbingSpanExporter` enforces the export privacy boundary in-process
  before OTLP: raw URLs and query strings, captured headers, **all span
  events**, and
  SDK-generated error descriptions never leave the process. A failing span
  keeps the ERROR status and the exception type; nothing else. Covered by
  `api/tests/unit/test_telemetry.py`.

## Span budget

Telemetry is meant to stay cheap enough to leave on. Measured on a
rehearsal stack (mixed workflow load, concurrency 4, local OTLP sink,
interleaved off/on phases), the first version, which also instrumented
SQLAlchemy, asyncpg and Redis and kept the ASGI send/receive spans, exported
about 45 spans per workflow execute. Across two measurement rounds it cut
throughput 17-33% and added 31-38% to median request latency and 19-24% to
CPU per request. Most of those spans carried nothing useful:

- SQLAlchemy produced only a `connect` span per pool checkout. The async
  engine is created through a function bound before instrumentation, so
  statements were never traced.
- asyncpg traced only `BEGIN`/`COMMIT`/`ROLLBACK`. SQLAlchemy runs
  statements through prepared statements, which it does not wrap.
- Redis traced each command individually.
- ASGI emitted a span for every `send` and `receive` message.

Without them a workflow execute exports about 10 spans: the request,
dispatch, launch, start-up and run chain plus outbound calls. The measured
cost fell to 5-11% of median latency, 3-8% of throughput and 6-13% of CPU
per request across three rounds. About two thirds of that CPU is in the forked runs, which spend a
few milliseconds each setting up a tracer and recording their spans.
Tracing each SQL statement through SQLAlchemy instead adds about 45 spans
per execute and cost 17% of throughput and 27% of median latency. Query cost
and frequency come from `pg_stat_statements`. Add a client instrumentation
only with a measurement showing its cost.

## Test seams

- Unit: in-memory exporters/readers assert ID-only run attributes, bounded
  metric dimensions, and durable context propagation through staging,
  claim, release/requeue and remote child payloads. Missing/invalid context
  starts a root; consecutive runs cannot inherit each other's context.
- Unit: default-off setup for each entry point creates no exporter/reader
  threads or telemetry traffic. Test idempotent setup/cleanup and unavailable
  collector behavior without changing run results or shutdown deadlines.
- Privacy: inject sentinel secrets/content in SQL literals, Redis commands
  and pipelines, URLs, headers, bodies and exceptions. Assert none appear
  anywhere in the exported trace/metric envelope, including automatic spans.
- One cross-process happy path against an OTLP stub receiver: execute short
  workflow and agent runs using the actual launcher/templates/children;
  prove connected request→run traces and the three run metrics reach the
  receiver before normal child exit. Separately test cancellation, export
  timeout and process isolation at the lowest useful layer.
- No live-collector tests in CI. These are future implementation acceptance
  checks; docs CI or skipped runtime suites do not prove telemetry works.

## Rollout

1. Separately authorized platform PR: deps via the `requirements.lock`
   pip-compile flow, shared lifecycle and privacy setup,
   `docs/runbooks/otel-operator-handoff.md` (env
   contract + minimal collector example), compose/Kustomize overlay
   example with placeholder endpoint. Classify that runtime PR independently
   under Product Updates policy; this design-only PR is omitted.
2. Operator-repo PR: collector, Tempo/Mimir wiring, dashboards, alerts.
3. Validation: debug stack against a local `otelcol` showing one
   connected workflow-run trace and the three run metrics.

## Explicitly out of scope

Scheduler-claim/dispatch spans, tail sampling, OTLP log export,
client-side tracing, per-tenant sampling rules, and any operator URL,
credential, or retention setting in this repo.
