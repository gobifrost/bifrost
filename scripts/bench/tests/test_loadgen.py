from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path

# The generator runs on the Bifrost image; stand in for its two third-party imports.
for name in ("asyncpg", "httpx"):
    sys.modules.setdefault(name, types.ModuleType(name))
_SPEC = importlib.util.spec_from_file_location("loadgen", Path(__file__).resolve().parents[1] / "incluster" / "loadgen.py")
assert _SPEC and _SPEC.loader
loadgen = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(loadgen)


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


if __name__ == "__main__":
    unittest.main()
