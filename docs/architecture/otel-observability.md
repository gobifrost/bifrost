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
   FastAPI, SQLAlchemy, asyncpg, `redis.asyncio`, httpx, logging, and
   system metrics. No custom exporter, sampler, or propagator code.
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
   (`run.id`, `org.id`, `handler`) — never user content, keys, or tokens,
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

- SQLAlchemy/asyncpg: disable statement/parameter capture; no SQL text,
  bind values or result content.
- Redis (including async clients and pipelines): suppress command arguments,
  keys and values. Redis command formatting can include cached content;
  retain only a fixed command name and safe timing/status metadata.
- FastAPI/httpx: suppress raw URL paths/query strings, credentials, request
  and response bodies and headers. Use route templates or reviewed fixed
  operation names, never user-supplied URLs as span names/attributes.
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
