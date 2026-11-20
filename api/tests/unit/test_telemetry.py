"""Unit tests for src.core.telemetry.

Pure stdlib + opentelemetry assertions: no DB, cache, or network. The
module under test imports nothing from src, so these also run outside the
Docker stack.

The global tracer provider can only be set once per process, so a single
module-scoped provider is installed and each test attaches its own
in-memory exporter to it. Spans fan out to every attached processor, so
tests stay independent in any order.
"""

import pytest

pytest.importorskip("opentelemetry")

from src.core import telemetry
from src.core.telemetry import (
    ScrubbingSpanExporter,
    extract_context,
    inject_traceparent,
    is_enabled,
    run_span,
)

_provider = None


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    yield


@pytest.fixture(autouse=True)
def _provider_installed():
    """Every test needs a real provider: without one the global tracer is a
    proxy that reports invalid span contexts, so inject/extract no-op."""
    _ensure_provider()
    yield


def _ensure_provider():
    from opentelemetry.trace import set_tracer_provider

    global _provider
    if _provider is None:
        from opentelemetry.sdk.trace import TracerProvider

        _provider = TracerProvider()
        try:
            set_tracer_provider(_provider)
        except Exception:  # noqa: BLE001 — another test module already set one
            from opentelemetry import trace as trace_api

            _provider = trace_api.get_tracer_provider()  # type: ignore[assignment]
    return _provider


def _capture():
    """Attach a fresh in-memory exporter to the shared provider."""
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    exporter = InMemorySpanExporter()
    _ensure_provider().add_span_processor(SimpleSpanProcessor(exporter))
    return exporter


def _tracer():
    from opentelemetry import trace

    return trace.get_tracer(__name__)


def test_disabled_by_default():
    assert not is_enabled()
    # Helpers are safe no-ops when disabled.
    message: dict = {}
    inject_traceparent(message)
    assert message == {}
    assert extract_context(message) is None
    assert extract_context(None) is None
    assert extract_context({"other": 1}) is None


def test_inject_extract_round_trip():
    message: dict = {}
    with _tracer().start_as_current_span("parent"):
        inject_traceparent(message)
    assert "traceparent" in message
    assert extract_context(message) is not None


def test_run_span_records_handler_and_links_parent():
    exporter = _capture()
    message: dict = {}
    with _tracer().start_as_current_span("parent"):
        inject_traceparent(message)
    with run_span(handler="workflow.execute", run_id="run-1", message=message):
        pass
    spans = {s.name: s for s in exporter.get_finished_spans()}
    child = spans["run.workflow.execute"]
    assert child.attributes["bifrost.handler"] == "workflow.execute"
    assert child.attributes["bifrost.execution.id"] == "run-1"
    assert child.parent is not None
    assert child.parent.is_remote


def test_run_span_marks_error_without_user_data():
    from opentelemetry.trace import StatusCode

    exporter = _capture()
    raised: str | None = None
    try:
        with run_span(handler="agent.run", run_id="run-2", message={}):
            raise ValueError("user secret detail")
    except ValueError as exc:
        raised = str(exc)
    assert raised == "user secret detail"

    span = exporter.get_finished_spans()[-1]
    assert span.name == "run.agent.run"
    assert span.status.status_code == StatusCode.ERROR
    # Only the exception type is recorded: never message, never stack frames.
    assert span.status.description == "ValueError"
    assert "user secret detail" not in str(span.status)
    assert not span.events, "no exception event may be recorded"


def test_no_network_when_disabled(monkeypatch):
    """init_telemetry refuses to configure anything without an endpoint."""
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    assert telemetry.init_telemetry() is False


def test_export_scrubs_statements_urls_and_exceptions():
    from opentelemetry.trace import SpanKind, Status, StatusCode

    inner = _capture_inner()
    tracer = _tracer()
    with tracer.start_as_current_span(
        "auto", kind=SpanKind.CLIENT, record_exception=False
    ) as span:
        span.set_attribute("db.statement", "SELECT * FROM users WHERE email = 'a@b.c'")
        span.set_attribute("db.system", "postgresql")
        span.set_attribute("db.query.parameter.email", "a@b.c")
        span.set_attribute("http.target", "/api/files/read?token=secret&x=1")
        span.set_attribute("url.full", "https://host/api/files/read?token=secret")
        span.set_attribute("url.query", "token=secret")
        span.set_attribute("url.path", "/api/files/read")
        span.set_attribute("http.route", "/api/files/{path:path}")
        span.set_attribute("http.request.header.authorization", "Bearer secret")
        # Redis FT.SEARCH leaks the query and returned document contents.
        span.set_attribute("redis.search.query", "@email:{a@b.c}")
        span.set_attribute("redis.search.xdoc_1.email", "a@b.c")
        span.set_attribute("redis.search.total", 1)
        span.add_event(
            "exception",
            attributes={
                "exception.type": "ValueError",
                "exception.message": "user secret detail",
                "exception.stacktrace": "frame...",
            },
        )
        span.set_status(Status(StatusCode.ERROR, "ValueError: user secret detail"))
    ScrubbingSpanExporter(inner).export([span])
    (exported,) = inner.get_finished_spans()
    attrs = dict(exported.attributes)
    assert "db.statement" not in attrs
    assert "db.query.parameter.email" not in attrs
    assert "url.query" not in attrs
    assert "url.full" not in attrs
    assert "url.path" not in attrs
    assert "http.target" not in attrs
    assert "http.request.header.authorization" not in attrs
    assert "redis.search.query" not in attrs
    assert "redis.search.xdoc_1.email" not in attrs
    # Useful, non-content attributes survive.
    assert attrs["db.system"] == "postgresql"
    assert attrs["http.route"] == "/api/files/{path:path}"
    assert attrs["redis.search.total"] == 1
    # No span events survive export, exception or otherwise.
    assert list(exported.events) == []
    assert exported.status.status_code == StatusCode.ERROR
    assert not exported.status.description


def test_export_keeps_type_only_error_status():
    from opentelemetry.trace import Status, StatusCode

    inner = _capture_inner()
    with _tracer().start_as_current_span("manual", record_exception=False) as span:
        span.set_status(Status(StatusCode.ERROR, "RuntimeError"))
    ScrubbingSpanExporter(inner).export([span])
    (exported,) = inner.get_finished_spans()
    assert exported.status.description == "RuntimeError"


def _capture_inner():
    """A standalone exporter used only as the scrubber's downstream sink."""
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    return InMemorySpanExporter()


def test_sampler_env_is_honoured(monkeypatch):
    """OTEL_TRACES_SAMPLER/ARG actually reach the provider (design decision 6)."""
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry.sdk.trace.sampling import (
        ALWAYS_OFF,
        ParentBased,
        TraceIdRatioBased,
    )

    monkeypatch.setenv("OTEL_TRACES_SAMPLER", "always_off")
    assert telemetry._sampler() is ALWAYS_OFF

    monkeypatch.delenv("OTEL_TRACES_SAMPLER")
    monkeypatch.setenv("OTEL_TRACES_SAMPLER", "parentbased_traceidratio")
    monkeypatch.setenv("OTEL_TRACES_SAMPLER_ARG", "0.25")
    sampler = telemetry._sampler()
    assert isinstance(sampler, ParentBased)
    assert isinstance(sampler._root, TraceIdRatioBased)
    # The argument must actually reach the sampler, not just its type.
    assert getattr(sampler._root, "_rate", None) == 0.25

    # Unknown values fall back instead of failing.
    monkeypatch.setenv("OTEL_TRACES_SAMPLER", "not-a-sampler")
    assert isinstance(telemetry._sampler(), ParentBased)


def test_baggage_never_crosses_the_queue():
    """Only W3C trace context is propagated; baggage is not (design decision 5)."""
    from opentelemetry import baggage

    from opentelemetry.context import attach, detach

    message: dict = {}
    with _tracer().start_as_current_span("parent"):
        token = attach(baggage.set_baggage("tenant_secret", "must-not-travel"))
        try:
            inject_traceparent(message)
        finally:
            detach(token)
    assert "traceparent" in message
    assert "baggage" not in message

    # An incoming baggage header is ignored on extract.
    ctx = extract_context(
        {
            "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
            "baggage": "tenant_secret=must-not-travel",
        }
    )
    assert ctx is not None
    assert baggage.get_baggage("tenant_secret", ctx) is None


def test_run_span_carries_contract_attributes():
    """Names match the infra observability contract (see bifrost-infra)."""
    exporter = _capture()
    with run_span(
        handler="workflow.execute",
        run_id="exec-1",
        message={},
        workflow_id="wf-1",
        queue_wait_s=1.23456,
    ):
        pass
    span = exporter.get_finished_spans()[-1]
    assert span.attributes["bifrost.execution.id"] == "exec-1"
    assert span.attributes["bifrost.workflow.id"] == "wf-1"
    assert span.attributes["bifrost.queue.wait_s"] == 1.235
    assert "bifrost.run.id" not in span.attributes


def test_health_exclusions_match_the_url_the_instrumentor_builds():
    """ASGI matches excluded_urls against scheme://host/path, not the path."""
    import re

    patterns = telemetry._DEFAULT_EXCLUDED_URLS.split(",")
    compiled = [re.compile(p) for p in patterns]

    def excluded(url: str) -> bool:
        return any(p.search(url) for p in compiled)

    base = "http://api:8000"
    assert excluded(f"{base}/health")
    assert excluded(f"{base}/health/ready")
    assert excluded(f"{base}/ready")
    # Must not over-match a real route that merely starts with the prefix.
    assert not excluded(f"{base}/healthz-report")
    assert not excluded(f"{base}/api/health")
