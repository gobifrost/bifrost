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
    "profile": {"worker_replicas": 1, "env": {"MALLOC_ARENA_MAX": "2"}, "thp_emulation": False, "sequential": 2, "burst": 2, "burst_rounds": 1},
    "classes": [
        {"name": "wf-noop", "pass": 1, "sequential": _OK, "burst": _OK,
         "db_per_run": {"xact": 36.4, "rows_written": 12.0, "wal_kib": 21.5},
         "worker_children": {"samples": 9, "peak_child_private_mib": 24.5, "peak_children": 3, "peak_template_private_mib": 168.0}},
        {"name": "agent-codemode", "pass": 1, "sequential": _OK, "burst": _BAD},
    ],
    "db_idle": {
        "seconds": 60.0,
        "rate": {"xact_per_s": 31.2, "rows_written_per_s": 4.0, "wal_kib_per_s": 2.5},
        "top_tables": [["scheduler_task_state", 120, 0], ["work_dispatches", 0, 900]],
    },
    "snapshots": [
        {"label": "fresh", "api": _API, "containers": [{"role": "worker", "working_set_mib": 85.0}], "db_mib": 40.0, "wal_mib": 10.0},
        {"label": "idle", "api": _API, "containers": [], "db_mib": 52.5, "wal_mib": 34.0},
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

    def test_database_load_is_reported(self) -> None:
        text = report.render(RESULT)
        self.assertIn("| 36 | 21.5 |", text)
        self.assertIn("31.2 transactions/s", text)
        self.assertIn("`work_dispatches`", text)
        self.assertIn("Database grew 12.5 MiB; WAL written 24.0 MiB", text)

    def test_worker_processes_are_reported(self) -> None:
        text = report.render(RESULT)
        self.assertIn("## Worker processes", text)
        self.assertIn("| `wf-noop` | 1 | 24.5 | 3 | 168.0 |", text)


if __name__ == "__main__":
    unittest.main()
