from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import search  # noqa: E402


class ConfirmationDecisionTest(unittest.TestCase):
    def test_settled_outcomes(self) -> None:
        self.assertEqual(search.confirmation_decision("pass", {"wf": ("pass", 3000)}, 2995, False), ("pass", 2995))
        self.assertEqual(search.confirmation_decision("fail", {"wf": ("pass", 3000)}, 2995, False)[0], "fail")
        self.assertEqual(search.confirmation_decision("pass", {"wf": ("fail", 3000)}, 2995, False)[0], "fail")
        self.assertEqual(search.confirmation_decision("invalid", {}, 2995, False)[0], "invalid")

    def test_one_failure_extends_to_the_observations_it_needs(self) -> None:
        self.assertEqual(search.confirmation_decision("pass", {"wf": ("insufficient", 4744)}, 2995, False), ("extend", 4744))

    def test_zero_failure_shortfall_extends_with_more_headroom(self) -> None:
        outcome, target = search.confirmation_decision("pass", {"wf": ("insufficient", 2995)}, 2995, False)
        self.assertEqual(outcome, "extend")
        self.assertGreater(target, 2995)

    def test_only_a_capped_segment_concludes_inconclusive(self) -> None:
        self.assertEqual(search.confirmation_decision("pass", {"wf": ("insufficient", 9000)}, 4744, True), ("inconclusive", 4744))
        self.assertEqual(search.confirmation_decision("pass", {"wf": ("insufficient", 9000)}, 4744, False)[0], "extend")

    def test_other_gate_inconclusive_is_inconclusive(self) -> None:
        self.assertEqual(search.confirmation_decision("inconclusive", {"wf": ("pass", 3000)}, 2995, False)[0], "inconclusive")

    def test_needing_more_than_an_hour_still_runs_the_capped_window(self) -> None:
        window, capped = search.confirmation_seconds(1.5, {"wf": 100}, 4744)
        self.assertEqual((window, capped), (search.CONFIRM_CAP_S, True))


if __name__ == "__main__":
    unittest.main()
