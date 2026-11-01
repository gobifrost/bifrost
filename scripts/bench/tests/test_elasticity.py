from __future__ import annotations

import sys
import unittest
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import elasticity  # noqa: E402


class ElasticityControlsTest(unittest.TestCase):
    @staticmethod
    def _floor_sample(count: int = 1) -> dict:
        workers = [{
            "state": "ready", "pod_uid": f"pod-{index}", "lifecycle": {"ended_at_epoch_s": None},
            "heartbeat_freshness": {"state": "fresh"},
            "reported_heartbeat_state": {
                "active_runs": {"status": "reported", "value": 0},
                "memory_reserved_bytes": {"status": "reported", "value": 0},
                "memory_budget_bytes": {"status": "reported", "value": 1},
                "memory_pressured": {"status": "reported", "value": False},
            },
        } for index in range(count)]
        pods = [{"pod_uid": f"pod-{index}", "phase": "Running", "deletion_at": None,
                 "ready": {"value": True}} for index in range(count)]
        return {"worker_db": {"workers": workers}, "work": {"status": "observed", "counts": {
            "executions": {"status": "reported", "value": 0}, "agent_runs": {"status": "reported", "value": 0},
            "dispatches": {"status": "reported", "value": 0}, "claimed_dispatches": {"status": "reported", "value": 0},
        }}, "pods": {"status": "observed", "pods": pods}}

    def test_profile_keeps_the_maximum_envelope_fixed_for_every_arm(self) -> None:
        profile = elasticity.build_profile(0, 4)
        self.assertEqual(profile["quota"], {"cpu": "5200m", "memory": "5664Mi", "storage": "50Gi", "pods": 20, "pvcs": 4})
        self.assertEqual(profile["components"], {
            "api": {"replicas": 1, "cpu": "1250m", "memory": "640Mi"},
            "client": {"replicas": 1, "cpu": "100m", "memory": "32Mi"},
            "redis": {"cpu": "150m", "memory": "64Mi"},
            "postgres": {"cpu": "1", "memory": "768Mi", "storage": "20Gi"},
            "pooler": {"cpu": "100m", "memory": "64Mi"},
        })
        self.assertEqual(profile["worker_policy"], {
            "warm_workers": 0, "max_workers": 4, "worker_memory_mib": 1024,
            "largest_worker_mib": 1024, "idle_seconds": 300,
        })

    def test_policy_projection_is_a_strict_scalar_allowlist(self) -> None:
        projected = elasticity.policy_controls({
            "policy": {"warm_workers": 1, "max_workers": 2, "worker_memory_mib": 1024,
                       "largest_worker_mib": 1024, "idle_seconds": 300, "secret": "must-not-appear"},
            "job_types": ["must-not-appear"],
        })
        self.assertEqual(projected, {"warm_workers": 1, "max_workers": 2, "worker_memory_mib": 1024,
                                     "largest_worker_mib": 1024, "idle_seconds": 300})
        with self.assertRaisesRegex(RuntimeError, "missing"):
            elasticity.policy_controls({"policy": {"warm_workers": 1}})

    def test_floor_requires_observed_zero_or_exactly_one_ready_worker(self) -> None:
        empty = {"worker_db": {"workers": []}, "work": {"status": "observed", "counts": {
            "executions": {"status": "reported", "value": 0}, "agent_runs": {"status": "reported", "value": 0},
            "dispatches": {"status": "reported", "value": 0}, "claimed_dispatches": {"status": "reported", "value": 0},
        }}, "pods": {"status": "observed", "pods": []}}
        self.assertEqual(elasticity.floor_state(empty, 0), "ready")
        self.assertEqual(elasticity.floor_state(empty, 1), "waiting")
        self.assertEqual(elasticity.floor_state({**empty, "pods": {"status": "observed"}}, 0), "waiting")
        one = self._floor_sample()
        self.assertEqual(elasticity.floor_state(one, 1), "ready")
        self.assertEqual(elasticity.floor_state(one, 0), "waiting")

    def test_floor_fails_closed_for_missing_or_non_idle_worker_evidence(self) -> None:
        sample = self._floor_sample()
        cases = []
        missing = deepcopy(sample)
        del missing["worker_db"]["workers"]
        cases.append(missing)
        non_list = deepcopy(sample)
        non_list["worker_db"]["workers"] = "unknown"
        cases.append(non_list)
        stale = deepcopy(sample)
        stale["worker_db"]["workers"][0]["heartbeat_freshness"] = {"state": "stale"}
        cases.append(stale)
        unmatched = deepcopy(sample)
        unmatched["pods"]["pods"][0]["pod_uid"] = "other"
        cases.append(unmatched)
        terminating = deepcopy(sample)
        terminating["pods"]["pods"][0]["deletion_at"] = "2026-10-31T00:00:00Z"
        cases.append(terminating)
        for field, value in (("active_runs", 1), ("memory_reserved_bytes", 1), ("memory_pressured", True)):
            changed = deepcopy(sample)
            changed["worker_db"]["workers"][0]["reported_heartbeat_state"][field]["value"] = value
            cases.append(changed)
        for candidate in cases:
            self.assertEqual(elasticity.floor_state(candidate, 1), "waiting")

    def test_floor_accepts_exactly_four_ready_workers(self) -> None:
        self.assertEqual(elasticity.floor_state(self._floor_sample(4), 4), "ready")

    def test_run_arm_retains_checkpoint_and_cleans_up_after_probe_failure(self) -> None:
        cluster, current = Mock(), Mock()
        current.result = {}

        class Host:
            snapshots: list[dict] = []

            def __init__(self, *_args) -> None:
                return None

            def __enter__(self):
                return self

            def __exit__(self, *_args) -> None:
                return None

            def intervals(self, *_args) -> list[dict]:
                return []

        def failed_floor(_cluster, _warm, *, checkpoint, **_kwargs):
            checkpoint({"raw": "checkpoint"})
            raise RuntimeError("probe failed")

        with TemporaryDirectory() as raw, patch.object(elasticity.run, "Run", return_value=current), \
             patch.object(elasticity.deploy, "reset") as reset, patch.object(elasticity.run, "_prepare"), \
             patch.object(elasticity, "_assert_policy"), patch.object(elasticity.search, "HostSampler", Host), \
             patch.object(elasticity, "wait_for_floor", side_effect=failed_floor):
            with self.assertRaisesRegex(RuntimeError, "probe failed"):
                elasticity.run_arm(0, 1, "candidate:local", Path(raw) / "result", cluster=cluster)
            self.assertEqual(reset.call_count, 2)
            self.assertTrue(current.result["cleanup"]["verified"])
            timeline = Path(raw) / "result" / "timeline.jsonl"
            self.assertIn('"raw": "checkpoint"', timeline.read_text(encoding="utf-8"))

    def test_warm_four_is_a_valid_shared_profile_but_not_an_elastic_cli_arm(self) -> None:
        self.assertEqual(elasticity.build_profile(4, 4)["worker_policy"]["warm_workers"], 4)


if __name__ == "__main__":
    unittest.main()
