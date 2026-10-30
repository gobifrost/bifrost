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
    "kind": "classes",
    "images": {"api": {"tag": "bifrost-api:kind-x", "id": "sha256:feedfacefeedface0000"}},
    "kind_version": "v0.30.0",
    "cnpg_version": "1.30.1",
    "host": {"cpus": 8, "load_average": [1.0, 1.0, 1.0]},
    "external_nodes": [],
    "deploy": {"envelope": {"max_occupancy": "3.900 CPU / 7.69 GiB", "headroom": "0.100 CPU / 0.31 GiB"}, "admitted": {"admitted": "2.600 CPU / 5.69 GiB"}},
    "profile_name": "m",
    "backend": "fake",
    "api_pod_changes": 0,
    "profile": {
        "quota": {"cpu": "4", "memory": "8Gi"},
        "components": {"api": {"replicas": 1, "cpu": "1250m", "memory": "1280Mi"}, "postgres": {"cpu": "1", "memory": "2Gi"}},
        "worker_policy": {"warm_workers": 1, "max_workers": 2, "worker_memory_mib": 2048},
        "worker_cpu": "650m", "env": {"MALLOC_ARENA_MAX": "2"}, "fake_llm_latency_ms": 0,
        "sequential": 2, "burst": 2, "burst_rounds": 1,
    },
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
        {"label": "fresh", "api": _API, "containers": [{"role": "worker", "pod": "bifrost-worker-one", "working_set_mib": 85.0, "rss_mib": 80.0}], "db_mib": 40.0, "wal_mib": 10.0},
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


class CapacityReportTest(unittest.TestCase):
    def test_memory_peaks_are_simultaneous_and_exclude_infrastructure_and_warmup(self) -> None:
        def pod(gib: float) -> dict:
            value = int(gib * 2**30)
            return {"memory_bytes": {"current": value, "working_set": value,
                                     "anon": value, "file": 0, "kernel": 0}}

        step = {"started": 100, "window": [10, 20], "host": [
            {"at": 105, "pods": {"bench-tenant/api": pod(10)}},
            {"at": 110, "pods": {"bench-tenant/api": pod(2), "bench-tenant/worker": pod(0.5),
                                 "bench-infra/driver": pod(10)}},
            {"at": 120, "pods": {"bench-tenant/api": pod(0.5), "bench-tenant/worker": pod(2)}},
        ]}
        text = "\n".join(report._runtime_memory({"confirmations": [step]}))
        self.assertIn("| Charged | 2560.0 | 2.684 |", text)
        self.assertIn("sampled lower bounds", text)

    def test_confirmation_is_labeled_without_an_unmeasured_saturation_bracket(self) -> None:
        text = "\n".join(report._capacity({
            "mode": "confirmation-only", "mix_name": "sdk-heavy", "profile": {
                "capacity": {"mixes": {"sdk-heavy": {"wf-sdk": 100}}, "thresholds_source": "frozen"}
            },
        }))
        self.assertIn("## Fixed-rate confirmation", text)
        self.assertIn("no exploratory search or saturation bracket was measured", text)
        self.assertNotIn("Exploratory ramp bracket", text)

    def test_preflight_is_explicitly_not_a_capacity_claim(self) -> None:
        text = "\n".join(report._capacity({
            "mode": "confirmation-only", "mix_name": "sdk-heavy",
            "profile": {"capacity": {"mixes": {"sdk-heavy": {"wf-sdk": 100}}, "thresholds_source": "frozen"}},
            "preflight": {"overall": "fail", "submitted_classes": ["wf-sdk"], "required_classes": ["wf-sdk"]},
        }))
        self.assertIn("## Representative Correctness Preflight", text)
        self.assertIn("does not establish sustained capacity", text)
        self.assertIn("long confirmation was not run", text)

    def test_steps_render_with_gates_and_validity(self) -> None:
        gate = {"status": "pass", "value": 1.2, "limit": 3.0}
        step = {
            "rate": 2.0, "window_s": 240.0, "overall": "invalid",
            "verdict": {"throughput": {"value": 0.998}, "drift": {"value": 0.0004},
                        "latency": {"wf-sdk": {"queue_p95": gate, "e2e_p95": gate}}},
            "records": [{"outcome": "correct"}, {"outcome": "failed"}],
            "validity": {"invalid": ["external CPU above 1.5 cores in 5/48 intervals"], "warnings": []},
        }
        profile = {**RESULT["profile"], "capacity": {"mixes": {"sdk-heavy": {"wf-sdk": 100}}, "thresholds_source": "provisional"}}
        text = report.render({**RESULT, "kind": "capacity", "profile": profile, "mix_name": "sdk-heavy", "bracket": [None, None], "steps": [step]})
        self.assertIn("| 2.000 | 240 | invalid | 0.9980 | 0.0004 | 1.20 | 1 | external CPU above 1.5 cores in 5/48 intervals |", text)
        self.assertNotIn("## Execution times", text)
        self.assertIn("**Sustainable rate (confirmed): none confirmed**", text)
        self.assertIn("| fresh | bifrost-worker-one | 85.0 | 80.0 |", text)


if __name__ == "__main__":
    unittest.main()
