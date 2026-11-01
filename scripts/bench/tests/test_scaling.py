from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import scaling  # noqa: E402


def ready_sample(workers: int = 1) -> dict:
    return {
        "worker_db": {"workers": [{
            "state": "ready", "pod_uid": f"pod-{index}", "heartbeat_freshness": {"state": "fresh"},
        } for index in range(workers)]},
        "pods": {"status": "observed", "pods": [{
            "pod_uid": f"pod-{index}", "phase": "Running", "ready": {"value": True}, "deletion_at": None,
        } for index in range(workers)]},
    }


class MemoryRun:
    instances: list = []

    def __init__(self, cluster, kind, profile_name, profile, backend, out, api_image):
        self.result = {"api_image_provenance": {"image": api_image}}
        self.out = out
        self.instances.append(self)

    def save(self):
        self.out.mkdir(parents=True, exist_ok=True)
        (self.out / "result.json").write_text(json.dumps(self.result))


class ScalingTest(unittest.TestCase):
    def test_fixed_count_needs_matching_ready_pods_and_fresh_heartbeats(self):
        self.assertTrue(scaling.fixed_count(ready_sample(4), 4))
        changes = (
            lambda value: value["pods"]["pods"][0].update(deletion_at="now"),
            lambda value: value["pods"]["pods"][0].update(pod_uid="replacement"),
            lambda value: value["worker_db"]["workers"][0].update(state="starting"),
            lambda value: value["worker_db"]["workers"][0].update(heartbeat_freshness={"state": "stale"}),
            lambda value: value.pop("worker_db"),
        )
        for change in changes:
            with self.subTest(change=change):
                sample = ready_sample(4)
                change(sample)
                self.assertFalse(scaling.fixed_count(sample, 4))

    @contextmanager
    def harness(self, root: Path, outcomes: list[str], *, preflight_status="pass", confirmation="pass"):
        cluster = Mock()
        prepared = []
        samples = ready_sample()

        def prepare(_cluster, _current, profile, _backend, api_image):
            prepared.append(copy.deepcopy(profile))
            return {"workflows": {"wf-sdk": "sdk"}}

        def during(_cluster, _phase, checkpoint, action):
            checkpoint(samples, "measured")
            return action()

        def step(_cluster, _ids, rate, _plan, _seed, _disk):
            return {"overall": outcomes.pop(0), "rate": rate, "validity": {"invalid": []}}

        with ExitStack() as stack:
            stack.enter_context(patch.object(scaling.run, "Run", MemoryRun))
            stack.enter_context(patch.object(scaling.run, "_prepare", side_effect=prepare))
            reset = stack.enter_context(patch.object(scaling.deploy, "reset"))
            stack.enter_context(patch.object(scaling.elasticity, "wait_for_floor", return_value=samples))
            stack.enter_context(patch.object(scaling.elasticity, "observe", return_value=samples))
            stack.enter_context(patch.object(scaling.elasticity, "_assert_policy", side_effect=lambda _cluster, controls: controls))
            stack.enter_context(patch.object(scaling.elasticity, "_during", side_effect=during))
            stack.enter_context(patch.object(scaling.preflight, "run", return_value={"overall": preflight_status}))
            trial = stack.enter_context(patch.object(scaling.search, "run_step", side_effect=step))
            confirmed = stack.enter_context(patch.object(scaling.search, "confirm", return_value={"overall": confirmation, "validity": {"invalid": []}}))
            yield cluster, prepared, reset, trial, confirmed, samples

    def test_each_rate_and_confirmation_get_an_independent_fresh_tenant(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "arm"
            with self.harness(out, ["pass", "fail", "pass", "fail"]) as (cluster, prepared, reset, trial, confirmed, _):
                scaling.run_arm(1, "sdk-heavy", "stock-pinned", out, cluster=cluster)
                result = json.loads((out / "result.json").read_text())
                self.assertEqual(len(prepared), 6)  # preflight + four independent ramp trials + confirmation
                self.assertEqual(reset.call_count, 7)  # includes final verified teardown
                self.assertEqual(trial.call_count, 4)
                self.assertEqual(confirmed.call_count, 1)
                self.assertEqual(result["sustainable_rate"], confirmed.call_args.args[2])
                self.assertTrue(result["cleanup"]["verified"])
                self.assertEqual({profile["worker_policy"]["warm_workers"] for profile in prepared}, {1})
                self.assertEqual({profile["worker_policy"]["max_workers"] for profile in prepared}, {1})

    def test_invalid_or_inconclusive_window_stops_without_a_replacement(self):
        for outcome in ("invalid", "inconclusive"):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as directory:
                out = Path(directory) / "arm"
                with self.harness(out, [outcome]) as (cluster, _, _, trial, confirmed, _):
                    scaling.run_arm(1, "sdk-heavy", "stock-pinned", out, cluster=cluster)
                    result = json.loads((out / "result.json").read_text())
                    self.assertEqual(trial.call_count, 1)
                    confirmed.assert_not_called()
                    self.assertIsNone(result["sustainable_rate"])
                    self.assertEqual(result["stopped"], outcome)

    def test_failed_preflight_prevents_all_capacity_load(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "arm"
            with self.harness(out, [], preflight_status="fail") as (cluster, _, reset, trial, confirmed, _):
                scaling.run_arm(1, "sdk-heavy", "stock-pinned", out, cluster=cluster)
                trial.assert_not_called()
                confirmed.assert_not_called()
                self.assertEqual(reset.call_count, 2)

    def test_failed_strict_confirmation_is_retained_without_trying_a_lower_rate(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "arm"
            with self.harness(out, ["pass", "fail", "fail", "fail"], confirmation="fail") as (cluster, _, _, _, confirmed, _):
                scaling.run_arm(1, "sdk-heavy", "stock-pinned", out, cluster=cluster)
                result = json.loads((out / "result.json").read_text())
                self.assertIsNone(result["sustainable_rate"])
                self.assertEqual(len(result["confirmations"]), 1)
                self.assertEqual(result["confirmations"][0]["overall"], "fail")
                self.assertEqual(confirmed.call_count, 1)

    def test_sampled_count_change_invalidates_and_preserves_raw_window(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "arm"
            with self.harness(out, ["pass"]) as (cluster, _, _, _, confirmed, samples):
                samples["pods"]["pods"].clear()
                scaling.run_arm(1, "sdk-heavy", "stock-pinned", out, cluster=cluster)
                result = json.loads((out / "result.json").read_text())
                self.assertEqual(result["steps"][0]["rate"], 1)
                self.assertEqual(result["steps"][0]["overall"], "invalid")
                confirmed.assert_not_called()
                self.assertIsNone(result["sustainable_rate"])

    def test_declared_rate_ceiling_is_a_confirmed_lower_bound_without_an_extra_trial(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "arm"
            with self.harness(out, ["pass"]) as (cluster, _, _, trial, confirmed, _):
                scaling.run_arm(1, "sdk-heavy", "stock-pinned", out, cluster=cluster, max_rate=1)
                result = json.loads((out / "result.json").read_text())
                self.assertEqual(trial.call_count, 1)
                self.assertEqual(confirmed.call_count, 1)
                self.assertEqual(result["capacity_bound"], "lower-bound-only")
                self.assertEqual(result["bracket"], [1.0, None])


if __name__ == "__main__":
    unittest.main()
