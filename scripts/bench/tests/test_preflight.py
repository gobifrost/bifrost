from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import preflight  # noqa: E402
from cluster import RemoteCleanupError  # noqa: E402


PLAN = {
    "seed": 17,
    "mix": {"wf-noop": 1, "wf-sdk": 1},
    "deadlines_s": {"wf-noop": 10.0, "wf-sdk": 20.0},
}
SENT = {
    "started": 100.0,
    "requests": [
        {"id": "one", "class": "wf-noop", "scheduled": 101.0, "sent": 101.0, "responded": 101.1, "http_status": 200, "error": None},
        {"id": "two", "class": "wf-sdk", "scheduled": 102.0, "sent": 102.0, "responded": 102.1, "http_status": 200, "error": None},
    ],
}
FAULTS = {"restarts": 0, "oom_killed": 0, "evicted": 0, "quota_denials": 0}

ROWS = {
    "outcomes": {
        "one": {"request_id": "one", "kind": "workflow", "status": "Success", "completed": 103.0, "started": 102.0, "created": 101.0, "correct": True, "error": None},
        "two": {"request_id": "two", "kind": "workflow", "status": "Success", "completed": 104.0, "started": 103.0, "created": 102.0, "correct": True, "error": None},
    },
    "rows": [
        {"request_id": "one", "kind": "workflow", "status": "Success", "completed": 103.0, "started": 102.0, "created": 101.0, "correct": True, "error": None},
        {"request_id": "two", "kind": "workflow", "status": "Success", "completed": 104.0, "started": 103.0, "created": 102.0, "correct": True, "error": None},
    ],
    "duplicates": {}, "missing": [],
}


class PreflightTest(unittest.TestCase):
    def _cluster(self, outcomes=ROWS, faults=(FAULTS, FAULTS)):
        cluster = Mock()
        command = Mock()
        command.result.return_value = SENT
        cluster.start_exec_python.return_value = command
        cluster.exec_python.return_value = outcomes
        cluster.tenant_faults.side_effect = faults
        return cluster, command

    def test_passes_only_after_all_mix_classes_are_correct_by_deadline(self) -> None:
        cluster, command = self._cluster()
        with patch.object(preflight.search.time, "time", return_value=200.0):
            result = preflight.run(cluster, {"workflows": {}}, 3.0, PLAN)
        self.assertEqual(result["overall"], "pass")
        self.assertTrue(all(result["checks"].values()))
        self.assertEqual(result["durable_outcomes"]["rows"], ROWS["rows"])
        payload = json.loads(cluster.start_exec_python.call_args.args[2][1])
        self.assertTrue(payload["event_frames"])
        self.assertEqual(payload["seed"], preflight.search.confirmation_seed(PLAN))
        self.assertEqual(result["profile_seed"], PLAN["seed"])
        self.assertEqual(result["arrival_seed"], payload["seed"])

    def test_progress_is_available_before_waiting_for_the_latest_deadline(self) -> None:
        cluster, _ = self._cluster()
        observed: list[set[str]] = []

        def progress(value: dict) -> None:
            observed.append(set(value))

        def wait(seconds: float) -> None:
            self.assertGreater(seconds, 0)
            self.assertEqual(len(observed), 1)
            self.assertIn("sent", observed[0])
            self.assertIn("immediate_log_tails", observed[0])
            self.assertNotIn("durable_outcomes", observed[0])

        with patch.object(preflight.search.time, "time", return_value=100.0), \
             patch.object(preflight.search.time, "sleep", side_effect=wait):
            result = preflight.run(cluster, {}, 3.0, PLAN, on_progress=progress)
        self.assertEqual(result["overall"], "pass")
        self.assertEqual(len(observed), 2)
        self.assertIn("durable_outcomes", observed[1])

    def test_http_200_with_durable_failure_does_not_pass(self) -> None:
        outcomes = {**ROWS, "outcomes": {**ROWS["outcomes"], "two": {**ROWS["outcomes"]["two"], "status": "Failed", "correct": False}},
                    "rows": [ROWS["rows"][0], {**ROWS["rows"][1], "status": "Failed", "correct": False}]}
        cluster, _ = self._cluster(outcomes)
        with patch.object(preflight.search.time, "time", return_value=200.0):
            result = preflight.run(cluster, {}, 3.0, PLAN)
        self.assertEqual(result["overall"], "fail")
        self.assertFalse(result["checks"]["all_submitted_correct_by_deadline"])
        self.assertIn("failure_log_tails", result)

    def test_deadline_duplicate_and_faults_each_fail(self) -> None:
        late = {**ROWS, "outcomes": {**ROWS["outcomes"], "two": {**ROWS["outcomes"]["two"], "completed": 200.0}},
                "rows": [ROWS["rows"][0], {**ROWS["rows"][1], "completed": 200.0}]}
        for label, outcomes, faults, check in (
            ("deadline", late, (FAULTS, FAULTS), "all_submitted_correct_by_deadline"),
            ("duplicate", {**ROWS, "duplicates": {"one": 2}}, (FAULTS, FAULTS), "no_duplicate_durable_executions"),
            ("fault", ROWS, (FAULTS, {**FAULTS, "restarts": 1}), "no_tenant_faults"),
        ):
            with self.subTest(label=label):
                cluster, _ = self._cluster(outcomes, faults)
                with patch.object(preflight.search.time, "time", return_value=200.0):
                    result = preflight.run(cluster, {}, 3.0, PLAN)
                self.assertEqual(result["overall"], "fail")
                self.assertFalse(result["checks"][check])

    def test_missing_mix_class_is_inconclusive_without_retry(self) -> None:
        cluster, _ = self._cluster()
        cluster.start_exec_python.return_value.result.return_value = {**SENT, "requests": [SENT["requests"][0]]}
        only_one = {**ROWS, "outcomes": {"one": ROWS["outcomes"]["one"]}, "rows": [ROWS["rows"][0]]}
        cluster.exec_python.return_value = only_one
        with patch.object(preflight.search.time, "time", return_value=200.0):
            result = preflight.run(cluster, {}, 3.0, PLAN)
        self.assertEqual(result["overall"], "inconclusive")
        self.assertFalse(result["checks"]["every_mix_class_scheduled_and_sent"])
        cluster.start_exec_python.assert_called_once()


    def test_unsent_request_is_inconclusive_when_coverage_is_the_only_problem(self) -> None:
        cluster, _ = self._cluster()
        unsent = {**SENT, "requests": [{**SENT["requests"][0], "sent": None}, SENT["requests"][1]]}
        cluster.start_exec_python.return_value.result.return_value = unsent
        with patch.object(preflight.search.time, "time", return_value=200.0):
            result = preflight.run(cluster, {}, 3.0, PLAN)
        self.assertEqual(result["overall"], "inconclusive")
        self.assertFalse(result["checks"]["all_submitted_correct_by_deadline"])

    def test_known_durable_failure_takes_precedence_over_missing_mix_coverage(self) -> None:
        cluster, _ = self._cluster()
        cluster.start_exec_python.return_value.result.return_value = {**SENT, "requests": [SENT["requests"][0]]}
        failed = {**ROWS, "outcomes": {"one": {**ROWS["outcomes"]["one"], "correct": False}},
                  "rows": [{**ROWS["rows"][0], "correct": False}]}
        cluster.exec_python.return_value = failed
        with patch.object(preflight.search.time, "time", return_value=200.0):
            result = preflight.run(cluster, {}, 3.0, PLAN)
        self.assertEqual(result["overall"], "fail")
        self.assertFalse(result["checks"]["every_mix_class_scheduled_and_sent"])
        self.assertFalse(result["checks"]["all_submitted_correct_by_deadline"])

    def test_incomplete_fault_counters_are_invalid_not_zero_faults(self) -> None:
        cluster, _ = self._cluster(faults=(FAULTS, {"restarts": 0}))
        with patch.object(preflight.search.time, "time", return_value=200.0):
            result = preflight.run(cluster, {}, 3.0, PLAN)
        self.assertEqual(result["overall"], "invalid")
        self.assertEqual(result["fault_counters"]["before"], FAULTS)
        self.assertEqual(result["fault_counters"]["after"], {"restarts": 0})
        self.assertEqual(result["durable_outcomes"]["rows"], ROWS["rows"])
        self.assertNotIn("faults", result)


    def test_remote_cleanup_error_is_saved_as_invalid_for_guarded_reset(self) -> None:
        cluster, _ = self._cluster()
        cluster.start_exec_python.return_value.result.side_effect = RemoteCleanupError("driver was not deleted")
        result = preflight.run(cluster, {}, 3.0, PLAN)
        self.assertEqual(result["overall"], "invalid")
        self.assertTrue(result["requires_namespace_teardown"])
        self.assertIn("RemoteCleanupError", result["error"])

    def test_log_tails_are_bounded_and_written_at_the_step_boundary(self) -> None:
        cluster, _ = self._cluster()
        cluster.kubectl.side_effect = [
            "pod/bifrost-api-one\npod/bifrost-worker-one\npod/redis-0\n", "api tail", "worker tail",
        ]
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as temporary, patch.object(preflight.search.time, "time", return_value=200.0):
            result = preflight.run(cluster, {}, 3.0, PLAN, Path(temporary))
            tails = result["immediate_log_tails"]
            self.assertEqual(set(tails), {"bifrost-api-one", "bifrost-worker-one"})
            self.assertEqual(Path(tails["bifrost-api-one"]).read_text(), "api tail")
            self.assertTrue(Path(tails["bifrost-worker-one"]).name.startswith("after-step-"))
