from __future__ import annotations

import math
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import capacity  # noqa: E402


def _record(
    identifier: str,
    outcome: str,
    scheduled: float,
    completed: float | None,
    queue: float | None = 1.0,
    kind: str = "wf",
) -> dict:
    return {
        "id": identifier,
        "class": kind,
        "scheduled_s": scheduled,
        "sent_s": scheduled,
        "completed_s": completed,
        "queue_s": queue,
        "outcome": outcome,
    }


class PercentileTest(unittest.TestCase):
    def test_failures_rank_as_infinity(self) -> None:
        self.assertEqual(capacity.failure_aware_percentile([1.0, 2.0, None], 0.50), 2.0)
        self.assertTrue(math.isinf(capacity.failure_aware_percentile([1.0, 2.0, None], 0.95)))

    def test_distribution_free_interval_uses_order_ranks(self) -> None:
        low, high = capacity.percentile_ci(400, 0.95)
        self.assertEqual(low, 371)
        self.assertEqual(high, 389)

    def test_percentile_ci_preserves_failed_observations(self) -> None:
        result = capacity.percentile_with_ci([float(index) for index in range(379)] + [None] * 21, 0.95)
        self.assertTrue(math.isinf(result["value"]))
        self.assertTrue(math.isinf(result["high"]))
        self.assertTrue(math.isinf(result["half_width"]))


class ReliabilityTest(unittest.TestCase):
    def test_known_clopper_pearson_bounds(self) -> None:
        self.assertAlmostEqual(capacity.clopper_pearson_upper(0, 3000), 0.000998, places=6)
        self.assertAlmostEqual(capacity.clopper_pearson_upper(1, 100), 0.0466, places=4)

    def test_failure_free_observations_for_one_tenth_percent(self) -> None:
        self.assertIn(capacity.observations_needed(0.001), range(2994, 2997))


class BootstrapTest(unittest.TestCase):
    @staticmethod
    def _ar(phi: float, n: int, seed: int) -> list[tuple[float, float]]:
        generator = random.Random(seed)
        residual = 0.0
        points = []
        for index in range(n):
            residual = phi * residual + generator.uniform(-1.0, 1.0)
            points.append((float(index), residual))
        return points

    def test_flat_ar_series_is_conclusive_and_seeded(self) -> None:
        points = self._ar(0.3, 240, 7)
        first = capacity.residual_block_bootstrap_upper(points, seed=91, resamples=300)
        self.assertEqual(first, capacity.residual_block_bootstrap_upper(points, seed=91, resamples=300))
        self.assertTrue(first["conclusive"])
        self.assertGreater(first["upper"], 0.0)
        self.assertLess(first["upper"], 0.1)
        self.assertEqual(set(first["uppers"]), {"half", "chosen", "double"})

    def test_strong_dependence_in_a_short_window_is_inconclusive(self) -> None:
        self.assertFalse(capacity.residual_block_bootstrap_upper(self._ar(0.95, 120, 3), seed=1, resamples=50)["conclusive"])

    def test_seasonal_dependence_behind_insignificant_short_lags_is_spanned(self) -> None:
        generator = random.Random(11)
        values = [0.0] * 252
        for index in range(12, 252):
            values[index] = 0.9 * values[index - 12] + generator.gauss(0, 1)
        points = [(float(index), value) for index, value in enumerate(values[12:])]
        result = capacity.residual_block_bootstrap_upper(points, seed=2, resamples=50)
        self.assertGreaterEqual(result["block"], 24)
        self.assertFalse(result["conclusive"])

    def test_upper_bound_covers_a_zero_slope_when_conclusive(self) -> None:
        misses = conclusive = 0
        for seed in range(80):
            points = self._ar(0.5, 240, 1000 + seed)
            result = capacity.residual_block_bootstrap_upper(points, seed=seed, resamples=200)
            if result["conclusive"]:
                conclusive += 1
                misses += result["upper"] < 0.0
        self.assertGreater(conclusive, 60)
        self.assertLessEqual(misses / conclusive, 0.10)

    def test_later_lag_dependence_lengthens_the_block(self) -> None:
        generator = random.Random(5)
        periodic = [(float(index), 3.0 * ((index // 6) % 2) + generator.gauss(0, 0.3)) for index in range(240)]
        result = capacity.residual_block_bootstrap_upper(periodic, seed=1, resamples=50)
        self.assertGreater(result["block"], 12)
        self.assertFalse(result["conclusive"])

    def test_increasing_series_exceeds_a_small_drift_tolerance(self) -> None:
        points = [(float(index), float(index)) for index in range(120)]
        result = capacity.residual_block_bootstrap_upper(points, seed=4, resamples=100)
        self.assertGreater(result["upper"], 0.001)


class VerdictTest(unittest.TestCase):
    def test_failed_work_cannot_improve_latency_or_throughput(self) -> None:
        records = [_record(str(index), "correct", float(index), float(index) + 2.0) for index in range(400)]
        thresholds = {
            "queue_p95_s": {"wf": 2.0},
            "e2e_p95_s": {"wf": 3.0},
        }
        backlog = [(float(index), 1.0) for index in range(120)]
        before = capacity.step_verdict(records, backlog, (0.0, 500.0), 10.0, thresholds, seed=3, mix={"wf": 1.0})
        after = capacity.step_verdict(
            records + [_record("failed", "failed", 450.0, None, queue=None)],
            backlog,
            (0.0, 500.0),
            10.0,
            thresholds,
            seed=3,
            mix={"wf": 1.0},
        )
        self.assertLessEqual(after["throughput"]["value"], before["throughput"]["value"])
        self.assertGreaterEqual(
            after["latency"]["wf"]["queue_p95"]["value"],
            before["latency"]["wf"]["queue_p95"]["value"],
        )

    def test_inconclusive_drift_does_not_pass(self) -> None:
        result = capacity.step_verdict(
            [_record(str(index), "correct", float(index), float(index) + 1.0) for index in range(400)],
            [(float(index), 1.0) for index in range(20)],
            (0.0, 500.0),
            1.0,
            {"queue_p95_s": {"wf": 2.0}, "e2e_p95_s": {"wf": 2.0}},
            seed=1,
            mix={"wf": 1.0},
        )
        self.assertEqual(result["drift"]["status"], "inconclusive")
        self.assertEqual(result["overall"], "inconclusive")


    def test_classes_are_gated_by_configured_weight_and_need_minimum_observations(self) -> None:
        records = [_record(str(index), "correct", float(index), float(index) + 1.0) for index in range(400)]
        backlog = [(float(index) * 2, 1.0) for index in range(250)]
        thresholds = {"queue_p95_s": {"wf": 2.0, "rare": 2.0}, "e2e_p95_s": {"wf": 2.0, "rare": 2.0}}
        result = capacity.step_verdict(records, backlog, (0.0, 500.0), 1.0, thresholds, seed=1, mix={"wf": 0.9, "rare": 0.1})
        self.assertEqual(result["reliability"]["rare"]["status"], "inconclusive")
        self.assertEqual(result["reliability"]["rare"]["observations"], 0)
        self.assertIsNone(result["latency"]["rare"]["e2e_p95"]["value"])
        self.assertNotEqual(result["overall"], "pass")
        few = capacity.step_verdict(records[:300], backlog, (0.0, 500.0), 1.0, thresholds, seed=1, mix={"wf": 1.0})
        self.assertEqual(few["reliability"]["wf"]["status"], "inconclusive")
        lowered = capacity.step_verdict(records[:300], backlog, (0.0, 500.0), 1.0, {**thresholds, "min_observations": 300}, seed=1, mix={"wf": 1.0})
        self.assertEqual(lowered["reliability"]["wf"]["status"], "inconclusive")


class SearchTest(unittest.TestCase):
    def test_geometric_ramp_then_two_bisections(self) -> None:
        self.assertEqual(capacity.search_next([(10.0, "pass")]), 12.5)
        first = capacity.search_next([(10.0, "pass"), (12.5, "fail")])
        self.assertAlmostEqual(first, math.sqrt(125.0))
        second = capacity.search_next([(10.0, "pass"), (12.5, "fail"), (first, "pass")])
        self.assertAlmostEqual(second, math.sqrt(first * 12.5))
        self.assertIsNone(capacity.search_next([(10.0, "pass"), (12.5, "fail"), (first, "pass"), (second, "fail")]))

    def test_invalid_results_are_ignored(self) -> None:
        self.assertEqual(capacity.search_next([(10.0, "pass"), (12.5, "invalid")]), 12.5)


if __name__ == "__main__":
    unittest.main()
