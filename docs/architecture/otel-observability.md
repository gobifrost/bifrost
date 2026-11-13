# OTel observability — traces + metrics v1

Ship platform traces and metrics to operator-owned backends over OTLP/HTTP.
Instrumentation lives here; collectors, storage, dashboards, and alerts live
in the operator's repo.

## Goal

An operator who sets one env var on the API/worker/scheduler deployments
gets W3C-distributed traces (request → run) and a minimal metric set in
their own Tempo/Mimir (or equivalent), with zero behavior change when the
env var is absent.

## Non-goals

- Log export. Plain-text logs keep flowing to the operator's Loki; v1 only
  injects trace context into log records for correlation.
- Dashboards, alerts, collectors, retention. Operator repo owns those.
- Prometheus text exposition (superseded by OTLP; the 2026-04-23
  deferral's alerting trigger is met by OTel metrics instead).
- Scheduler-internal and worker-pool spans, tail sampling, browser tracing.

## Decisions

1. **Distro over hand-rolled setup.** `opentelemetry-distro` +
   `opentelemetry-exporter-otlp-proto-http` plus instrumentations for
   FastAPI, SQLAlchemy, asyncpg, `redis.asyncio`, httpx, logging, and
   system metrics. No custom exporter, sampler, or propagator code.
2. **Default off, env-only config.** Setup runs in `app_lifespan`
   (`api/src/main.py`) only when `OTEL_EXPORTER_OTLP_ENDPOINT` is set;
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
   per the `log_safe` discipline.
5. **Propagation rides the dispatch payload.** `PoolClaim.message` gains a
   `traceparent` entry (W3C format) at enqueue; the worker extracts and
   re-attaches it after fork. No side-channel, no schema migration — the
   message dict already carries arbitrary keys.
6. **Sampling: `parentbased_always_on` default**, env-overridable.
   Self-hosted volumes are small; operators with volume tune down.
7. **Metrics are minimal**: runtime/system metrics plus three manual
   instruments — run started count, run duration histogram, run failed
   count (workflow + agent dimensions). `PeriodicExportingMetricReader`
   on the same OTLP endpoint.
8. **DB statement audit is an implementation checkpoint.** Auto-
   instrumentation may capture literal SQL. Verify sanitization during
   implementation; fall back to disabling statement capture rather than
   shipping user data in spans.

## Data flow

```
request (auto span) → enqueue: inject traceparent into PoolClaim.message
  → worker fork → extract → run span (child of request span)
  → spans + metrics → OTLP/HTTP → operator collector → Tempo/Mimir
logs (unchanged path) → Loki, now carrying trace_id/span_id
```

## Test seams

- Unit: `InMemorySpanExporter` / `InMemoryMetricReader` asserting run
  spans carry ID-only attributes and the dispatch payload round-trips
  a valid `traceparent`; default-off path asserts no-op providers.
- E2E (one happy path): OTLP endpoint pointed at a stub receiver;
  a workflow run produces a connected request→run trace.
- No live-collector tests in CI; the merge queue stays the gate.

## Rollout

1. Platform PR: deps via the `requirements.lock` pip-compile flow,
   lifespan setup, `docs/runbooks/otel-operator-handoff.md` (env
   contract + minimal collector example), compose/Kustomize overlay
   example with placeholder endpoint. Product Updates: omit.
2. Operator-repo PR: collector, Tempo/Mimir wiring, dashboards, alerts.
3. Validation: debug stack against a local `otelcol` showing one
   connected workflow-run trace and the three run metrics.

## Explicitly out of scope

Scheduler-claim/dispatch spans, tail sampling, OTLP log export,
client-side tracing, per-tenant sampling rules, and any operator URL,
credential, or retention setting in this repo.
