"""Tests for pure local Kind worker-pool drill invariants."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


KUBERNETES_SCRIPTS = Path(__file__).resolve().parent / "kubernetes"


def pool_drills():
    sys.path.insert(0, str(KUBERNETES_SCRIPTS))
    try:
        spec = importlib.util.spec_from_file_location(
            "pool_drills_under_test", KUBERNETES_SCRIPTS / "pool_drills.py"
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.pop(0)


class PoolDrillTests(unittest.TestCase):
    def test_workflow_capacity_excludes_api_agent_executors(self) -> None:
        drills = pool_drills()
        pool = sys.modules[drills.live_workers.__module__]
        with patch.object(pool, "sql", return_value=[]) as query:
            self.assertEqual(pool.live_workers("static"), [])
        self.assertIn("supported_handlers ? 'workflow.execute'", query.call_args.args[0])

    def test_rollout_wait_beyond_sixty_seconds_uses_short_status_reads(self) -> None:
        drills = pool_drills()
        spike = sys.modules[drills.wait_for_rollout.__module__]
        pending = {
            "metadata": {"generation": 4}, "spec": {"replicas": 2},
            "status": {"observedGeneration": 4, "replicas": 3, "updatedReplicas": 1, "availableReplicas": 3},
        }
        complete = {
            **pending, "status": {"observedGeneration": 4, "replicas": 2, "updatedReplicas": 2, "availableReplicas": 2},
        }
        with (
            patch.object(spike, "kubectl", side_effect=[json.dumps(pending), json.dumps(complete)]) as request,
            patch.object(spike.time, "monotonic", side_effect=[0, 61, 61.1, 61.2, 62, 62.1]),
            patch.object(spike.time, "sleep"),
        ):
            drills.wait_for_rollout("deployment/bifrost-api", 600)
        self.assertEqual(request.call_count, 2)
        request.assert_called_with("get", "deployment/bifrost-api", "-o", "json", "--request-timeout=10s", timeout=538)

    def test_rollout_failure_is_reported_without_waiting_to_the_deadline(self) -> None:
        drills = pool_drills()
        spike = sys.modules[drills.wait_for_rollout.__module__]
        deployment = {
            "metadata": {"generation": 4}, "spec": {"replicas": 2},
            "status": {"observedGeneration": 4, "conditions": [{"type": "Progressing", "reason": "ProgressDeadlineExceeded", "message": "failed rollout"}]},
        }
        with (
            patch.object(spike, "kubectl", return_value=json.dumps(deployment)),
            self.assertRaisesRegex(RuntimeError, "failed rollout"),
        ):
            drills.wait_for_rollout("deployment/bifrost-api", 600)

    def test_rollout_ignores_a_previous_generations_failure_condition(self) -> None:
        drills = pool_drills()
        spike = sys.modules[drills.wait_for_rollout.__module__]
        stale = {
            "metadata": {"generation": 5}, "spec": {"replicas": 2},
            "status": {"observedGeneration": 4, "conditions": [{"type": "Progressing", "reason": "ProgressDeadlineExceeded"}]},
        }
        complete = {
            **stale, "status": {"observedGeneration": 5, "replicas": 2, "updatedReplicas": 2, "availableReplicas": 2},
        }
        with (
            patch.object(spike, "kubectl", side_effect=[json.dumps(stale), json.dumps(complete)]) as request,
            patch.object(spike.time, "sleep"),
        ):
            drills.wait_for_rollout("deployment/bifrost-api", 600)
        self.assertEqual(request.call_count, 2)

    def test_rollout_accepts_a_deployment_scaled_to_zero(self) -> None:
        drills = pool_drills()
        spike = sys.modules[drills.wait_for_rollout.__module__]
        deployment = {"metadata": {"generation": 4}, "spec": {"replicas": 0}, "status": {"observedGeneration": 4}}
        with patch.object(spike, "kubectl", return_value=json.dumps(deployment)):
            drills.wait_for_rollout("deployment/bifrost-worker", 600)

    def test_rollout_stops_at_its_existing_deadline(self) -> None:
        drills = pool_drills()
        spike = sys.modules[drills.wait_for_rollout.__module__]
        with (
            patch.object(spike.time, "monotonic", side_effect=[0, 601]),
            self.assertRaises(TimeoutError),
        ):
            drills.wait_for_rollout("deployment/bifrost-api", 600)

    def test_rollout_request_is_capped_by_the_remaining_deadline(self) -> None:
        drills = pool_drills()
        spike = sys.modules[drills.wait_for_rollout.__module__]
        complete = {"metadata": {"generation": 4}, "spec": {"replicas": 0}, "status": {"observedGeneration": 4}}
        with (
            patch.object(spike, "kubectl", return_value=json.dumps(complete)) as request,
            patch.object(spike.time, "monotonic", side_effect=[0, 599.5, 600.1]),
            self.assertRaises(TimeoutError),
        ):
            drills.wait_for_rollout("deployment/bifrost-api", 600)
        request.assert_called_once_with("get", "deployment/bifrost-api", "-o", "json", "--request-timeout=0.5s", timeout=0.5)

    def test_controller_restart_burst_exceeds_two_warm_worker_budgets(self) -> None:
        drills = pool_drills()
        warm_budget = 8 * drills.INLINE_RUN_RESERVATION_BYTES

        burst = drills.controller_restart_burst_count(warm_budget)

        self.assertEqual(burst, 17)
        self.assertGreater(burst, warm_budget // drills.INLINE_RUN_RESERVATION_BYTES)

    def test_active_job_binding_accepts_create_window_but_rejects_uid_change(self) -> None:
        drills = pool_drills()
        with (
            patch.object(drills, "worker_job_bindings", return_value={"worker-a": None}),
            patch.object(drills, "active_pool_jobs", return_value={"worker-a": "job-a"}),
        ):
            self.assertEqual(drills.assert_active_jobs_bound(), {"worker-a": "job-a"})
            self.assertIsNone(drills.snapshot_active_job_bindings())

        with (
            patch.object(drills, "worker_job_bindings", return_value={"worker-a": "job-b"}),
            patch.object(drills, "active_pool_jobs", return_value={"worker-a": "job-a"}),
            self.assertRaisesRegex(AssertionError, "changed Job UIDs"),
        ):
            drills.assert_active_jobs_bound()

    def test_scale_up_between_snapshots_does_not_report_a_false_orphan(self) -> None:
        drills = pool_drills()
        bindings = {}

        def create_job_after_committing_reservation():
            bindings["worker-a"] = "job-a"
            return {"worker-a": "job-a"}

        with (
            patch.object(drills, "active_pool_jobs", side_effect=create_job_after_committing_reservation),
            patch.object(drills, "worker_job_bindings", side_effect=lambda: dict(bindings)),
        ):
            self.assertEqual(drills.assert_active_jobs_bound(), {"worker-a": "job-a"})

    def test_retained_snapshot_rejects_an_extra_bound_job(self) -> None:
        drills = pool_drills()
        with (
            patch.object(drills, "worker_job_bindings", return_value={"worker-a": "job-a", "worker-b": "job-b"}),
            patch.object(drills, "active_pool_jobs", return_value={"worker-a": "job-a", "worker-b": "job-b"}),
            self.assertRaisesRegex(AssertionError, "unexpected Jobs"),
        ):
            drills.assert_retained_job_bindings({"worker-a": "job-a"})
