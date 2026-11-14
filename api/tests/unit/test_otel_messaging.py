"""OTel messaging spans around the work-dispatch queue hop.

``stage_dispatch`` publishes (PRODUCER) and ``PoolWorker._run_claim``
consumes (CONSUMER), so one trace reads request -> publish -> process ->
run. No DB, cache or network: the dispatch is staged through a stub
session and the claim runs a stub agent consumer.

The global tracer provider can only be set once per process, so each test
points ``opentelemetry.trace.get_tracer`` (the one lookup both modules under
test make) at its own provider with an in-memory exporter. Nothing global is
installed, so this file and ``test_telemetry.py`` stay independent.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

pytest.importorskip("opentelemetry")

from src.services.work_dispatch import stage_dispatch
from src.worker.admission import Admission
from src.worker.pool_main import PoolClaim, PoolWorker


class _StubDb:
    """`stage_dispatch` only adds to its caller's session."""

    def __init__(self) -> None:
        self.added: list = []

    def add(self, obj) -> None:  # type: ignore[no-untyped-def]
        self.added.append(obj)


class _StubConsumer:
    async def process_message(self, body: dict) -> None:
        return None


@pytest.fixture
def exported(monkeypatch):
    """Spans the code under test records, with the global provider untouched."""
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(trace, "get_tracer", provider.get_tracer)
    return exporter


def _tracer():
    from opentelemetry import trace

    return trace.get_tracer(__name__)


def _worker() -> PoolWorker:
    worker = PoolWorker(incarnation_id=uuid4())
    worker.admission = Admission(
        budget_bytes=1024 * 1024 * 1024,
        under_pressure=lambda: False,
        running_workflow_ids=set,
        workflow_growth=dict,
    )
    worker._agent_consumer = _StubConsumer()  # type: ignore[assignment]
    return worker


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.setenv("BIFROST_SECRET_KEY", "k" * 40)
    yield


async def test_a_queue_hop_is_one_trace(exported, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from opentelemetry.trace import SpanKind

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector.invalid")
    subject_id = uuid4()
    queued_at = datetime.now(timezone.utc)
    with _tracer().start_as_current_span("request"):
        dispatch = stage_dispatch(
            _StubDb(),  # type: ignore[arg-type]
            family="agent_run",
            subject_id=subject_id,
            handler="agent.run",
            handler_version=1,
            message={"dispatch_id": "unused", "user": "content"},
            payload={"parameters": {"secret": "content"}},
            memory_request_bytes=1024,
            queued_at=queued_at,
        )
    await _worker()._run_claim(
        PoolClaim(
            dispatch_id=dispatch.id,
            handler="agent.run",
            message=dispatch.message,
            subject_id=subject_id,
            memory_request_bytes=1024,
            queued_at=queued_at,
        )
    )

    spans = {span.name: span for span in exported.get_finished_spans()}
    publish = spans["agent_run publish"]
    process = spans["agent.run process"]
    run = spans["run.agent.run"]

    assert publish.kind is SpanKind.PRODUCER
    assert dict(publish.attributes) == {
        "messaging.system": "postgresql",
        "messaging.operation": "publish",
        "messaging.destination.name": "agent_run",
        "bifrost.dispatch.id": str(dispatch.id),
    }
    assert process.kind is SpanKind.CONSUMER
    assert dict(process.attributes) == {
        "messaging.system": "postgresql",
        "messaging.operation": "process",
        "messaging.destination.name": "agent.run",
        "messaging.message.id": str(dispatch.id),
    }
    # The payload carries the producer's context, not the request's.
    assert dispatch.message["traceparent"] == (
        f"00-{publish.context.trace_id:032x}-{publish.context.span_id:016x}-01"
    )
    assert publish.parent.span_id == spans["request"].context.span_id
    assert process.parent.span_id == publish.context.span_id
    assert run.parent.span_id == process.context.span_id
    assert run.context.trace_id == publish.context.trace_id


def test_disabled_telemetry_stages_nothing(exported) -> None:  # type: ignore[no-untyped-def]
    dispatch = stage_dispatch(
        _StubDb(),  # type: ignore[arg-type]
        family="agent_run",
        subject_id=uuid4(),
        handler="agent.run",
        handler_version=1,
        message={"user": "content"},
        payload={"parameters": {"secret": "content"}},
        memory_request_bytes=1024,
    )

    assert "traceparent" not in dispatch.message
    assert exported.get_finished_spans() == ()
