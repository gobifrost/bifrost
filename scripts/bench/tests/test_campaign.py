from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import ANY, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import campaign  # noqa: E402
import jsoncodec  # noqa: E402
import run  # noqa: E402


class FakeCluster:
    def __init__(self) -> None:
        self.verified = 0

    def verify(self) -> None:
        self.verified += 1

    def image_provenance(self, tag: str, image_id: str) -> dict:
        return {"tag": tag, "index_digest": image_id, "config_digest": "sha256:config"}


class MixedCampaignTest(unittest.TestCase):
    image = "bifrost-api:mixed-campaign"
    image_record = {"tag": image, "id": "sha256:index"}

    def _frozen_inputs(self):
        return (
            patch.object(run, "_image_record", return_value=self.image_record),
            patch.object(run, "_git_sha", return_value="freeze123"),
        )

    def _create_manifest(self, root: Path, cluster: FakeCluster | None = None) -> tuple[Path, FakeCluster]:
        cluster = cluster or FakeCluster()
        image, revision = self._frozen_inputs()
        with image, revision:
            path = campaign.create_manifest(cluster, "m-capacity", self.image, root)
        return path, cluster

    def _passing_preflight(self, manifest_path: Path) -> Path:
        _, manifest_hash = campaign.load_manifest(manifest_path)
        path = campaign._preflight_path(manifest_path)
        result = {
            "kind": "mixed-confirmation-preflight",
            "campaign_manifest_sha256": manifest_hash,
            "status": "pass",
            "preflight": {"overall": "pass"},
            "cleanup": {"verified": True},
        }
        contents = json.dumps(jsoncodec.encode(result), allow_nan=False).encode("utf-8")
        campaign._atomic_create(path, contents)
        campaign._write_hash_create(path, contents)
        return path

    def test_manifest_freezes_the_shared_confirmation_seed_rule_and_cannot_be_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "campaign"
            path, cluster = self._create_manifest(root)
            manifest = json.loads(path.read_text(encoding="utf-8"))
            plan = campaign._resolved_plan(run.load_profile("m-capacity"))

            self.assertEqual(manifest["indices"], [0, 1, 2, 3, 4])
            self.assertEqual(manifest["seeds"], [run.search.confirmation_seed(plan, index) for index in range(5)])
            self.assertEqual(cluster.verified, 1)
            image, revision = self._frozen_inputs()
            with image, revision, self.assertRaisesRegex(ValueError, "already contains evidence"):
                campaign.create_manifest(cluster, "m-capacity", self.image, root)
            self.assertEqual(cluster.verified, 1)

    def test_dirty_harness_revision_is_rejected_before_cluster_mutation(self) -> None:
        cluster = FakeCluster()
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(run, "_git_sha", return_value="freeze123-dirty"):
            with self.assertRaisesRegex(ValueError, "clean, nonempty"):
                campaign.create_manifest(cluster, "m-capacity", self.image, Path(temporary) / "campaign")
        self.assertEqual(cluster.verified, 0)

    def test_manifest_bound_preflight_is_saved_before_verified_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "campaign"
            manifest_path, _ = self._create_manifest(root)
            cluster = FakeCluster()
            candidate = {"kind": "representative-correctness-preflight", "overall": "pass", "sent": []}
            image, revision = self._frozen_inputs()
            with image, revision, \
                 patch.object(campaign, "Cluster", return_value=cluster), \
                 patch.object(campaign.deploy, "reset") as reset, \
                 patch.object(run, "_prepare", return_value={"connections": {}}) as prepare, \
                 patch.object(campaign.preflight, "run", return_value=candidate) as execute:
                output = campaign.run_preflight(manifest_path)

            result = jsoncodec.decode(json.loads(output.read_text(encoding="utf-8")))
            self.assertEqual(reset.call_count, 2)
            prepare.assert_called_once_with(cluster, ANY, run.load_profile("m-capacity"), "fake", self.image)
            execute.assert_called_once_with(
                cluster,
                {"connections": {}},
                campaign.RATE_PER_S,
                campaign._resolved_plan(run.load_profile("m-capacity")),
                root / "preflight-logs",
                ANY,
            )
            self.assertEqual(result["status"], "pass")
            self.assertEqual(result["preflight"], candidate)
            self.assertEqual(result["cleanup"], {"verified": True})
            campaign._verify_hash(output)

    def test_failed_preflight_is_retained_and_blocks_segment_without_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "campaign"
            manifest_path, _ = self._create_manifest(root)
            cluster = FakeCluster()
            image, revision = self._frozen_inputs()
            with image, revision, \
                 patch.object(campaign, "Cluster", return_value=cluster), \
                 patch.object(campaign.deploy, "reset"), \
                 patch.object(run, "_prepare", return_value={"connections": {}}), \
                 patch.object(campaign.preflight, "run", return_value={"overall": "fail", "records": []}):
                output = campaign.run_preflight(manifest_path)
            self.assertEqual(jsoncodec.decode(json.loads(output.read_text(encoding="utf-8")))["status"], "fail")
            retained = output.read_bytes()
            with patch.object(campaign, "Cluster") as blocked:
                with self.assertRaises(ValueError):
                    campaign.run_segment(manifest_path, 0)
                with self.assertRaisesRegex(ValueError, "already started"):
                    campaign.run_preflight(manifest_path)
            blocked.assert_not_called()
            self.assertEqual(output.read_bytes(), retained)

    def test_segment_runs_one_declared_seed_and_saves_raw_step_before_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "campaign"
            manifest_path, _ = self._create_manifest(root)
            self._passing_preflight(manifest_path)
            manifest, _ = campaign.load_manifest(manifest_path)
            output_path = Path(manifest["segments"][2]["output_path"])
            cluster = FakeCluster()
            cluster.db_counters = Mock(return_value={})
            calls: list[dict] = []
            reset_snapshots: list[dict] = []

            def one_step(*args, **kwargs):
                calls.append({"args": args, "kwargs": kwargs})
                kwargs["on_event"]({"event": "started", "started": 1_000.0})
                kwargs["checkpoint"]({"phase": "sent", "sent": {"requests": [{"id": "request"}]}})
                kwargs["checkpoint"]({
                    "phase": "outcomes",
                    "sent": {"requests": [{"id": "request"}]},
                    "outcomes": {"outcomes": {}, "duplicates": {}, "missing": [], "rows": []},
                })
                return {
                    "overall": "pass", "seed": args[4], "rate": args[2], "window_s": kwargs["window_s"],
                    "confirm": kwargs["confirm"], "window": (90.0, 3690.0),
                    "records": [{"class": "wf-sdk", "scheduled_s": 100.0, "outcome": "correct"}],
                    "sent": [{"id": "request"}], "outcomes": {"outcomes": {}, "duplicates": [], "missing": []},
                }

            def reset(_cluster):
                if output_path.exists():
                    reset_snapshots.append(jsoncodec.decode(json.loads(output_path.read_text(encoding="utf-8"))))

            def prepare(_cluster, current, _profile, _backend, _image):
                current.result["api_image_provenance"] = {"config_digest": "sha256:config"}
                current.save()
                return {"connections": {}}

            def idle_evidence(current, _profile):
                current.result["db_idle"] = {
                    "seconds": 10.0, "rate": {"xact_per_s": 0.0}, "wal_stats_reset": "stable", "top_tables": [],
                }
                current.result["snapshots"].append({"label": "idle"})
                current.save()

            def snapshot(current, label):
                current.result["snapshots"].append({"label": label})
                current.save()

            all_phases = {
                "seconds": 3_810.0, "tables": {}, "xact": 1,
                "wal_bytes": 2_048, "wal_generated_bytes": 1_024, "wal_stats_reset": "stable",
            }
            image, revision = self._frozen_inputs()
            with image, revision, \
                 patch.object(campaign, "Cluster", return_value=cluster), \
                 patch.object(campaign.deploy, "reset", side_effect=reset) as reset_mock, \
                 patch.object(run, "_prepare", side_effect=prepare) as prepare_mock, \
                 patch.object(run, "capture_idle_db", side_effect=idle_evidence) as capture_idle, \
                 patch.object(run, "snapshot", side_effect=snapshot) as snapshot_capture, \
                 patch.object(run.stats, "db_delta", return_value=all_phases), \
                 patch.object(campaign.search, "run_step", side_effect=one_step):
                output = campaign.run_segment(manifest_path, 2)

            result = jsoncodec.decode(json.loads(output.read_text(encoding="utf-8")))
            self.assertEqual(len(calls), 1)
            self.assertEqual(
                calls[0]["args"][4],
                run.search.confirmation_seed(campaign._resolved_plan(run.load_profile("m-capacity")), 2),
            )
            self.assertEqual(calls[0]["kwargs"]["window_s"], 3600)
            self.assertTrue(calls[0]["kwargs"]["confirm"])
            self.assertTrue(calls[0]["kwargs"]["cancel_driver"])
            self.assertTrue(callable(calls[0]["kwargs"]["on_event"]))
            self.assertTrue(callable(calls[0]["kwargs"]["checkpoint"]))
            prepare_mock.assert_called_once_with(cluster, ANY, run.load_profile("m-capacity"), "fake", self.image)
            capture_idle.assert_called_once_with(ANY, run.load_profile("m-capacity"))
            snapshot_capture.assert_called_once_with(ANY, "settled")
            self.assertEqual(reset_mock.call_count, 2)
            self.assertIn("step", reset_snapshots[-1])
            self.assertEqual(reset_snapshots[-1]["api_image_provenance"], {"config_digest": "sha256:config"})
            self.assertEqual(reset_snapshots[-1]["db_idle"]["seconds"], 10.0)
            self.assertEqual(reset_snapshots[-1]["all_phases_db_window"], {
                "label": "warmup+measure+drain", **all_phases,
            })
            self.assertEqual([snapshot["label"] for snapshot in reset_snapshots[-1]["snapshots"]], ["idle", "settled"])
            self.assertEqual(result["status"], "pass")
            self.assertEqual(result["step"]["reliability_status"], "pending_pooled")
            self.assertEqual(result["step"]["outcomes"]["duplicates"], [])
            self.assertEqual(result["attempt"]["events"], [{"event": "started", "started": 1_000.0}])
            self.assertIn("raw_sent", result["attempt"])
            self.assertIn("raw_outcomes", result["attempt"])
            self.assertTrue(campaign._hash_path(output).exists())

    def test_started_segment_is_not_replaced_before_cluster_access(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest_path, _ = self._create_manifest(Path(temporary) / "campaign")
            manifest, _ = campaign.load_manifest(manifest_path)
            output = Path(manifest["segments"][0]["output_path"])
            output.parent.mkdir(parents=True)
            output.write_text("evidence", encoding="utf-8")
            with patch.object(campaign, "Cluster") as cluster:
                with self.assertRaisesRegex(ValueError, "already started"):
                    campaign.run_segment(manifest_path, 0)
            cluster.assert_not_called()

    def test_frozen_image_mismatch_stops_before_reset(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest_path, _ = self._create_manifest(Path(temporary) / "campaign")
            self._passing_preflight(manifest_path)
            cluster = FakeCluster()
            with patch.object(campaign, "Cluster", return_value=cluster), \
                 patch.object(run, "_image_record", return_value={"tag": self.image, "id": "sha256:other"}), \
                 patch.object(run, "_git_sha", return_value="freeze123"), \
                 patch.object(campaign.deploy, "reset") as reset:
                with self.assertRaisesRegex(ValueError, "selected API image"):
                    campaign.run_segment(manifest_path, 0)

            reset.assert_not_called()

    def test_dirty_harness_revision_stops_segment_before_reset(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest_path, _ = self._create_manifest(Path(temporary) / "campaign")
            self._passing_preflight(manifest_path)
            cluster = FakeCluster()
            with patch.object(campaign, "Cluster", return_value=cluster), \
                 patch.object(run, "_image_record", return_value=self.image_record), \
                 patch.object(run, "_git_sha", return_value="freeze123-dirty"), \
                 patch.object(campaign.deploy, "reset") as reset:
                with self.assertRaisesRegex(ValueError, "clean, nonempty"):
                    campaign.run_segment(manifest_path, 0)
            reset.assert_not_called()

    def test_segment_exception_is_hashed_as_invalid_after_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "campaign"
            manifest_path, _ = self._create_manifest(root)
            self._passing_preflight(manifest_path)
            manifest, _ = campaign.load_manifest(manifest_path)
            output = Path(manifest["segments"][1]["output_path"])
            cluster = FakeCluster()
            cluster.db_counters = Mock(return_value={})
            image, revision = self._frozen_inputs()
            with image, revision, \
                 patch.object(campaign, "Cluster", return_value=cluster), \
                 patch.object(campaign.deploy, "reset") as reset, \
                 patch.object(run, "_prepare", return_value={"connections": {}}), \
                 patch.object(run, "capture_idle_db"), \
                 patch.object(campaign.search, "run_step", side_effect=RuntimeError("loadgen failed")):
                with self.assertRaisesRegex(RuntimeError, "loadgen failed"):
                    campaign.run_segment(manifest_path, 1)

            result = jsoncodec.decode(json.loads(output.read_text(encoding="utf-8")))
            self.assertEqual(reset.call_count, 2)
            self.assertEqual(result["status"], "invalid")
            self.assertEqual(result["cleanup"], {"verified": True})
            self.assertIn("loadgen failed", result["error"])
            campaign._verify_hash(output)

    def _aggregate_fixture(self, root: Path, count: int = 599, statuses: dict[int, str] | None = None) -> Path:
        paths = [root / "segments" / f"segment-{index}.json" for index in range(campaign.SEGMENT_COUNT)]
        manifest = {
            "plan": campaign.PLAN,
            "mix_name": campaign.MIX_NAME,
            "mix": {"wf-sdk": 100},
            "deadlines_s": {"wf-sdk": 60.0},
            "warmup_s": campaign.WARMUP_S,
            "drain_s": campaign.DRAIN_S,
            "rate_per_s": campaign.RATE_PER_S,
            "measured_window_s": campaign.MEASURED_WINDOW_S,
            "indices": list(range(campaign.SEGMENT_COUNT)), "seeds": list(range(100, 105)),
            "eligible_classes": ["wf-sdk"],
            "segments": [
                {
                    "index": index,
                    "seed": 100 + index,
                    "output_path": str(path),
                    "hash_path": str(campaign._hash_path(path)),
                }
                for index, path in enumerate(paths)
            ],
        }
        manifest_path = root / "campaign-manifest.json"
        contents = campaign._json_bytes(manifest)
        campaign._atomic_create(manifest_path, contents)
        campaign._write_hash_create(manifest_path, contents)
        manifest_hash = campaign._sha256_bytes(contents)
        self._passing_preflight(manifest_path)
        for index, path in enumerate(paths):
            status = (statuses or {}).get(index, "pass")
            gate_status = "fail" if status == "fail" else "inconclusive" if status == "inconclusive" else "pass"
            ids = [f"{index}-{row}" for row in range(count)]
            sent = [
                {
                    "id": request_id,
                    "class": "wf-sdk",
                    "scheduled": 1_100.0,
                    "sent": 1_100.0,
                    "responded": 1_101.0,
                    "http_status": 200,
                    "error": None,
                }
                for request_id in ids
            ]
            durable = {
                request_id: {
                    "request_id": request_id,
                    "correct": True,
                    "completed": 1_101.0,
                    "started": 1_100.5,
                    "created": 1_100.1,
                    "status": "Success",
                    "error": None,
                }
                for request_id in ids
            }
            gate = {"status": gate_status, "value": 0.0, "limit": 1.0}
            step = {
                "overall": status,
                "started": 1_000.0,
                "seed": 100 + index,
                "rate": campaign.RATE_PER_S,
                "window_s": campaign.MEASURED_WINDOW_S, "confirm": True, "window": [90.0, 3690.0],
                "sent": sent,
                "outcomes": {"outcomes": durable, "duplicates": {}, "missing": [], "rows": list(durable.values())},
                "missing": [],
                "faults": {
                    "restarts": 0,
                    "oom_killed": 0,
                    "evicted": 0,
                    "quota_denials": 0,
                    "duplicate_executions": 0,
                },
                "backlog": [],
                "host": [],
                "validity": {"invalid": [], "warnings": []},
                "verdict": {
                    "overall": gate_status,
                    "throughput": gate,
                    "drift": gate,
                    "tenant_faults": gate,
                    "latency": {"wf-sdk": {"queue_p95": gate, "e2e_p95": gate}},
                    "reliability": {"wf-sdk": gate},
                },
                "records": campaign.search.to_records(sent, {"outcomes": durable}, 1_000.0, {"wf-sdk": 60.0}),
            }
            result = {
                "status": status,
                "campaign_manifest_sha256": manifest_hash,
                "index": index,
                "seed": 100 + index,
                "step": step,
                "cleanup": {"verified": True},
            }
            encoded = json.dumps(jsoncodec.encode(result), allow_nan=False).encode("utf-8")
            campaign._atomic_create(path, encoded)
            campaign._write_hash_create(path, encoded)
        return manifest_path

    def test_aggregate_pools_actual_records_for_exact_reliability_bound(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest_path = self._aggregate_fixture(Path(temporary))
            result = campaign.aggregate(manifest_path)

        self.assertEqual(result["overall"], "pass")
        self.assertEqual(result["pooled_reliability"]["wf-sdk"], {
            "observations": 2995,
            "submitted": 2995,
            "accepted": 2995,
            "completed": 2995,
            "failures": 0,
            "invalid_records": 0,
            "upper_95": ANY,
            "limit": 0.001, "status": "pass",
        })
        self.assertLessEqual(result["pooled_reliability"]["wf-sdk"]["upper_95"], 0.001)

    def test_aggregate_preserves_failure_precedence_over_insufficient_pooling(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest_path = self._aggregate_fixture(Path(temporary), count=1, statuses={0: "fail"})
            result = campaign.aggregate(manifest_path)

        self.assertEqual(result["overall"], "fail")
        self.assertEqual(result["pooled_reliability"]["wf-sdk"]["status"], "inconclusive")

    def test_aggregate_uses_inconclusive_for_only_insufficient_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest_path = self._aggregate_fixture(Path(temporary), count=1)
            result = campaign.aggregate(manifest_path)

        self.assertEqual(result["overall"], "inconclusive")

    def test_aggregate_marks_tampered_segment_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = self._aggregate_fixture(root)
            (root / "segments" / "segment-3.json").write_text("{}", encoding="utf-8")
            result = campaign.aggregate(manifest_path)

        self.assertEqual(result["overall"], "invalid")
        self.assertEqual(result["segments"][3]["status"], "invalid")

    def test_aggregate_rejects_correct_work_without_a_send_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = self._aggregate_fixture(root)
            path = root / "segments" / "segment-0.json"
            result = jsoncodec.decode(json.loads(path.read_text(encoding="utf-8")))
            result["step"]["records"][0]["sent_s"] = None
            contents = json.dumps(jsoncodec.encode(result), allow_nan=False).encode("utf-8")
            path.write_bytes(contents)
            campaign._hash_path(path).write_text(f"{campaign._sha256_bytes(contents)}\n", encoding="ascii")
            aggregate = campaign.aggregate(manifest_path)

        self.assertEqual(aggregate["overall"], "invalid")
        self.assertEqual(aggregate["pooled_reliability"]["wf-sdk"]["invalid_records"], 1)

    def test_aggregate_rejects_reused_request_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = self._aggregate_fixture(root)
            first = root / "segments" / "segment-0.json"
            second = root / "segments" / "segment-1.json"
            first_result = jsoncodec.decode(json.loads(first.read_text(encoding="utf-8")))
            second_result = jsoncodec.decode(json.loads(second.read_text(encoding="utf-8")))
            old_id = second_result["step"]["sent"][0]["id"]
            reused_id = first_result["step"]["sent"][0]["id"]
            second_result["step"]["sent"][0]["id"] = reused_id
            second_result["step"]["records"][0]["id"] = reused_id
            durable = second_result["step"]["outcomes"]["outcomes"]
            durable[reused_id] = durable.pop(old_id)
            durable[reused_id]["request_id"] = reused_id
            second_result["step"]["outcomes"]["rows"][0]["request_id"] = reused_id
            contents = json.dumps(jsoncodec.encode(second_result), allow_nan=False).encode("utf-8")
            second.write_bytes(contents)
            campaign._hash_path(second).write_text(f"{campaign._sha256_bytes(contents)}\n", encoding="ascii")
            aggregate = campaign.aggregate(manifest_path)

        self.assertEqual(aggregate["overall"], "invalid")
        self.assertEqual(aggregate["segments"][1]["status"], "invalid")

    def test_aggregate_rejects_status_mismatch_or_unverified_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = self._aggregate_fixture(root)
            path = root / "segments" / "segment-2.json"
            result = jsoncodec.decode(json.loads(path.read_text(encoding="utf-8")))
            result["status"] = "fail"
            result["cleanup"] = {"verified": False}
            contents = json.dumps(jsoncodec.encode(result), allow_nan=False).encode("utf-8")
            path.write_bytes(contents)
            campaign._hash_path(path).write_text(f"{campaign._sha256_bytes(contents)}\n", encoding="ascii")
            aggregate = campaign.aggregate(manifest_path)

        self.assertEqual(aggregate["overall"], "invalid")
        self.assertEqual(aggregate["segments"][2]["status"], "invalid")

    def test_cli_exposes_only_frozen_campaign_inputs(self) -> None:
        cluster = FakeCluster()
        with patch.object(run, "Cluster", return_value=cluster), \
             patch.object(campaign, "create_manifest", return_value=Path("/tmp/manifest")) as create, \
             patch.object(
                 sys,
                 "argv",
                 [
                     "run.py", "campaign-init", "--profile", "m-capacity", "--api-image", self.image,
                     "--out", "/tmp/out",
                 ],
             ):
            run.main()
        create.assert_called_once_with(cluster, "m-capacity", self.image, Path("/tmp/out"))

        with patch.object(campaign, "run_preflight", return_value=Path("/tmp/preflight")) as preflight_run, \
             patch.object(sys, "argv", ["run.py", "campaign-preflight", "--manifest", "/tmp/manifest"]):
            run.main()
        preflight_run.assert_called_once_with(Path("/tmp/manifest"))

        with patch.object(campaign, "run_segment", return_value=Path("/tmp/segment")) as segment, \
             patch.object(sys, "argv", ["run.py", "confirm-segment", "--manifest", "/tmp/manifest", "--index", "4"]):
            run.main()
        segment.assert_called_once_with(Path("/tmp/manifest"), 4)

        with patch.object(
            campaign,
            "aggregate",
            return_value={"kind": "mixed-confirmation-campaign", "overall": "inconclusive"},
        ) as aggregate, \
             patch.object(sys, "argv", ["run.py", "campaign-aggregate", "--manifest", "/tmp/manifest"]):
            run.main()
        aggregate.assert_called_once_with(Path("/tmp/manifest"))


if __name__ == "__main__":
    unittest.main()
