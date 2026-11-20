"""Disabled telemetry must stay dormant even beside an independent provider."""

import os
import subprocess
import sys
import textwrap

import pytest


@pytest.mark.parametrize("endpoint", [None, ""])
@pytest.mark.parametrize("external_provider", [False, True])
def test_disabled_process_preserves_work_without_telemetry(endpoint, external_provider):
    # Provider registration is process-global and single assignment. A fresh
    # interpreter proves default startup instead of inheriting another test's SDK.
    script = textwrap.dedent('''
        import socket
        import sys
        import threading
        from opentelemetry import trace, metrics
        from src.core import telemetry

        def forbidden(*args, **kwargs):
            raise AssertionError("disabled telemetry attempted setup or networking")

        socket.socket.connect = forbidden
        socket.create_connection = forbidden
        spans = None
        if sys.argv[1] == "True":
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import SimpleSpanProcessor
            from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
            spans = InMemorySpanExporter()
            provider = TracerProvider()
            provider.add_span_processor(SimpleSpanProcessor(spans))
            trace.set_tracer_provider(provider)
        before_provider = trace.get_tracer_provider()
        before_meter = metrics.get_meter_provider()
        before_threads = {t.ident for t in threading.enumerate()}
        telemetry._resource = forbidden
        telemetry._instrument_libraries = forbidden
        assert telemetry.init_telemetry() is False
        assert telemetry.instrument_fastapi_app(object()) is False
        telemetry.prepare_child_telemetry()
        assert telemetry.init_telemetry_in_child() is False

        original = ValueError("original application failure")
        with trace.get_tracer("independent-library").start_as_current_span("external"):
            carrier = {"user": "unchanged"}
            telemetry.inject_traceparent(carrier)
            assert carrier == {"user": "unchanged"}
            assert telemetry.extract_context({"traceparent": "00-" + "ab"*16 + "-" + "cd"*8 + "-01"}) is None
            result = {"success": False, "error_type": "ValueError", "result": {"unchanged": True}}
            with telemetry.launch_span("run", {}) as launch:
                with telemetry.child_run_span("run", {}) as child:
                    telemetry.mark_child_run(child, result)
                    assert result == {"success": False, "error_type": "ValueError", "result": {"unchanged": True}}
            try:
                with telemetry.run_span(handler="workflow.execute", run_id="run", message=None):
                    raise original
            except ValueError as caught:
                assert caught is original
            else:
                raise AssertionError("original exception swallowed")
        assert telemetry.take_child_spans() == []
        telemetry.relay_child_spans(None)
        telemetry.shutdown_telemetry()
        assert trace.get_tracer_provider() is before_provider
        assert metrics.get_meter_provider() is before_meter
        assert {t.ident for t in threading.enumerate()} == before_threads
        assert telemetry._run_started is None
        assert telemetry._child_buffer is None
        assert not telemetry._initialized
        assert not telemetry._child_initialized
        assert not any(name.startswith("opentelemetry.instrumentation.") or name.startswith("opentelemetry.exporter.") for name in sys.modules)
        if spans is not None:
            assert [span.name for span in spans.get_finished_spans()] == ["external"]
    ''')
    env = os.environ.copy()
    for key in list(env):
        if key.startswith("OTEL_"):
            del env[key]
    if endpoint is not None:
        env["OTEL_EXPORTER_OTLP_ENDPOINT"] = endpoint
    result = subprocess.run(
        [sys.executable, "-c", script, str(external_provider)],
        env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
