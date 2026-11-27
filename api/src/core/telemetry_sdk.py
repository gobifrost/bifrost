"""SDK implementation loaded only for enabled operator telemetry.

Templates preload this module before forking enabled children. The disabled
path never imports it, so SDK classes do not enter unconfigured API/workers.
"""
from __future__ import annotations

import threading
from typing import Any

from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.trace import SpanKind, Status, StatusCode

from src.core.telemetry import _CHILD_SPAN_LIMIT, _scrub_attributes, _scrub_status


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


def _span_to_wire(span: ReadableSpan) -> dict[str, Any]:
    """Plain, picklable and already scrubbed form of a finished span."""
    ctx = span.context
    parent = span.parent
    status = _scrub_status(span.status)
    scope = span.instrumentation_scope
    return {
        "name": span.name,
        "trace_id": ctx.trace_id,
        "span_id": ctx.span_id,
        "trace_flags": int(ctx.trace_flags),
        "parent": None if parent is None else (parent.trace_id, parent.span_id, parent.is_remote),
        "kind": span.kind.value,
        "start": span.start_time,
        "end": span.end_time,
        "attributes": dict(_scrub_attributes(span.attributes) or {}),
        "status": (status.status_code.value, status.description),
        "resource": dict(span.resource.attributes),
        "scope": None if scope is None else (scope.name, scope.version),
    }


def _span_from_wire(wire: dict[str, Any]) -> ReadableSpan:
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.util.instrumentation import InstrumentationScope
    from opentelemetry.trace import SpanContext, TraceFlags

    parent = wire["parent"]
    scope = wire["scope"]
    return ReadableSpan(
        name=str(wire["name"]),
        context=SpanContext(
            trace_id=int(wire["trace_id"]),
            span_id=int(wire["span_id"]),
            is_remote=False,
            trace_flags=TraceFlags(int(wire["trace_flags"])),
        ),
        parent=None if parent is None else SpanContext(
            trace_id=int(parent[0]), span_id=int(parent[1]), is_remote=bool(parent[2]),
            trace_flags=TraceFlags(int(wire["trace_flags"])),
        ),
        resource=Resource(dict(wire["resource"])),
        attributes=dict(wire["attributes"]),
        events=[],
        links=[],
        kind=SpanKind(int(wire["kind"])),
        status=Status(StatusCode(int(wire["status"][0])), wire["status"][1]),
        instrumentation_scope=None if scope is None else InstrumentationScope(scope[0], scope[1]),
        start_time=wire["start"],
        end_time=wire["end"],
    )


class _ChildSpanBuffer(SpanProcessor):
    """Span processor that keeps a forked child's finished spans in memory.

    Thread-free and bounded: spans past the limit are counted and dropped.
    Spans are scrubbed as they end, so raw content never reaches the pipe.
    """

    def __init__(self, limit: int = _CHILD_SPAN_LIMIT) -> None:
        self._limit = limit
        self._lock = threading.Lock()
        self._spans: list[dict[str, Any]] = []
        self.dropped = 0

    def on_end(self, span: ReadableSpan) -> None:
        if not span.context.trace_flags.sampled:
            return
        with self._lock:
            if len(self._spans) >= self._limit:
                self.dropped += 1
                return
            self._spans.append(_span_to_wire(span))

    def take(self) -> list[dict[str, Any]]:
        with self._lock:
            spans, self._spans = self._spans, []
        return spans


