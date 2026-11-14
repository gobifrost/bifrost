"""Unit tests for child-side telemetry after ``os.fork``.

Fork copies memory but no threads, so a child must install its own
providers. The OTLP exporter package is faked in ``sys.modules``: these
tests need no collector, database, or network.
"""

import sys
import types

import pytest

pytest.importorskip("opentelemetry")

import opentelemetry.metrics._internal as metrics_internal
from opentelemetry import trace as trace_api
from opentelemetry.sdk.metrics.export import MetricExporter, MetricExportResult
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags

from src.core import telemetry

_ENDPOINT = "http://collector.invalid:4318"
_TRACE_ID = int("ab" * 16, 16)
_SPAN_ID = int("cd" * 8, 16)


class _FakeSpanExporter(InMemorySpanExporter):
    """In-memory stand-in for ``OTLPSpanExporter``."""

    created: list["_FakeSpanExporter"] = []

    def __init__(self, *args, **kwargs):
        super().__init__()
        self.created.append(self)


class _BrokenSpanExporter:
    def __init__(self, *args, **kwargs):
        raise RuntimeError("exporter unavailable")


class _FakeMetricExporter(MetricExporter):
    def export(self, metrics_data, timeout_millis=10_000, **kwargs):
        return MetricExportResult.SUCCESS

    def force_flush(self, timeout_millis=10_000):
        return True

    def shutdown(self, timeout_millis=30_000, **kwargs):
        pass


def _fake_exporters(monkeypatch, span_exporter=_FakeSpanExporter):
    _FakeSpanExporter.created.clear()
    _fake_module(
        monkeypatch,
        "opentelemetry.exporter.otlp.proto.http.trace_exporter",
        "OTLPSpanExporter",
        span_exporter,
    )
    _fake_module(
        monkeypatch,
        "opentelemetry.exporter.otlp.proto.http.metric_exporter",
        "OTLPMetricExporter",
        _FakeMetricExporter,
    )


def _fake_module(monkeypatch, name, attr, value):
    module = types.ModuleType(name)
    setattr(module, attr, value)
    monkeypatch.setitem(sys.modules, name, module)


@pytest.fixture(autouse=True)
def _isolate_global_state(monkeypatch):
    """Child init replaces process-global providers; put them back so other
    test modules sharing this interpreter are unaffected."""
    saved = (
        trace_api._TRACER_PROVIDER,
        trace_api._TRACER_PROVIDER_SET_ONCE._done,
        metrics_internal._METER_PROVIDER,
        metrics_internal._METER_PROVIDER_SET_ONCE._done,
    )
    for name, value in (
        ("_initialized", False),
        ("_child_initialized", False),
        ("_meter", None),
        ("_run_started", None),
        ("_run_duration", None),
        ("_run_failed", None),
    ):
        monkeypatch.setattr(telemetry, name, value)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    yield
    # Stop the fake child's metric reader thread before dropping the provider.
    telemetry.flush_telemetry_in_child()
    trace_api._TRACER_PROVIDER, trace_api._TRACER_PROVIDER_SET_ONCE._done = saved[0], saved[1]
    (
        metrics_internal._METER_PROVIDER,
        metrics_internal._METER_PROVIDER_SET_ONCE._done,
    ) = saved[2], saved[3]


def _parent_context():
    parent = NonRecordingSpan(
        SpanContext(
            trace_id=_TRACE_ID,
            span_id=_SPAN_ID,
            is_remote=True,
            trace_flags=TraceFlags(TraceFlags.SAMPLED),
        )
    )
    return trace_api.set_span_in_context(parent)


def test_noop_when_disabled():
    before = trace_api._TRACER_PROVIDER
    assert telemetry.init_telemetry_in_child() is False
    assert trace_api._TRACER_PROVIDER is before
    # Flushing an uninitialized child is a no-op too.
    telemetry.flush_telemetry_in_child()


def test_child_spans_export_under_supplied_parent(monkeypatch):
    _fake_exporters(monkeypatch)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", _ENDPOINT)
    assert telemetry.init_telemetry_in_child() is True

    (sink,) = _FakeSpanExporter.created
    tracer = trace_api.get_tracer(__name__)
    with tracer.start_as_current_span("child.http", context=_parent_context()) as span:
        span.set_attribute("bifrost.child", True)

    # SimpleSpanProcessor exports on span end: no flush needed.
    (recorded,) = sink.get_finished_spans()
    assert recorded.name == "child.http"
    assert recorded.attributes["bifrost.child"] is True
    assert recorded.context.trace_id == _TRACE_ID
    assert recorded.parent is not None
    assert recorded.parent.span_id == _SPAN_ID


def test_idempotent(monkeypatch):
    _fake_exporters(monkeypatch)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", _ENDPOINT)
    assert telemetry.init_telemetry_in_child() is True
    assert telemetry.init_telemetry_in_child() is True
    # The second call installs nothing new.
    assert len(_FakeSpanExporter.created) == 1


def test_never_raises_when_setup_fails(monkeypatch):
    _fake_exporters(monkeypatch, span_exporter=_BrokenSpanExporter)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", _ENDPOINT)
    try:
        result = telemetry.init_telemetry_in_child()
    except Exception as e:  # noqa: BLE001 — must never propagate
        pytest.fail(f"init_telemetry_in_child raised: {e}")
    assert result is False


def test_child_exporter_is_synchronous_and_scrubbing(monkeypatch):
    _fake_exporters(monkeypatch)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", _ENDPOINT)
    assert telemetry.init_telemetry_in_child() is True

    processors = trace_api.get_tracer_provider()._active_span_processor._span_processors
    assert not any(isinstance(p, BatchSpanProcessor) for p in processors)
    (processor,) = processors
    assert isinstance(processor, SimpleSpanProcessor)
    assert isinstance(processor.span_exporter, telemetry.ScrubbingSpanExporter)

    (sink,) = _FakeSpanExporter.created
    with trace_api.get_tracer(__name__).start_as_current_span("auto") as span:
        span.set_attribute("db.statement", "SELECT token FROM sessions")
        span.set_attribute("db.system", "postgresql")
    (recorded,) = sink.get_finished_spans()
    assert "db.statement" not in recorded.attributes
    assert recorded.attributes["db.system"] == "postgresql"
