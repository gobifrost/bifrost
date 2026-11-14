"""OpenTelemetry setup: traces + metrics to operator backends. Default off.

Everything here degrades to no-ops when ``OTEL_EXPORTER_OTLP_ENDPOINT`` is
unset, so unconfigured installs pay nothing and emit nothing. Depends only
on the standard library plus ``opentelemetry-api`` at import time;
SDK/exporter/instrumentation packages are imported lazily inside
:func:`init_telemetry` so worker children and the CLI never pay import
cost (or fail) when telemetry is absent.

Conventions (see docs/architecture/otel-observability.md):
- Span/message attributes carry IDs only — never user content, keys, or
  secrets. This mirrors the ``log_safe`` discipline for logs.
- A failed run sets the span status to ERROR with the exception *type*
  only. Tracebacks and messages can carry user data, so they stay out.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import threading
import time
from typing import Any, Iterator

from opentelemetry import metrics, propagate, trace
from opentelemetry.sdk.trace import Event as SdkEvent
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.trace import SpanKind, Status, StatusCode

logger = logging.getLogger(__name__)

SERVICE_NAME_DEFAULT = "bifrost-api"
TRACEPARENT_KEY = "traceparent"

# Attributes dropped at export: content-bearing values from automatic
# instrumentation. Audited against the pinned instrumentor versions
# (0.62b1) — see docs/architecture/otel-observability.md.
_DROPPED_ATTRIBUTE_KEYS = frozenset(
    {
        # SQL text and parameters (SQLAlchemy, asyncpg)
        "db.statement",
        "db.query.text",
        "db.query.parameter",
        # HTTP query strings (ASGI/FastAPI new + old semconv)
        "url.query",
        # Redis FT.SEARCH emits the query and, worse, *document contents*
        # as redis.search.xdoc_<idx>.<field>; and FT.CREATE emits the
        # schema derived from its args.
        "redis.search.query",
        "redis.create_index.fields",
    }
)
# Prefix rules for the same families (indexed attribute names).
_DROPPED_ATTRIBUTE_PREFIXES = ("redis.search.xdoc", "db.query.parameter.")
# URLs ship without query string or fragment (tokens, codes, filters).
_URL_KEYS = frozenset({"http.target", "http.url", "url.full"})
# An ERROR description is kept only when it is a bare exception type name.
# Anything longer came from an SDK auto-description ("Type: message") and
# may carry user data.
_TYPE_ONLY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_initialized = False


def is_enabled() -> bool:
    """True only when the operator pointed us at a collector."""
    return bool(os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"))


def _scrub_attributes(attributes: Any) -> dict[str, Any]:
    if not attributes:
        return {}
    cleaned: dict[str, Any] = {}
    for key, value in dict(attributes).items():
        if key in _DROPPED_ATTRIBUTE_KEYS:
            continue
        if key.startswith(_DROPPED_ATTRIBUTE_PREFIXES):
            continue
        if key in _URL_KEYS and isinstance(value, str):
            value = value.split("?", 1)[0].split("#", 1)[0]
        cleaned[key] = value
    return cleaned


def _scrub_event(event: Any) -> Any:
    # Exception events carry message + stack trace. Keep the fact that an
    # exception of a given type occurred; drop everything else.
    if getattr(event, "name", None) == "exception":
        attrs = getattr(event, "attributes", None) or {}
        kept = (
            {"exception.type": attrs["exception.type"]}
            if "exception.type" in attrs
            else {}
        )
        return SdkEvent(
            name=event.name, attributes=kept, timestamp=event.timestamp
        )
    return event


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
    events, query text, and URL query strings are scrubbed here so no raw
    content leaves the process, regardless of instrumentor defaults.
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
                events=[_scrub_event(e) for e in (s.events or [])],
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


def init_telemetry() -> bool:
    """Initialize providers, exporters, and instrumentors. Idempotent.

    Returns True when telemetry is active, False when disabled (no
    endpoint) or when setup failed. Never raises: observability must not
    break startup.
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
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        resource = Resource.create(
            {
                "service.name": os.environ.get("OTEL_SERVICE_NAME", SERVICE_NAME_DEFAULT),
                "service.version": os.environ.get("BIFROST_VERSION", "dev"),
            }
        )
        trace.set_tracer_provider(TracerProvider(resource=resource))
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


def _instrument_libraries() -> None:
    """Auto-instrumentation. Each import is local so a missing optional
    package degrades to one warning instead of a failed startup."""
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        # K8s probes are not interesting spans.
        FastAPIInstrumentor().instrument(excluded_urls="health,ready")
    except Exception as e:  # noqa: BLE001
        logger.warning("FastAPI instrumentation unavailable: %s", e)
    try:
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

        # NOTE (statement audit): this records db.statement. SQLAlchemy
        # renders server-side placeholders ($1, %s), not literals, and the
        # codebase never uses literal_binds. Re-audit if either changes.
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
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().instrument()
    except Exception as e:  # noqa: BLE001
        logger.warning("HTTPX instrumentation unavailable: %s", e)
    try:
        from opentelemetry.instrumentation.logging import LoggingInstrumentor

        # Inject trace_id/span_id into records for Loki correlation.
        # The log *format* is untouched.
        LoggingInstrumentor().instrument(set_logging_format=False)
    except Exception as e:  # noqa: BLE001
        logger.warning("logging instrumentation unavailable: %s", e)
    try:
        from opentelemetry.instrumentation.system_metrics import (
            SystemMetricsInstrumentor,
        )

        SystemMetricsInstrumentor().instrument()
    except Exception as e:  # noqa: BLE001
        logger.warning("system metrics instrumentation unavailable: %s", e)


_child_initialized = False


def _discard_inherited_providers() -> None:
    """Forget the providers inherited across ``os.fork``.

    A process may set a provider only once, and that guard is ordinary
    module state, so it has to be cleared before the child installs its
    own. The SDK exposes no public reset; these private attributes are it.
    """
    import opentelemetry.metrics._internal as metrics_internal
    from opentelemetry import trace as trace_api

    trace_api._TRACER_PROVIDER_SET_ONCE._done = False
    trace_api._TRACER_PROVIDER = None
    metrics_internal._METER_PROVIDER_SET_ONCE._done = False
    metrics_internal._METER_PROVIDER = None
    # Instruments created before the fork are bound to the parent's
    # provider; drop them so the child binds its own on next use.
    global _meter, _run_started, _run_duration, _run_failed
    _meter = None
    _run_started = None
    _run_duration = None
    _run_failed = None


def init_telemetry_in_child() -> bool:
    """Install child-local providers in a process forked from the launcher.

    ``os.fork`` copies memory but no threads, so an inherited
    ``BatchSpanProcessor`` has no worker left to drain its queue: spans
    the child creates would never leave the process. The child therefore
    installs its own providers on a thread-free path —
    ``SimpleSpanProcessor`` exports each span synchronously as it ends.
    Libraries are instrumented here too: execution children are forked
    from a template process that never ran :func:`init_telemetry`.

    Idempotent, never raises, and a no-op when telemetry is disabled.
    """
    global _child_initialized, _initialized
    if _child_initialized:
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
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor

        _discard_inherited_providers()
        resource = Resource.create(
            {
                "service.name": os.environ.get("OTEL_SERVICE_NAME", SERVICE_NAME_DEFAULT),
                "service.version": os.environ.get("BIFROST_VERSION", "dev"),
            }
        )
        trace.set_tracer_provider(TracerProvider(resource=resource))
        trace.get_tracer_provider().add_span_processor(
            SimpleSpanProcessor(ScrubbingSpanExporter(OTLPSpanExporter()))
        )
        metrics.set_meter_provider(
            MeterProvider(
                resource=resource,
                metric_readers=[PeriodicExportingMetricReader(OTLPMetricExporter())],
            )
        )
        _instrument_libraries()
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

    Spans already exported synchronously as they ended; this drains the
    metric reader and closes exporters. A slow or unreachable collector
    must not delay the child's exit, so the flush runs in a daemon thread
    that is abandoned if it outlives ``timeout_s``.
    """
    if not _child_initialized or not _initialized:
        return
    try:
        flusher = threading.Thread(target=shutdown_telemetry, daemon=True)
        flusher.start()
        flusher.join(timeout_s)
    except Exception as e:  # noqa: BLE001 — exit must not be blocked
        logger.debug("OpenTelemetry child flush failed: %s", e)


def shutdown_telemetry() -> None:
    """Flush exporters. Best-effort; safe to call when disabled."""
    global _initialized
    if not _initialized:
        return
    _initialized = False
    try:
        trace.get_tracer_provider().shutdown()
    except Exception:  # noqa: BLE001, S110 — best effort on the way out
        pass
    try:
        metrics.get_meter_provider().shutdown()
    except Exception:  # noqa: BLE001, S110 — best effort on the way out
        pass


def inject_traceparent(message: dict[str, Any]) -> None:
    """Stamp the current trace context onto a dispatch message in place.

    Writes nothing unless a span is actually active, so payloads never
    carry dummy trace IDs — including when telemetry is disabled, where
    the global tracer only hands out invalid contexts.
    """
    span = trace.get_current_span()
    if not span.get_span_context().is_valid:
        return
    propagate.inject(message)


def extract_context(message: dict[str, Any] | None) -> Any | None:
    """Pull a parent trace context out of a dispatch message, if present."""
    if not isinstance(message, dict) or TRACEPARENT_KEY not in message:
        return None
    return propagate.extract(message)


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
            "bifrost.run.started", description="Claimed runs by handler"
        )
        _run_duration = _meter.create_histogram(
            "bifrost.run.duration", unit="s", description="Claim-to-finish seconds by handler and status"
        )
        _run_failed = _meter.create_counter(
            "bifrost.run.failed", description="Runs ending in error by handler"
        )
    return _run_started, _run_duration, _run_failed


@contextlib.contextmanager
def run_span(*, handler: str, run_id: str, message: dict[str, Any] | None) -> Iterator[Any]:
    """Server span for one claimed run, linked to the dispatching trace.

    Records started/duration/failed metrics with handler + status
    dimensions. Exceptions propagate after marking the span failed.
    """
    started_counter, duration_histogram, failed_counter = _instruments()
    parent = extract_context(message)
    tracer = trace.get_tracer(__name__)
    attributes = {"bifrost.handler": handler, "bifrost.run.id": run_id}
    started_counter.add(1, {"bifrost.handler": handler})
    start = time.monotonic()
    status = "ok"
    try:
        # record_exception / set_status_on_exception are off on purpose:
        # the SDK defaults would attach the exception message and stack
        # frames, which can carry user data. Failure is signaled by the
        # ERROR status (type only) plus the failed-run counter instead.
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
