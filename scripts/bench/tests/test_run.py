from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from copy import deepcopy
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import run  # noqa: E402


def _step(rate: float, overall: str) -> dict:
    gate = {"status": "pass", "value": 0.2, "limit": 1.0}
    return {
        "rate": rate,
        "started": 100.0,
        "window": (0.0, 300.0),
        "window_s": 300.0,
        "overall": overall,
        "confirm": True,
        "verdict": {"throughput": {"value": 1.0}, "drift": {"value": 0.0},
                    "latency": {"wf-sdk": {"e2e_p95": gate}}},
        "validity": {"invalid": [], "warnings": []},
        "records": [{"outcome": "correct"}],
    }


class FakeCluster:
    def __init__(self) -> None:
        self.identity = SimpleNamespace(worktree_hash="test")

    def verify(self) -> None:
        pass

    def external_nodes(self) -> list[str]:
        return []


class RunHarnessTest(unittest.TestCase):
    selected = "bifrost-api:selected"
    default_images = {"api": "bifrost-api:default", "client": "bifrost-client:default"}

    def test_constrained_smoke_preserves_full_envelope_and_declares_reduced_catalog(self) -> None:
        full, smoke = run.load_profile("4gb-capacity"), run.load_profile("4gb-capacity-smoke")
        for field in ("quota", "components", "worker_cpu", "worker_policy", "capacity"):
            self.assertEqual(full[field], smoke[field])
        self.assertLess(sum(smoke["seed"].values()), sum(full["seed"].values()))

    def test_deployed_images_record_actual_infra_identity_without_environment(self) -> None:
        cluster = Mock()
        cluster.kubectl.side_effect = [json.dumps({"items": []}), json.dumps({"items": [{
            "metadata": {"name": "bench-driver", "uid": "uid"},
            "spec": {"containers": [{"name": "driver", "image": "older-api", "env": [{"secret": "never-report"}]}]},
            "status": {"containerStatuses": [{"name": "driver", "imageID": "sha256:actual"}]},
        }]})]
        self.assertEqual(run.deployed_images(cluster), [{"namespace": "bench-infra", "pod": "bench-driver", "uid": "uid",
                         "container": "driver", "image": "older-api", "image_id": "sha256:actual"}])

    def test_failed_confirmation_saves_strict_json_and_regenerates_unbounded_latency(self) -> None:
        cluster = FakeCluster()
        step = _step(3.90625, "fail")
        step["verdict"]["latency"]["wf-sdk"]["e2e_p95"] = {
            "status": "fail", "value": float("inf"), "limit": 1.5,
            "ci": {"value": float("inf"), "low": float("inf"), "high": float("inf")},
        }
        step["records"] = [{"outcome": "failed", "error": "SDK insert returned HTTP 500"}]

        def reject_nonstandard_constant(value: str):
            raise ValueError(f"nonstandard JSON constant: {value}")

        with tempfile.TemporaryDirectory() as temporary, self._common_patches(cluster):
            current = run.Run(cluster, "capacity", "m-capacity", run.load_profile("m-capacity"),
                              "fake", Path(temporary), self.selected)
            current.result.update({"mode": "confirmation-only", "mix_name": "sdk-heavy", "rate": 3.90625,
                                   "confirmations": [step], "steps": [], "sustainable_rate": None,
                                   "bracket": None, "stopped": None})
            current.save()
            stored = json.loads((current.out / "result.json").read_text(), parse_constant=reject_nonstandard_constant)
            original_report = (current.out / "report.md").read_text()

        self.assertEqual(stored["schema_version"], 3)
        self.assertEqual(stored["confirmations"][0]["overall"], "fail")
        self.assertEqual(stored["confirmations"][0]["records"], step["records"])
        self.assertIsNone(stored["confirmations"][0]["verdict"]["latency"]["wf-sdk"]["e2e_p95"]["value"])
        self.assertEqual(current.result["confirmations"][0]["verdict"]["latency"]["wf-sdk"]["e2e_p95"]["value"], float("inf"))
        self.assertEqual(run.report.render(stored), original_report)
        self.assertIn("| inf |", original_report)
        self.assertIn("| fail |", original_report)

    @contextmanager
    def _common_patches(self, cluster: FakeCluster):
        ids = {**{tag: f"sha256:{name}" for name, tag in self.default_images.items()}, self.selected: "sha256:selected"}
        with ExitStack() as patches:
            patches.enter_context(patch.object(run, "Cluster", return_value=cluster))
            patches.enter_context(patch.object(run, "_git_sha", return_value="abc1234"))
            patches.enter_context(patch.object(run.deploy, "images", return_value=self.default_images))
            patches.enter_context(patch.object(run, "_image_id", side_effect=lambda tag: ids.get(tag, "")))
            yield

    def test_capacity_explicit_image_is_deployed_and_recorded_by_id(self) -> None:
        cluster = FakeCluster()
        prepared: list[str] = []

        def prepare(_cluster, _current, _profile, _backend, image):
            prepared.append(image)
            return {"connections": {}}

        def search(_cluster, ids, _plan, _disk, on_step):
            self.assertEqual(ids, {"connections": {}})
            step = _step(3.90625, "pass")
            on_step(step)
            return {"steps": [step], "bracket": [3.90625, 4.0], "stopped": None}

        with tempfile.TemporaryDirectory() as temporary, self._common_patches(cluster), \
             patch.object(run, "_prepare", side_effect=prepare), \
             patch.object(run.deploy, "reset"), \
             patch.object(run.search, "search", side_effect=search), \
             patch.object(run.search, "confirm", return_value=_step(3.90625, "pass")):
            out = Path(temporary) / "capacity"
            run.run_capacity("m-capacity", "sdk-heavy", self.selected, out)

            result = json.loads((out / "result.json").read_text())
            rendered = (out / "report.md").read_text()

        self.assertEqual(prepared, [self.selected, self.selected])
        self.assertEqual(result["images"]["api"], {"tag": self.selected, "id": "sha256:selected"})
        self.assertEqual(result["sustainable_rate"], 3.90625)
        self.assertIn("`bifrost-api:selected` (sha256:selected)", rendered)

    def test_run_cli_passes_explicit_image_to_class_harness(self) -> None:
        with patch.object(sys, "argv", ["run.py", "run", "--profile", "m-import", "--api-image", self.selected]), \
             patch.object(run, "run_classes") as classes:
            run.main()
        classes.assert_called_once_with("m-import", "fake", None, self.selected)

    def test_import_fixture_is_rejected_for_non_import_profile_before_cluster_creation(self) -> None:
        fixture = run.import_diagnostic.fixture_manifest()
        with tempfile.TemporaryDirectory() as temporary:
            manifest = Path(temporary) / "private-import-fixture.json"
            manifest.write_text(json.dumps(fixture), encoding="utf-8")
            with patch.object(run, "Cluster") as cluster:
                with self.assertRaisesRegex(SystemExit, "only supported for imports_diagnostic"):
                    run.run_classes("m", "fake", Path(temporary) / "classes", self.selected, manifest)
            cluster.assert_not_called()

    def test_invalid_import_fixture_is_rejected_before_cluster_creation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest = Path(temporary) / "private-import-fixture.json"
            manifest.write_text("{not json", encoding="utf-8")
            with patch.object(run, "Cluster") as cluster:
                with self.assertRaisesRegex(SystemExit, "invalid import fixture manifest"):
                    run.run_classes("m-import", "fake", Path(temporary) / "classes", self.selected, manifest)
            cluster.assert_not_called()

    def test_run_cli_passes_private_import_fixture_only_when_supplied(self) -> None:
        manifest = Path("/private/import-fixture.json")
        with patch.object(sys, "argv", [
            "run.py", "run", "--profile", "m-import", "--api-image", self.selected,
            "--import-fixture", str(manifest),
        ]), patch.object(run, "run_classes") as classes:
            run.main()
        classes.assert_called_once_with("m-import", "fake", None, self.selected, import_fixture_path=manifest)

    def test_prepare_preserves_private_fixture_metadata_without_sources(self) -> None:
        fixture = run.import_diagnostic.fixture_manifest()
        cluster = Mock()
        cluster.identity = SimpleNamespace()
        cluster.image_provenance.return_value = {"verified": True}
        cluster.exec_python.return_value = {"connections": {}}
        cluster.api_image_evidence.return_value = {"pod": "api"}
        cluster.kubectl.return_value = '{"items": []}'
        current = SimpleNamespace(
            result={},
            driver=Mock(return_value={"import_fixture": run.import_diagnostic.fixture_metadata(fixture)}),
            save=Mock(),
            expected_api_pods=None,
        )
        profile = {"imports_diagnostic": True, "seed": {}, "worker_policy": {}, "table_docs": 0, "settle_s": 0}
        with patch.object(run, "_image_record", return_value={"id": "sha256:selected"}), \
             patch.object(run.deploy, "apply", return_value={}), \
             patch.object(run, "check_destinations"), \
             patch.object(run.time, "sleep"):
            run._prepare(cluster, current, profile, "fake", self.selected, import_fixture=fixture)

        setup = current.driver.call_args.args[1]
        self.assertEqual(setup["import_fixture"], fixture)
        self.assertEqual(current.result["import_fixture"], run.import_diagnostic.fixture_metadata(fixture))
        self.assertNotIn("files", current.result["import_fixture"])
        self.assertEqual(current.result["setup"]["import_fixture"], run.import_diagnostic.fixture_metadata(fixture))

    def test_classes_refuses_nonempty_evidence_directory_before_cluster_access(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary) / "classes"
            out.mkdir()
            (out / "result.json").write_text("previous", encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "already contains evidence"):
                run.run_classes("m-import", "fake", out, self.selected)

    def test_import_classes_explicit_image_reaches_run_and_prepare(self) -> None:
        cluster = Mock()
        cluster.identity = SimpleNamespace()
        cluster.db_counters.return_value = {
            "tables": {}, "xact": 0, "rollbacks": 0, "rows_written": 0, "wal_bytes": 0,
            "wal_generated_bytes": 0, "wal_stats_reset": "stable",
        }
        cluster.container_stats.return_value = []
        prepared: list[tuple[str | None, dict | None]] = []
        created: list[str | None] = []
        runs: list[object] = []

        class CapturingRun:
            def __init__(self, _cluster, _kind, _profile_name, _profile, _backend, out, api_image=None) -> None:
                created.append(api_image)
                self.cluster = _cluster
                self.out = out
                self.result = {"classes": [], "snapshots": []}
                runs.append(self)

            def driver(self, _mode, _args, timeout_s):
                record = {
                    "id": "execution", "status": "Success", "result": {
                        "ok": True, "endpoint": "https://imports.invalid/fixture", "signature": "import-fixture-v1",
                    },
                    "created_at": "2026-10-30T12:00:00Z", "started_at": "2026-10-30T12:00:00Z",
                    "completed_at": "2026-10-30T12:00:00.001Z", "duration_ms": 1,
                }
                return {"sequential": [record] * 10, "burst": []}

            def save(self) -> None:
                pass

            def snapshot(self, _label) -> None:
                pass

        class Sampler:
            samples: list[dict] = []

            def __init__(self, _cluster) -> None:
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args) -> None:
                pass

        fixture = run.import_diagnostic.fixture_manifest()
        with tempfile.TemporaryDirectory() as temporary:
            manifest = Path(temporary) / "private-import-fixture.json"
            manifest.write_text(json.dumps(fixture), encoding="utf-8")
            with patch.object(run, "Cluster", return_value=cluster), \
                 patch.object(run, "Run", CapturingRun), \
                 patch.object(run, "WorkerSampler", Sampler), \
                 patch.object(
                     run,
                     "_prepare",
                     side_effect=lambda *_args, **kwargs: prepared.append(
                         (_args[-1], kwargs.get("import_fixture")),
                     ) or {"connections": {}},
                 ), \
                 patch.object(run.import_diagnostic, "capture", side_effect=RuntimeError("worker logs unavailable")), \
                 patch.object(run.time, "sleep"):
                run.run_classes("m-import", "fake", Path(temporary) / "classes", self.selected, manifest)

        self.assertEqual(created, [self.selected])
        self.assertEqual(prepared, [(self.selected, fixture)])
        self.assertEqual(runs[0].result["imports_diagnostic"]["overall"], "invalid")
        self.assertIn("worker logs unavailable", runs[0].result["imports_diagnostic"]["groups"][0]["capture_error"])

    def test_classes_persists_per_run_costs_after_shared_idle_capture(self) -> None:
        def counters(xact: int, wal: int, generated: int) -> dict:
            return {
                "tables": {}, "xact": xact, "rollbacks": 0, "rows_written": xact,
                "wal_bytes": wal, "wal_generated_bytes": generated, "wal_stats_reset": "stable",
            }

        cluster = Mock()
        cluster.identity = SimpleNamespace()
        cluster.container_stats.return_value = []
        cluster.db_counters.side_effect = [
            counters(0, 0, 0), counters(2, 1_024, 512),
            counters(2, 1_024, 512), counters(4, 2_048, 1_024),
        ]
        saved: list[dict] = []
        runs: list[object] = []
        profile = {
            "classes": ["wf-sdk"], "sequential": 1, "burst": 0, "burst_rounds": 0, "timeout_s": 1,
            "settle_s": 0, "capacity": {"disk": {}},
        }

        class CapturingRun:
            def __init__(self, _cluster, _kind, _profile_name, _profile, _backend, out, _api_image=None) -> None:
                self.out = out
                self.result = {"classes": [], "snapshots": []}
                runs.append(self)

            def driver(self, _mode, _args, timeout_s):
                record = {
                    "id": "execution", "status": "Success", "created_at": "2026-10-30T12:00:00Z",
                    "started_at": "2026-10-30T12:00:00Z", "completed_at": "2026-10-30T12:00:00.001Z",
                }
                return {"sequential": [record], "burst": []}

            def save(self) -> None:
                saved.append(deepcopy(self.result))

            def snapshot(self, _label) -> None:
                pass

        class Sampler:
            samples: list[dict] = []

            def __init__(self, _cluster) -> None:
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args) -> None:
                pass

        def idle_capture(current, _profile):
            current.result["db_idle"] = {"seconds": 1.0, "rate": {
                "xact_per_s": 0.0, "rows_written_per_s": 0.0, "wal_kib_per_s": 0.0,
                "wal_generated_kib_per_s": 0.0,
            }, "wal_stats_reset": "stable", "top_tables": []}
            current.save()
            return current.result["db_idle"]["rate"]

        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(run, "load_profile", return_value=profile), \
             patch.object(run, "Cluster", return_value=cluster), \
             patch.object(run, "Run", CapturingRun), \
             patch.object(run, "WorkerSampler", Sampler), \
             patch.object(run, "_prepare", return_value={"connections": {}}), \
             patch.object(run, "capture_idle_db", side_effect=idle_capture), \
             patch.object(run.time, "sleep"), \
             patch.object(run.time, "monotonic", side_effect=[0.0, 1.0, 2.0, 3.0]):
            run.run_classes("m", "fake", Path(temporary) / "classes", self.selected)

        self.assertEqual(runs[0].result["classes"][0]["db_per_run"]["wal_generated_kib"], 0.5)
        self.assertEqual(saved[-1]["classes"][0]["db_per_run"]["wal_generated_kib"], 0.5)
        self.assertEqual(saved[-1]["classes"][1]["db_per_run"]["wal_generated_kib"], 0.5)

    def test_import_raw_records_and_trace_are_saved_before_counter_failure(self) -> None:
        cluster = Mock()
        cluster.identity = SimpleNamespace()
        counter = {"tables": {}, "xact": 0, "rollbacks": 0, "rows_written": 0, "wal_bytes": 0,
                   "wal_generated_bytes": 0, "wal_stats_reset": None}
        cluster.db_counters.side_effect = [counter, RuntimeError("database counters unavailable")]
        saved: list[dict] = []

        class CapturingRun:
            def __init__(self, _cluster, _kind, _profile_name, _profile, _backend, out, _api_image=None) -> None:
                self.out = out
                self.result = {"classes": [], "snapshots": []}

            def driver(self, _mode, _args, timeout_s):
                record = {
                    "id": "execution", "status": "Success", "result": {
                        "ok": True, "endpoint": "https://imports.invalid/fixture", "signature": "import-fixture-v1",
                    },
                    "created_at": "2026-10-30T12:00:00Z", "started_at": "2026-10-30T12:00:00Z",
                    "completed_at": "2026-10-30T12:00:00.001Z", "duration_ms": 1,
                }
                return {"sequential": [record] * 10, "burst": []}

            def save(self) -> None:
                saved.append(deepcopy(self.result))

            def snapshot(self, _label) -> None:
                pass

        class Sampler:
            samples: list[dict] = []

            def __init__(self, _cluster) -> None:
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args) -> None:
                pass

        trace = {
            "kind": "partial-imports-diagnostic", "overall": "pass", "expected_execution_ids": ["execution"] * 10,
            "sources": [], "events": [], "missing_execution_ids": [], "duplicate_execution_ids": [],
            "unmatched_execution_ids": [], "invalid_events": [],
        }
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(run, "Cluster", return_value=cluster), \
             patch.object(run, "Run", CapturingRun), \
             patch.object(run, "WorkerSampler", Sampler), \
             patch.object(run, "_prepare", return_value={"connections": {}}), \
             patch.object(run.import_diagnostic, "capture", return_value=trace), \
             patch.object(run.time, "sleep"):
            with self.assertRaisesRegex(RuntimeError, "counters unavailable"):
                run.run_classes("m-import", "fake", Path(temporary) / "classes", self.selected)

        self.assertTrue(any(entry["classes"] and entry["classes"][0].get("raw") for entry in saved))
        self.assertTrue(any(entry.get("imports_diagnostic", {}).get("groups") for entry in saved))

    def test_drill_explicit_image_is_deployed_and_recorded_by_id(self) -> None:
        cluster = FakeCluster()
        prepared: list[str] = []

        def prepare(_cluster, _current, _profile, _backend, image):
            prepared.append(image)
            return {"connections": {}}

        drill = {"failures": 0, "requests": 1, "first_success_after_ready_s": 0.0, "checks": {}, "passed": True}
        with tempfile.TemporaryDirectory() as temporary, self._common_patches(cluster), \
             patch.object(run, "_prepare", side_effect=prepare), \
             patch.object(run.deploy, "reset"), \
             patch.object(run.drills, "drill", return_value=drill):
            out = Path(temporary) / "drill"
            run.run_drill("m-capacity", "sdk-heavy", 3.90625, "pooler-delete", self.selected, out)

            result = json.loads((out / "result.json").read_text())
            rendered = (out / "report.md").read_text()

        self.assertEqual(prepared, [self.selected])
        self.assertEqual(result["images"]["api"], {"tag": self.selected, "id": "sha256:selected"})
        self.assertIn("`bifrost-api:selected` (sha256:selected)", rendered)

    def test_d4_is_persisted_as_correctness_only_not_capacity(self) -> None:
        cluster = FakeCluster()
        prepared: list[str] = []

        def prepare(_cluster, _current, _profile, _backend, image):
            prepared.append(image)
            return {"connections": {}}

        def d4(_cluster, _ids, _plan, _disk, _run_id, persist_initial):
            initial = {"kind": "redis-empty-restart", "mode": "correctness-only", "passed": False, "overall": "invalid"}
            persist_initial(initial)
            return {**initial, "passed": True, "overall": "pass"}

        with tempfile.TemporaryDirectory() as temporary, self._common_patches(cluster), \
             patch.object(run, "_prepare", side_effect=prepare), \
             patch.object(run.deploy, "reset"), \
             patch.object(run.drills, "d4", side_effect=d4):
            out = Path(temporary) / "d4"
            run.run_drill("m-capacity", "mixed", 1.0, "redis-empty-restart", self.selected, out)
            result = json.loads((out / "result.json").read_text())

        self.assertEqual(prepared, [self.selected])
        self.assertEqual(result["mode"], "correctness-only")
        self.assertTrue(result["not_capacity"])
        self.assertEqual(result["drill"]["overall"], "pass")

    def test_d4_rejects_any_rate_other_than_one(self) -> None:
        with self.assertRaisesRegex(SystemExit, "correctness-only"):
            run.run_drill("m-capacity", "mixed", 1.1, "redis-empty-restart", self.selected, None)

    def test_prepare_records_node_chain_before_deploy_and_running_api_after_restart(self) -> None:
        cluster = Mock()
        cluster.identity = SimpleNamespace()
        provenance = {
            "tag": self.selected,
            "host_id": "sha256:host",
            "node": {"index_id": "sha256:index", "manifest_id": "sha256:manifest", "config_id": "sha256:config"},
            "cri_config_id": "sha256:config",
        }
        cluster.image_provenance.return_value = provenance
        cluster.api_image_evidence.return_value = {
            "api_pod": {"image_id": "containerd://sha256:config", "digest": "sha256:config"},
            "api_pods": [{"image_id": "containerd://sha256:config", "digest": "sha256:config"}],
        }
        cluster.kubectl.return_value = '{"items": []}'
        cluster.exec_python.return_value = {"connections": {}}
        current = SimpleNamespace(
            result={"images": {"api": {"id": "sha256:host"}}},
            driver=Mock(return_value={"seeded": True}),
            expected_api_pods=(("old", "old"),),
            save=Mock(),
        )
        profile = run.load_profile("m-capacity")

        with patch.object(run.deploy, "apply", return_value={"admitted": {}}) as apply, \
             patch.object(run, "check_destinations"), \
             patch.object(run, "_image_id", return_value="sha256:host"), \
             patch.object(run.time, "sleep"):
            self.assertEqual(run._prepare(cluster, current, profile, "fake", self.selected), {"connections": {}})

        cluster.image_provenance.assert_called_once_with(self.selected, "sha256:host")
        apply.assert_called_once_with(cluster, profile, self.selected)
        cluster.restart_api.assert_called_once()
        cluster.api_image_evidence.assert_called_once_with(provenance)
        self.assertEqual(current.result["api_image_provenance"]["api_pod"]["digest"], "sha256:config")
        loadgen_payload = json.loads(cluster.exec_python.call_args.args[2][1])
        self.assertEqual(loadgen_payload["classes"], ["agent-chat", "agent-tools", "wf-io", "wf-noop", "wf-sdk"])
        self.assertEqual(current.save.call_count, 4)
        self.assertNotIn("admission_rejection", current.result)
        self.assertIsNone(current.expected_api_pods)

    def test_prepare_saves_admission_rejection_evidence_before_raising(self) -> None:
        pods = [{"metadata": {"name": "bifrost-worker-rejected"}}]
        quota_status = {"used": {"requests.cpu": "900m", "requests.memory": "1Gi"}}
        rejection = run.deploy.AdmittedEnvelopeError("admitted resources disagree", pods, quota_status)
        cluster = Mock()
        cluster.identity = SimpleNamespace()
        cluster.image_provenance.return_value = {"verified": True}
        saved: list[dict] = []
        current = SimpleNamespace(
            result={},
            save=lambda: saved.append(deepcopy(current.result)),
            expected_api_pods=None,
        )
        with patch.object(run, "_image_record", return_value={"id": "sha256:selected"}), \
             patch.object(run.deploy, "apply", side_effect=rejection):
            with self.assertRaises(run.deploy.AdmittedEnvelopeError):
                run._prepare(cluster, current, {"settle_s": 0}, "fake", self.selected)

        evidence = {"reason": "admitted resources disagree", "pods": pods, "quota_status": quota_status}
        self.assertEqual(current.result["admission_rejection"], evidence)
        self.assertEqual(saved[-1]["admission_rejection"], evidence)
        cluster.exec_python.assert_not_called()

    def test_confirmation_only_saves_before_setup_and_never_claims_a_failed_rate(self) -> None:
        cluster = FakeCluster()
        cluster.db_counters = Mock(side_effect=[{"xact": 1}, {"xact": 2}, {"xact": 3}, {"xact": 4}])
        events: list[str] = []

        def prepare(_cluster, current, _profile, _backend, image):
            persisted = json.loads((current.out / "result.json").read_text())
            self.assertEqual(image, self.selected)
            self.assertEqual(persisted["mode"], "confirmation-only")
            self.assertEqual(persisted["sustainable_rate"], None)
            current.result["api_image_provenance"] = {"tag": image, "phase": len([event for event in events if event == "prepare"])}
            events.append("prepare")
            return {"connections": {}}

        def confirm(*_args):
            events.append("confirm")
            return _step(3.90625, "fail")

        def save(current):
            events.append(f"save:{len(current.result['confirmations'])}")
            original_save(current)

        original_save = run.Run.save
        original_idle_capture = run.capture_idle_db
        idle_window = {"seconds": 60.0, "tables": {"idle": [1, 2]}, "wal_stats_reset": "stable"}
        all_phases = {"seconds": 600.0, "tables": {"all": [3, 4]}, "wal_stats_reset": "stable"}

        def shared_idle_capture(*args, **kwargs):
            events.append("shared-idle-capture")
            return original_idle_capture(*args, **kwargs)

        with tempfile.TemporaryDirectory() as temporary, self._common_patches(cluster), \
             patch.object(run, "_prepare", side_effect=prepare), \
             patch.object(run.deploy, "reset") as reset, \
             patch.object(run.Run, "save", new=save), \
             patch.object(run, "capture_idle_db", side_effect=shared_idle_capture), \
             patch.object(run.Run, "snapshot", side_effect=lambda label: events.append(f"snapshot:{label}")), \
             patch.object(run.time, "sleep", side_effect=lambda seconds: events.append(f"sleep:{seconds}")), \
             patch.object(run.time, "monotonic", side_effect=[10.0, 70.0, 100.0, 700.0]), \
             patch.object(run.stats, "db_delta", side_effect=[idle_window, all_phases]), \
             patch.object(run.stats, "db_rate", return_value={
                 "xact_per_s": 0.1, "rollbacks_per_s": 0.0, "rows_written_per_s": 0.0,
                 "wal_kib_per_s": 0.0, "wal_generated_kib_per_s": 0.0,
             }), \
             patch.object(run.stats, "top_tables", return_value=[("idle", 1, 2)]), \
             patch.object(run.preflight, "run", side_effect=lambda *_args: events.append("preflight") or {"overall": "pass"}), \
             patch.object(run.search, "confirm", side_effect=confirm):
            out = Path(temporary) / "confirmation-only"
            run.run_confirmation("m-capacity", "sdk-heavy", 3.90625, self.selected, out)

            result = json.loads((out / "result.json").read_text())

        self.assertEqual(result["kind"], "capacity")
        self.assertEqual(result["mode"], "confirmation-only")
        self.assertEqual(result["steps"], [])
        self.assertEqual(result["confirmations"][0]["overall"], "fail")
        self.assertIsNone(result["sustainable_rate"])
        self.assertEqual(result["db_idle"], {
            "seconds": 60.0,
            "wal_stats_reset": "stable",
            "rate": {
                "xact_per_s": 0.1, "rollbacks_per_s": 0.0, "rows_written_per_s": 0.0,
                "wal_kib_per_s": 0.0, "wal_generated_kib_per_s": 0.0,
            },
            "top_tables": [["idle", 1, 2]],
        })
        self.assertEqual(result["all_phases_db_window"], {"label": "warmup+measure+drain", **all_phases})
        self.assertEqual(events[:7], ["save:0", "prepare", "save:0", "preflight", "save:0", "prepare", "save:0"])
        profile = run.load_profile("m-capacity")
        sleep_events = [event for event in events if event.startswith("sleep:")]
        self.assertEqual(sleep_events, [
            f"sleep:{profile['idle_s'] - profile['idle_db_s']}",
            f"sleep:{profile['idle_db_s']}",
        ])
        self.assertLess(events.index("shared-idle-capture"), events.index(sleep_events[0]))
        self.assertLess(events.index("shared-idle-capture"), events.index("snapshot:idle"))
        self.assertLess(events.index("snapshot:idle"), events.index("confirm"))
        self.assertLess(events.index("confirm"), events.index("snapshot:settled"))
        self.assertEqual(result["preflight_setup"]["api_image_provenance"]["phase"], 0)
        self.assertEqual(result["confirmation_setup"]["api_image_provenance"]["phase"], 1)
        self.assertEqual(reset.call_count, 2)

    def test_failed_preflight_stops_before_long_confirmation(self) -> None:
        cluster = FakeCluster()

        def prepare(_cluster, current, _profile, _backend, image):
            current.result["api_image_provenance"] = {"tag": image}
            return {"connections": {}}

        with tempfile.TemporaryDirectory() as temporary, self._common_patches(cluster), \
             patch.object(run, "_prepare", side_effect=prepare), \
             patch.object(run.deploy, "reset") as reset, \
             patch.object(run.preflight, "run", return_value={"overall": "fail", "records": [], "failure_log_tails": {}}), \
             patch.object(run.search, "confirm") as confirm:
            out = Path(temporary) / "preflight-fail"
            run.run_confirmation("m-capacity", "sdk-heavy", 3.90625, self.selected, out)
            result = json.loads((out / "result.json").read_text())
        self.assertEqual(reset.call_count, 2)
        confirm.assert_not_called()
        self.assertEqual(result["stopped"], "preflight-failed")
        self.assertEqual(result["preflight"]["namespace_cleanup"], {"verified": True})
        self.assertEqual(result["preflight"]["overall"], "fail")

    def test_preflight_progress_is_saved_before_deadline_reconciliation(self) -> None:
        cluster = FakeCluster()
        observed: dict[str, object] = {}

        def prepare(_cluster, current, _profile, _backend, image):
            current.result["api_image_provenance"] = {"tag": image}
            return {"connections": {}}

        def preflight_run(_cluster, _ids, _rate, _plan, _logs, on_progress):
            on_progress({"overall": "running", "sent": [{"id": "request"}], "immediate_log_tails": {}})
            observed["saved"] = json.loads((out / "result.json").read_text())["preflight"]
            return {"overall": "fail", "records": []}

        with tempfile.TemporaryDirectory() as temporary, self._common_patches(cluster), \
             patch.object(run, "_prepare", side_effect=prepare), \
             patch.object(run.deploy, "reset"), \
             patch.object(run.preflight, "run", side_effect=preflight_run), \
             patch.object(run.search, "confirm") as confirm:
            out = Path(temporary) / "preflight-progress"
            run.run_confirmation("m-capacity", "sdk-heavy", 3.90625, self.selected, out)
        confirm.assert_not_called()
        self.assertEqual(observed["saved"], {"overall": "running", "sent": [{"id": "request"}], "immediate_log_tails": {}})

    def test_confirmation_prepare_failure_keeps_preflight_proof(self) -> None:
        cluster = FakeCluster()
        calls = 0

        def prepare(_cluster, current, _profile, _backend, image):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("confirmation deployment failed")
            current.result["api_image_provenance"] = {"tag": image, "phase": "preflight"}
            return {"connections": {}}

        with tempfile.TemporaryDirectory() as temporary, self._common_patches(cluster), \
             patch.object(run, "_prepare", side_effect=prepare), \
             patch.object(run.deploy, "reset"), \
             patch.object(run.preflight, "run", return_value={"overall": "pass"}):
            out = Path(temporary) / "confirmation-prepare-fail"
            with self.assertRaisesRegex(RuntimeError, "confirmation deployment failed"):
                run.run_confirmation("m-capacity", "sdk-heavy", 3.90625, self.selected, out)
            result = json.loads((out / "result.json").read_text())
        self.assertEqual(result["preflight"]["overall"], "pass")
        self.assertEqual(result["preflight_setup"]["api_image_provenance"], {"tag": self.selected, "phase": "preflight"})
        self.assertNotIn("confirmation_setup", result)

    def test_unverified_preflight_transport_cleanup_resets_before_return(self) -> None:
        cluster = FakeCluster()
        invalid = {"overall": "invalid", "requires_namespace_teardown": True, "error": "remote cleanup"}

        def prepare(_cluster, current, _profile, _backend, image):
            current.result["api_image_provenance"] = {"tag": image}
            return {"connections": {}}

        with tempfile.TemporaryDirectory() as temporary, self._common_patches(cluster), \
             patch.object(run, "_prepare", side_effect=prepare), \
             patch.object(run.deploy, "reset") as reset, \
             patch.object(run.preflight, "run", return_value=invalid), \
             patch.object(run.search, "confirm") as confirm:
            out = Path(temporary) / "preflight-cleanup"
            run.run_confirmation("m-capacity", "sdk-heavy", 3.90625, self.selected, out)
            result = json.loads((out / "result.json").read_text())
        self.assertEqual(reset.call_count, 2)
        confirm.assert_not_called()
        self.assertEqual(result["preflight"]["namespace_cleanup"], {"verified": True})

    def test_idle_counter_reset_saves_raw_window_before_raising(self) -> None:
        before = {
            "xact": 1, "rollbacks": 0, "rows_written": 0, "wal_bytes": 100,
            "wal_generated_bytes": 100, "wal_stats_reset": "before", "tables": {},
        }
        after = {**before, "xact": 2, "wal_bytes": 200, "wal_generated_bytes": 200, "wal_stats_reset": "after"}
        current = SimpleNamespace(cluster=SimpleNamespace(db_counters=Mock(side_effect=[before, after])), result={},
                                  save=Mock(), snapshot=Mock())
        profile = {"idle_s": 60, "idle_db_s": 30}
        with patch.object(run.time, "sleep"), patch.object(run.time, "monotonic", side_effect=[1.0, 31.0]):
            with self.assertRaisesRegex(run.stats.DbCounterWindowError, "stats_reset changed"):
                run.capture_idle_db(current, profile)

        self.assertEqual(current.result["db_idle_error"], {
            "reason": "pg_stat_wal stats_reset changed during database window", "before": before, "after": after,
        })
        current.save.assert_called_once()
        current.snapshot.assert_not_called()

    def test_snapshot_records_processes_for_each_worker(self) -> None:
        cluster = FakeCluster()
        cluster.api_pod_records = Mock(return_value=[
            {"pod": "bifrost-api-1", "pod_uid": "api-uid-1"},
            {"pod": "bifrost-api-2", "pod_uid": "api-uid-2"},
        ])
        cluster.smaps = Mock(return_value="ignored")
        cluster.db_counters = Mock(return_value={
            "db_bytes": 0, "wal_bytes": 0, "wal_generated_bytes": 0, "wal_stats_reset": None,
        })
        cluster.container_stats = Mock(return_value=[])
        cluster.worker_pods = Mock(return_value=["worker-a", "worker-b"])
        processes = {
            "worker-a": [{"pid": 15, "depth": 1, "private_mib": 48.0}],
            "worker-b": [{"pid": 16, "depth": 2, "private_mib": 24.0}],
        }
        cluster.worker_processes = Mock(side_effect=lambda pod: processes[pod])
        summary = SimpleNamespace(rss_kib=0, pss_kib=0, thp_kib=0, regions={})

        with tempfile.TemporaryDirectory() as temporary, self._common_patches(cluster), \
             patch.object(run.smaps, "summarize", return_value=summary), \
             patch.object(run.report, "render", return_value="report"):
            current = run.Run(cluster, "classes", "m", run.load_profile("m"), "fake", Path(temporary) / "snapshot")
            current.snapshot("idle")

        self.assertEqual(current.result["snapshots"][0]["worker_processes"], processes)
        self.assertEqual(current.result["snapshots"][0]["api_pods"], [
            {"pod": "bifrost-api-1", "pod_uid": "api-uid-1"},
            {"pod": "bifrost-api-2", "pod_uid": "api-uid-2"},
        ])
        self.assertEqual(current.result["snapshots"][0]["api_sample"], {
            "pod": "bifrost-api-1", "pod_uid": "api-uid-1",
            "selection": "sorted ready API replica sample for smaps",
        })
        self.assertEqual(current.result["snapshots"][0]["wal_generated_bytes"], 0)
        self.assertEqual(current.result["snapshots"][0]["wal_generated_mib"], 0.0)
        self.assertIsNone(current.result["snapshots"][0]["wal_stats_reset"])
        self.assertEqual(cluster.worker_processes.call_args_list[0].args, ("worker-a",))
        self.assertEqual(cluster.worker_processes.call_args_list[1].args, ("worker-b",))

    def test_snapshot_rejects_any_api_replica_identity_change(self) -> None:
        cluster = FakeCluster()
        cluster.api_pod_records = Mock(side_effect=[
            [{"pod": "bifrost-api-1", "pod_uid": "api-uid-1"}, {"pod": "bifrost-api-2", "pod_uid": "api-uid-2"}],
            [{"pod": "bifrost-api-1", "pod_uid": "api-uid-1"}, {"pod": "bifrost-api-2", "pod_uid": "replacement-uid"}],
        ])
        cluster.smaps = Mock(return_value="ignored")
        cluster.db_counters = Mock(return_value={
            "db_bytes": 0, "wal_bytes": 0, "wal_generated_bytes": 0, "wal_stats_reset": None,
        })
        cluster.container_stats = Mock(return_value=[])
        cluster.worker_pods = Mock(return_value=[])
        cluster.worker_image_evidence = Mock(return_value={})
        summary = SimpleNamespace(rss_kib=0, pss_kib=0, thp_kib=0, regions={})

        with tempfile.TemporaryDirectory() as temporary, self._common_patches(cluster), \
             patch.object(run.smaps, "summarize", return_value=summary), \
             patch.object(run.report, "render", return_value="report"):
            current = run.Run(cluster, "classes", "m", run.load_profile("m"), "fake", Path(temporary) / "snapshot")
            current.snapshot("before")
            with self.assertRaisesRegex(SystemExit, "API replica identities changed"):
                current.snapshot("after")

        self.assertEqual(current.result["api_pod_changes"], 1)

    def test_selected_image_must_exist_before_deployment(self) -> None:
        cluster = FakeCluster()
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(run, "Cluster", return_value=cluster), \
             patch.object(run, "_git_sha", return_value="abc1234"), \
             patch.object(run.deploy, "images", return_value=self.default_images), \
             patch.object(run, "_image_id", return_value=""), \
             patch.object(run.deploy, "reset") as reset:
            with self.assertRaisesRegex(SystemExit, "not available locally"):
                run.run_confirmation("m-capacity", "sdk-heavy", 3.90625, self.selected, Path(temporary) / "missing")
        reset.assert_not_called()

    def test_confirmation_rejects_nonfinite_or_nonpositive_rate_before_reset(self) -> None:
        for rate in (float("nan"), float("inf"), float("-inf"), 0.0, -0.1):
            with self.subTest(rate=rate), patch.object(run.deploy, "reset") as reset:
                with self.assertRaisesRegex(SystemExit, "finite and greater than zero"):
                    run.run_confirmation("m-capacity", "sdk-heavy", rate, self.selected, None)
            reset.assert_not_called()

    def test_confirmation_refuses_a_nonempty_output_before_reset(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary) / "confirmation-only"
            out.mkdir()
            result = out / "result.json"
            result.write_text('{"existing": true}\n')

            with patch.object(run.deploy, "reset") as reset:
                with self.assertRaisesRegex(SystemExit, "already contains evidence"):
                    run.run_confirmation("m-capacity", "sdk-heavy", 3.90625, self.selected, out)

            reset.assert_not_called()
            self.assertEqual(result.read_text(), '{"existing": true}\n')

    def test_ab_variants_record_the_selected_image_ids(self) -> None:
        cluster = FakeCluster()
        image_a, image_b = "bifrost-api:a", "bifrost-api:b"
        captured: list[object] = []

        class CapturingRun:
            def __init__(self, *args) -> None:
                self.result: dict = {}
                captured.append(self)

            def save(self) -> None:
                pass

        def arm(_cluster, _profile, _plan, image, _rate, prepare):
            prepare(image)
            return {
                "overall": "pass", "idle": {"xact_per_s": 0.0, "rollbacks_per_s": 0.0},
                "xact_per_correct": 0.0, "api_cpu_s_per_correct": 0.0,
            }

        def prepare(_cluster, current, _profile, _backend, image):
            current.result["api_image_provenance"] = {"tag": image, "host_id": f"sha256:{image[-1]}"}
            return {"connections": {}}

        with self._common_patches(cluster), \
             patch.object(run, "Run", CapturingRun), \
             patch.object(run, "_image_id", side_effect=lambda tag: {image_a: "sha256:a", image_b: "sha256:b"}.get(tag, "sha256:default")), \
             patch.object(run, "_prepare", side_effect=prepare), \
             patch.object(run.ab, "arm", side_effect=arm), \
             patch.object(run.ab, "compare", return_value=[]):
            run.run_ab("m-capacity", "sdk-heavy", 3.90625, image_a, image_b, "AB", None)

        self.assertEqual(captured[0].result["variants"], {
            "a": {"tag": image_a, "id": "sha256:a"},
            "b": {"tag": image_b, "id": "sha256:b"},
        })
        self.assertEqual([arm["image_provenance"]["host_id"] for arm in captured[0].result["arms"]], ["sha256:a", "sha256:b"])

    def test_failed_replace_preserves_the_last_complete_result(self) -> None:
        cluster = FakeCluster()
        with tempfile.TemporaryDirectory() as temporary, self._common_patches(cluster), \
             patch.object(run.report, "render", return_value="new report"):
            out = Path(temporary) / "atomic"
            out.mkdir()
            result_path = out / "result.json"
            result_path.write_text('{"previous": true}\n')
            current = run.Run(cluster, "classes", "m", run.load_profile("m"), "fake", out)
            real_replace = os.replace

            def interrupted(source, destination):
                if Path(destination) == result_path:
                    raise OSError("interrupted replacement")
                real_replace(source, destination)

            with patch.object(run.os, "replace", side_effect=interrupted):
                with self.assertRaisesRegex(OSError, "interrupted replacement"):
                    current.save()

            self.assertEqual(result_path.read_text(), '{"previous": true}\n')
            self.assertEqual(list(out.glob(".result.json.*")), [])


if __name__ == "__main__":
    unittest.main()


class D4NamespaceCleanupTest(RunHarnessTest):
    def test_unverified_remote_cleanup_resets_guarded_namespaces_before_return(self) -> None:
        cluster = FakeCluster()
        drill = {"overall": "invalid", "passed": False, "requires_namespace_teardown": True}
        with tempfile.TemporaryDirectory() as temporary, self._common_patches(cluster), \
             patch.object(run, "_prepare", return_value={"connections": {}}), \
             patch.object(run.drills, "d4", return_value=drill), \
             patch.object(run.deploy, "reset") as reset:
            out = Path(temporary) / "d4-cleanup"
            run.run_drill("m-capacity", "mixed", 1.0, "redis-empty-restart", self.selected, out)
            result = json.loads((out / "result.json").read_text())
        self.assertEqual(reset.call_count, 2)
        self.assertEqual(result["drill"]["namespace_cleanup"], {"verified": True})

    def test_namespace_cleanup_failure_is_persisted_and_propagated(self) -> None:
        cluster = FakeCluster()
        drill = {"overall": "invalid", "passed": False, "requires_namespace_teardown": True}
        with tempfile.TemporaryDirectory() as temporary, self._common_patches(cluster), \
             patch.object(run, "_prepare", return_value={"connections": {}}), \
             patch.object(run.drills, "d4", return_value=drill), \
             patch.object(run.deploy, "reset", side_effect=[None, RuntimeError("namespace stuck")]):
            out = Path(temporary) / "d4-cleanup-failure"
            with self.assertRaisesRegex(RuntimeError, "namespace stuck"):
                run.run_drill("m-capacity", "mixed", 1.0, "redis-empty-restart", self.selected, out)
            result = json.loads((out / "result.json").read_text())
        self.assertEqual(result["drill"]["namespace_cleanup"], {
            "verified": False, "error": "RuntimeError: namespace stuck",
        })
