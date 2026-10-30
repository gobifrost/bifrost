from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

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

    def test_confirmation_seed_has_a_single_shared_definition(self) -> None:
        self.assertEqual(search.confirmation_seed({"seed": 17}), 1017)
        self.assertEqual(search.confirmation_seed({"seed": 17}, 2), 1019)

    def test_checkpoint_persists_raw_sent_and_outcomes_before_later_gates(self) -> None:
        sent = {
            "started": 1_000.0,
            "requests": [{"id": "request", "class": "wf", "scheduled": 1_090.0, "sent": 1_090.0,
                          "responded": 1_090.1, "http_status": 200, "error": None}],
            "backlog": [],
            "driver_lag_p99_s": None,
        }
        outcomes = {"outcomes": {"request": {"correct": True, "completed": 1_091.0, "started": 1_090.2,
                                                   "created": 1_090.1, "status": "Success", "error": None}},
                    "duplicates": {}, "missing": [], "rows": []}
        checkpoints: list[dict] = []
        cluster = Mock()
        cluster.tenant_faults.return_value = {}
        command = Mock()
        command.result.side_effect = lambda _timeout, on_event: on_event({"event": "started"}) or sent
        cluster.start_exec_python.return_value = command
        cluster.exec_python.return_value = outcomes

        class Host:
            snapshots: list[dict] = []

            def __init__(self, *_args) -> None:
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args) -> None:
                pass

            def intervals(self, *_args) -> list[dict]:
                return []

        plan = {
            "mix": {"wf": 100}, "warmup_s": 90, "drain_s": 120, "window_min_s": 300,
            "thresholds": {"drift_fraction": 0.001}, "deadlines_s": {"wf": 60},
        }
        with patch.object(search, "HostSampler", Host), \
             patch.object(search.hostmetrics, "assess", return_value={"invalid": [], "warnings": []}), \
             patch.object(search.capacity, "step_verdict", return_value={"overall": "pass"}) as verdict, \
             patch.object(search.time, "sleep"):
            search.run_step(cluster, {}, 2.236, plan, 9, {}, window_s=3600, confirm=True,
                            on_event=lambda _event: None, cancel_driver=True, checkpoint=checkpoints.append)

        self.assertEqual([item["phase"] for item in checkpoints], ["sent", "outcomes"])
        self.assertEqual(checkpoints[-1]["outcomes"], outcomes)
        self.assertTrue(json.loads(cluster.start_exec_python.call_args.args[2][1])["event_frames"])
        self.assertTrue(cluster.start_exec_python.call_args.kwargs["cancel_driver"])
        verdict.assert_called_once()


if __name__ == "__main__":
    unittest.main()


class InconclusiveExtensionTest(unittest.TestCase):
    """An inconclusive ramp step is rerun with its window doubled, clamped to the cap."""

    def _windows(self, first_window: float) -> list[float]:
        windows: list[float] = []

        def fake_step(cluster, ids, rate, plan, seed, disk, window_s=None, confirm=False):
            window = window_s or first_window
            windows.append(window)
            return {"rate": rate, "overall": "inconclusive", "window_s": window}

        original = search.run_step
        search.run_step = fake_step
        try:
            search.search(None, {}, {"start_rate": 2.0, "seed": 1}, {}, lambda step: None)
        finally:
            search.run_step = original
        return windows

    def test_final_extension_is_clamped_to_the_cap(self) -> None:
        self.assertEqual(self._windows(600.0), [600.0, 1200.0, search.EXTEND_CAP_S])

    def test_window_already_past_the_cap_is_not_extended(self) -> None:
        self.assertEqual(self._windows(2400.0), [2400.0])
