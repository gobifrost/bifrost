from __future__ import annotations

import asyncio
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


httpx = types.ModuleType("httpx")
httpx.AsyncClient = object
httpx.Response = object
sys.modules.setdefault("httpx", httpx)
os.environ.setdefault("BENCH_API_URL", "http://bench-api.test")

source = (Path(__file__).resolve().parents[1] / "incluster" / "driver.py").read_text(encoding="utf-8")
driver = types.ModuleType("bench_driver")
exec(compile(source.rsplit("\nmain()", 1)[0], "bench-driver", "exec"), driver.__dict__)


class Response:
    status_code = 200
    text = ""

    def __init__(self, body: dict) -> None:
        self.body = body

    def json(self) -> dict:
        return self.body


class Client:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def get(self, path: str, params: dict | None = None) -> Response:
        self.calls.append(path)
        if path == "/api/executions":
            return Response({"executions": [{
                "execution_id": "run-1", "status": "Success",
                "created_at": "2026-10-30T12:00:00Z", "started_at": "2026-10-30T12:00:01Z",
                "completed_at": "2026-10-30T12:00:02Z", "duration_ms": 17,
            }]})
        return Response({
            "execution_id": "run-1", "status": "Success",
            "created_at": "2026-10-30T12:00:00Z", "started_at": "2026-10-30T12:00:01Z",
            "completed_at": "2026-10-30T12:00:02Z", "duration_ms": 17,
            "result": {"ok": True, "endpoint": "https://imports.invalid/fixture", "signature": "import-fixture-v1"},
        })


class WaitAllTest(unittest.TestCase):
    def test_import_result_uses_one_terminal_detail_read_and_preserves_output(self) -> None:
        client = Client()
        records = asyncio.run(driver._wait_all(client, "/api/executions", {}, ["run-1"], 1, include_result=True))
        self.assertEqual(client.calls, ["/api/executions", "/api/executions/run-1"])
        self.assertEqual(records[0]["duration_ms"], 17)
        self.assertEqual(records[0]["result"]["signature"], "import-fixture-v1")

    def test_normal_class_keeps_list_only_wait_path(self) -> None:
        client = Client()
        records = asyncio.run(driver._wait_all(client, "/api/executions", {}, ["run-1"], 1))
        self.assertEqual(client.calls, ["/api/executions"])
        self.assertIsNone(records[0]["result"])


class InteractiveRunTest(unittest.TestCase):
    def test_interactive_class_submits_generated_request_id_and_reads_terminal_result(self) -> None:
        class InteractiveClient:
            def __init__(self) -> None:
                self.submissions: list[dict] = []

            async def post(self, path: str, json: dict) -> Response:
                self.submissions.append({"path": path, "json": json})
                return Response({"execution_id": "interactive-run"})

            async def get(self, path: str, params: dict | None = None) -> Response:
                if path == "/api/executions":
                    return Response({"executions": [{"execution_id": "interactive-run", "status": "Success"}]})
                return Response({"execution_id": "interactive-run", "status": "Success", "result": {
                    "ok": True, "variant": "shallow_list", "request_id": self.submissions[0]["json"]["input_data"]["bench_request_id"],
                }})

            async def aclose(self) -> None:
                return None

        client = InteractiveClient()

        async def fake_client() -> InteractiveClient:
            return client

        async def fake_workflow_ids(_http: InteractiveClient) -> dict[str, str]:
            return {"bench_wf_interactive_shallow_list": "workflow-id"}

        with patch.object(driver, "client", fake_client), patch.object(driver, "_workflow_ids", fake_workflow_ids), \
             patch.object(driver, "POLL_INTERVAL_S", 0):
            outcome = asyncio.run(driver.run_class("wf-interactive-shallow-list", 1, 0, 1))

        submitted = client.submissions[0]
        self.assertEqual(submitted["path"], "/api/workflows/execute")
        self.assertEqual(submitted["json"]["workflow_id"], "workflow-id")
        self.assertTrue(submitted["json"]["input_data"]["bench_request_id"])
        self.assertEqual(outcome["sequential"][0]["result"]["variant"], "shallow_list")

    def test_fake_api_span_reader_uses_only_the_in_cluster_service(self) -> None:
        class SpanClient:
            base_url = ""

            async def __aenter__(self) -> "SpanClient":
                return self

            async def __aexit__(self, *_args: object) -> None:
                return None

            async def get(self, path: str) -> Response:
                self.path = path
                return Response({"spans": [{"tag": "generated-id", "kind": "sites"}]})

        span_client = SpanClient()
        with patch.object(driver.httpx, "AsyncClient", return_value=span_client) as factory:
            spans = asyncio.run(driver.fake_api_spans())
        factory.assert_called_once_with(base_url="http://bench-fake-api.bench-infra:8080", timeout=30)
        self.assertEqual(span_client.path, "/spans")
        self.assertEqual(spans["spans"][0]["tag"], "generated-id")


if __name__ == "__main__":
    unittest.main()
