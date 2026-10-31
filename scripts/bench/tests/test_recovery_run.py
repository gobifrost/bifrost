from __future__ import annotations

import sys
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "incluster"))

import recovery_driver  # noqa: E402
import recovery_run  # noqa: E402
import search  # noqa: E402


def profile() -> dict:
    return {
        "capacity": {
            "seed": 11, "warmup_s": 2.0, "window_min_s": 3.0, "drain_s": 1.0,
            "deadlines_s": {"wf-noop": 10.0}, "disk": {"name": "disk", "id": "1:1"},
            "thresholds": {"queue_p95_s": {"wf-noop": 2.0}, "e2e_p95_s": {"wf-noop": 3.0}},
        },
    }


def environment() -> dict:
    return {"git_sha": "test", "images": {"api": {"tag": "candidate", "id": "sha256:test"}},
            "kind_version": "kind", "cnpg_version": "cnpg", "host": {"cpus": 1, "load_average": [0.0, 0.0, 0.0]},
            "external_nodes": []}


class RecoveryFixtureTest(unittest.TestCase):
    def test_recovery_plan_reuses_capacity_timing_but_declares_one_fixture_class(self) -> None:
        plan = recovery_run.recovery_plan(profile(), 2.0)
        self.assertEqual(plan["mix"], {recovery_driver.RECOVERY_CLASS: 1.0})
        self.assertEqual(plan["deadlines_s"], {recovery_driver.RECOVERY_CLASS: 10.0})
        self.assertEqual(plan["thresholds"]["e2e_p95_s"], {recovery_driver.RECOVERY_CLASS: 3.0})

    def test_predeclared_requests_are_fresh_and_token_bound_before_load(self) -> None:
        plan = recovery_run.recovery_plan(profile(), 2.0)
        cluster = Mock()
        cluster.exec_python.return_value = {"workflows": {recovery_driver.RECOVERY_CLASS: "workflow"}, "fixture": {"append_only": True}}
        with patch.object(recovery_run.search, "window_seconds", return_value=4.0):
            ids, tokens, fixture = recovery_run._fixture_ids(cluster, plan, 2.0, "first")
        self.assertTrue(fixture["fixture"]["append_only"])
        self.assertTrue(ids["recovery"]["requests"])
        self.assertEqual(tokens, {request["id"]: request["effect_token"] for request in ids["recovery"]["requests"]})
        self.assertNotEqual(ids["recovery"]["requests"], recovery_driver.request_plan("second", 2.0, 6.0, 11))
        self.assertEqual(cluster.exec_python.call_args.args[2][0], "prepare")

    def test_token_aware_loadgen_is_restored_after_recovery_step(self) -> None:
        original = search.LOADGEN
        with recovery_run._recovery_loadgen():
            self.assertEqual(search.LOADGEN, recovery_run.RECOVERY_DRIVER)
        self.assertEqual(search.LOADGEN, original)


class RecoveryRunLifecycleTest(unittest.TestCase):
    def test_d3_runner_saves_fresh_mapping_and_holds_cleanup_after_result(self) -> None:
        cluster = Mock()
        ids = {"workflows": {recovery_driver.RECOVERY_CLASS: "workflow"}, "recovery": {"requests": [{"id": "r", "effect_token": "t", "offset": 0.1}]}}
        result = {"passed": True, "overall": "passed"}
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(recovery_run, "Cluster", return_value=cluster), \
             patch.object(recovery_run.bench_run, "load_profile", return_value=profile()), \
             patch.object(recovery_run.bench_run, "environment", return_value=environment()), \
             patch.object(recovery_run.deploy, "reset") as reset, \
             patch.object(recovery_run.bench_run, "_prepare", return_value={"workflows": {"wf-noop": "normal"}}) as prepare, \
             patch.object(recovery_run, "_fixture_ids", return_value=(ids, {"r": "t"}, {"append_only": True})), \
             patch.object(recovery_run.recovery, "d3", return_value=result) as d3:
            out = Path(temporary) / "fresh"
            recovery_run.run("d3", "m-capacity", 1.0, out, "bifrost-api:candidate")
            saved = json.loads((out / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["status"], "passed")
            self.assertEqual(saved["recovery_request_mapping"]["requests"], ids["recovery"]["requests"])
            self.assertEqual(reset.call_count, 2)
            self.assertTrue(saved["cleanup"]["verified"])
            self.assertEqual(d3.call_args.args[1], ids)
            self.assertEqual(prepare.call_args.args[4], "bifrost-api:candidate")
            self.assertEqual(saved["api_image_selected"], "bifrost-api:candidate")

    def test_runner_persists_error_then_performs_guarded_cleanup(self) -> None:
        cluster = Mock()
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(recovery_run, "Cluster", return_value=cluster), \
             patch.object(recovery_run.bench_run, "load_profile", return_value=profile()), \
             patch.object(recovery_run.bench_run, "environment", return_value=environment()), \
             patch.object(recovery_run.deploy, "reset") as reset, \
             patch.object(recovery_run.bench_run, "_prepare", side_effect=RuntimeError("setup failed")):
            out = Path(temporary) / "fresh"
            with self.assertRaisesRegex(RuntimeError, "setup failed"):
                recovery_run.run("d3", "m-capacity", 1.0, out)
            saved = json.loads((out / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["status"], "invalid")
        self.assertIn("setup failed", saved["error"])
        self.assertEqual(reset.call_count, 2)
        self.assertTrue(saved["cleanup"]["verified"])


if __name__ == "__main__":
    unittest.main()
