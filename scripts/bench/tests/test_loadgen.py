from __future__ import annotations

import importlib.util
import asyncio
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

# The generator runs on the Bifrost image; stand in for its two third-party imports.
for name in ("asyncpg", "httpx"):
    sys.modules.setdefault(name, types.ModuleType(name))
_SPEC = importlib.util.spec_from_file_location("loadgen", Path(__file__).resolve().parents[1] / "incluster" / "loadgen.py")
assert _SPEC and _SPEC.loader
loadgen = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(loadgen)


class _Response:
    def __init__(self, status_code: int, value: object) -> None:
        self.status_code, self._value, self.text = status_code, value, "failure"

    def json(self) -> object:
        return self._value


class _Session:
    def __init__(self, workflows: list[dict], agents: list[dict]) -> None:
        self.workflows, self.agents = workflows, agents
        self.calls: list[tuple[str, str]] = []
        self.http = self

    async def login(self) -> None:
        return None

    async def aclose(self) -> None:
        return None

    async def request(self, method: str, path: str, **_kwargs: object) -> _Response:
        self.calls.append((method, path))
        if path == "/api/workflows":
            return _Response(200, self.workflows)
        if path == "/api/agents":
            return _Response(200, self.agents)
        if path == "/api/admin/ai/connections":
            return _Response(200, [{"name": "fake", "endpoint": "http://fake"}])
        return _Response(500, {})


class ScheduleTest(unittest.TestCase):
    def test_same_seed_same_trace(self) -> None:
        mix = {"wf-noop": 50, "wf-sdk": 50}
        self.assertEqual(loadgen.schedule(5, 60, mix, 7), loadgen.schedule(5, 60, mix, 7))
        self.assertNotEqual(loadgen.schedule(5, 60, mix, 7), loadgen.schedule(5, 60, mix, 8))

    def test_rate_and_mix_are_respected(self) -> None:
        arrivals = loadgen.schedule(20, 600, {"wf-noop": 75, "wf-io": 25}, 1)
        self.assertAlmostEqual(len(arrivals) / 600, 20, delta=0.6)
        share = sum(1 for _, cls in arrivals if cls == "wf-io") / len(arrivals)
        self.assertAlmostEqual(share, 0.25, delta=0.02)
        self.assertTrue(all(0 <= offset < 600 for offset, _ in arrivals))
        self.assertEqual([offset for offset, _ in arrivals], sorted(offset for offset, _ in arrivals))


class CorrectTest(unittest.TestCase):
    io = {"calls": 3, "response_bytes": 2000, "latency_ms": 200}

    def test_expected_outputs(self) -> None:
        self.assertTrue(loadgen.correct("wf-noop", "Success", {"ok": True}, self.io))
        self.assertTrue(loadgen.correct("wf-sdk", "Success", {"configs": 5, "docs": 20}, self.io))
        self.assertTrue(loadgen.correct("wf-io", "Success", {"calls": 3, "bytes": 6000}, self.io))
        self.assertTrue(loadgen.correct("agent-chat", "completed", {"text": "bench complete"}, self.io))

    def test_terminal_success_with_wrong_output_is_not_correct(self) -> None:
        self.assertFalse(loadgen.correct("wf-sdk", "Success", {"configs": 4}, self.io))
        self.assertFalse(loadgen.correct("wf-io", "Success", {"calls": 3, "bytes": 5999}, self.io))
        self.assertFalse(loadgen.correct("wf-noop", "Failed", {"ok": True}, self.io))
        self.assertFalse(loadgen.correct("agent-tools", "failed", None, self.io))


class OutcomeRowsTest(unittest.TestCase):
    def test_keeps_all_durable_rows_before_the_single_outcome_projection(self) -> None:
        rows = [
            ("workflow", ("request-a", "execution-a", "Success", {"configs": 5}, 1.0, 2.0, 3.0, None)),
            ("workflow", ("request-a", "execution-b", "Success", {"configs": 5}, 1.0, 2.0, 4.0, None)),
        ]

        result = loadgen.reconcile_rows(rows, {"request-a": "wf-sdk"}, {"calls": 3, "response_bytes": 2000})

        self.assertEqual([row["durable_id"] for row in result["rows"]], ["execution-a", "execution-b"])
        self.assertEqual(result["duplicates"], {"request-a": 2})
        self.assertEqual(result["outcomes"]["request-a"]["durable_id"], "execution-a")


class PrepareTest(unittest.TestCase):
    def test_ordinary_capacity_does_not_require_import_workflows(self) -> None:
        session = _Session([{"name": "bench_wf_noop", "id": "noop"}, {"name": "bench_wf_sdk", "id": "sdk"}], [])
        with patch.object(loadgen, "Session", return_value=session):
            result = asyncio.run(loadgen.prepare(["wf-noop", "wf-sdk"]))
        self.assertEqual(result["workflows"], {"wf-noop": "noop", "wf-sdk": "sdk"})
        self.assertEqual(result["agents"], {})
        self.assertNotIn(("PUT", "/api/files/editor/content"), session.calls)

    def test_import_workflows_are_required_when_explicitly_selected(self) -> None:
        session = _Session([], [])
        with patch.object(loadgen, "Session", return_value=session):
            with self.assertRaisesRegex(SystemExit, "bench_wf_import_control"):
                asyncio.run(loadgen.prepare(["wf-import-control", "wf-import-graph"]))

    def test_agent_only_plan_requires_only_the_selected_agent(self) -> None:
        session = _Session([], [{"name": "bench_agent_chat", "id": "chat"}])
        with patch.object(loadgen, "Session", return_value=session):
            result = asyncio.run(loadgen.prepare(["agent-chat"]))
        self.assertEqual(result["workflows"], {})
        self.assertEqual(result["agents"], {"agent-chat": "chat"})


if __name__ == "__main__":
    unittest.main()
