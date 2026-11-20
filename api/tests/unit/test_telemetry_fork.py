"""Unit tests for child-side telemetry after ``os.fork``.

Fork copies memory but no threads, so a child installs its own tracer
provider from what its template prepared before forking. It exports nothing itself: finished spans are buffered, returned
with the run result and exported by the parent's long-lived processor. The
OTLP exporter package is faked in ``sys.modules``: these tests need no
collector, database, or network.
"""

import sys
import types

import pytest

pytest.importorskip("opentelemetry")

import opentelemetry.metrics._internal as metrics_internal
from opentelemetry import trace as trace_api
from opentelemetry.sdk.metrics.export import MetricExporter, MetricExportResult
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import NonRecordingSpan, SpanContext, StatusCode, TraceFlags

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
        ("_child_buffer", None),
        ("_child_resource", None),
        ("_child_http_instrumentable", False),
        ("_child_started_ns", None),
        ("_relay_processor", None),
    ):
        monkeypatch.setattr(telemetry, name, value)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    yield
    trace_api._TRACER_PROVIDER, trace_api._TRACER_PROVIDER_SET_ONCE._done = saved[0], saved[1]
    (
        metrics_internal._METER_PROVIDER,
        metrics_internal._METER_PROVIDER_SET_ONCE._done,
    ) = saved[2], saved[3]


def _prepared_child(monkeypatch):
    """Enable telemetry and prepare as the template does, then set up the
    child the way a fork does."""
    _fake_exporters(monkeypatch)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", _ENDPOINT)
    telemetry.prepare_child_telemetry()
    assert telemetry.init_telemetry_in_child() is True


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
    telemetry.prepare_child_telemetry()
    assert telemetry._child_resource is None
    assert telemetry.init_telemetry_in_child() is False
    assert trace_api._TRACER_PROVIDER is before
    # Nothing is buffered, so there is nothing to return with the result.
    assert telemetry.take_child_spans() == []


def test_child_stays_untraced_without_template_preparation(monkeypatch):
    _fake_exporters(monkeypatch)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", _ENDPOINT)
    assert telemetry.init_telemetry_in_child() is False


def test_dependency_conflict_found_by_the_template_skips_http_instrumentation(monkeypatch):
    import opentelemetry.instrumentation.dependencies as dependencies

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", _ENDPOINT)
    monkeypatch.setattr(dependencies, "get_dependency_conflicts", lambda deps: "httpx 0.1 conflicts")
    telemetry.prepare_child_telemetry()
    assert telemetry._child_resource is not None
    assert telemetry._child_http_instrumentable is False


def test_child_buffers_spans_without_an_exporter(monkeypatch):
    _prepared_child(monkeypatch)

    # No exporter, thread or collector connection in the one-shot child.
    assert _FakeSpanExporter.created == []
    (processor,) = trace_api.get_tracer_provider()._active_span_processor._span_processors
    from src.core.telemetry_sdk import _ChildSpanBuffer

    assert isinstance(processor, _ChildSpanBuffer)

    with trace_api.get_tracer(__name__).start_as_current_span("child.http", context=_parent_context()) as span:
        span.set_attribute("bifrost.child", True)
        span.set_attribute("db.statement", "SELECT token FROM sessions")
    (wire,) = telemetry.take_child_spans()
    assert telemetry.take_child_spans() == []  # drained
    assert wire["name"] == "child.http" and wire["trace_id"] == _TRACE_ID
    assert wire["parent"] == (_TRACE_ID, _SPAN_ID, True)
    # Scrubbed before it leaves the child, not only at export.
    assert wire["attributes"] == {"bifrost.child": True}
    assert wire["resource"]["bifrost.process.role"] == "run"


def test_buffer_is_bounded(monkeypatch):
    _prepared_child(monkeypatch)
    telemetry._child_buffer._limit = 3
    tracer = trace_api.get_tracer(__name__)
    for i in range(5):
        with tracer.start_as_current_span(f"s{i}", context=_parent_context()):
            pass
    assert [w["name"] for w in telemetry.take_child_spans()] == ["s0", "s1", "s2"]


def test_child_run_span_parents_on_the_dispatch_and_marks_failure(monkeypatch):
    _prepared_child(monkeypatch)
    carrier = {}
    with trace_api.use_span(trace_api.get_current_span(_parent_context())):
        telemetry.inject_traceparent(carrier)
    context = {"workflow_id": "wf-1", **carrier}

    started_ns = telemetry._child_started_ns
    with telemetry.child_run_span("exec-1", context) as span:
        with trace_api.get_tracer(__name__).start_as_current_span("GET"):
            pass
        telemetry.mark_child_run(span, {"success": False, "error_type": "ValueError", "error": "secret"})
    with telemetry.child_run_span("exec-2", {}) as span:
        telemetry.mark_child_run(span, {"success": False, "error_type": "not a type: secret"})
    with telemetry.child_run_span("exec-3", {}) as span:
        telemetry.mark_child_run(span, {"success": True})

    startup, get, run1, run2, run3 = telemetry.take_child_spans()
    assert run1["name"] == "workflow.run" and run1["parent"][:2] == (_TRACE_ID, _SPAN_ID)
    # The first run starts at child set-up and opens with its start-up span.
    assert run1["start"] == startup["start"] == started_ns
    assert startup["name"] == "workflow.startup" and startup["parent"][1] == run1["span_id"]
    assert startup["end"] <= get["start"]
    assert run2["start"] > run1["end"]  # later runs start when they begin
    assert run1["attributes"] == {"bifrost.execution.id": "exec-1", "bifrost.workflow.id": "wf-1"}
    assert get["parent"][1] == run1["span_id"]  # outbound calls nest under the run
    assert run1["status"] == (StatusCode.ERROR.value, "ValueError")
    assert run2["status"] == (StatusCode.ERROR.value, None)  # message-like types are not exported
    assert run3["status"][0] == StatusCode.UNSET.value
    assert run2["parent"] is None  # no traceparent, so the run starts its own trace


def test_parent_relays_returned_spans_through_its_batch_processor(monkeypatch):
    sink = InMemorySpanExporter()
    processor = SimpleSpanProcessor(sink)
    monkeypatch.setattr(telemetry, "_relay_processor", processor)
    _prepared_child(monkeypatch)
    with trace_api.get_tracer(__name__).start_as_current_span("child-work", context=_parent_context()):
        pass
    wire = telemetry.take_child_spans()

    telemetry.relay_child_spans(wire + [{"name": "broken"}, "not a span"])
    (span,) = sink.get_finished_spans()
    assert span.name == "child-work"
    assert span.context.trace_id == _TRACE_ID and span.parent.span_id == _SPAN_ID
    assert span.resource.attributes["bifrost.process.role"] == "run"
    # Nothing to relay, or telemetry off in the parent: a silent no-op.
    telemetry.relay_child_spans(None)
    monkeypatch.setattr(telemetry, "_relay_processor", None)
    telemetry.relay_child_spans(wire)
    assert len(sink.get_finished_spans()) == 1


def test_idempotent(monkeypatch):
    _prepared_child(monkeypatch)
    buffer = telemetry._child_buffer
    assert telemetry.init_telemetry_in_child() is True
    assert telemetry._child_buffer is buffer


def test_never_raises_when_setup_fails(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", _ENDPOINT)
    telemetry.prepare_child_telemetry()

    def broken(*args, **kwargs):
        raise RuntimeError("sampler unavailable")

    monkeypatch.setattr(telemetry, "_sampler", broken)
    try:
        result = telemetry.init_telemetry_in_child()
    except Exception as e:  # noqa: BLE001 — must never propagate
        pytest.fail(f"init_telemetry_in_child raised: {e}")
    assert result is False


@pytest.mark.skipif(not hasattr(__import__("os"), "fork"), reason="requires os.fork")
@pytest.mark.filterwarnings("ignore:This process .* is multi-threaded")
def test_real_fork_child_returns_spans_over_a_pipe(monkeypatch):
    """A genuine fork: the child resets inherited providers, records a span
    and returns it through a multiprocessing pipe, the way the run result
    travels. The parent relays it to its own processor.

    The other tests here simulate the child in-process; this one proves the
    real boundary (inherited provider state, pickling) because that is what
    production exercises.
    """
    import multiprocessing
    import os

    _fake_exporters(monkeypatch)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", _ENDPOINT)
    sink = InMemorySpanExporter()
    monkeypatch.setattr(telemetry, "_relay_processor", SimpleSpanProcessor(sink))
    telemetry.prepare_child_telemetry()  # the template's step, before it forks

    recv, send = multiprocessing.Pipe(duplex=False)
    pid = os.fork()
    if pid == 0:
        # ----- child -----
        try:
            recv.close()
            started = telemetry.init_telemetry_in_child()
            with telemetry.child_run_span("exec-1", {}):
                pass
            send.send({"success": True, telemetry.CHILD_SPANS_KEY: telemetry.take_child_spans(), "started": started})
        finally:
            os._exit(0)

    # ----- parent -----
    # Close our copy of the send end so a child that dies before replying
    # surfaces as EOFError instead of a hang.
    send.close()
    assert recv.poll(10), "child did not reply"
    result = recv.recv()
    os.waitpid(pid, 0)
    assert result["started"] is True
    telemetry.relay_child_spans(result.pop(telemetry.CHILD_SPANS_KEY))
    startup, run = sink.get_finished_spans()
    assert (startup.name, run.name) == ("workflow.startup", "workflow.run")
    assert startup.parent.span_id == run.context.span_id
    assert run.attributes["bifrost.execution.id"] == "exec-1"


def test_launch_span_parents_on_the_dispatch_and_marks_failure(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", _ENDPOINT)
    from opentelemetry.sdk.trace import TracerProvider

    sink = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(sink))
    telemetry._discard_inherited_providers()
    trace_api.set_tracer_provider(provider)
    carrier = {}
    with trace_api.use_span(trace_api.get_current_span(_parent_context())):
        telemetry.inject_traceparent(carrier)

    with telemetry.launch_span("exec-1", {"workflow_id": "wf-1", **carrier}):
        pass
    with pytest.raises(MemoryError):
        with telemetry.launch_span("exec-2", {}):
            raise MemoryError("Launcher memory pressure rejects fork")

    ok, failed = sink.get_finished_spans()
    assert ok.name == "workflow.launch" and ok.parent.span_id == _SPAN_ID
    assert dict(ok.attributes) == {"bifrost.execution.id": "exec-1", "bifrost.workflow.id": "wf-1"}
    assert ok.status.status_code == StatusCode.UNSET
    assert failed.status.status_code == StatusCode.ERROR
    assert failed.status.description == "MemoryError"  # type only
    assert failed.events == ()
