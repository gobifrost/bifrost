"""OpenTelemetry setup: traces + metrics to operator backends. Default off.

Everything here degrades to no-ops when ``OTEL_EXPORTER_OTLP_ENDPOINT`` is
unset, so unconfigured installs pay nothing and emit nothing. Depends only
on the standard library plus ``opentelemetry-api`` at import time;
SDK/exporter/instrumentation packages are imported lazily inside the init
helpers so the CLI never pays import cost (or fails) when telemetry is
absent.

Conventions (see docs/architecture/otel-observability.md):
- Span attributes carry IDs and fixed names only — never user content,
  keys, or secrets. URL paths/queries and captured headers are dropped at
  export, not merely trimmed.
- A failed dispatch sets the span status to ERROR with the exception
  *type* only. Tracebacks and messages can carry user data, so they stay
  out.
- Trace context crosses the queue in the dispatch payload as one W3C
  ``traceparent``; baggage is never injected or extracted.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import threading
import time
from typing import Any, Iterator

from opentelemetry import metrics, trace
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.trace import SpanKind, Status, StatusCode
from opentelemetry.trace.propagation.tracecontext import (
    TraceContextTextMapPropagator,
)

logger = logging.getLogger(__name__)

TRACEPARENT_KEY = "traceparent"
# Only W3C trace context crosses the queue. The global composite propagator
# also carries baggage, which the design forbids (it can smuggle client
# state into persisted dispatch rows and outbound calls).
_PROPAGATOR = TraceContextTextMapPropagator()

# Per-process service identity: API, launcher and run children must be
# distinguishable in the backend.
_SERVICE_DEFAULT_BY_ROLE = {
    "api": "bifrost-api",
    "worker": "bifrost-worker",
    "run": "bifrost-run",
}

# Attributes dropped at export: content-bearing values from automatic
# instrumentation. Audited against the pinned instrumentor versions
# (0.62b1) — see docs/architecture/otel-observability.md.
_DROPPED_ATTRIBUTE_KEYS = frozenset(
    {
        # SQL text and parameters (SQLAlchemy, asyncpg)
        "db.statement",
        "db.query.text",
        "db.query.parameter",
        # Raw URLs and paths: they carry ids, slugs and (on outbound calls)
        # credentials. The route template (http.route) remains.
        "url.full",
        "url.path",
        "url.query",
        "http.url",
        "http.target",
        # Redis FT.SEARCH emits the query and, worse, *document contents*
        # as redis.search.xdoc_<idx>.<field>; FT.CREATE emits the schema
        # derived from its args.
        "redis.search.query",
        "redis.create_index.fields",
    }
)
# Prefix rules for the same families (indexed attribute names).
_DROPPED_ATTRIBUTE_PREFIXES = (
    "redis.search.xdoc",
    "db.query.parameter.",
    # Captured headers are opt-in for operators, but a credential in a
    # header must never reach a collector even then.
    "http.request.header.",
    "http.response.header.",
)
# An ERROR description is kept only when it is a bare exception type name.
# Anything longer came from an SDK auto-description ("Type: message") and
# may carry user data.
_TYPE_ONLY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Health probes are not interesting spans; anchored so a path like
# "/healthz-report" is not excluded by accident, and overridable by the
# standard env var.
# The ASGI layer matches these against "scheme://host/path", so the
# patterns must tolerate that prefix and cannot be anchored to "/".
_DEFAULT_EXCLUDED_URLS = (
    r"^https?://[^/]+/health(?:/|$),^https?://[^/]+/ready$"
)

_initialized = False
_child_initialized = False


def is_enabled() -> bool:
    """True only when the operator pointed us at a collector."""
    return bool(os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"))


def _resource(role: str) -> Any:
    from opentelemetry.sdk.resources import Resource

    default_name = _SERVICE_DEFAULT_BY_ROLE.get(role, _SERVICE_DEFAULT_BY_ROLE["api"])
    return Resource.create(
        {
            "service.name": os.environ.get("OTEL_SERVICE_NAME", default_name),
            "service.version": os.environ.get("BIFROST_VERSION", "dev"),
            "bifrost.process.role": role,
        }
    )


def _sampler() -> Any:
    """Resolve the standard sampler env vars; default parentbased_always_on."""
    from opentelemetry.sdk.trace.sampling import (
        ALWAYS_OFF,
        ALWAYS_ON,
        ParentBased,
        TraceIdRatioBased,
    )

    name = os.environ.get("OTEL_TRACES_SAMPLER", "parentbased_always_on").strip()
    try:
        ratio = float(os.environ.get("OTEL_TRACES_SAMPLER_ARG", "0.1"))
    except ValueError:
        ratio = 0.1
    samplers = {
        "always_on": lambda: ALWAYS_ON,
        "always_off": lambda: ALWAYS_OFF,
        "traceidratio": lambda: TraceIdRatioBased(ratio),
        "parentbased_always_on": lambda: ParentBased(ALWAYS_ON),
        "parentbased_always_off": lambda: ParentBased(ALWAYS_OFF),
        "parentbased_traceidratio": lambda: ParentBased(TraceIdRatioBased(ratio)),
    }
    factory = samplers.get(name)
    if factory is None:
        logger.warning("Unknown OTEL_TRACES_SAMPLER %r; using parentbased_always_on", name)
        return ParentBased(ALWAYS_ON)
    return factory()


def _scrub_attributes(attributes: Any) -> dict[str, Any]:
    if not attributes:
        return {}
    return {
        key: value
        for key, value in dict(attributes).items()
        if key not in _DROPPED_ATTRIBUTE_KEYS
        and not key.startswith(_DROPPED_ATTRIBUTE_PREFIXES)
    }


def _scrub_status(status: Any) -> Any:
    if (
        status is not None
        and status.status_code is StatusCode.ERROR
        and not (
            isinstance(status.description, str)
            and _TYPE_ONLY_RE.match(status.description)
        )
    ):
        return Status(StatusCode.ERROR)
    return status


class ScrubbingSpanExporter(SpanExporter):
    """Export-time privacy boundary for auto-instrumented spans.

    `run_span` already emits clean spans (type-only status, no events).
    This handles everything else: SDK auto-status descriptions, exception
    events, SQL text, Redis payloads, raw URLs and captured headers are
    scrubbed here so no raw content leaves the process, regardless of
    instrumentor defaults.
    """

    def __init__(self, exporter: SpanExporter) -> None:
        self._exporter = exporter

    def export(self, spans: Any) -> SpanExportResult:
        scrubbed = [
            ReadableSpan(
                name=s.name,
                context=s.context,
                parent=s.parent,
                resource=s.resource,
                attributes=_scrub_attributes(s.attributes),
                events=[],
                links=s.links,
                kind=s.kind,
                status=_scrub_status(s.status),
                instrumentation_scope=s.instrumentation_scope,
                start_time=s.start_time,
                end_time=s.end_time,
            )
            for s in spans
        ]
        return self._exporter.export(scrubbed)

    def shutdown(self) -> None:
        self._exporter.shutdown()


def init_telemetry(role: str = "api") -> bool:
    """Initialize providers, exporters, and instrumentors. Idempotent.

    ``role`` selects the default service name (``api``, ``worker``) and is
    recorded as ``bifrost.process.role``. Returns True when telemetry is
    active, False when disabled (no endpoint) or when setup failed. Never
    raises: observability must not break startup.
    """
    global _initialized
    if _initialized:
        return True
    if not is_enabled():
        return False
    try:
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
            OTLPMetricExporter,
        )
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        resource = _resource(role)
        trace.set_tracer_provider(
            TracerProvider(resource=resource, sampler=_sampler())
        )
        trace.get_tracer_provider().add_span_processor(
            BatchSpanProcessor(ScrubbingSpanExporter(OTLPSpanExporter()))
        )
        metrics.set_meter_provider(
            MeterProvider(
                resource=resource,
                metric_readers=[PeriodicExportingMetricReader(OTLPMetricExporter())],
            )
        )
        _instrument_libraries()
    except Exception as e:  # noqa: BLE001 — telemetry must never break startup
        logger.warning("OpenTelemetry setup failed, continuing without telemetry: %s", e)
        return False
    _initialized = True
    logger.info("OpenTelemetry enabled")
    return True


def instrument_fastapi_app(app: Any) -> bool:
    """Instrument one already-constructed FastAPI app.

    ``FastAPIInstrumentor().instrument()`` only patches the ``FastAPI``
    class, so it never reaches an app built before initialization. Call
    this right after construction, before the middleware stack is built.
    """
    if not is_enabled():
        return False
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(
            app,
            excluded_urls=(
                os.environ.get("OTEL_PYTHON_FASTAPI_EXCLUDED_URLS")
                or _DEFAULT_EXCLUDED_URLS
            ),
        )
        return True
    except Exception as e:  # noqa: BLE001 — never break startup
        logger.warning("FastAPI instrumentation unavailable: %s", e)
        return False


def _instrument_libraries() -> None:
    """Auto-instrumentation for a process that serves requests and owns
    runtime metrics. Each import is local so a missing optional package
    degrades to one warning instead of a failed startup."""
    try:
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

        # Statement text is dropped by the export scrubber; bound values
        # are placeholders. See docs/architecture/otel-observability.md.
        SQLAlchemyInstrumentor().instrument()
    except Exception as e:  # noqa: BLE001
        logger.warning("SQLAlchemy instrumentation unavailable: %s", e)
    try:
        from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor

        AsyncPGInstrumentor().instrument()
    except Exception as e:  # noqa: BLE001
        logger.warning("asyncpg instrumentation unavailable: %s", e)
    try:
        from opentelemetry.instrumentation.redis import RedisInstrumentor

        RedisInstrumentor().instrument()
    except Exception as e:  # noqa: BLE001
        logger.warning("Redis instrumentation unavailable: %s", e)
    _instrument_http_client()
    try:
        from opentelemetry.instrumentation.system_metrics import (
            SystemMetricsInstrumentor,
        )

        # Runtime metrics belong to the owning process only; children skip
        # this so a one-shot run cannot emit duplicate host series.
        SystemMetricsInstrumentor().instrument()
    except Exception as e:  # noqa: BLE001
        logger.warning("system metrics instrumentation unavailable: %s", e)


def _instrument_http_client() -> None:
    """Outbound HTTP spans. This is the only instrumentation a run child
    needs: it talks to the engine SDK over a private socket and does no
    DB/Redis work itself."""
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().instrument()
    except Exception as e:  # noqa: BLE001
        logger.warning("HTTPX instrumentation unavailable: %s", e)


def _discard_inherited_providers() -> None:
    """Drop providers inherited across ``os.fork``.

    There is no public reset API. Without this the child keeps the
    parent's provider object and a ``BatchSpanProcessor`` whose worker
    thread does not exist after fork, so spans silently queue forever.
    """
    global _meter, _run_started, _run_duration, _run_failed
    try:
        trace._TRACER_PROVIDER_SET_ONCE._done = False  # noqa: SLF001
        trace._TRACER_PROVIDER = None  # noqa: SLF001
    except Exception:  # noqa: BLE001, S110 — best effort; spans then no-op
        pass
    try:
        metrics._internal._METER_PROVIDER = None  # noqa: SLF001
        metrics._internal._METER_PROVIDER_SET_ONCE._done = False  # noqa: SLF001
    except Exception:  # noqa: BLE001, S110 — best effort
        pass
    # Re-bind lazily so a child never reuses instruments bound in the parent.
    _meter = _run_started = _run_duration = _run_failed = None


def init_telemetry_in_child() -> bool:
    """Install child-local providers in a process forked from the launcher.

    ``os.fork`` copies memory but no threads, so an inherited
    ``BatchSpanProcessor`` has no worker left to drain its queue: spans
    the child creates would never leave the process. The child installs
    only what it uses — an HTTP-client instrumentor and a thread-free
    ``SimpleSpanProcessor`` — and no metric reader, so a one-shot run
    cannot duplicate the owning process's runtime metrics.

    Idempotent, never raises, and a no-op when telemetry is disabled.
    """
    global _child_initialized, _initialized
    if _child_initialized:
        return True
    if not is_enabled():
        return False
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        _discard_inherited_providers()
        trace.set_tracer_provider(
            TracerProvider(resource=_resource("run"), sampler=_sampler())
        )
        # A batch processor is safe here because its thread is created in
        # the child, after fork, and the caller flushes with a bounded wait
        # before exit. It keeps collector latency out of workflow execution:
        # a synchronous processor would make every SDK HTTP call wait.
        trace.get_tracer_provider().add_span_processor(
            BatchSpanProcessor(
                ScrubbingSpanExporter(OTLPSpanExporter()),
                schedule_delay_millis=2000,
                max_export_batch_size=256,
            )
        )
        _instrument_http_client()
    except Exception as e:  # noqa: BLE001 — telemetry must never break a run
        logger.warning("OpenTelemetry child setup failed, continuing untraced: %s", e)
        _initialized = False
        return False
    _child_initialized = True
    _initialized = True
    logger.info("OpenTelemetry enabled in forked child (pid=%s)", os.getpid())
    return True


def flush_telemetry_in_child(timeout_s: float = 5.0) -> None:
    """Flush a forked child's telemetry before it exits. Never raises.

    Spans already exported synchronously as they ended; this closes the
    exporter. A slow or unreachable collector must not delay the child's
    exit, so the flush runs in a daemon thread that is abandoned if it
    outlives ``timeout_s``.
    """
    if not _child_initialized or not _initialized:
        return
    try:
        flusher = threading.Thread(target=shutdown_telemetry, daemon=True)
        flusher.start()
        flusher.join(timeout_s)
    except Exception as e:  # noqa: BLE001 — exit must not be blocked
        logger.debug("OpenTelemetry child flush failed: %s", e)


def shutdown_telemetry(timeout_s: float = 10.0) -> None:
    """Flush exporters. Best-effort; safe to call when disabled.

    Runs the SDK shutdown in a daemon thread with a bounded join: a slow
    collector must not stall API or launcher shutdown.
    """
    global _initialized
    if not _initialized:
        return
    _initialized = False

    def _run() -> None:
        try:
            trace.get_tracer_provider().shutdown()
        except Exception:  # noqa: BLE001, S110 — best effort on the way out
            pass
        try:
            metrics.get_meter_provider().shutdown()
        except Exception:  # noqa: BLE001, S110 — best effort on the way out
            pass

    try:
        flusher = threading.Thread(target=_run, daemon=True)
        flusher.start()
        flusher.join(timeout_s)
    except Exception as e:  # noqa: BLE001 — shutdown must not be blocked
        logger.debug("OpenTelemetry shutdown failed: %s", e)


def inject_traceparent(message: dict[str, Any]) -> None:
    """Stamp the current trace context onto a dispatch message in place.

    Writes nothing unless a span is actually active, so payloads never
    carry dummy trace IDs — including when telemetry is disabled, where
    the global tracer only hands out invalid contexts. Baggage is never
    written: only W3C trace context crosses the queue.
    """
    span = trace.get_current_span()
    if not span.get_span_context().is_valid:
        return
    _PROPAGATOR.inject(message)


def extract_context(message: dict[str, Any] | None) -> Any | None:
    """Pull a parent trace context out of a dispatch message, if present."""
    if not isinstance(message, dict) or TRACEPARENT_KEY not in message:
        return None
    return _PROPAGATOR.extract(message)


_meter = None
_run_started = None
_run_duration = None
_run_failed = None


def _instruments() -> tuple[Any, Any, Any]:
    """Lazily bound OTel instruments (no-ops until a provider exists)."""
    global _meter, _run_started, _run_duration, _run_failed
    if _run_started is None:
        _meter = metrics.get_meter(__name__)
        _run_started = _meter.create_counter(
            "bifrost.dispatch.started", description="Claimed dispatches by handler"
        )
        _run_duration = _meter.create_histogram(
            "bifrost.dispatch.duration",
            unit="s",
            description="Claim to hand-off/return seconds by handler and status",
        )
        _run_failed = _meter.create_counter(
            "bifrost.dispatch.failed",
            description="Dispatches that raised before handing off, by handler",
        )
    return _run_started, _run_duration, _run_failed


@contextlib.contextmanager
def run_span(
    *,
    handler: str,
    run_id: str,
    message: dict[str, Any] | None,
    workflow_id: str | None = None,
    queue_wait_s: float | None = None,
) -> Iterator[Any]:
    """Span for one claimed dispatch, from claim to hand-off.

    For ``workflow.execute`` this covers claim → routed to a child, not
    the workflow's own execution time; execution duration and terminal
    status live in the platform's run records. Metrics are therefore
    named ``bifrost.dispatch.*`` to describe what is actually measured.
    Exceptions propagate after marking the span failed.
    """
    started_counter, duration_histogram, failed_counter = _instruments()
    parent = extract_context(message)
    tracer = trace.get_tracer(__name__)
    # Names follow the infra observability contract so Tempo lookups by
    # bifrost.execution.id work as documented.
    attributes: dict[str, Any] = {
        "bifrost.handler": handler,
        "bifrost.execution.id": run_id,
    }
    if workflow_id:
        attributes["bifrost.workflow.id"] = workflow_id
    if queue_wait_s is not None:
        attributes["bifrost.queue.wait_s"] = round(queue_wait_s, 3)
    started_counter.add(1, {"bifrost.handler": handler})
    start = time.monotonic()
    status = "ok"
    try:
        # record_exception / set_status_on_exception are off on purpose:
        # the SDK defaults would attach the exception message and stack
        # frames, which can carry user data. Failure is signaled by the
        # ERROR status (type only) plus the failed counter instead.
        with tracer.start_as_current_span(
            f"run.{handler}",
            context=parent,
            kind=SpanKind.SERVER,
            attributes=attributes,
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            try:
                yield span
            except Exception as e:
                status = "error"
                # Type only: messages and tracebacks can carry user data.
                span.set_status(StatusCode.ERROR, type(e).__name__)
                failed_counter.add(1, {"bifrost.handler": handler})
                raise
    finally:
        duration_histogram.record(
            time.monotonic() - start, {"bifrost.handler": handler, "status": status}
        )
