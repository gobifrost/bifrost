from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import stats  # noqa: E402


def _record(status: str, created: str, started: str | None, completed: str | None) -> dict:
    return {"id": "x", "status": status, "created_at": created, "started_at": started, "completed_at": completed}


class PercentileTest(unittest.TestCase):
    def test_nearest_rank(self) -> None:
        values = [float(v) for v in range(1, 21)]
        self.assertEqual(stats.percentile(values, 0.50), 10.0)
        self.assertEqual(stats.percentile(values, 0.95), 19.0)
        self.assertEqual(stats.percentile([5.0], 0.95), 5.0)


class TimingsTest(unittest.TestCase):
    def test_queue_run_and_e2e_come_from_record_timestamps(self) -> None:
        result = stats.timings([
            _record("Success", "2026-10-27T00:00:00Z", "2026-10-27T00:00:00.500Z", "2026-10-27T00:00:02Z"),
        ])
        self.assertEqual(result["succeeded"], 1)
        self.assertEqual(result["queue_ms"]["p50"], 500.0)
        self.assertEqual(result["run_ms"]["p50"], 1500.0)
        self.assertEqual(result["e2e_ms"]["p50"], 2000.0)

    def test_failed_and_unfinished_records_are_counted_not_timed(self) -> None:
        result = stats.timings([
            _record("completed", "2026-10-27T00:00:00+00:00", "2026-10-27T00:00:01+00:00", "2026-10-27T00:00:02+00:00"),
            _record("Failed", "2026-10-27T00:00:00Z", "2026-10-27T00:00:01Z", "2026-10-27T00:00:09Z"),
            _record("Running", "2026-10-27T00:00:00Z", "2026-10-27T00:00:01Z", None),
        ])
        self.assertEqual(result["runs"], 3)
        self.assertEqual(result["succeeded"], 1)
        self.assertEqual(result["failed"], 2)
        self.assertEqual(result["e2e_ms"]["max"], 2000.0)


class CpuTest(unittest.TestCase):
    def test_cpu_per_run_by_role_skips_containers_that_came_or_went(self) -> None:
        before = [
            {"pod": "bifrost-api-1", "container": "api", "role": "api", "cpu_core_seconds": 10.0},
            {"pod": "bifrost-worker-a", "container": "worker", "role": "worker", "cpu_core_seconds": 5.0},
            {"pod": "bifrost-worker-gone", "container": "worker", "role": "worker", "cpu_core_seconds": 1.0},
        ]
        after = [
            {"pod": "bifrost-api-1", "container": "api", "role": "api", "cpu_core_seconds": 12.0},
            {"pod": "bifrost-worker-a", "container": "worker", "role": "worker", "cpu_core_seconds": 6.0},
            {"pod": "bifrost-worker-new", "container": "worker", "role": "worker", "cpu_core_seconds": 9.0},
            {"pod": "postgres-0", "container": "postgres", "role": "infra", "cpu_core_seconds": 3.0},
        ]
        self.assertEqual(stats.cpu_ms_per_run(before, after, 4), {"api": 500.0, "worker": 250.0})


if __name__ == "__main__":
    unittest.main()
