from __future__ import annotations

import json
import sys
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import hosting_sizing  # noqa: E402


def profile(*, workers: int = 2) -> dict:
    return {
        "worker_policy": {
            "warm_workers": workers, "max_workers": workers,
            "worker_memory_mib": 512, "largest_worker_mib": 512, "idle_seconds": 300,
        },
        "capacity": {
            "mixes": {"sdk-heavy": {"wf-sdk": 1.0}, "mixed": {"wf-sdk": 1.0}},
            "disk": {"device": "259:0"}, "seed": 17,
        },
    }


def worker_sample(workers: int = 2) -> dict:
    return {
        "worker_db": {"workers": [{
            "state": "ready", "pod_uid": f"worker-{index}", "heartbeat_freshness": {"state": "fresh"},
            "lifecycle": {"ended_at_epoch_s": None},
        } for index in range(workers)]},
        "pods": {"status": "observed", "pods": [{
            "pod_uid": f"worker-{index}", "phase": "Running", "ready": {"value": True}, "deletion_at": None,
        } for index in range(workers)]},
    }


class MemoryRun:
    instances: list["MemoryRun"] = []

    def __init__(self, _cluster, _kind, _profile_name, _profile, _backend, out, _api_image) -> None:
        self.out = out
        self.result: dict = {"api_image_provenance": {"api_pods": []}}
        self.instances.append(self)

    def save(self) -> None:
        self.out.mkdir(parents=True, exist_ok=True)
        (self.out / "result.json").write_text(json.dumps(self.result), encoding="utf-8")


class HostingSizingTest(unittest.TestCase):
    def setUp(self) -> None:
        MemoryRun.instances.clear()

    def _harness(self, loaded: dict, outcomes: list[str], *, confirmation: str = "pass"):
        cluster = Mock()
        cluster.api_pod_records.return_value = [
            {"pod": "bifrost-api-a", "pod_uid": "uid-a"},
            {"pod": "bifrost-api-b", "pod_uid": "uid-b"},
        ]
        cluster.exec_python.return_value = {"status": "pass", "table_rows": 1, "documents": 0}
        prepared: list[str] = []

        def prepare(_cluster, _current, _profile, _backend, _image):
            prepared.append("prepared")
            return {"workflows": {"wf-sdk": "sdk"}}

        def step(_cluster, _ids, rate, _plan, _seed, _disk, **_kwargs):
            return {"overall": outcomes.pop(0), "rate": rate, "validity": {"invalid": []}}

        def during(_cluster, phase, checkpoint, action, on_result=None):
            checkpoint(worker_sample(), phase)
            value = action()
            if on_result:
                on_result(value)
            return value

        return cluster, prepared, (
            patch.object(hosting_sizing.run, "load_profile", return_value=loaded),
            patch.object(hosting_sizing.deploy, "check_profile"),
            patch.object(hosting_sizing.run, "Run", MemoryRun),
            patch.object(hosting_sizing.run, "_prepare", side_effect=prepare),
            patch.object(hosting_sizing.deploy, "reset"),
            patch.object(hosting_sizing.elasticity, "_assert_policy", side_effect=lambda _cluster, controls: controls),
            patch.object(hosting_sizing.elasticity, "wait_for_floor", return_value={"floor": "ready"}),
            patch.object(hosting_sizing.elasticity, "_during", side_effect=during),
            patch.object(hosting_sizing.preflight, "run", return_value={"overall": "pass"}),
            patch.object(hosting_sizing.search, "run_step", side_effect=step),
            patch.object(hosting_sizing.search, "confirm", return_value={"overall": confirmation, "validity": {"invalid": []}}),
            patch.object(hosting_sizing.db_profile, "measure", side_effect=lambda *_args, **_kwargs: nullcontext()),
        )

    @staticmethod
    def _fixture(directory: Path) -> Path:
        path = directory / "ensure.py"
        path.write_text("fixture", encoding="utf-8")
        return path

    def test_sdk_stops_after_first_failure_then_runs_one_midpoint_and_one_confirmation(self) -> None:
        loaded = profile()
        cluster, prepared, patches = self._harness(loaded, ["pass", "fail", "pass"])
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            with patches[0], patches[1] as check, patches[2], patches[3], patches[4] as reset, patches[5], patches[6], patches[7], patches[8], patches[9] as trials, patches[10] as confirm, patches[11]:
                hosting_sizing.run_campaign("hosting-4gb-fixed-workers-2", "candidate:local", Path(raw) / "out",
                                            cluster=cluster, rates=(2, 4, 8), confirm=True,
                                            ensure_fixture=self._fixture(directory))
            result = json.loads((Path(raw) / "out" / "result.json").read_text(encoding="utf-8"))

        self.assertEqual([call.args[2] for call in trials.call_args_list], [2, 4, 3])
        confirm.assert_called_once()
        self.assertEqual(confirm.call_args.args[2], 3)
        self.assertIn("on_segment", confirm.call_args.kwargs)
        self.assertIn("checkpoint", confirm.call_args.kwargs)
        self.assertEqual(len(prepared), 5)  # preflight + three rate arms + one confirmation arm
        self.assertEqual(reset.call_count, 6)  # every fresh seed plus final teardown
        check.assert_called_once_with(loaded)
        self.assertEqual(result["sustainable_rate"], 3)
        self.assertTrue(result["cleanup"]["verified"])

    def test_invalid_rate_stops_without_midpoint_or_confirmation(self) -> None:
        loaded = profile()
        cluster, _prepared, patches = self._harness(loaded, ["invalid"])
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8], patches[9] as trials, patches[10] as confirm, patches[11]:
                hosting_sizing.run_campaign("hosting-4gb-fixed-workers-2", "candidate:local", Path(raw) / "out",
                                            cluster=cluster, rates=(2, 4), confirm=True,
                                            ensure_fixture=self._fixture(directory))
            result = json.loads((Path(raw) / "out" / "result.json").read_text(encoding="utf-8"))

        self.assertEqual(trials.call_count, 1)
        confirm.assert_not_called()
        self.assertEqual(result["stopped"], "invalid")

    def test_confirmation_retains_raw_segment_and_partial_checkpoint_before_assessment(self) -> None:
        loaded = profile()
        cluster, _prepared, patches = self._harness(loaded, ["pass", "fail", "pass"])

        def confirm(*_args, **kwargs):
            raw = {"overall": "pass", "rate": 3, "sent": {"requests": ["request"]}}
            kwargs["checkpoint"]({"sent": raw["sent"]})
            kwargs["on_segment"](raw)
            raw["reliability_evidence"] = {"wf-sdk": ("pass", 100)}
            return {"overall": "pass", "validity": {"invalid": []}}

        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], \
                 patches[8], patches[9], patch.object(hosting_sizing.search, "confirm", side_effect=confirm), patches[11]:
                hosting_sizing.run_campaign("hosting-4gb-fixed-workers-2", "candidate:local", directory / "out",
                                            cluster=cluster, rates=(2, 4), confirm=True,
                                            ensure_fixture=self._fixture(directory))
            result = json.loads((directory / "out" / "result.json").read_text(encoding="utf-8"))
            timeline = (directory / "out" / "timeline.jsonl").read_text(encoding="utf-8")

        self.assertEqual(result["confirmation_segments"], [
            {"overall": "pass", "rate": 3, "sent": {"requests": ["request"]}},
        ])
        self.assertIn('"phase": "strict-confirmation:raw"', timeline)
        self.assertIn('"phase": "strict-confirmation:segment"', timeline)

    def test_mixed_diagnostic_requires_one_rate_and_skips_strict_confirmation(self) -> None:
        loaded = profile()
        cluster, _prepared, patches = self._harness(loaded, ["pass"])
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8], patches[9] as trials, patches[10] as confirm, patches[11]:
                hosting_sizing.run_campaign("hosting-4gb-fixed-workers-2", "candidate:local", Path(raw) / "out",
                                            cluster=cluster, mix_name="mixed", rates=(2,), confirm=True,
                                            ensure_fixture=self._fixture(directory))
            result = json.loads((Path(raw) / "out" / "result.json").read_text(encoding="utf-8"))

        self.assertEqual(trials.call_count, 1)
        confirm.assert_not_called()
        self.assertEqual(result["confirmation"], "skipped for mixed diagnostic")

    def test_worker_evidence_uses_observation_frames_not_action_summary(self) -> None:
        loaded = profile()
        cluster, _prepared, patches = self._harness(loaded, ["pass"])

        def during(_cluster, phase, checkpoint, action, on_result=None):
            checkpoint(worker_sample(), phase)
            value = action()
            if on_result:
                on_result(value)
            checkpoint({"action_result": value}, f"{phase}_result")
            return value

        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], \
                 patch.object(hosting_sizing.elasticity, "_during", side_effect=during), patches[8], patches[9], patches[10], patches[11]:
                hosting_sizing.run_campaign("hosting-4gb-fixed-workers-2", "candidate:local", directory / "out",
                                            cluster=cluster, rates=(2,), ensure_fixture=self._fixture(directory))
            result = json.loads((directory / "out" / "result.json").read_text(encoding="utf-8"))

        self.assertEqual(result["steps"][0]["overall"], "pass")
        self.assertEqual(result["steps"][0]["fixed_worker_evidence"], {
            "status": "observed", "required_ready": 2, "samples": 1,
            "scope": "five-second sequential observations during warmup, measure, and drain",
        })

    def test_raw_step_is_saved_before_a_worker_observer_failure_invalidates_the_campaign(self) -> None:
        loaded = profile()
        cluster, _prepared, patches = self._harness(loaded, ["pass"])

        def observer_failure(_cluster, phase, checkpoint, action, on_result=None):
            checkpoint(worker_sample(), phase)
            value = action()
            if on_result:
                on_result(value)
            checkpoint({"action_result": value}, f"{phase}_result")
            raise RuntimeError("observer failed after raw step")

        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], \
                 patch.object(hosting_sizing.elasticity, "_during", side_effect=observer_failure), patches[8], patches[9], patches[10], patches[11]:
                with self.assertRaisesRegex(RuntimeError, "observer failed"):
                    hosting_sizing.run_campaign("hosting-4gb-fixed-workers-2", "candidate:local", directory / "out",
                                                cluster=cluster, rates=(2,), ensure_fixture=self._fixture(directory))
            result = json.loads((directory / "out" / "result.json").read_text(encoding="utf-8"))

        self.assertEqual(result["steps"][0]["rate"], 2)
        self.assertEqual(result["steps"][0]["fixed_worker_evidence"]["status"], "observed")
        self.assertEqual(result["status"], "invalid")

    def test_fixed_profile_must_use_an_exact_positive_worker_count_before_cluster_access(self) -> None:
        loaded = profile()
        loaded["worker_policy"]["warm_workers"] = 0
        with tempfile.TemporaryDirectory() as raw, patch.object(hosting_sizing.run, "load_profile", return_value=loaded), \
             patch.object(hosting_sizing, "Cluster") as cluster:
            with self.assertRaisesRegex(ValueError, "fixed worker count"):
                hosting_sizing.run_campaign("hosting-4gb-fixed-workers-2", "candidate:local", Path(raw) / "out",
                                            ensure_fixture=self._fixture(Path(raw)))
        cluster.assert_not_called()


if __name__ == "__main__":
    unittest.main()
