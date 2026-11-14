# OTel operator handoff

Ship Bifrost traces and metrics to your own backends (Tempo/Mimir,
Jaeger/Prometheus, or any OTLP/HTTP receiver). The platform only emits;
you own collection, storage, retention, dashboards, and alerts.

## What the platform sends (and doesn't)

| Signal | Content | Notes |
|---|---|---|
| Traces | HTTP server spans (auto), DB/Redis/HTTP-client spans (auto), `run.<handler>` spans covering one dispatch (claim → routed or finished), plus a `bifrost.queue.wait_s` attribute | No log bodies, no SQL text, no Redis args, no raw URL paths or queries: `http.target`/`http.url`/`url.full`/`url.path` are dropped at export; `http.route` (the template) remains |
| Error status | `ERROR` with the exception *type* only | Messages and stack traces never leave the process |
| Span attributes | IDs only (`bifrost.execution.id`, `bifrost.workflow.id`, `bifrost.handler`, protocol/host/port/db metadata) | No user content, keys, tokens, or request/response bodies |
| Logs | Unchanged plain text to your existing pipeline | Not exported, and no trace/span IDs are injected into records in v1: that needs a structured log format, which the design keeps out of scope |
| Metrics | Request rate/duration (auto), runtime/system metrics, `bifrost.dispatch.started`, `bifrost.dispatch.duration`, `bifrost.dispatch.failed` | Dispatch metrics measure claim → hand-off, not workflow execution time (that lives in run records). No per-user/per-org cardinality beyond the handler name |

## Enable it

Set on the API, worker, and scheduler deployments:

```bash
OTEL_EXPORTER_OTLP_ENDPOINT=https://otel-collector.internal:4318
```

Optional tuning (all standard OpenTelemetry env vars, no Bifrost knobs):

```bash
OTEL_SERVICE_NAME=bifrost                 # default if unset
BIFROST_VERSION=$(cat version)            # service.version; the image sets this at build
OTEL_TRACES_SAMPLER=parentbased_traceidratio
OTEL_TRACES_SAMPLER_ARG=0.1              # default is always-on
OTEL_METRIC_EXPORT_INTERVAL=60000        # ms between metric pushes
OTEL_RESOURCE_ATTRIBUTES=deployment.environment=prod,host.name=worker-1
OTEL_PYTHON_FASTAPI_EXCLUDED_URLS='^/health(/|$),^/ready$,^/noisy'  # default shown first; regex, extend to silence routes
```

Without `OTEL_EXPORTER_OTLP_ENDPOINT`, every OpenTelemetry call in the
platform is a no-op: no spans, no metrics, no connections, no log-format
changes.

## Minimal collector (example, not a recommendation)

```yaml
receivers:
  otlp:
    protocols:
      http:
        endpoint: 0.0.0.0:4318
processors:
  batch: {}
  tail_sampling:
    decision_wait: 10s
    policies: [{name: errors, type: status_code, status_code: {status_codes: [ERROR]}}]
exporters:
  otlphttp/tempo:
    endpoint: https://tempo.internal:4318
  otlphttp/mimir:
    endpoint: https://mimir.internal:9090/api/v1/otlp
service:
  pipelines:
    traces:
      receivers: [otlp]
      processors: [tail_sampling, batch]
      exporters: [otlphttp/tempo]
    metrics:
      receivers: [otlp]
      processors: [batch]
      exporters: [otlphttp/mimir]
```

Keep your sampling and retention policy on this side; the platform always
sends head-based (default always-on, tunable via the env vars above) and
never filters.

## Verify the pipeline

1. Point a debug stack at a local collector (`otelcol --config example.yaml`).
2. Run any workflow; confirm one trace reads
   `request → <family> publish → <handler> process → run.<handler>`,
   linked by the W3C `traceparent` the platform injects at dispatch.
   Note: spans emitted inside the forked execution child arrive as their
   own traces (roots) until the work payload carries that context;
   correlate them by execution id.
3. Trigger a failing run; confirm the span status is `ERROR` with a bare
   exception-type description and no message, stack, or events.
4. Confirm DB spans carry operation/host but no statement text, HTTP spans
   carry route templates without query strings, and Redis spans carry the
   command name without args.

## What we deliberately do not export

- Exception messages, stack traces, or span events (rebuilt run spans
  never create them; the export scrubber strips them from automatic spans).
- SQL text and parameters (`db.statement`, `db.query.text`,
  `db.query.parameter.*`).
- Redis command arguments (`db.statement`), `FT.SEARCH` queries
  (`redis.search.query`), returned document fields
  (`redis.search.xdoc_*`), and `FT.CREATE` schemas
  (`redis.create_index.fields`). Index/command names and counts remain.
- Raw URLs entirely: `http.target`, `http.url`, `url.full`, `url.path`,
  `url.query`. Route templates (`http.route`) remain for grouping.
- Captured request/response headers, if an operator enables header
  capture: those attribute families are dropped at export.
- Log bodies. `log_safety` rules still apply to anything that reaches
  a log line; trace context is correlation metadata only.

Enforcement lives in `api/src/core/telemetry.py::ScrubbingSpanExporter`,
which rewrites spans immediately before OTLP export — so a future
instrumentor change cannot leak content by default. If a new integration
needs any of the above, it requires an explicit design decision and a test
proving the redaction — never a quiet default change.
