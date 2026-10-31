from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import browser  # noqa: E402


def streamed_events() -> list[dict]:
    return [
        {"type": "invoke", "t_ms": 1.0, "request_id": "request-e"},
        {
            "type": "post",
            "t_ms": 2.0,
            "execution_id": "e",
            "method": "POST",
            "path": "/api/workflows/execute",
        },
        {
            "type": "get",
            "t_ms": 2.5,
            "execution_id": "e",
            "status": "Running",
            "method": "GET",
            "path": "/api/executions/e",
        },
        {"type": "subscribe", "t_ms": 3.0, "execution_id": "e", "channel": "execution:e"},
        {"type": "terminal", "t_ms": 4.0, "execution_id": "e", "status": "Success"},
        {
            "type": "get",
            "t_ms": 5.0,
            "execution_id": "e",
            "status": "Success",
            "method": "GET",
            "path": "/api/executions/e",
        },
        {"type": "render", "t_ms": 6.0, "execution_id": "e", "status": "Success"},
    ]


class BrowserDiagnosticTest(unittest.TestCase):
    def test_observation_batches_bound_browser_concurrency_per_variant(self) -> None:
        batches = browser.observation_batches()
        self.assertEqual(len(batches), len(browser.VARIANTS))
        self.assertEqual([[item["variant"] for item in batch] for batch in batches], [
            [variant, variant, variant] for variant in browser.VARIANTS
        ])
        self.assertEqual([[item["ordinal"] for item in batch] for batch in batches], [[0, 1, 2]] * 4)

    def test_run_matrix_requires_both_stages_for_every_batched_page(self) -> None:
        runs = [
            {"variant": page["variant"], "ordinal": page["ordinal"], "stage": stage}
            for batch in browser.observation_batches()
            for page in batch
            for stage in ("first_load", "steady")
        ]
        browser.validate_run_matrix(runs)
        with self.assertRaisesRegex(ValueError, "expected"):
            browser.validate_run_matrix(runs[:-1])

    def test_fixture_is_a_source_project_and_deterministic_archive(self) -> None:
        files = browser.source_files()
        self.assertIn("src/main.tsx", files)
        self.assertNotIn("src/main.test.tsx", files)
        self.assertIn(b"useWorkflow", files["src/main.tsx"])
        with tempfile.TemporaryDirectory() as tmp:
            receipt = browser.write_source_zip(Path(tmp) / "fixture.zip", files)
        self.assertEqual(receipt["source_sha256"], browser.source_manifest(files)["source_sha256"])
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "src/main.tsx"):
                browser.source_files(Path(tmp))

    def test_event_validation_classifies_streamed_sdk_path_and_safe_fields(self) -> None:
        receipt = browser.validate_events(streamed_events())
        self.assertEqual(receipt["execution_id"], "e")
        self.assertEqual(receipt["request_id"], "request-e")
        self.assertEqual(receipt["transport_path"], "streamed_ws")
        self.assertEqual(receipt["browser_wall_ms"], 5.0)
        bad = streamed_events()
        bad[3]["payload"] = "not allowed"
        with self.assertRaises(ValueError):
            browser.validate_events(bad)
        missing_request = streamed_events()
        missing_request[0].pop("request_id")
        with self.assertRaisesRegex(ValueError, "generated request ID"):
            browser.validate_events(missing_request)

    def test_event_validation_accepts_sdk_fast_paths_without_synthetic_events(self) -> None:
        inline = [
            {"type": "invoke", "t_ms": 1, "request_id": "request-inline"},
            {
                "type": "post",
                "t_ms": 2,
                "execution_id": "inline",
                "status": "Success",
                "method": "POST",
                "path": "/api/workflows/execute",
            },
        ]
        immediate_get = [
            {"type": "invoke", "t_ms": 1, "request_id": "request-get"},
            {
                "type": "post",
                "t_ms": 2,
                "execution_id": "get",
                "status": "Pending",
                "method": "POST",
                "path": "/api/workflows/execute",
            },
            {
                "type": "get",
                "t_ms": 3,
                "execution_id": "get",
                "status": "Success",
                "method": "GET",
                "path": "/api/executions/get",
            },
        ]
        self.assertEqual(browser.validate_events(inline)["transport_path"], "inline_post")
        self.assertEqual(browser.validate_events(immediate_get)["transport_path"], "immediate_get")

    def test_native_overlapping_terminal_checks_do_not_extend_render_latency(self) -> None:
        events = streamed_events()
        events.append({"type": "get", "t_ms": 7.0, "execution_id": "e", "status": "Success",
                       "method": "GET", "path": "/api/executions/e"})
        receipt = browser.validate_events(events)
        self.assertEqual(receipt["terminal_get_count"], 2)
        self.assertEqual(receipt["browser_wall_ms"], 5.0)

    def test_ready_check_can_observe_terminal_before_the_websocket_frame(self) -> None:
        events = streamed_events()
        events.insert(4, {"type": "get", "t_ms": 3.5, "execution_id": "e", "status": "Success",
                          "method": "GET", "path": "/api/executions/e"})
        self.assertEqual(browser.validate_events(events)["transport_path"], "immediate_get")

    def test_build_receipt_requires_completed_deploy_and_sdk_provenance(self) -> None:
        files = browser.source_files()
        with tempfile.TemporaryDirectory() as tmp:
            source = browser.write_source_zip(Path(tmp) / "fixture.zip", files)
        job = {
            "id": "accepted",
            "job_type": "application.deploy",
            "status": "succeeded",
            "execution_backend": "kubernetes",
            "created_at": "before",
            "started_at": "start",
            "completed_at": "end",
            "memory_required_bytes": 1,
            "result": {"application_id": "app", "deployment_id": "deploy"},
        }
        application = {
            "id": "app",
            "sdk_package_version": "v",
            "sdk_fingerprint": "hash",
            "sdk_contract_version": 3,
            "sdk_binding_api_major": 3,
            "sdk_built_at": "now",
        }
        receipt = browser.build_receipt(source, {"job_id": "accepted"}, job, application)
        self.assertEqual(receipt["sdk"]["sdk_contract_version"], 3)
        self.assertEqual(receipt["build"]["execution_backend"], "kubernetes")
        self.assertEqual(receipt["deploy"]["active_deployment_id"], "deploy")
        application["sdk_binding_api_major"] = None
        bundled = browser.build_receipt(source, {"job_id": "accepted"}, job, application)
        self.assertIsNone(bundled["sdk"]["sdk_binding_api_major"])
        with self.assertRaisesRegex(ValueError, "observed application"):
            browser.build_receipt(source, {"job_id": "accepted"}, job, {**application, "id": "other"})
        with self.assertRaisesRegex(ValueError, "IDs differ"):
            browser.build_receipt(source, {"job_id": "other"}, job, application)

    def test_runner_receipt_requires_current_playwright_digest(self) -> None:
        receipt = browser.runner_receipt(
            "mcr.microsoft.com/playwright:v1.64.1-noble@sha256:" + "a" * 64,
            "sha256:" + "b" * 64, True,
        )
        self.assertEqual(receipt["playwright_version"], "1.64.1")
        with self.assertRaises(ValueError):
            browser.runner_receipt(
                browser.BROWSER_RUNNER_IMAGE + "@sha256:not-a-digest",
                "sha256:" + "b" * 64,
                True,
            )
        with self.assertRaises(ValueError):
            browser.runner_receipt(
                "mcr.microsoft.com/playwright:v1.64.1-noble@sha256:" + "a" * 64,
                "sha256:" + "b" * 64,
                False,
            )

    def test_driver_explicitly_records_its_self_signed_tls_opt_in(self) -> None:
        driver = browser.DRIVER_PATH.read_text(encoding="utf-8")
        self.assertIn("ignoreHTTPSErrors: true", driver)


if __name__ == "__main__":
    unittest.main()
