from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import soak  # noqa: E402


PROFILE = {"capacity": {"warmup_s": 2, "drain_s": 3, "disk": {}}, "idle_s": 0, "idle_db_s": 0}


def passing_aggregate(rate: float = 10.0) -> dict:
    return {
        "overall": "pass",
        "campaign_manifest_sha256": "manifest-digest",
        "campaign_manifest": {
            "profile_name": "m-capacity", "resolved_profile_sha256": "profile-digest", "rate_per_s": rate,
            "mix_name": "mixed", "mix": {"wf-sdk": 1},
            "api_image": {"tag": "bifrost-api:test", "id": "sha256:test"}, "seeds": [1],
        },
    }


def counters(db_bytes: int) -> dict:
    return {"db_bytes": db_bytes, "wal_generated_bytes": 20}


class FakeRun:
    """Small durable artifact fake; production orchestration remains under test."""

    instances: list["FakeRun"] = []

    def __init__(self, *args: object) -> None:
        self.out = args[5]
        self.result: dict = {"snapshots": []}
        self.expected_api_uid: str | None = None
        self.events: list[str] = []
        self._save_lock = threading.RLock()
        FakeRun.instances.append(self)

    def initialize(self) -> None:
        self.out.mkdir()
        self.save()

    def save(self) -> None:
        (self.out / "result.json").write_text(json.dumps(self.result), encoding="utf-8")

    def raw(self, kind: str, value: dict, *, save: bool) -> None:
        self.events.append(f"raw:{kind}:{value.get('phase', '')}")
        with (self.out / "raw-checkpoints.jsonl").open("a", encoding="utf-8") as file:
            file.write(json.dumps({"kind": kind, "value": value}) + "\n")
        if save:
            self.save()

    def snapshot(self, label: str) -> None:
        self.events.append(f"snapshot:{label}")


class FakeCluster:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self._counters = [counters(100), counters(90)]

    def verify(self) -> None:
        self.calls.append("verify")

    def api_pod(self) -> tuple[str, str]:
        return "bifrost-api-test", "api-uid"

    def db_counters(self) -> dict:
        self.calls.append("db_counters")
        return self._counters.pop(0)


class FakeSampler:
    instances: list["FakeSampler"] = []
    fail_on_start = False

    def __init__(self, cluster: FakeCluster, api_pod: str, current: FakeRun) -> None:
        self.samples = [
            {"object_bytes": 20, "object_count": 2, "connection_states": []},
            {"object_bytes": 26, "object_count": 3, "connection_states": []},
        ]
        self.started = False
        self.error: BaseException | None = None
        FakeSampler.instances.append(self)

    def start(self) -> None:
        self.started = True
        if self.fail_on_start:
            self.error = RuntimeError("sampler failed")

    def stop(self) -> None:
        return None

    def sample(self) -> None:
        return None


class SourceTest(unittest.TestCase):
    def test_rejects_nonpassing_campaign_before_cluster_or_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(soak.campaign, "aggregate", return_value={"overall": "fail"}), \
             patch.object(soak, "Cluster") as cluster:
            output = Path(temporary) / "soak"
            with self.assertRaisesRegex(ValueError, "fully passing"):
                soak.run(Path(temporary) / "manifest.json", output)

        cluster.assert_not_called()
        self.assertFalse(output.exists())

    def test_rejects_profile_hash_change_before_cluster_or_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(soak.campaign, "aggregate", return_value=passing_aggregate()), \
             patch.object(soak.bench_run, "load_profile", return_value=PROFILE), \
             patch.object(soak.campaign, "_sha256_value", return_value="changed-profile"), \
             patch.object(soak, "Cluster") as cluster:
            output = Path(temporary) / "soak"
            with self.assertRaisesRegex(ValueError, "no longer matches"):
                soak.run(Path(temporary) / "manifest.json", output)

        cluster.assert_not_called()
        self.assertFalse(output.exists())

    def test_rejects_image_runtime_change_before_reset_or_output(self) -> None:
        cluster = Mock()
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(soak.campaign, "aggregate", return_value=passing_aggregate()), \
             patch.object(soak.bench_run, "load_profile", return_value=PROFILE), \
             patch.object(soak.campaign, "_sha256_value", return_value="profile-digest"), \
             patch.object(soak, "Cluster", return_value=cluster), \
             patch.object(soak.campaign, "_verify_frozen_inputs", side_effect=ValueError("image changed")), \
             patch.object(soak.deploy, "reset") as reset:
            output = Path(temporary) / "soak"
            with self.assertRaisesRegex(ValueError, "image changed"):
                soak.run(Path(temporary) / "manifest.json", output)

        cluster.verify.assert_called_once_with()
        reset.assert_not_called()
        self.assertFalse(output.exists())

    def test_rejects_existing_output_before_cluster_mutation(self) -> None:
        aggregate = {
            "overall": "pass",
            "campaign_manifest_sha256": "manifest-digest",
            "campaign_manifest": {
                "profile_name": "m-capacity", "resolved_profile_sha256": "profile-digest", "rate_per_s": 10.0,
                "mix_name": "mixed", "mix": {"wf-sdk": 1},
                "api_image": {"tag": "bifrost-api:test", "id": "sha256:test"}, "seeds": [1],
            },
        }
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(soak.campaign, "aggregate", return_value=aggregate), \
             patch.object(soak.bench_run, "load_profile", return_value={"capacity": {"warmup_s": 1, "drain_s": 1, "disk": {}}}), \
             patch.object(soak.campaign, "_sha256_value", return_value="profile-digest"), \
             patch.object(soak, "Cluster") as cluster:
            output = Path(temporary) / "soak"
            output.mkdir()
            with self.assertRaisesRegex(ValueError, "already contains evidence"):
                soak.run(Path(temporary) / "manifest.json", output)

        cluster.assert_not_called()


class RateTest(unittest.TestCase):
    def test_uses_seventy_percent_of_the_confirmed_rate(self) -> None:
        self.assertEqual(soak.soak_rate(10.0), 7.0)

    def test_rejects_invalid_confirmation_rate(self) -> None:
        with self.assertRaises(ValueError):
            soak.soak_rate(0.0)

    def test_cli_rejects_rate_and_profile_overrides(self) -> None:
        for forbidden in ("--rate", "--profile"):
            with self.subTest(forbidden=forbidden), \
                 patch.object(sys, "argv", ["soak.py", "--campaign-manifest", "evidence", "--out", "out", forbidden, "x"]), \
                 patch.object(soak, "run") as run:
                with self.assertRaises(SystemExit):
                    soak.main()
                run.assert_not_called()


class EvidenceSafetyTest(unittest.TestCase):
    def test_preserves_database_reclamation_as_a_signed_delta(self) -> None:
        self.assertEqual(soak._database_size_delta({"db_bytes": 200}, {"db_bytes": 150}), -50)

    def test_rejects_invalid_database_counter(self) -> None:
        with self.assertRaises(ValueError):
            soak._database_size_delta({"db_bytes": 200}, {"db_bytes": -1})

    def test_surfaces_sampler_error_without_waiting_for_soak_completion(self) -> None:
        error = RuntimeError("object listing failed")
        with self.assertRaisesRegex(RuntimeError, "object listing failed"):
            soak._surface_sampler_error(SimpleNamespace(error=error))


class RunOrchestrationTest(unittest.TestCase):
    def setUp(self) -> None:
        FakeRun.instances.clear()
        FakeSampler.instances.clear()
        FakeSampler.fail_on_start = False

    def test_persists_passed_campaign_run_evidence_with_terminal_failures_in_denominator(self) -> None:
        cluster = FakeCluster()
        step = {
            "overall": "pass", "verdict": {"overall": "pass"}, "validity": {"invalid": []}, "host": [], "started": 1_000.0,
            "records": [{"id": "failed", "outcome": "failed", "http_status": 500}],
            "outcomes": {"outcomes": {
                "failed": {"kind": "workflow", "status": "Failed", "completed": 1_001.0},
            }, "duplicates": {}, "missing": []},
            "backlog": [],
        }
        reset = Mock(side_effect=lambda _cluster: FakeRun.instances[-1].events.append("reset"))

        def run_step(*args: object, **kwargs: object) -> dict:
            kwargs["on_event"]({"event": "started"})
            kwargs["checkpoint"]({"phase": "sent", "sent": {"requests": []}})
            kwargs["checkpoint"]({"phase": "outcomes", "outcomes": step["outcomes"]})
            return step

        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(soak.campaign, "aggregate", return_value=passing_aggregate()), \
             patch.object(soak.bench_run, "load_profile", return_value=PROFILE), \
             patch.object(soak.campaign, "_sha256_value", return_value="profile-digest"), \
             patch.object(soak.campaign, "_verify_frozen_inputs", return_value=(PROFILE, {**PROFILE["capacity"], "mix": {"wf-sdk": 1}})), \
             patch.object(soak, "Cluster", return_value=cluster), \
             patch.object(soak, "SoakRun", FakeRun), \
             patch.object(soak, "MinuteSampler", FakeSampler), \
             patch.object(soak.deploy, "reset", reset), \
             patch.object(soak.bench_run, "_prepare", return_value={"workflow": "id"}), \
             patch.object(soak.bench_run, "capture_idle_db"), \
             patch.object(soak.bench_run, "capture_db_window", return_value={"wal_generated_bytes": 8}), \
             patch.object(soak.search, "run_step", side_effect=run_step) as search_run:
            output = Path(temporary) / "soak"
            self.assertEqual(soak.run(Path(temporary) / "manifest.json", output), output)

        rate = search_run.call_args.args[2]
        self.assertEqual(rate, 7.0)
        self.assertEqual(search_run.call_args.kwargs["window_s"], soak.SOAK_WINDOW_S)
        result = FakeRun.instances[0].result
        self.assertEqual(result["terminal_counts"], {"durably_terminal": 1, "correct_successes": 0, "unfinished": 0})
        self.assertEqual(result["database_growth_counters"], {"before": counters(100), "after": counters(90)})
        self.assertEqual(result["growth_per_1000_terminal"]["db_bytes_per_1000_terminal"], -10_000.0)
        events = FakeRun.instances[0].events
        final_reset = events.index("reset", 1)
        self.assertLess(events.index("raw:checkpoint:sent"), final_reset)
        self.assertLess(events.index("raw:checkpoint:outcomes"), final_reset)

    def test_cleanup_runs_after_driver_failure(self) -> None:
        self._assert_cleanup_after_failure("driver")

    def test_cleanup_runs_after_sampler_failure(self) -> None:
        self._assert_cleanup_after_failure("sampler")

    def _assert_cleanup_after_failure(self, failure: str) -> None:
        cluster = FakeCluster()
        reset = Mock(side_effect=lambda _cluster: FakeRun.instances[-1].events.append("reset"))

        def run_step(*args: object, **kwargs: object) -> dict:
            if failure == "driver":
                raise RuntimeError("driver failed")
            kwargs["on_event"]({"event": "started"})
            kwargs["checkpoint"]({"phase": "sent", "sent": {"requests": []}})
            raise AssertionError("sampler error should abort the driver callback")

        FakeSampler.fail_on_start = failure == "sampler"
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(soak.campaign, "aggregate", return_value=passing_aggregate()), \
             patch.object(soak.bench_run, "load_profile", return_value=PROFILE), \
             patch.object(soak.campaign, "_sha256_value", return_value="profile-digest"), \
             patch.object(soak.campaign, "_verify_frozen_inputs", return_value=(PROFILE, {**PROFILE["capacity"], "mix": {"wf-sdk": 1}})), \
             patch.object(soak, "Cluster", return_value=cluster), \
             patch.object(soak, "SoakRun", FakeRun), \
             patch.object(soak, "MinuteSampler", FakeSampler), \
             patch.object(soak.deploy, "reset", reset), \
             patch.object(soak.bench_run, "_prepare", return_value={"workflow": "id"}), \
             patch.object(soak.bench_run, "capture_idle_db"), \
             patch.object(soak.search, "run_step", side_effect=run_step):
            with self.assertRaisesRegex(RuntimeError, "failed"):
                soak.run(Path(temporary) / "manifest.json", Path(temporary) / "soak")

        self.assertEqual(reset.call_count, 2)
        self.assertEqual(FakeRun.instances[0].result["status"], "invalid")


class MinuteSamplerContractTest(unittest.TestCase):
    def test_uses_driver_sql_and_api_pod_object_listing_contract(self) -> None:
        cluster = Mock()
        cluster.driver_sql.return_value = [{"state": "idle", "connections": 2}]
        cluster.exec_python.return_value = {"object_count": 3, "object_bytes": 40}
        current = SimpleNamespace(_save_lock=threading.RLock(), result={}, raw=Mock())

        sample = soak.MinuteSampler(cluster, "bifrost-api-test", current).sample()

        self.assertEqual(sample["connection_states"], [{"state": "idle", "connections": 2}])
        cluster.driver_sql.assert_called_once_with(soak.CONNECTION_STATES_SQL)
        cluster.exec_python.assert_called_once_with(
            "bifrost-api-test", soak.METRICS, ["objects", "{}"], timeout_s=60, namespace=soak.guard.TENANT_NS,
        )
        current.raw.assert_called_once_with("minute_metric", sample, save=True)
