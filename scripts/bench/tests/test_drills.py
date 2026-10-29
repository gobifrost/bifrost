from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import drills  # noqa: E402


def _r(sent: float, outcome: str = "correct") -> dict:
    return {"sent_s": sent, "outcome": outcome}


WORKER = {"id": "w1", "state": "ready", "generation": 3}


class ClassifyTest(unittest.TestCase):
    def test_bounded_failures_then_clean_recovery_passes(self) -> None:
        records = [_r(t) for t in range(0, 60)] + [_r(61, "failed"), _r(62, "failed")] + [_r(t) for t in range(66, 500)]
        result = drills.classify(records, fault_s=60.0, ready_s=65.0, workers_before=[WORKER], workers_after=[WORKER])
        self.assertTrue(result["passed"], result["checks"])
        self.assertEqual(result["failures"], {"before": 0, "during": 2, "after": 0})
        self.assertEqual(result["first_success_after_ready_s"], 1.0)

    def test_lost_executor_or_new_owner_generation_fails(self) -> None:
        records = [_r(t) for t in range(0, 500)]
        lost = drills.classify(records, 60.0, 65.0, [WORKER], [{**WORKER, "state": "lost"}])
        self.assertFalse(lost["checks"]["no_executor_lost"])
        moved = drills.classify(records, 60.0, 65.0, [WORKER], [{**WORKER, "generation": 4}])
        self.assertFalse(moved["checks"]["no_owner_regeneration"])

    def test_failures_after_the_recovery_bound_fail_the_clean_tail(self) -> None:
        records = [_r(t) for t in range(0, 300)] + [_r(300, "failed")] + [_r(t) for t in range(301, 500)]
        self.assertFalse(drills.classify(records, 60.0, 65.0, [WORKER], [WORKER])["checks"]["clean_tail"])


if __name__ == "__main__":
    unittest.main()
