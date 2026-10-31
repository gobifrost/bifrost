from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import integration_diagnostic  # noqa: E402


def record(variant: str, request_id: str = "request-1") -> dict:
    sites = 88 if variant in {"shallow_list", "detail_hydration"} else 0
    details = 88 if variant == "detail_hydration" else 0
    return {"id": "execution-1", "status": "Success", "result": {
        "ok": True, "variant": variant, "request_id": request_id,
        "workload_version": integration_diagnostic.WORKLOAD_VERSION,
        "site_count": sites, "detail_count": details,
        "workflow_spans": {"workflow_wall_ms": 1.0, "sdk_mapping_wall_ms": 0.5, "provider_wall_ms": 0.5},
    }}


class InteractiveFixtureTest(unittest.TestCase):
    def test_fixture_reuses_import_graph_and_declares_all_comparison_variants(self) -> None:
        fixture = integration_diagnostic.fixture_manifest()
        self.assertEqual(integration_diagnostic.validate_fixture_manifest(fixture), fixture)
        self.assertEqual((fixture["file_count"], fixture["source_bytes"]), (20, 411_390))
        self.assertIn("bench_import_fixture/graph/providers.py", fixture["files"])
        self.assertEqual(set(integration_diagnostic.WORKFLOW_NAMES), {"control", "same_body_graph", "shallow_list", "detail_hydration"})
        self.assertEqual(integration_diagnostic.CLASS_VARIANTS["wf-interactive-detail-hydration"], "detail_hydration")
        self.assertIn("asyncio.Semaphore(DETAIL_FANOUT)", fixture["files"]["bench_interactive_fixture/shared/workload.py"])
        self.assertIn("await integrations.get", fixture["files"]["bench_interactive_fixture/shared/authorization.py"])
        self.assertIn("httpx.AsyncClient", fixture["files"]["bench_interactive_fixture/shared/client.py"])
        self.assertIn("module_09", fixture["files"]["bench_interactive_fixture/catalog/entry.py"])

    def test_manifest_rejects_bad_source_evidence_and_unsafe_paths(self) -> None:
        fixture = integration_diagnostic.fixture_manifest()
        unsafe = copy.deepcopy(fixture)
        unsafe["files"]["../escape.py"] = unsafe["files"].pop("bench_interactive_fixture/shared/client.py")
        with self.assertRaises(ValueError):
            integration_diagnostic.validate_fixture_manifest(unsafe)
        altered = copy.deepcopy(fixture)
        altered["source_sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            integration_diagnostic.validate_fixture_manifest(altered)

    def test_detail_result_requires_88_reads_and_correlates_exact_provider_spans(self) -> None:
        item = record("detail_hydration")
        spans = ([{"tag": "request-1", "kind": "sites"}]
                 + [{"tag": "request-1", "kind": "site_detail"} for _ in range(88)])
        result = integration_diagnostic.correlate_provider_spans([item], spans, "detail_hydration")
        self.assertEqual(result["overall"], "pass")
        self.assertEqual(result["provider_counts"]["request-1"], {"sites": 1, "site_detail": 88})
        self.assertEqual(result["per_execution"]["execution-1"]["workflow_spans"]["workflow_wall_ms"], 1.0)

    def test_same_body_graph_rejects_unexpected_provider_span(self) -> None:
        result = integration_diagnostic.correlate_provider_spans(
            [record("same_body_graph")], [{"tag": "request-1", "kind": "sites"}], "same_body_graph"
        )
        self.assertEqual(result["overall"], "invalid")
        self.assertEqual(result["invalid_spans"], [{"tag": "request-1", "kind": "sites"}])


if __name__ == "__main__":
    unittest.main()
