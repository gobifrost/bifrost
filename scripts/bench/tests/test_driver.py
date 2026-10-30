from __future__ import annotations

import asyncio
import os
import sys
import types
import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
