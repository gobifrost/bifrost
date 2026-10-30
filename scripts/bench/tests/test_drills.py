from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

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


class D4ReconciliationTest(unittest.TestCase):
    deadlines = {"wf-sdk": 60.0}

    def test_accepts_http_success_or_a_durable_row_and_keeps_all_rows(self) -> None:
        requests = [
            {"id": "http", "class": "wf-sdk", "sent": 103.0, "http_status": 202},
            {"id": "lost-response", "class": "wf-sdk", "sent": 104.0, "http_status": None},
        ]
        rows = [
            {"kind": "workflow", "request_id": "http", "status": "Success", "completed": 105.0, "correct": True},
            {"kind": "workflow", "request_id": "lost-response", "status": "Success", "completed": 106.0, "correct": True},
        ]

        result = drills.reconcile_d4(requests, rows, ready_at=102.0, deadlines=self.deadlines)

        self.assertTrue(result["checks"]["accepted_one_durable_row"])
        self.assertTrue(result["checks"]["accepted_terminal_by_deadline"])
        self.assertEqual(result["requests"]["lost-response"]["accepted_by"], "durable-row")
        self.assertEqual(result["requests"]["http"]["rows"], [rows[0]])
        self.assertEqual(result["first_correct_after_ready_s"], 3.0)

    def test_zero_or_multiple_rows_for_an_accepted_request_are_explicit_failures(self) -> None:
        requests = [
            {"id": "missing", "class": "wf-sdk", "sent": 100.0, "http_status": 202},
            {"id": "duplicate", "class": "wf-sdk", "sent": 101.0, "http_status": None},
        ]
        rows = [
            {"kind": "workflow", "request_id": "duplicate", "status": "Success", "completed": 105.0, "correct": True},
            {"kind": "workflow", "request_id": "duplicate", "status": "Success", "completed": 106.0, "correct": True},
        ]

        result = drills.reconcile_d4(requests, rows, ready_at=102.0, deadlines=self.deadlines)

        self.assertFalse(result["checks"]["accepted_one_durable_row"])
        self.assertEqual(result["requests"]["missing"]["failure"], "accepted_zero_durable_rows")
        self.assertEqual(result["requests"]["duplicate"]["failure"], "accepted_multiple_durable_rows")

    def test_first_correct_must_be_sent_after_ready_and_within_thirty_seconds(self) -> None:
        requests = [
            {"id": "before", "class": "wf-sdk", "sent": 90.0, "http_status": 202},
            {"id": "late", "class": "wf-sdk", "sent": 101.0, "http_status": 202},
        ]
        rows = [
            {"kind": "workflow", "request_id": "before", "status": "Success", "completed": 103.0, "correct": True},
            {"kind": "workflow", "request_id": "late", "status": "Success", "completed": 134.0, "correct": True},
        ]

        result = drills.reconcile_d4(requests, rows, ready_at=100.0, deadlines=self.deadlines)

        self.assertEqual(result["first_correct_after_ready_s"], 34.0)
        self.assertFalse(result["checks"]["first_correct_after_ready"])


class D4InjectorTest(unittest.TestCase):
    def test_waits_for_actual_start_and_a_pre_fault_send_before_uid_delete(self) -> None:
        cluster = Mock()
        cluster.delete_pod_uid.return_value = {"metadata": {"uid": "redis-old", "deletionTimestamp": "2026-10-30T00:00:00Z"}}
        pod = {"metadata": {"name": "redis-0", "uid": "redis-old"}}
        ready = {
            "ready_at": 102.0,
            "old_uid_gone": True,
            "replacement_uid": "redis-new",
            "empty_dir_after": {"name": "data", "emptyDir": {}},
            "empty_dir_unchanged": True,
            "redis": {"pong": True, "run_id": "new", "sentinel_exists": False, "dbsize": "3"},
        }
        preseed = {"uid": "redis-old", "before": {"run_id": "old"}, "sentinel": "d4-sentinel:test", "empty_dir_before": {"name": "data", "emptyDir": {}}}

        with patch.object(drills, "redis_pod", return_value=pod), \
             patch.object(drills, "wait_d4_ready", return_value=ready):
            injector = drills.RedisInjector(cluster, preseed, fault_delay_s=0.0)
            injector.start()
            injector.observe({"event": "started", "started": 100.0})
            injector.observe({"event": "request_sent", "request_id": "first", "sent": 101.0})
            result = injector.join(2)

        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["first_request_id"], "first")
        self.assertEqual(cluster.delete_pod_uid.call_args.args, ("redis-0", "redis-old"))


if __name__ == "__main__":
    unittest.main()


def _redis_pod(uid: str = "redis-old", *, deleting: bool = False) -> dict:
    return {
        "metadata": {
            "name": "redis-0", "namespace": "bench-tenant", "uid": uid,
            "labels": {"app.kubernetes.io/name": "redis"},
            **({"deletionTimestamp": "2026-10-30T00:00:00Z"} if deleting else {}),
            "ownerReferences": [{"kind": "StatefulSet", "name": "redis", "controller": True}],
        },
        "status": {
            "conditions": [{"type": "Ready", "status": "True"}],
            "containerStatuses": [{"name": "redis", "ready": True}],
        },
    }


class D4RedisTargetTest(unittest.TestCase):
    def test_selector_rejects_extra_terminating_redis_pod(self) -> None:
        cluster = Mock()
        cluster.kubectl.return_value = json.dumps({"items": [_redis_pod(), _redis_pod("old", deleting=True)]})
        with self.assertRaisesRegex(RuntimeError, "exactly one Redis selector pod"):
            drills.redis_pod(cluster)

    def test_preseed_requires_pong_and_the_sentinel(self) -> None:
        cluster = Mock()
        with patch.object(drills, "redis_pod", return_value=_redis_pod()), \
             patch.object(drills, "_redis_exec", return_value="OK"), \
             patch.object(drills, "_redis_snapshot", return_value={"pong": False, "sentinel_exists": True}), \
             patch.object(drills, "_empty_dir_spec", return_value={"name": "data", "emptyDir": {}}):
            with self.assertRaisesRegex(RuntimeError, "requires PONG"):
                drills.preseed_d4(cluster, "test")


class D4ReadyProofTest(unittest.TestCase):
    def setUp(self) -> None:
        self.preseed = {
            "uid": "old", "empty_dir_before": {"name": "data", "emptyDir": {}},
            "before": {"run_id": "old-run"},
        }
        self.snapshot = {"pong": True, "run_id": "new-run", "sentinel_exists": False}

    def test_each_required_replacement_proof_is_a_gate(self) -> None:
        base = drills.d4_ready_checks(self.preseed, True, "new", {"name": "data", "emptyDir": {}}, self.snapshot)
        self.assertTrue(all(base.values()))
        cases = {
            "old_uid_gone": (False, "new", {"name": "data", "emptyDir": {}}, self.snapshot),
            "replacement_uid_changed": (True, "old", {"name": "data", "emptyDir": {}}, self.snapshot),
            "empty_dir_unchanged": (True, "new", {"name": "other", "emptyDir": {}}, self.snapshot),
            "redis_pong": (True, "new", {"name": "data", "emptyDir": {}}, {**self.snapshot, "pong": False}),
            "redis_run_id_changed": (True, "new", {"name": "data", "emptyDir": {}}, {**self.snapshot, "run_id": "old-run"}),
            "preseeded_sentinel_missing": (True, "new", {"name": "data", "emptyDir": {}}, {**self.snapshot, "sentinel_exists": True}),
        }
        for name, args in cases.items():
            with self.subTest(name=name):
                self.assertFalse(drills.d4_ready_checks(self.preseed, *args)[name])


class D4FinalReconciliationTest(unittest.TestCase):
    plan = {"warmup_s": 0, "seed": 4, "deadlines_s": {"wf-sdk": 10.0}, "io": None}
    step = {
        "overall": "pass", "started": 0.0, "window_s": 90.0,
        "sent": [{"id": "request", "class": "wf-sdk", "sent": 10.0, "scheduled": 10.0, "http_status": 202}],
        "outcomes": {"rows": []},
    }
    preseed = {"uid": "old", "before": {"run_id": "old-run"}, "empty_dir_before": {"name": "data", "emptyDir": {}}}

    def test_invalid_injector_still_waits_for_and_persists_final_reconciliation(self) -> None:
        cluster = Mock()
        cluster.driver_sql.side_effect = [[], []]
        cluster.exec_python.return_value = {"rows": [
            {"kind": "workflow", "request_id": "request", "status": "Success", "completed": 12.0, "correct": True},
        ]}
        injector = Mock()
        injector.state = {"status": "error"}
        injector.join.return_value = {"status": "error", "error": "injection failed"}
        saved: list[dict] = []
        with patch.object(drills, "preseed_d4", return_value=self.preseed), \
             patch.object(drills, "RedisInjector", return_value=injector), \
             patch.object(drills.search, "run_step", return_value=self.step), \
             patch.object(drills.time, "time", return_value=100.0):
            result = drills.d4(cluster, {}, self.plan, {}, "run", saved.append)
        self.assertEqual(result["overall"], "invalid")
        self.assertTrue(result["final_reconciliation"]["available"])
        self.assertEqual(result["final"]["outcomes"]["rows"][0]["request_id"], "request")
        self.assertEqual(len(saved), 2)

    def test_unavailable_final_reconciliation_requires_namespace_teardown(self) -> None:
        cluster = Mock()
        cluster.driver_sql.side_effect = [[], []]
        cluster.exec_python.side_effect = RuntimeError("driver gone")
        injector = Mock()
        injector.state = {"status": "error"}
        injector.join.return_value = {"status": "error"}
        saved: list[dict] = []
        with patch.object(drills, "preseed_d4", return_value=self.preseed), \
             patch.object(drills, "RedisInjector", return_value=injector), \
             patch.object(drills.search, "run_step", return_value=self.step), \
             patch.object(drills.time, "time", return_value=100.0):
            result = drills.d4(cluster, {}, self.plan, {}, "run", saved.append)
        self.assertFalse(result["final_reconciliation"]["available"])
        self.assertTrue(result["requires_namespace_teardown"])
        self.assertEqual(result["overall"], "invalid")

    def test_nonterminal_status_is_not_a_terminal_acceptance(self) -> None:
        result = drills.reconcile_d4(
            self.step["sent"],
            [{"kind": "workflow", "request_id": "request", "status": "Running", "completed": 12.0, "correct": False}],
            ready_at=None, deadlines=self.plan["deadlines_s"],
        )
        self.assertFalse(result["checks"]["accepted_terminal_by_deadline"])
        self.assertEqual(result["requests"]["request"]["terminal_status"], "Running")

    def test_typed_remote_cleanup_failure_requires_namespace_teardown(self) -> None:
        cluster = Mock()
        cluster.driver_sql.side_effect = [[], []]
        injector = Mock()
        injector.join.return_value = {"status": "error"}
        saved: list[dict] = []
        with patch.object(drills, "preseed_d4", return_value=self.preseed), \
             patch.object(drills, "RedisInjector", return_value=injector), \
             patch.object(drills.search, "run_step", side_effect=drills.RemoteCleanupError("unverified")):
            result = drills.d4(cluster, {}, self.plan, {}, "run", saved.append)
        self.assertTrue(result["requires_namespace_teardown"])
        self.assertFalse(result["final_reconciliation"]["available"])


    def test_unknown_terminal_status_is_rejected_by_kind_allowlist(self) -> None:
        result = drills.reconcile_d4(
            self.step["sent"],
            [{"kind": "workflow", "request_id": "request", "status": "Mystery", "completed": 12.0, "correct": False}],
            ready_at=None, deadlines=self.plan["deadlines_s"],
        )
        self.assertFalse(result["checks"]["accepted_terminal_by_deadline"])
        self.assertEqual(result["requests"]["request"]["failure"], "accepted_nonterminal_status")

    def test_fault_timing_requires_pre_fault_send_and_offered_window(self) -> None:
        injected = {"fault_at": 85.0, "first_request_sent_at": 84.0}
        self.assertTrue(all(drills.d4_timing_checks(self.step, self.plan, injected).values()))
        late = drills.d4_timing_checks(self.step, self.plan, {"fault_at": 91.0, "first_request_sent_at": 92.0})
        self.assertFalse(late["fault_inside_offered_window"])
        self.assertFalse(late["pre_fault_request_sent"])

    def test_worker_evidence_retains_raw_states_losses_and_session_changes(self) -> None:
        before = [{"id": "a", "state": "ready", "generation": 1}, {"id": "b", "state": "ready", "generation": 2}]
        after = [{"id": "a", "state": "lost", "generation": 1}, {"id": "b", "state": "ready", "generation": 3}]
        evidence = drills.d4_worker_evidence(before, after)
        self.assertEqual(evidence["before"], before)
        self.assertEqual(evidence["after"], after)
        self.assertEqual(evidence["lost"], [after[0]])
        self.assertEqual(evidence["remote_session_generation_changes"], [{"id": "b", "before": 2, "after": 3}])
