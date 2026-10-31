from __future__ import annotations

import json
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "incluster"))

import recovery  # noqa: E402
import recovery_driver  # noqa: E402


def pod(name: str, uid: str, labels: dict, container: str) -> dict:
    return {"metadata": {"name": name, "uid": uid, "namespace": "bench-tenant", "labels": labels},
            "spec": {"nodeName": "kind-bench-control-plane"},
            "status": {"conditions": [{"type": "Ready", "status": "True"}],
                       "containerStatuses": [{"name": container, "ready": True, "containerID": "containerd://deadbeef"}]}}


class TargetProofTest(unittest.TestCase):
    def test_d3_requires_the_cnpg_primary_selector_and_one_ready_instance(self) -> None:
        cluster = Mock()
        primary = pod("bifrost-db-1", "old", recovery.PRIMARY_LABELS, "postgres")
        cluster.kubectl.side_effect = [json.dumps({"status": {"readyInstances": 1}}), json.dumps({"items": [primary]})]
        with patch.object(recovery.guard, "check_fault_target"):
            result = recovery.cnpg_primary(cluster)
        self.assertEqual(result["metadata"]["uid"], "old")
        self.assertIn("cnpg.io/instanceRole=primary", cluster.kubectl.call_args_list[1].args[3])

    def test_delete_rechecks_same_uid_before_one_delete(self) -> None:
        target = pod("bifrost-db-1", "old", recovery.PRIMARY_LABELS, "postgres")
        cluster = Mock()
        with patch.object(recovery.time, "time", return_value=50.0), \
             patch.object(recovery, "cnpg_primary", return_value=target):
            evidence = recovery.delete_exact(cluster, target, recovery.cnpg_primary, kind="CNPG primary")
        self.assertEqual(evidence["old_uid"], "old")
        cluster.verify.assert_called()
        cluster.kubectl.assert_called_once_with("delete", "pod", "bifrost-db-1", "--wait=false")

    def test_d5_captures_grace_and_quota_before_api_delete(self) -> None:
        cluster = Mock()
        api = pod("api-old", "api-uid", recovery.API_LABELS, "api")
        cluster.kubectl.side_effect = [
            json.dumps({"spec": {"template": {"spec": {"terminationGracePeriodSeconds": 30}}}}),
            json.dumps({"status": {"used": {"pods": "4"}}}),
        ]
        proof = recovery.api_restart_preflight(cluster, api)
        self.assertEqual(proof["termination_grace_seconds"], 30)
        self.assertEqual(proof["quota_before"]["used"]["pods"], "4")

    def test_replacement_requires_changed_uid_and_readiness_within_bound(self) -> None:
        old = pod("api-old", "old", recovery.API_LABELS, "api")
        replacement = pod("api-new", "new", recovery.API_LABELS, "api")
        self.assertFalse(recovery.replacement_proof("old", old, ready_at=20.0, fault_at=10.0, bound_s=60.0)["within_bound"])
        self.assertTrue(recovery.replacement_proof("old", replacement, ready_at=20.0, fault_at=10.0, bound_s=60.0)["within_bound"])


class EffectsAndDeadlinesTest(unittest.TestCase):
    def test_zero_or_duplicate_effects_fail_strict_effects_even_when_terminal(self) -> None:
        requests = [{"id": "zero", "http_status": 202, "effect_token": "a"}, {"id": "double", "http_status": 202, "effect_token": "b"}]
        rows = [
            {"request_id": "zero", "kind": "workflow", "status": "Success", "completed": 4.0},
            {"request_id": "double", "kind": "workflow", "status": "Success", "completed": 4.0},
        ]
        effects = [{"bench_request_id": "double", "effect_token": "b"}, {"bench_request_id": "double", "effect_token": "b"}]
        result = recovery.effect_assessment(requests, rows, effects)
        self.assertTrue(result["terminal_and_loss"])
        self.assertFalse(result["strict_effects"])
        self.assertEqual(result["per_request"]["zero"]["effect_count"], 0)

    def test_db_recovery_clock_does_not_replace_normal_send_time_slo(self) -> None:
        requests = [{"id": "r", "class": "wf", "sent": 10.0}]
        rows = [{"request_id": "r", "kind": "workflow", "status": "Success", "completed": 35.0}]
        result = recovery.recovery_deadlines(requests, rows, db_ready_at=30.0, deadlines_s={"wf": 10.0})
        self.assertFalse(result["normal_send_time_slo"])
        self.assertTrue(result["recovery_deadline"])

    def test_unaccepted_submission_is_preserved_but_does_not_gate_recovery_deadline(self) -> None:
        result = recovery.recovery_deadlines(
            [{"id": "network-error", "class": "wf", "sent": 10.0, "http_status": None}], [],
            db_ready_at=20.0, deadlines_s={"wf": 10.0},
        )
        self.assertTrue(result["normal_send_time_slo"])
        self.assertTrue(result["recovery_deadline"])
        self.assertEqual(result["submitted_unaccepted"][0]["request"]["id"], "network-error")

    def test_duplicate_durable_id_is_invalid_without_last_row_wins(self) -> None:
        request = {"id": "same", "class": "wf", "sent": 10.0, "http_status": 202, "effect_token": "t"}
        rows = [
            {"request_id": "same", "kind": "workflow", "status": "Success", "completed": 12.0},
            {"request_id": "same", "kind": "workflow", "status": "Success", "completed": 13.0},
        ]
        effects = [{"bench_request_id": "same", "effect_token": "t"}]
        self.assertFalse(recovery.effect_assessment([request], rows, effects)["terminal_and_loss"])
        deadline = recovery.recovery_deadlines([request], rows, db_ready_at=11.0, deadlines_s={"wf": 10.0})
        self.assertFalse(deadline["normal_send_time_slo"])

    def test_pre_ready_terminal_does_not_get_a_recovery_deadline_extension(self) -> None:
        result = recovery.recovery_deadlines(
            [{"id": "before", "class": "wf", "sent": 10.0, "http_status": 202}],
            [{"request_id": "before", "kind": "workflow", "status": "Success", "completed": 15.0}],
            db_ready_at=30.0, deadlines_s={"wf": 10.0},
        )
        self.assertEqual(result["per_request"]["before"]["recovery_deadline"], 20.0)


class WorkerTargetTest(unittest.TestCase):
    def test_d6_requires_one_durable_running_owner_not_an_arbitrary_worker(self) -> None:
        cluster = Mock()
        cluster.driver_sql.return_value = [{"worker_id": "w", "pod_uid": "u", "execution_id": "e"}]
        worker = pod("worker-1", "u", {}, "worker")
        cluster.kubectl.return_value = json.dumps({"items": [worker]})
        owned, selected = recovery.busy_worker(cluster)
        self.assertEqual(owned["worker_id"], "w")
        self.assertEqual(selected["metadata"]["name"], "worker-1")

    def test_d6_kill_uses_crictl_resolved_exact_container_pid(self) -> None:
        cluster = Mock()
        cluster.identity.node_name = "kind-bench-control-plane"
        owned = {"worker_id": "w", "execution_id": "e", "pod_uid": "u"}
        worker = pod("worker-1", "u", {}, "worker")
        with patch.object(recovery.subprocess, "run", side_effect=[Mock(stdout=json.dumps({"info": {"pid": 123}})), Mock()]) as run, \
             patch.object(recovery.time, "time", return_value=7.0):
            result = recovery.kill_worker_process(cluster, owned, worker)
        self.assertEqual(result["pid"], 123)
        self.assertEqual(result["loss_bound_s"], 35.0)
        self.assertEqual(run.call_args_list[1].args[0][-2:], ["-9", "123"])

    def test_d6_uses_first_sampling_times_for_loss_and_replacement_admission(self) -> None:
        cluster = Mock()
        cluster.driver_sql.return_value = [{"id": "old", "state": "ready"}, {"id": "already-ready", "state": "ready"}]
        sampler = recovery.WorkerRecoverySampler(cluster, "old")
        sampler.snapshot_before_kill()
        sampler.mark_killed(100.0)
        sampler.observe([{"id": "old", "state": "lost", "end_reason": "heartbeat_timeout"}], observed_at=120.0)
        sampler.observe([{"id": "old", "state": "lost"}, {"id": "already-ready", "state": "ready"}], observed_at=121.0)
        self.assertIsNone(sampler.first_replacement_ready_at)
        sampler.observe([{"id": "old", "state": "lost"}, {"id": "new", "state": "ready"}], observed_at=122.0)
        result = recovery.worker_recovery_assessment({"killed_at": 100.0, "loss_bound_s": 35.0}, sampler.evidence())
        self.assertTrue(result["loss_within_lease_plus_one_sweep"])
        self.assertTrue(result["replacement_admitted"])
        self.assertEqual(result["lost_terminal_status"], "lost")

    def test_d6_slot_release_requires_no_running_claim_on_killed_worker(self) -> None:
        cluster = Mock()
        cluster.driver_sql.return_value = []
        result = recovery.worker_slot_release(cluster, "123e4567-e89b-12d3-a456-426614174000")
        self.assertTrue(result["released"])
        self.assertIn("execution_worker_claims", cluster.driver_sql.call_args.args[0])


class RestartInjectorTest(unittest.TestCase):
    def test_injector_cannot_delete_before_started_and_request_sent(self) -> None:
        cluster, selector = Mock(), Mock()
        target = pod("api-old", "old", recovery.API_LABELS, "api")
        selector.return_value = target
        injector = recovery.RestartInjector(
            cluster, selector=selector, kind="API", delay_s=0.0,
            ready=lambda _injected, _cancelled: {"ready_at": time.time(), "within_bound": True},
        )
        with patch.object(recovery, "delete_exact", return_value={"pod": "api-old", "old_uid": "old", "fault_at": time.time()}):
            injector.start()
            selector.assert_not_called()
            injector.observe({"event": "started", "started": time.time()})
            selector.assert_not_called()
            injector.observe({"event": "request_sent", "sent": time.time(), "request_id": "r"})
            result = injector.join(1)
        self.assertEqual(result["status"], "ready")
        selector.assert_called_once_with(cluster)

    def test_injector_error_is_saved_as_invalid_evidence(self) -> None:
        selector = Mock(side_effect=RuntimeError("target unavailable"))
        injector = recovery.RestartInjector(
            Mock(), selector=selector, kind="API", delay_s=0.0,
            ready=Mock(),
        )
        injector.start()
        injector.observe({"event": "started", "started": time.time()})
        injector.observe({"event": "request_sent", "sent": time.time(), "request_id": "r"})
        result = injector.join(1)
        self.assertEqual(result["status"], "error")
        self.assertIn("target unavailable", result["error"])

    def test_d5_marks_a_completed_step_invalid_when_injector_failed(self) -> None:
        cluster = Mock()
        step = {"started": 1.0, "window_s": 10.0}
        with patch.object(recovery, "_restart_step", return_value=(step, {"status": "error", "error": "delete failed"}, None)):
            result = recovery.d5(cluster, {}, {"seed": 1, "warmup_s": 1.0}, {}, rate=1.0)
        self.assertEqual(result["overall"], "invalid")
        self.assertFalse(result["passed"])


class WorkloadDeclarationTest(unittest.TestCase):
    def test_recovery_workload_is_fresh_and_append_only(self) -> None:
        declared = recovery_driver.declaration()
        self.assertEqual(declared["workload_version"], "recovery-effects-v1")
        self.assertTrue(declared["append_only"])
        self.assertNotIn("upsert", declared["source"].lower())
        self.assertNotIn("on conflict", declared["source"].lower())
        self.assertEqual(recovery_driver.effect_token("request"), recovery_driver.effect_token("request"))
        self.assertIn("/health/detailed", Path(recovery_driver.__file__).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
