from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import run  # noqa: E402


class FakeCluster:
    image = "bifrost-api:lane-test"

    def __init__(self, *, fail_reserved: bool = False) -> None:
        self.fail_reserved = fail_reserved
        self.requests: list[dict] = []
        self.profile: dict = {}

    def apply_profile(self, profile: dict) -> None:
        self.profile = profile

    def container_stats(self) -> list[dict]:
        return [{"role": "worker", "working_set_mib": 100}]

    def exec_python(self, pod: str, script: Path, args: list[str], timeout_s: int) -> dict:
        assert pod == "bench-driver" and args[0] == "lane_mix"
        request = json.loads(args[1])
        self.requests.append(request)
        if self.fail_reserved and request["reserved"]:
            raise RuntimeError("Reserved capacity did not become ready")
        record = {
            "id": "sample", "status": "Success",
            "created_at": "2026-11-09T00:00:00Z",
            "started_at": "2026-11-09T00:00:00.500Z",
            "completed_at": "2026-11-09T00:00:01Z",
        }
        return {"raw": {kind: [record] for kind in ("no_background", "mixed_short", "background")}}


class LaneComparisonTest(unittest.TestCase):
    def run_comparison(self, cluster: FakeCluster, output: Path) -> None:
        with (
            patch.object(run, "Cluster", return_value=cluster),
            patch.object(run, "apply_bench_resources"),
            patch.object(run, "_git_sha", return_value="abc-dirty"),
            patch.object(run, "_image_id", return_value="sha256:lane-test"),
        ):
            run.run_lanes(output)

    def test_reports_both_allocations_from_raw_platform_timestamps(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            cluster = FakeCluster()
            self.run_comparison(cluster, output)
            result = json.loads((output / "result.json").read_text())
            self.assertEqual(cluster.profile["worker_policy"]["max_workers"], 2)
            self.assertEqual(cluster.requests, [{"reserved": 0}, {"reserved": 1}])
            for variant in result["variants"]:
                self.assertEqual(variant["timings"]["mixed_short"]["queue_ms"]["p99"], 500)
                self.assertEqual(variant["timings"]["mixed_short"]["e2e_ms"]["p95"], 1000)
            report = (output / "report.md").read_text()
            self.assertIn("Shared | mixed_short", report)
            self.assertIn("Reserved | mixed_short", report)
            self.assertIn("not actual RAM/CPU pressure", report)

    def test_failure_preserves_completed_variant_and_error_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            with self.assertRaisesRegex(RuntimeError, "capacity did not become ready"):
                self.run_comparison(FakeCluster(fail_reserved=True), output)
            result = json.loads((output / "result.json").read_text())
            self.assertEqual(len(result["variants"]), 1)
            self.assertIn("Reserved capacity", result["error"])
            self.assertFalse((output / "report.md").exists())


if __name__ == "__main__":
    unittest.main()
