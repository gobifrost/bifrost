from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import execution_rates  # noqa: E402
import search  # noqa: E402


def record(identifier: str, scheduled: float, completed: float | None, **changes) -> dict:
    return {"id": identifier, "class": "wf-sdk", "scheduled_s": scheduled, "sent_s": scheduled,
            "responded_s": scheduled + 0.1, "created_s": scheduled + 0.05,
            "completed_s": completed, "status": "Success" if completed is not None else "Running",
            "outcome": "correct", "http_status": 200, "durable_found": True, "output_correct": True,
            "deadline_met": True, "duplicate_durable_rows": 0, **changes}


class ExecutionRatesTest(unittest.TestCase):
    def test_event_rates_and_arrival_cohort_keep_warmup_and_drain_separate(self) -> None:
        result = execution_rates.summarize([
            record("warmup", 8, 10), record("inside", 11, 12), record("drain", 19, 20),
            record("boundary", 20, 21),
        ], (10, 20))
        counts = result["total"]["event_counts"]
        cohort = result["total"]["arrival_cohort"]
        self.assertEqual(counts["scheduled"], 2)
        self.assertEqual(counts["correct_completed"], 2)
        self.assertEqual(result["total"]["event_rates_per_s"]["correct_completed"], 0.2)
        self.assertEqual(cohort["scheduled"], 2)
        self.assertEqual(cohort["correct_completed"], 2)

    def test_late_correct_work_is_physical_throughput_but_misses_deadline(self) -> None:
        result = execution_rates.summarize([
            record("late", 10, 19, outcome="failed", deadline_met=False),
        ], (10, 20))["total"]
        self.assertEqual(result["event_counts"]["correct_completed"], 1)
        self.assertEqual(result["event_counts"]["correct_by_deadline_completed"], 0)
        self.assertEqual(result["arrival_cohort"]["correct_late"], 1)
        self.assertEqual(result["arrival_cohort"]["failed_terminal"], 0)

    def test_acceptance_union_preserves_lost_responses_missing_entities_and_duplicates(self) -> None:
        result = execution_rates.summarize([
            record("both", 10, 11),
            record("lost-response", 11, 12, http_status=None),
            record("http-only", 12, None, durable_found=False),
            record("neither", 13, None, http_status=503, durable_found=False),
            record("duplicate", 14, 15, duplicate_durable_rows=1, outcome="failed"),
        ], (10, 20))["total"]["arrival_cohort"]
        self.assertEqual(result["accepted_union"], 4)
        self.assertEqual((result["http_only"], result["durable_only"], result["http_and_durable"]), (1, 1, 2))
        self.assertEqual(result["neither_accepted"], 1)
        self.assertEqual(result["duplicate_request_ids"], 1)
        self.assertEqual(result["extra_durable_rows"], 1)

    def test_workflow_and_agent_work_units_are_reported_separately(self) -> None:
        result = execution_rates.summarize([
            record("workflow", 10, 11), record("agent", 11, 12, **{"class": "agent-tools", "status": "completed"}),
        ], (10, 20))
        self.assertEqual(result["by_kind"]["workflow"]["event_rates_per_s"]["correct_completed"], 0.1)
        self.assertEqual(result["by_kind"]["agent"]["event_rates_per_s"]["correct_completed"], 0.1)
        self.assertEqual(result["total"]["event_rates_per_s"]["correct_completed"], 0.2)

    def test_duplicate_request_records_and_empty_windows_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unique"):
            execution_rates.summarize([record("same", 10, 11)] * 2, (10, 20))
        with self.assertRaisesRegex(ValueError, "positive"):
            execution_rates.summarize([], (10, 10))

    def test_latency_has_explicit_missing_values_and_keeps_late_successes(self) -> None:
        result = execution_rates.summarize([
            record("late", 10, 19, outcome="failed", deadline_met=False, admission_s=0.5, queue_s=1),
            record("not-sent", 11, None, outcome="failed", sent_s=None, durable_found=False),
        ], (10, 20))["total"]["observed_latency_s"]
        self.assertEqual(result["send_to_correct_terminal"], {"observed": 1, "missing": 1, "p50": 9, "p95": 9, "max": 9})
        self.assertEqual(result["admission_all_responses"]["missing"], 1)
        self.assertIsNone(execution_rates.summarize([], (10, 20))["total"]["observed_latency_s"]["queue_all_started"]["p95"])

    def test_platform_window_rejects_nonfinite_inputs_and_counts_nested_work(self) -> None:
        for start, end in ((1, float("inf")), (2, 1), (float("nan"), 2)):
            with self.assertRaises(ValueError):
                execution_rates.platform_activity_sql(start, end)
        sql = execution_rates.platform_activity_sql(10, 20)
        self.assertIn("left join workflows", sql)
        self.assertIn("e.completed_at < to_timestamp(20.000000000)", sql)


class DurableTimingTest(unittest.TestCase):
    def test_late_and_failed_terminal_timings_survive_reconciliation(self) -> None:
        sent = [{"id": "late", "class": "wf-sdk", "scheduled": 100, "sent": 100,
                 "responded": 101, "http_status": 200, "error": None}]
        durable = {"correct": True, "completed": 190, "started": 105, "created": 101,
                   "status": "Success", "durable_id": "execution", "error": None}
        row = search.to_records(sent, {"outcomes": {"late": durable}}, 100, {"wf-sdk": 60})[0]
        self.assertEqual(row["outcome"], "failed")
        self.assertTrue(row["output_correct"])
        self.assertFalse(row["deadline_met"])
        self.assertEqual((row["created_s"], row["started_s"], row["completed_s"], row["queue_s"]), (1, 5, 90, 4))
        durable.update({"correct": False, "status": "Failed"})
        failed = search.to_records(sent, {"outcomes": {"late": durable}}, 100, {"wf-sdk": 60})[0]
        self.assertEqual(failed["completed_s"], 90)
        self.assertFalse(failed["output_correct"])


if __name__ == "__main__":
    unittest.main()
