from __future__ import annotations

import asyncio
import importlib.util
import unittest
from pathlib import Path


class ElasticityDriverTest(unittest.TestCase):
    @staticmethod
    def _driver():
        path = Path(__file__).resolve().parents[1] / "incluster" / "elasticity_driver.py"
        spec = importlib.util.spec_from_file_location("elasticity_driver_test", path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_policy_reads_canonical_path_and_projects_only_scalar_controls(self) -> None:
        driver = self._driver()
        calls: list[tuple[str, str]] = []

        class Response:
            status_code = 200

            @staticmethod
            def json() -> dict:
                return {"policy": {"warm_workers": 1, "max_workers": 2, "worker_memory_mib": 1024,
                                   "largest_worker_mib": 1024, "idle_seconds": 300, "private": "omit"},
                        "job_types": ["omit"]}

        class Session:
            def __init__(self) -> None:
                self.http = self

            async def login(self) -> None:
                return None

            async def request(self, method: str, path: str):
                calls.append((method, path))
                return Response()

            async def aclose(self) -> None:
                return None

        driver._load = lambda _payload: type("Loadgen", (), {"Session": Session})
        result = asyncio.run(driver.policy({"loadgen_source": "unused"}))
        self.assertEqual(calls, [("GET", "/api/admin/kubernetes/execution")])
        self.assertEqual(result, {"policy": {"warm_workers": 1, "max_workers": 2, "worker_memory_mib": 1024,
                                              "largest_worker_mib": 1024, "idle_seconds": 300}})

    def test_admission_late_missing_and_duplicate_rows_are_observed_failures(self) -> None:
        driver = self._driver()
        requests = [
            {"id": "admission", "sent": 10.0, "error": "HTTP 503"},
            {"id": "late", "sent": 10.0, "error": None},
            {"id": "missing", "sent": 10.0, "error": None},
            {"id": "duplicate", "sent": 10.0, "error": None},
        ]
        reconciliation = {"outcomes": {
            "late": {"created": 10.0, "started": 11.0, "completed": 71.0, "correct": True},
            "duplicate": {"created": 10.0, "started": 11.0, "completed": 12.0, "correct": True},
        }, "missing": ["missing"], "duplicates": {"duplicate": 2}}
        result = driver.reconciled_records(requests, reconciliation)
        self.assertEqual(result["verdict"], "observed_failure")
        self.assertEqual(result["late_count"], 1)
        self.assertEqual(result["not_completed_by_deadline_count"], 3)
        self.assertFalse(next(row for row in result["requests"] if row["id"] == "late")["deadline_met"])

    def test_wait_result_and_pending_rows_are_observed_failures(self) -> None:
        driver = self._driver()
        loadgen_source = (Path(__file__).resolve().parents[1] / "incluster" / "loadgen.py").read_text(encoding="utf-8")
        loadgen = driver._load({"loadgen_source": loadgen_source})
        wrong = loadgen.reconcile_rows(
            [("workflow", ("wrong", "execution-1", "Success", {"waited_s": 1}, 10.0, 11.0, 12.0, None))],
            {"wrong": "wf-wait"}, loadgen.IO_DEFAULTS,
        )
        pending = loadgen.reconcile_rows(
            [("workflow", ("pending", "execution-2", "Pending", None, 10.0, None, None, None))],
            {"pending": "wf-wait"}, loadgen.IO_DEFAULTS,
        )
        wrong_result = driver.reconciled_records([{"id": "wrong", "sent": 10.0, "error": None}], wrong)
        pending_result = driver.reconciled_records([{"id": "pending", "sent": 10.0, "error": None}], pending)
        self.assertEqual(wrong_result["verdict"], "observed_failure")
        self.assertFalse(wrong_result["requests"][0]["correct"])
        self.assertEqual(pending_result["verdict"], "observed_failure")
        self.assertIsNone(pending_result["requests"][0]["completed"])

    def test_correct_result_needs_valid_timestamps_and_matching_http_identity(self) -> None:
        driver = self._driver()
        request = {"id": "r", "sent": 10.0, "durable_id": "execution", "error": None}
        row = {"created": 10.1, "started": 10.2, "completed": 12.2, "durable_id": "execution", "correct": True}
        base = {"outcomes": {"r": row}, "missing": [], "duplicates": {}}
        self.assertEqual(driver.reconciled_records([request], base)["verdict"], "observed")
        for change in ({"started": None}, {"completed": 9.0}, {"durable_id": "other"}):
            reconciliation = {**base, "outcomes": {"r": {**row, **change}}}
            self.assertEqual(driver.reconciled_records([request], reconciliation)["verdict"], "observed_failure")


if __name__ == "__main__":
    unittest.main()
