from __future__ import annotations

import sys
import unittest
from unittest.mock import Mock, patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ab  # noqa: E402


def _arm(image: str, idle: float, xact: float) -> dict:
    return {"api_image": image, "idle": {"xact_per_s": idle, "rollbacks_per_s": idle / 2},
            "xact_per_correct": xact, "rollbacks_per_correct": 1.0,
            "wal_lsn_kib_per_correct": 2.0, "wal_generated_kib_per_correct": 1.5,
            "api_cpu_s_per_correct": 0.01,
            "e2e_p95_s": {"wf-sdk": 0.5}, "overall": "pass"}


class IntervalTest(unittest.TestCase):
    def test_t_interval(self) -> None:
        result = ab.interval([1.0, 2.0, 3.0])
        self.assertAlmostEqual(result["mean"], 2.0)
        self.assertAlmostEqual(result["high"] - result["mean"], 4.303 * 1.0 / 3 ** 0.5, places=6)

    def test_single_value_has_no_interval_and_never_differs(self) -> None:
        self.assertFalse(ab.differs(ab.interval([1.0]), ab.interval([5.0, 5.1])))


class ArmCounterTest(unittest.TestCase):
    def test_arm_reports_pg_stat_wal_generated_bytes_per_correct_execution(self) -> None:
        def counters(xact: int, wal: int, generated: int) -> dict:
            return {
                "xact": xact, "rollbacks": 0, "rows_written": xact, "wal_bytes": wal,
                "wal_generated_bytes": generated, "wal_stats_reset": "stable", "tables": {},
            }

        cluster = Mock()
        cluster.db_counters.side_effect = [
            counters(0, 100, 10), counters(10, 1_124, 522),
            counters(10, 1_124, 522), counters(14, 2_148, 1_546),
        ]
        step = {
            "records": [{"outcome": "correct"}, {"outcome": "correct"}],
            "started": 0.0, "window": (0.0, 1.0), "host": [],
            "verdict": {"latency": {"wf-sdk": {"e2e_p95": {"value": 0.1}}}}, "overall": "pass",
        }
        profile = {"capacity": {"disk": {}}}
        plan = {"seed": 1, "ab_window_s": 1}
        with patch.object(ab.deploy, "reset"), patch.object(ab.time, "sleep"), \
             patch.object(ab.time, "monotonic", side_effect=[0.0, 10.0]), \
             patch.object(ab.search, "run_step", return_value=step):
            result = ab.arm(cluster, profile, plan, "image", 1.0, lambda _image: {})

        self.assertEqual(result["idle"]["wal_generated_kib_per_s"], 0.05)
        self.assertEqual(result["wal_lsn_kib_per_correct"], 0.5)
        self.assertEqual(result["wal_generated_kib_per_correct"], 0.5)


class CompareTest(unittest.TestCase):
    def test_disjoint_intervals_differ_and_overlapping_do_not(self) -> None:
        arms = [_arm("A", 9.0, 70), _arm("B", 4.5, 70), _arm("B", 4.6, 71), _arm("A", 9.1, 71), _arm("A", 9.2, 70), _arm("B", 4.4, 70)]
        rows = {row["metric"]: row for row in ab.compare(arms, {"a": "A", "b": "B"})}
        self.assertTrue(rows["idle transactions/s"]["differs"])
        self.assertFalse(rows["transactions per correct run"]["differs"])
        self.assertEqual(rows["steps passing all gates"], {"metric": "steps passing all gates", "a": "3/3", "b": "3/3"})
        self.assertIn("generated WAL KiB per correct run", rows)

    def test_a_variant_without_arms_yet_is_left_out(self) -> None:
        rows = {row["metric"]: row for row in ab.compare([_arm("A", 9.0, 70)], {"a": "A", "b": "B"})}
        self.assertNotIn("b", rows["wf-sdk e2e p95 s"])
        self.assertFalse(rows["wf-sdk e2e p95 s"]["differs"])
        self.assertEqual(rows["steps passing all gates"]["b"], "0/0")

    def test_infinite_p95_is_reported_not_averaged(self) -> None:
        arms = [_arm("A", 9.0, 70), {**_arm("B", 4.5, 70), "e2e_p95_s": {"wf-sdk": float("inf")}}]
        rows = {row["metric"]: row for row in ab.compare(arms, {"a": "A", "b": "B"})}
        self.assertEqual(rows["wf-sdk e2e p95 s"]["infinite_in"], {"a": 0, "b": 1})


if __name__ == "__main__":
    unittest.main()
