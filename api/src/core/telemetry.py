"""OpenTelemetry setup: traces + metrics to operator backends. Default off.

Everything here degrades to no-ops when ``OTEL_EXPORTER_OTLP_ENDPOINT`` is
unset or empty. Only the already-required OpenTelemetry API is imported
here; SDK/exporter/instrumentor code is loaded after the endpoint gate.
Disabled helpers do not use
other libraries' independently configured providers or propagate their context.

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
from typing import TYPE_CHECKING, Any, Iterator

from opentelemetry import metrics, trace
from opentelemetry.trace import SpanKind, Status, StatusCode
from opentelemetry.trace.propagation.tracecontext import (
    TraceContextTextMapPropagator,
)

if TYPE_CHECKING:
    from src.core.telemetry_sdk import _ChildSpanBuffer

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
        # Raw URLs and paths: they carry ids, slugs and (on outbound calls)
        # credentials. The route template (http.route) remains.
        "url.full",
        "url.path",
        "url.query",
        "http.url",
        "http.target",
    }
)
# Captured headers are opt-in for operators, but a credential in a header
# must never reach a collector even then.
_DROPPED_ATTRIBUTE_PREFIXES = (
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


def init_telemetry(role: str = "api") -> bool:
    """Initialize providers, exporters, and instrumentors. Idempotent.

    ``role`` selects the default service name (``api``, ``worker``) and is
    recorded as ``bifrost.process.role``. Returns True when telemetry is
    active, False when disabled (no endpoint) or when setup failed. Never
    raises: observability must not break startup.
    """
    global _initialized, _relay_processor
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

        from src.core.telemetry_sdk import ScrubbingSpanExporter

        resource = _resource(role)
        trace.set_tracer_provider(
            TracerProvider(resource=resource, sampler=_sampler())
        )
        processor = BatchSpanProcessor(ScrubbingSpanExporter(OTLPSpanExporter()))
        trace.get_tracer_provider().add_span_processor(processor)
        _relay_processor = processor
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

        # One server span per request. The per-message send/receive spans
        # tripled the API's span volume and said nothing the server span
        # does not.
        FastAPIInstrumentor.instrument_app(
            app,
            exclude_spans=["send", "receive"],
            excluded_urls=(
                os.environ.get("OTEL_PYTHON_FASTAPI_EXCLUDED_URLS")
                or _DEFAULT_EXCLUDED_URLS
            ),
        )
        _start_each_request_untraced(app)
        return True
    except Exception as e:  # noqa: BLE001 — never break startup
        logger.warning("FastAPI instrumentation unavailable: %s", e)
        return False


def _start_each_request_untraced(app: Any) -> None:
    """Run every request under an empty trace context.

    asyncio can carry one request's context into the next request on the
    same keep-alive connection (cpython#140947): uvicorn resumes reading
    from inside the finished request, so the next request's task copies its
    context. The ASGI instrumentor then sees a current span, ignores the
    incoming ``traceparent`` and nests the new request under the old one.
    Only the trace context is reset; the app's own context variables are
    set per request already.
    """
    from opentelemetry import context as otel_context

    build_instrumented_stack = app.build_middleware_stack

    def build_middleware_stack() -> Any:
        stack = build_instrumented_stack()

        async def untraced(scope: Any, receive: Any, send: Any) -> None:
            token = otel_context.attach(otel_context.Context())
            try:
                await stack(scope, receive, send)
            finally:
                otel_context.detach(token)

        return untraced

    app.build_middleware_stack = build_middleware_stack


def _instrument_libraries() -> None:
    """Auto-instrumentation for a process that serves requests and owns
    runtime metrics. Each import is local so a missing optional package
    degrades to one warning instead of a failed startup.

    Database and Redis clients are not instrumented. Their spans were most
    of the export volume, and most of the measured request overhead, while
    showing only pool checkouts, transaction statements and single cache
    commands. Query cost comes from ``pg_stat_statements`` instead. See
    docs/architecture/otel-observability.md.
    """
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
    """Outbound HTTP spans for a long-lived process. Run children instrument
    the HTTP client in :func:`init_telemetry_in_child` instead."""
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


# Spans a forked run child records travel back to its parent with the run
# result instead of being exported from the child: the child is one-shot, so
# an exporter there would mean a collector connection per run and an exit
# flush that holds the process when the collector is slow. The parent
# already owns a long-lived batch exporter.
CHILD_SPANS_KEY = "_otel_spans"
_CHILD_SPAN_LIMIT = 512
_child_buffer: _ChildSpanBuffer | None = None
# Resolved once in the template before it forks (prepare_child_telemetry),
# so a child's set-up never imports modules or reads package metadata.
_child_resource: Any = None
_child_http_instrumentable = False
# Wall clock at child set-up; the first run's span starts here so fork and
# start-up time is attributed instead of appearing as a gap.
_child_started_ns: int | None = None
# The long-lived process's batch processor; relayed child spans enter here.
_relay_processor: Any = None


def prepare_child_telemetry() -> None:
    """Resolve, once in a template process, what its forked children need.

    Loads the SDK and HTTPX instrumentor modules, builds the run resource,
    runs the instrumentor's dependency check and rehearses a run's tracing
    once. Children inherit all of it copy-on-write, so per-run set-up stays
    off the import and package-metadata paths (tens of milliseconds per fork
    when left to the child) and off every first-use cost.
    Never raises, and a no-op when telemetry is disabled.
    """
    global _child_resource, _child_http_instrumentable
    if not is_enabled():
        return
    try:
        from opentelemetry.instrumentation.dependencies import get_dependency_conflicts
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        conflict = get_dependency_conflicts(
            HTTPXClientInstrumentor().instrumentation_dependencies()
        )
        resource = _resource("run")
        _rehearse_child_run(resource)
    except Exception as e:  # noqa: BLE001 — telemetry must never break a template
        logger.warning("OpenTelemetry child preparation failed, runs stay untraced: %s", e)
        return
    if conflict is not None:
        logger.warning("HTTPX instrumentation unavailable: %s", conflict)
    _child_http_instrumentable = conflict is None
    _child_resource = resource


def _rehearse_child_run(resource: Any) -> None:
    """Trace one throwaway run in the template, as a child would.

    A child's first tracer, span and buffered span each pay one-time costs
    (the SDK's warning-filter patterns, attribute and context machinery,
    the wire conversion). Paying them here instead saved about a quarter of
    a child's telemetry CPU. The provider is private and discarded: the
    template's global tracing and current context are left untouched.
    """
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.sampling import ALWAYS_ON

    from src.core.telemetry_sdk import _ChildSpanBuffer

    # Always sampled: an unsampled rehearsal would skip the buffer and the
    # wire conversion it exists to warm.
    provider = TracerProvider(resource=resource, sampler=ALWAYS_ON)
    buffer = _ChildSpanBuffer()
    provider.add_span_processor(buffer)
    tracer = provider.get_tracer(__name__)
    with tracer.start_as_current_span(
        "workflow.run", attributes={"bifrost.execution.id": "template"}
    ):
        tracer.start_span("workflow.startup", start_time=time.time_ns()).end()
    buffer.take()
    provider.shutdown()


def init_telemetry_in_child() -> bool:
    """Install child-local tracing in a process forked from a template.

    ``os.fork`` copies memory but no threads, so inherited providers are
    discarded. The child records spans into a bounded in-memory buffer that
    :func:`take_child_spans` drains into the run result; it starts no
    exporter thread, opens no collector connection and installs no metric
    reader (a one-shot run must not duplicate the owning process's runtime
    metrics). Only the HTTP client is instrumented. Requires the template to
    have run :func:`prepare_child_telemetry`; without it the child stays
    untraced.

    Idempotent, never raises, and a no-op when telemetry is disabled.
    """
    global _initialized, _child_buffer, _child_started_ns
    if _child_buffer is not None:
        return True
    if not is_enabled() or _child_resource is None:
        return False
    started_ns = time.time_ns()
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        from opentelemetry.sdk.trace import TracerProvider

        from src.core.telemetry_sdk import _ChildSpanBuffer

        _discard_inherited_providers()
        buffer = _ChildSpanBuffer()
        provider = TracerProvider(resource=_child_resource, sampler=_sampler())
        provider.add_span_processor(buffer)
        trace.set_tracer_provider(provider)
        if _child_http_instrumentable:
            # The template already ran the dependency check.
            HTTPXClientInstrumentor().instrument(skip_dep_check=True)
    except Exception as e:  # noqa: BLE001 — telemetry must never break a run
        logger.warning("OpenTelemetry child setup failed, continuing untraced: %s", e)
        _initialized = False
        return False
    _child_buffer = buffer
    _child_started_ns = started_ns
    _initialized = True
    logger.debug("OpenTelemetry enabled in forked child (pid=%s)", os.getpid())
    return True


def take_child_spans() -> list[dict[str, Any]]:
    """Drain the forked child's finished spans for the run result."""
    buffer = _child_buffer
    if buffer is None:
        return []
    spans = buffer.take()
    if buffer.dropped:
        logger.debug("OpenTelemetry child dropped %d span(s) past the limit", buffer.dropped)
        buffer.dropped = 0
    return spans


def relay_child_spans(wire_spans: Any) -> None:
    """Export spans a forked child returned, through this process's batch
    processor. Never raises; malformed entries are skipped."""
    processor = _relay_processor
    if processor is None or not isinstance(wire_spans, list):
        return
    from src.core.telemetry_sdk import _span_from_wire

    for wire in wire_spans[:_CHILD_SPAN_LIMIT]:
        try:
            processor.on_end(_span_from_wire(wire))
        except Exception as e:  # noqa: BLE001 — a bad span must not break result handling
            logger.debug("OpenTelemetry dropped a relayed child span: %s", e)


def _execution_attributes(execution_id: str, context: dict[str, Any]) -> dict[str, Any]:
    attributes: dict[str, Any] = {"bifrost.execution.id": execution_id}
    workflow_id = context.get("workflow_id")
    if workflow_id:
        attributes["bifrost.workflow.id"] = str(workflow_id)
    return attributes


@contextlib.contextmanager
def launch_span(execution_id: str, context: dict[str, Any]) -> Iterator[Any]:
    """Span for a remote launcher forking a run, parented by the dispatch.

    Covers the fork command from receipt to the work being handed to the
    child, including any wait for the template. Exceptions propagate after
    marking the span failed with their type only.
    """
    if not is_enabled():
        yield trace.INVALID_SPAN
        return
    tracer = trace.get_tracer(__name__)
    with tracer.start_as_current_span(
        "workflow.launch",
        context=extract_context(context),
        kind=SpanKind.INTERNAL,
        attributes=_execution_attributes(execution_id, context),
        record_exception=False,
        set_status_on_exception=False,
    ) as span:
        try:
            yield span
        except Exception as e:
            span.set_status(StatusCode.ERROR, type(e).__name__)
            raise


@contextlib.contextmanager
def child_run_span(execution_id: str, context: dict[str, Any]) -> Iterator[Any]:
    """Span for a forked child's run, parented by the dispatching span.

    Covers the child's whole execution (module load and workflow code), so
    outbound calls nest under it. A child's first run starts at its
    telemetry set-up, just after fork, and opens with a ``workflow.startup``
    span for the time before the work began. Call :func:`mark_child_run`
    with the result.
    """
    if not is_enabled():
        yield trace.INVALID_SPAN
        return
    global _child_started_ns
    started_ns, _child_started_ns = _child_started_ns, None
    tracer = trace.get_tracer(__name__)
    attributes = _execution_attributes(execution_id, context)
    with tracer.start_as_current_span(
        "workflow.run",
        context=extract_context(context),
        kind=SpanKind.INTERNAL,
        attributes=attributes,
        start_time=started_ns,
        record_exception=False,
        set_status_on_exception=False,
    ) as span:
        if started_ns is not None:
            tracer.start_span(
                "workflow.startup", start_time=started_ns, attributes=attributes
            ).end()
        yield span


def mark_child_run(span: Any, result: Any) -> None:
    """ERROR status (exception type only) when the run did not succeed."""
    if not is_enabled() or not isinstance(result, dict) or result.get("success", False):
        return
    error_type = result.get("error_type")
    span.set_status(
        StatusCode.ERROR,
        error_type if isinstance(error_type, str) and _TYPE_ONLY_RE.match(error_type) else None,
    )


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
    if not is_enabled():
        return
    span = trace.get_current_span()
    if not span.get_span_context().is_valid:
        return
    _PROPAGATOR.inject(message)


def extract_context(message: dict[str, Any] | None) -> Any | None:
    """Pull a parent trace context out of a dispatch message, if present."""
    if not is_enabled() or not isinstance(message, dict) or TRACEPARENT_KEY not in message:
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
    if not is_enabled():
        yield trace.INVALID_SPAN
        return
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
