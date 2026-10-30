from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "incluster"))

import soak_metrics  # noqa: E402


class MemorySlopeTest(unittest.TestCase):
    def test_reports_each_observed_pod_after_thirty_minutes(self) -> None:
        samples = [
            {"at": 1_000.0, "pods": {"bench-tenant/bifrost-api-a": {"memory_bytes": {"current": 10}}}},
            {"at": 2_800.0, "pods": {"bench-tenant/bifrost-api-a": {"memory_bytes": {"current": 20}}}},
            {"at": 4_600.0, "pods": {"bench-tenant/bifrost-api-a": {"memory_bytes": {"current": 30}}}},
        ]

        slopes = soak_metrics.memory_slopes(samples, started_at=1_000.0)

        self.assertEqual(slopes["bench-tenant/bifrost-api-a"]["sample_count"], 2)
        self.assertEqual(slopes["bench-tenant/bifrost-api-a"]["coverage"], "observed")
        self.assertAlmostEqual(slopes["bench-tenant/bifrost-api-a"]["slope_bytes_per_hour"], 20.0)

    def test_marks_missing_coverage_without_inventing_a_slope(self) -> None:
        slopes = soak_metrics.memory_slopes(
            [{"at": 2_900.0, "pods": {"bench-tenant/bifrost-api-a": {"memory_bytes": {"current": 20}}}}],
            started_at=1_000.0,
        )

        result = slopes["bench-tenant/bifrost-api-a"]
        self.assertEqual(result["coverage"], "insufficient_samples")
        self.assertIsNone(result["slope_bytes_per_hour"])

    def test_accepts_existing_host_sampler_interval_shape(self) -> None:
        slopes = soak_metrics.memory_slopes([
            {"at": 2_800.0, "pods": {"bench-tenant/bifrost-api-a": {"memory_bytes": {"current": 20}}}},
            {"at": 4_600.0, "pods": {"bench-tenant/bifrost-api-a": {"memory_bytes": {"current": 30}}}},
        ], started_at=1_000.0)

        self.assertEqual(slopes["bench-tenant/bifrost-api-a"]["coverage"], "observed")
        self.assertEqual(slopes["bench-tenant/bifrost-api-a"]["first_seen_s"], 1_800.0)

    def test_filters_benchmark_infrastructure_and_aggregates_tenant_roles(self) -> None:
        samples = [
            {"at": 2_800.0, "pods": {
                "bench-tenant/bifrost-api-a": {"memory_bytes": {"current": 20}},
                "bench-tenant/bifrost-api-b": {"memory_bytes": {"current": 30}},
                "bench-infra/driver": {"memory_bytes": {"current": 9_999}},
            }},
            {"at": 4_600.0, "pods": {
                "bench-tenant/bifrost-api-a": {"memory_bytes": {"current": 30}},
                "bench-tenant/bifrost-api-b": {"memory_bytes": {"current": 50}},
                "bench-infra/driver": {"memory_bytes": {"current": 19_999}},
            }},
        ]

        slopes = soak_metrics.memory_slopes(samples, 1_000.0, include=lambda name: name.startswith("bench-tenant/"))
        roles = soak_metrics.role_memory_slopes(
            samples, 1_000.0, lambda _name: "api", include=lambda name: name.startswith("bench-tenant/"),
        )

        self.assertNotIn("bench-infra/driver", slopes)
        self.assertAlmostEqual(roles["api"]["slope_bytes_per_hour"], 60.0)
        self.assertEqual(roles["api"]["pods_per_sample"], [2, 2])


class TerminalCountsTest(unittest.TestCase):
    def test_failed_terminals_stay_in_the_growth_denominator(self) -> None:
        counts = soak_metrics.terminal_counts([
            {"id": "correct", "outcome": "correct"}, {"id": "failed", "outcome": "failed"},
            {"id": "unfinished", "outcome": "unfinished"},
        ], {"outcomes": {
            "correct": {"kind": "workflow", "status": "Success", "completed": 1.0},
            "failed": {"kind": "workflow", "status": "Failed", "completed": 2.0},
            "unfinished": {"kind": "workflow", "status": "Running", "completed": None},
        }})

        self.assertEqual(counts, {"durably_terminal": 2, "correct_successes": 1, "unfinished": 1})

    def test_rejects_missing_duplicate_and_nonterminal_completion_proof(self) -> None:
        records = [{"id": "ok", "outcome": "correct", "http_status": 200}]
        with self.assertRaisesRegex(ValueError, "no durable outcome"):
            soak_metrics.terminal_counts(records, {"outcomes": {}, "missing": ["ok"]})
        with self.assertRaisesRegex(ValueError, "duplicate"):
            soak_metrics.terminal_counts(records, {"outcomes": {}, "duplicates": {"ok": 2}})
        with self.assertRaisesRegex(ValueError, "nonterminal"):
            soak_metrics.terminal_counts(records, {"outcomes": {
                "ok": {"kind": "workflow", "status": "Running", "completed": 1.0},
            }})

    def test_rejects_accepted_request_without_durable_outcome(self) -> None:
        with self.assertRaisesRegex(ValueError, "no durable outcome"):
            soak_metrics.terminal_counts([{"id": "ok", "http_status": 200}], {"outcomes": {}})

    def test_terminal_statuses_are_required(self) -> None:
        records = [{"id": "ok", "outcome": "correct"}, {"id": "failed", "outcome": "failed"}]
        counts = soak_metrics.terminal_counts(records, {"outcomes": {
            "ok": {"kind": "agent", "status": "completed", "completed": 1.0},
            "failed": {"kind": "agent", "status": "failed", "completed": 2.0},
        }})

        self.assertEqual(counts, {"durably_terminal": 2, "correct_successes": 1, "unfinished": 0})

    def test_rejects_nonfinite_terminal_completion_timestamp(self) -> None:
        for completed in (float("nan"), float("inf")):
            with self.subTest(completed=completed), self.assertRaisesRegex(ValueError, "completion proof"):
                soak_metrics.terminal_counts([{"id": "terminal"}], {"outcomes": {
                    "terminal": {"kind": "workflow", "status": "Success", "completed": completed},
                }})


class GrowthTest(unittest.TestCase):
    def test_growth_per_thousand_uses_all_terminal_records(self) -> None:
        growth = soak_metrics.per_thousand({"db_bytes": 500, "wal_generated_bytes": 100, "object_bytes": 50, "object_count": 2}, 25)

        self.assertEqual(growth["db_bytes_per_1000_terminal"], 20_000.0)
        self.assertEqual(growth["wal_generated_bytes_per_1000_terminal"], 4_000.0)
        self.assertEqual(growth["object_bytes_per_1000_terminal"], 2_000.0)
        self.assertEqual(growth["object_count_per_1000_terminal"], 80.0)
