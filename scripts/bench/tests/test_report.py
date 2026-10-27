from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import report  # noqa: E402

_DIST = {"p50": 10.0, "p95": 20.0, "max": 30.0}
_OK = {"runs": 2, "succeeded": 2, "failed": 0, "failures": [], "queue_ms": _DIST, "run_ms": _DIST, "e2e_ms": _DIST}
_BAD = dict(_OK, succeeded=1, failed=1, failures=[{"id": "r1", "status": "Failed", "error": "boom"}])
_API = {"rss_mib": 400.0, "heap_rss_mib": 120.0, "anon_large_rss_mib": 230.0, "thp_mib": 0.0}

RESULT = {
    "run_id": "run-1",
    "started_at": "2026-10-27T00:00:00Z",
    "git_sha": "abc1234",
    "image_id": "sha256:feed",
    "profile_name": "baseline",
    "backend": "fake",
    "api_pod_changes": 0,
    "profile": {"worker_replicas": 1, "env": {"MALLOC_ARENA_MAX": "2"}, "thp_emulation": False, "sequential": 2, "burst": 2},
    "classes": [
        {"name": "wf-noop", "pass": 1, "sequential": _OK, "burst": _OK},
        {"name": "agent-codemode", "pass": 1, "sequential": _OK, "burst": _BAD},
    ],
    "snapshots": [
        {"label": "fresh", "api": _API, "containers": [{"role": "worker", "working_set_mib": 85.0}]},
    ],
}


class RenderTest(unittest.TestCase):
    def test_report_names_every_class_and_the_build(self) -> None:
        text = report.render(RESULT)
        self.assertIn("`wf-noop`", text)
        self.assertIn("`agent-codemode`", text)
        self.assertIn("abc1234", text)
        self.assertIn("| fresh | 400 | 120 | 230 | 0 | 85 |", text)

    def test_failures_are_listed(self) -> None:
        text = report.render(RESULT)
        self.assertIn("## Failures", text)
        self.assertIn("boom", text)
        self.assertIn("1 / 2 ⚠", text)


if __name__ == "__main__":
    unittest.main()
