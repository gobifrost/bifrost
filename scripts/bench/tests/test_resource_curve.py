from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from contextlib import ExitStack, contextmanager, nullcontext
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import resource_curve  # noqa: E402


def worker_frame(count: int) -> dict:
    return {"worker_db": {"workers": [{"state": "ready", "pod_uid": f"w-{i}",
             "heartbeat_freshness": {"state": "fresh"}, "lifecycle": {"ended_at_epoch_s": None}} for i in range(count)]},
            "pods": {"status": "observed", "pods": [{"pod_uid": f"w-{i}", "phase": "Running",
             "ready": {"value": True}, "deletion_at": None} for i in range(count)]}}


def profile(workers: int, _base_profile: str = "hosting-8gib-fixed-workers-1") -> dict:
    return {"capacity": {"seed": 10, "disk": {"name": "d", "id": "1:1"}, "mixes": {"sdk-heavy": {"wf-sdk": 100}},
             "thresholds": {}, "warmup_s": 30, "window_min_s": 120, "drain_s": 60},
            "worker_policy": {"warm_workers": workers, "max_workers": workers, "worker_memory_mib": 1024,
                              "largest_worker_mib": 1024, "idle_seconds": 300}}


class MemoryRun:
    def __init__(self, _cluster, _kind, _name, _profile, _backend, out, _image):
        self.out, self.result, self.saves = out, {}, 0

    def save(self) -> None:
        self.saves += 1
        self.out.mkdir(parents=True, exist_ok=True)
        (self.out / "result.json").write_text(json.dumps(self.result), encoding="utf-8")


class ResourceCurveTest(unittest.TestCase):
    def fixture(self, root: Path) -> Path:
        path = root / "ensure.py"
        path.write_text("# fixture", encoding="utf-8")
        return path

    @contextmanager
    def harness(self, outcomes: list[str], *, during=None, preflight="pass"):
        cluster = Mock()
        cluster.api_pod_records.return_value = [{"pod": "api-a", "pod_uid": "a"}, {"pod": "api-b", "pod_uid": "b"}]
        prepared: list[dict] = []

        def prepare(_cluster, current, loaded, _backend, _image):
            prepared.append(copy.deepcopy(loaded))
            current.result["api_image_provenance"] = {"tag": resource_curve.FROZEN_IMAGE}
            return {"tenant": len(prepared)}

        def observe(_cluster, phase, checkpoint, action, on_result=None):
            workers = int(phase.split(":")[0].split("-")[-1])
            checkpoint(worker_frame(workers), phase)
            value = action()
            if on_result:
                on_result(value)
            checkpoint({"action_result": value}, f"{phase}_result")
            return value

        def step(_cluster, _ids, rate, _plan, seed, _disk, **_kwargs):
            return {"overall": outcomes.pop(0), "rate": rate, "seed": seed, "validity": {"invalid": []}}

        with ExitStack() as stack:
            stack.enter_context(patch.object(resource_curve.run, "Run", MemoryRun))
            stack.enter_context(patch.object(resource_curve, "profile_for", side_effect=profile))
            stack.enter_context(patch.object(resource_curve.run, "_prepare", side_effect=prepare))
            reset = stack.enter_context(patch.object(resource_curve.deploy, "reset"))
            stack.enter_context(patch.object(resource_curve, "_fixture", return_value={"status": "pass"}))
            stack.enter_context(patch.object(resource_curve.elasticity, "_assert_policy", side_effect=lambda _c, value: value))
            stack.enter_context(patch.object(resource_curve.elasticity, "wait_for_floor", return_value=worker_frame(1)))
            stack.enter_context(patch.object(resource_curve, "_runtime_proof", return_value={"frozen": True}))
            stack.enter_context(patch.object(resource_curve.preflight, "run", return_value={"overall": preflight}))
            trial = stack.enter_context(patch.object(resource_curve.search, "run_step", side_effect=step))
            stack.enter_context(patch.object(resource_curve.db_profile, "measure", side_effect=lambda *_a, **_k: nullcontext()))
            stack.enter_context(patch.object(resource_curve.elasticity, "_during", side_effect=during or observe))
            yield cluster, prepared, reset, trial

    def run_campaign(self, root: Path, outcomes: list[str], **kwargs):
        with self.harness(outcomes, **kwargs) as harness:
            resource_curve.run_campaign(resource_curve.FROZEN_IMAGE, root / "out", owner_root=Path("/owner"),
                                        ensure_fixture=self.fixture(root), cluster=harness[0])
            result = json.loads((root / "out" / "result.json").read_text(encoding="utf-8"))
        return result, harness

    def test_constant_per_worker_profile_and_request_total(self) -> None:
        observed = resource_curve.profile_for(4)
        self.assertEqual(observed["worker_policy"], {
            "warm_workers": 4, "max_workers": 4, "worker_memory_mib": 1024, "largest_worker_mib": 1024, "idle_seconds": 300,
        })
        self.assertEqual(observed["worker_cpu"], "650m")
        self.assertEqual(observed["quota"], {"cpu": "5200m", "memory": "8Gi", "storage": "50Gi", "pods": 20, "pvcs": 4})
        self.assertEqual(observed["components"], {
            "api": {"replicas": 2, "cpu": "750m", "memory": "512Mi"},
            "client": {"replicas": 1, "cpu": "50m", "memory": "64Mi"},
            "redis": {"cpu": "100m", "memory": "64Mi"},
            "postgres": {"cpu": "750m", "memory": "1536Mi", "storage": "20Gi"},
            "pooler": {"cpu": "100m", "memory": "128Mi"},
        })
        self.assertEqual(resource_curve.search.window_seconds(2, {"wf-sdk": 100}, 120, 480), 240)

    def test_base_profile_keeps_shared_components_for_a_bounded_contrast(self) -> None:
        base = {"components": {"api": {"replicas": 2, "cpu": "1250m", "memory": "512Mi"},
                               "redis": {"cpu": "100m", "memory": "64Mi"}},
                "quota": {"cpu": "9", "memory": "9Gi"}, "capacity": {}, "worker_policy": {}}
        with patch.object(resource_curve.run, "load_profile", return_value=base), \
             patch.object(resource_curve.deploy, "check_profile"):
            observed = resource_curve.profile_for(1, "api-contrast")
        self.assertEqual(observed["components"], base["components"])
        self.assertEqual(observed["quota"], {"cpu": "5200m", "memory": "8Gi"})
        self.assertEqual(observed["worker_cpu"], "650m")
        self.assertEqual(resource_curve.parse_counts("1,4"), (1, 4))
        with self.assertRaisesRegex(Exception, "increasing subset"):
            resource_curve.parse_counts("4,1")

    def test_one_prepare_per_count_and_distinct_cohorts(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            result, (_cluster, prepared, _reset, trial) = self.run_campaign(Path(raw), ["pass"] * 12)
        self.assertEqual(len(prepared), 3)
        self.assertEqual(trial.call_count, 12)
        self.assertEqual([item["workers"] for item in result["counts"]], [1, 2, 4])
        for count in result["counts"]:
            self.assertEqual(len({step["cohort_uuid"] for step in count["steps"]}), 4)
            self.assertEqual({step["seed"] for step in count["steps"]}, {10, 11, 12, 13})
            self.assertEqual(count["bracket"], [16.0, None])

    def test_stops_a_count_at_first_nonpass_but_continues_next_count(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            result, (_cluster, _prepared, _reset, trial) = self.run_campaign(Path(raw), ["pass", "fail", "invalid", "inconclusive"])
        self.assertEqual([len(count["steps"]) for count in result["counts"]], [2, 1, 1])
        self.assertEqual([call.args[2] for call in trial.call_args_list], [2.0, 4.0, 2.0, 2.0])
        self.assertEqual(result["counts"][0]["bracket"], [2.0, 4.0])

    def test_invalid_rate_is_not_an_upper_bracket(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            result, _ = self.run_campaign(Path(raw), ["invalid", "invalid", "invalid"])
        self.assertEqual(result["counts"][0]["bracket"], [None, None])

    def test_action_result_is_not_a_worker_frame(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            result, _ = self.run_campaign(Path(raw), ["pass"] * 12)
        self.assertEqual(result["counts"][0]["steps"][0]["fixed_worker_evidence"]["status"], "observed")

    def test_raw_result_survives_observer_error_and_cleanup_runs(self) -> None:
        def observer_error(_cluster, phase, checkpoint, action, on_result=None):
            workers = int(phase.split(":")[0].split("-")[-1])
            checkpoint(worker_frame(workers), phase)
            value = action()
            on_result(value)
            checkpoint({"action_result": value}, f"{phase}_result")
            raise RuntimeError("observer failed")

        with tempfile.TemporaryDirectory() as raw, self.harness(["pass"], during=observer_error) as harness:
            with self.assertRaisesRegex(RuntimeError, "observer failed"):
                resource_curve.run_campaign(resource_curve.FROZEN_IMAGE, Path(raw) / "out", owner_root=Path("/owner"),
                                            ensure_fixture=self.fixture(Path(raw)), cluster=harness[0])
            result = json.loads((Path(raw) / "out" / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(result["counts"][0]["steps"][0]["rate"], 2.0)
        self.assertTrue(result["counts"][0]["cleanup"]["verified"])
        self.assertTrue(result["cleanup"]["verified"])

    def test_raw_result_survives_sql_counter_teardown_error(self) -> None:
        class BrokenCounters:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                raise RuntimeError("counter teardown failed")

        with tempfile.TemporaryDirectory() as raw, self.harness(["pass"]) as harness:
            with patch.object(resource_curve.db_profile, "measure", return_value=BrokenCounters()):
                with self.assertRaisesRegex(RuntimeError, "counter teardown failed"):
                    resource_curve.run_campaign(resource_curve.FROZEN_IMAGE, Path(raw) / "out", owner_root=Path("/owner"),
                                                ensure_fixture=self.fixture(Path(raw)), cluster=harness[0])
            result = json.loads((Path(raw) / "out" / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(result["counts"][0]["steps"][0]["rate"], 2.0)
        self.assertTrue(result["counts"][0]["cleanup"]["verified"])

    def test_preflight_stop_records_count_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as raw, self.harness([], preflight="fail") as harness:
            resource_curve.run_campaign(resource_curve.FROZEN_IMAGE, Path(raw) / "out", owner_root=Path("/owner"),
                                        ensure_fixture=self.fixture(Path(raw)), cluster=harness[0])
            result = json.loads((Path(raw) / "out" / "result.json").read_text(encoding="utf-8"))
        self.assertEqual([count["status"] for count in result["counts"]], ["preflight-stopped"] * 3)
        self.assertTrue(all(count["cleanup"]["verified"] for count in result["counts"]))

    def test_real_report_writer_accepts_the_diagnostic_result(self) -> None:
        real_run = resource_curve.run.Run
        real_profile = resource_curve.profile_for
        environment = {"images": {}, "git_sha": "test", "kind_version": "test", "cnpg_version": "test",
                       "host": {"cpus": 8, "load_average": [0, 0, 0]}, "external_nodes": []}
        with tempfile.TemporaryDirectory() as raw, self.harness([], preflight="fail") as harness:
            with patch.object(resource_curve.run, "Run", real_run), \
                 patch.object(resource_curve.run, "environment", return_value=environment), \
                 patch.object(resource_curve, "profile_for", side_effect=real_profile):
                resource_curve.run_campaign(resource_curve.FROZEN_IMAGE, Path(raw) / "out", owner_root=Path("/owner"),
                                            ensure_fixture=self.fixture(Path(raw)), cluster=harness[0], counts=(1,))
            report = (Path(raw) / "out" / "report.md").read_text()
        self.assertIn("## Short Diagnostic", report)


if __name__ == "__main__":
    unittest.main()
