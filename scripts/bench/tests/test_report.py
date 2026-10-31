from __future__ import annotations

import sys
from copy import deepcopy
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
         "db_per_run": {"xact": 36.4, "rows_written": 12.0, "wal_kib": 21.5, "wal_generated_kib": 18.5},
         "worker_children": {"samples": 9, "peak_child_private_mib": 24.5, "peak_children": 3, "peak_template_private_mib": 168.0}},
        {"name": "agent-codemode", "pass": 1, "sequential": _OK, "burst": _BAD},
    ],
    "db_idle": {
        "seconds": 60.0,
        "rate": {"xact_per_s": 31.2, "rows_written_per_s": 4.0, "wal_kib_per_s": 2.5,
                 "wal_generated_kib_per_s": 1.5},
        "wal_stats_reset": "reset-a",
        "top_tables": [["scheduler_task_state", 120, 0], ["work_dispatches", 0, 900]],
    },
    "snapshots": [
        {"label": "fresh", "api": _API, "containers": [{"role": "worker", "pod": "bifrost-worker-one", "working_set_mib": 85.0, "rss_mib": 80.0}], "db_mib": 40.0, "wal_mib": 10.0, "wal_generated_bytes": 8 * 1024 * 1024,
         "wal_generated_mib": 8.0, "wal_stats_reset": "reset-a"},
        {"label": "idle", "api": _API, "containers": [], "db_mib": 52.5, "wal_mib": 34.0,
         "wal_generated_bytes": 18 * 1024 * 1024, "wal_generated_mib": 18.0, "wal_stats_reset": "reset-a"},
    ],
}


class RenderTest(unittest.TestCase):
    def test_recovery_report_exposes_correctness_and_missing_drills(self) -> None:
        source = {key: value for key, value in RESULT.items() if key not in ("classes", "snapshots", "api_pod_changes")}
        text = report.render({**source, "kind": "recovery-drill", "drill": "d6", "status": "failed",
                              "result": {"passed": False, "assessment": {"lost_path_observed": False}},
                              "d7_d9": {"d7": "unimplemented: stale attempt proof"}})
        self.assertIn("## Recovery Correctness", text)
        self.assertIn("does not certify sustained capacity", text)
        self.assertIn('"lost_path_observed": false', text)
        self.assertIn("D7: unimplemented", text)
        self.assertNotIn("## Execution times", text)

    def test_retention_report_cannot_appear_as_empty_execution_benchmark(self) -> None:
        text = report.render({**RESULT, "kind": "retention-rehearsal", "stage": "complete",
                              "before_backdate": {"result_storage_by_case": {"entropy": {
                                  "executions": 20, "result_column_bytes": 90, "payload_utf8_bytes": 100}}}})
        self.assertIn("## Storage Sizing and Retention Rehearsal", text)
        self.assertIn("| entropy | 20 | 90 | 100 |", text)
        self.assertIn("Pricing remains unmeasured", text)
        self.assertNotIn("## Execution times", text)

    def test_storage_frames_preserve_distinct_sizes_and_missing_measurements(self) -> None:
        frame = {"collected_at": 10, "postgres": {"database": {"bytes": 2**20, "wal_retained_bytes": 2 * 2**20},
                  "physical_volume": {"allocated_bytes": 8 * 2**20}}, "pgbouncer": {"status": "unmeasured"}}
        text = "\n".join(report._storage_frames([{"storage": {"before": frame}}]))
        self.assertIn("| step 1 before | 10.000 | 1.000 | 2.000 | 8.000 | unmeasured", text)
        self.assertIn("do not add them", text)
        self.assertIn("not cloud provisioned or billable", text)

    def test_rate_report_includes_late_latency_and_nested_platform_work(self) -> None:
        import execution_rates
        record = {"id": "a", "class": "wf-sdk", "scheduled_s": 10, "sent_s": 10,
                  "completed_s": 19, "outcome": "failed", "output_correct": True, "status": "Success", "deadline_met": False}
        text = "\n".join(report._execution_rates([{"execution_rates": execution_rates.summarize([record], (10, 20)),
               "platform_activity": {"executions": [{"type": "tool", "status": "Success", "count": 3}], "execution_log_rows": 5}}]))
        self.assertIn("9.0000 / 9.0000 / 9.0000", text)
        self.assertIn("| executions | tool | Success | 3 |", text)
        self.assertIn("Execution log rows written in the measured window: 5", text)

    def test_soak_report_exposes_signed_growth_and_is_not_new_capacity(self) -> None:
        text = report.render({**RESULT, "kind": "soak", "status": "observational", "stage": "complete",
                              "terminal_counts": {"durably_terminal": 20, "correct_successes": 19, "unfinished": 0},
                              "growth_per_1000_terminal": {"db_bytes_per_1000_terminal": -1000, "object_bytes_per_1000_terminal": None},
                              "host_memory_slopes": {"identity_limit": "UID lifetimes retained", "per_role": {
                                  "worker": {"slope_bytes_per_hour": 2**20, "sample_count": 90, "coverage": "observed"}}}})
        self.assertIn("## Observational Soak", text)
        self.assertIn("does not certify a new capacity rate", text)
        self.assertIn("| db_bytes_per_1000_terminal | -1000.0 |", text)
        self.assertIn("| object_bytes_per_1000_terminal | unmeasured |", text)
        self.assertIn("| worker | 90 | 1.000 | observed |", text)
        self.assertNotIn("## Execution times", text)

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
        self.assertIn("| 36 | 21.5 | 18.5 |", text)
        self.assertIn("31.2 transactions/s", text)
        self.assertIn("1.5 KiB generated WAL/s", text)
        self.assertIn("`work_dispatches`", text)
        self.assertIn("Database grew 12.5 MiB; WAL LSN advance 24.0 MiB", text)
        self.assertIn("Generated WAL counter changed 10.0 MiB", text)
        self.assertIn("Generated WAL stats-reset marker: `reset-a`", text)

    def test_generated_snapshot_total_refuses_unknown_reset_or_regressing_bytes(self) -> None:
        for mutate, reason in (
            (lambda result: result["snapshots"][1].update({"wal_stats_reset": None}), "missing or invalid"),
            (lambda result: result["snapshots"][1].update({"wal_generated_bytes": 1}), "regressed"),
        ):
            result = deepcopy(RESULT)
            mutate(result)
            text = report.render(result)
            self.assertNotIn("Generated WAL counter changed", text)
            self.assertIn("Generated WAL counter unavailable across snapshots:", text)
            self.assertIn(reason, text)

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

    def test_import_diagnostic_is_explicitly_not_a_browser_or_customer_latency_claim(self) -> None:
        result = {**RESULT, "imports_diagnostic": {
            "overall": "invalid",
            "groups": [{"class": "wf-import-graph", "pass": 1, "overall": "invalid",
                        "missing_execution_ids": ["one"], "duplicate_execution_ids": [], "invalid_events": [],
                        "durable_timing": {
                            "durable_started_to_completed_ms": {"count": 1, "p50_ms": 12.5, "p95_ms": 12.5},
                            "engine_duration_ms": {"count": 0, "p50_ms": None, "p95_ms": None},
                        }}],
        }}
        text = report.render(result)
        self.assertIn("## Partial Imports Diagnostic", text)
        self.assertIn("not a Halo or customer-latency claim", text)
        self.assertIn("not polling, waiting, browser, or customer-latency observations", text)
        self.assertIn("terminal-output read occurs after Success", text)
        self.assertIn("partial and overlapping", text)
        self.assertIn("12.50 / 12.50", text)
        self.assertIn("unavailable", text)
        self.assertIn("`wf-import-graph`", text)

    def test_import_report_uses_verified_partial_timing_without_generic_false_failures(self) -> None:
        unavailable = {**_BAD, "succeeded": 0, "failed": 2,
                       "failures": [{"id": "one", "status": "Success", "error": None}]}
        result = {**RESULT, "classes": [{**RESULT["classes"][0], "sequential": unavailable}],
                  "imports_diagnostic": {"overall": "pass", "groups": [{
                      "class": "wf-import-graph", "pass": 1, "overall": "pass",
                      "execution_validation": {"overall": "pass"},
                      "durable_timing": {"durable_started_to_completed_ms": {
                          "count": 2, "p50_ms": 260, "p95_ms": 300}},
                  }]}}
        text = report.render(result)
        self.assertIn("260.00 / 300.00", text)
        self.assertIn("Trace result: **pass**", text)
        self.assertIn("## Import Diagnostic Counter Windows", text)
        self.assertIn("| 36 | 21.5 |", text)
        self.assertNotIn("## Execution times", text)
        self.assertNotIn("## Failures", text)
        self.assertNotIn("Success —", text)

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
