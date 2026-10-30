from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import import_diagnostic  # noqa: E402


def payload(execution_id: str) -> dict:
    phases = {name: 0 for name in import_diagnostic.PHASES}
    kinds = {name: 0 for name in import_diagnostic.KINDS}
    return {
        "execution_id": execution_id,
        "metadata": {
            "schema_version": 1, "partial_start": 1, "child_wall_ms": 1.25,
            "phase_counts": phases, "phase_wall_ms": {name: 0.25 for name in phases},
            "module_resolve": {"transport_calls": 0, "transport_wall_ms": 0.0, "cache_hits": 0,
                               "kind_counts": kinds, "kind_wall_ms": {name: 0.0 for name in kinds}},
            "virtual_import": {"compile_calls": 0, "compile_wall_ms": 0.0, "exec_calls": 0, "exec_wall_ms": 0.0},
        },
    }


class FakeCluster:
    def __init__(self, logs: list[dict]) -> None:
        self.logs = logs

    def worker_log_sources(self) -> list[dict]:
        return self.logs

    def api_log_source(self) -> dict:
        return {"pod": "api-a", "pod_uid": "api-uid-a", "container": "api", "logs": "API ready"}


class ImportDiagnosticTest(unittest.TestCase):
    def test_fixture_is_small_deterministic_and_keeps_graph_import_lazy(self) -> None:
        first = import_diagnostic.fixture_manifest()
        second = import_diagnostic.fixture_manifest()
        self.assertEqual(first["source_sha256"], second["source_sha256"])
        self.assertEqual(first["file_count"], 6)
        control = first["files"]["workflows/bench_import_control.py"]
        graph = first["files"]["workflows/bench_import_graph.py"]
        self.assertIn("return await _import_body", control)
        self.assertIn("return await _import_body", graph)
        self.assertNotIn("requests", control)
        self.assertIn("from bench_import_fixture.graph.entry import facade", graph)
        self.assertLess(graph.index("from bench_import_fixture.graph.entry import facade"), graph.index("async def _import_body"))
        self.assertIn("namespace root", first["files"]["bench_import_fixture/graph/__init__.py"])
        self.assertIn("import requests", first["files"]["bench_import_fixture/graph/providers.py"])
        self.assertEqual(import_diagnostic.EXPECTED_OUTPUT["signature"], "import-fixture-v1")

    def test_capture_requires_exactly_one_valid_event_per_execution(self) -> None:
        lines = "INFO bench.import_trace " + json.dumps(payload("one")) + "\n"
        with tempfile.TemporaryDirectory() as temporary:
            evidence = import_diagnostic.capture(FakeCluster([{"pod": "worker-a", "pod_uid": "uid-a", "logs": lines}]), Path(temporary), "wf-import-graph", 1, ["one"])
        self.assertEqual(evidence["overall"], "pass")
        self.assertEqual(evidence["events"][0]["source"]["pod_uid"], "uid-a")
        self.assertEqual(evidence["sources"][0]["container"], "api")
        self.assertTrue(evidence["sources"][0]["raw_log_path"].endswith(".log"))

    def test_capture_marks_missing_duplicate_and_invalid_events_invalid(self) -> None:
        invalid = payload("two")
        invalid["metadata"]["child_wall_ms"] = True
        lines = "\n".join((
            "INFO bench.import_trace " + json.dumps(payload("one")),
            "INFO bench.import_trace " + json.dumps(payload("one")),
            "INFO bench.import_trace " + json.dumps(invalid),
        ))
        with tempfile.TemporaryDirectory() as temporary:
            evidence = import_diagnostic.capture(FakeCluster([{"pod": "worker-a", "pod_uid": "uid-a", "logs": lines}]), Path(temporary), "wf-import-graph", 1, ["one", "two"])
        self.assertEqual(evidence["overall"], "invalid")
        self.assertEqual(evidence["missing_execution_ids"], ["two"])
        self.assertEqual(evidence["duplicate_execution_ids"], ["one"])
        self.assertEqual(len(evidence["invalid_events"]), 1)

    def test_trace_rejects_boolean_schema_markers(self) -> None:
        for name in ("schema_version", "partial_start"):
            traced = payload("one")
            traced["metadata"][name] = True
            self.assertIsNone(import_diagnostic.validate_trace(traced))

    def test_execution_validation_requires_success_and_exact_fixture_output(self) -> None:
        records = [
            {"id": "correct", "status": "Success", "result": import_diagnostic.EXPECTED_OUTPUT},
            {"id": "wrong-output", "status": "Success", "result": {"ok": True}},
            {"id": "wrong-status", "status": "Failed", "result": import_diagnostic.EXPECTED_OUTPUT},
        ]
        validation = import_diagnostic.validate_execution_records(records)
        self.assertEqual(validation["overall"], "invalid")
        self.assertEqual(validation["failures"], ["wrong-output", "wrong-status"])
        self.assertEqual(validation["records"][0]["result"], import_diagnostic.EXPECTED_OUTPUT)

    def test_durable_timing_keeps_nullable_engine_duration_out_of_summary(self) -> None:
        summary = import_diagnostic.durable_timing([
            {"started_at": "2026-10-30T12:00:00Z", "completed_at": "2026-10-30T12:00:00.250Z", "duration_ms": None},
            {"started_at": "2026-10-30T12:00:01Z", "completed_at": "2026-10-30T12:00:01.500Z", "duration_ms": 7.5},
        ])
        self.assertEqual(summary["durable_started_to_completed_ms"], {"count": 2, "p50_ms": 250.0, "p95_ms": 500.0})
        self.assertEqual(summary["engine_duration_ms"], {"count": 1, "p50_ms": 7.5, "p95_ms": 7.5})


if __name__ == "__main__":
    unittest.main()
