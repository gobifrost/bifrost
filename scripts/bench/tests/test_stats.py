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


def _db(xact: int, rows: int, wal: int, tables: dict) -> dict:
    return {"xact": xact, "rows_written": rows, "wal_bytes": wal, "db_bytes": 0, "tables": tables}


class DbTest(unittest.TestCase):
    def test_delta_keeps_only_tables_that_changed(self) -> None:
        delta = stats.db_delta(
            _db(100, 10, 1000, {"executions": [5, 50], "users": [0, 9], "quiet": [1, 1]}),
            _db(160, 40, 9192, {"executions": [25, 80], "users": [0, 30], "quiet": [1, 1], "new": [2, 0]}),
            seconds=10.0,
        )
        self.assertEqual(delta["xact"], 60)
        self.assertEqual(delta["rows_written"], 30)
        self.assertEqual(delta["wal_bytes"], 8192)
        self.assertEqual(delta["tables"], {"executions": [20, 30], "users": [0, 21], "new": [2, 0]})

    def test_per_run_subtracts_idle_rate_over_the_window(self) -> None:
        idle = stats.db_rate(stats.db_delta(_db(0, 0, 0, {}), _db(30, 10, 10240, {}), seconds=10.0))
        self.assertEqual(idle, {"xact_per_s": 3.0, "rows_written_per_s": 1.0, "wal_kib_per_s": 1.0})
        window = stats.db_delta(_db(0, 0, 0, {}), _db(230, 110, 112640, {}), seconds=20.0)
        self.assertEqual(
            stats.db_per_run(window, idle, runs=10),
            {"xact": 17.0, "rows_written": 9.0, "wal_kib": 9.0},
        )

    def test_top_tables_by_reads_plus_writes(self) -> None:
        tables = {"a": [1, 1], "b": [10, 0], "c": [0, 5], "d": [3, 3]}
        self.assertEqual(stats.top_tables(tables, 2), [("b", 10, 0), ("d", 3, 3)])


class WorkerSamplesTest(unittest.TestCase):
    def test_peaks_across_samples_and_pods(self) -> None:
        def proc(pid: int, depth: int, mib: float) -> dict:
            return {"pid": pid, "ppid": 0, "depth": depth, "private_mib": mib}

        samples = [
            {"pod-a": [proc(1, 0, 24), proc(15, 1, 48), proc(40, 2, 20)], "pod-b": [proc(1, 0, 24), proc(15, 1, 50)]},
            {"pod-a": [proc(1, 0, 24), proc(15, 1, 48), proc(41, 1, 168), proc(52, 2, 31), proc(53, 2, 22)]},
        ]
        self.assertEqual(stats.worker_children(samples), {
            "samples": 2, "peak_child_private_mib": 31.0, "peak_children": 2, "peak_template_private_mib": 168.0,
        })

    def test_no_samples(self) -> None:
        self.assertEqual(stats.worker_children([]), {
            "samples": 0, "peak_child_private_mib": 0.0, "peak_children": 0, "peak_template_private_mib": 0.0,
        })


if __name__ == "__main__":
    unittest.main()
