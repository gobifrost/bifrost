from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import queue_position_causal  # noqa: E402
import resource_curve  # noqa: E402


class QueuePositionCausalTest(unittest.TestCase):
    def test_selected_n2_profile_preserves_the_control_resources(self) -> None:
        profile = resource_curve.profile_for(queue_position_causal.COUNTS[0], queue_position_causal.BASE_PROFILE)

        self.assertEqual(profile["worker_policy"], {
            "warm_workers": 2, "max_workers": 2, "worker_memory_mib": 1024,
            "largest_worker_mib": 1024, "idle_seconds": 300,
        })
        self.assertEqual(profile["worker_cpu"], "650m")
        self.assertEqual(profile["components"]["api"], {"replicas": 2, "cpu": "750m", "memory": "512Mi"})
        self.assertEqual(profile["components"]["redis"], {"cpu": "100m", "memory": "64Mi"})
        self.assertEqual(profile["components"]["postgres"], {"cpu": "750m", "memory": "1536Mi", "storage": "20Gi"})
        self.assertEqual(
            {key: profile["capacity"][key] for key in ("warmup_s", "window_min_s", "drain_s")},
            {"warmup_s": 30, "window_min_s": 300, "drain_s": 60},
        )

    def test_delegates_exact_n2_ramp_to_the_canonical_runner_and_lock(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            owner, fixture, out, lock_path = (root / name for name in ("owner", "fixture.py", "out", "host.lock"))
            owner.mkdir()
            fixture.write_text("# fixture", encoding="utf-8")
            campaign = Mock(return_value=out)
            flock = Mock()
            with patch.object(resource_curve, "FROZEN_IMAGE", "other-image"), \
                 patch.object(resource_curve, "RATES", (16.0,)), \
                 patch.object(resource_curve, "run_campaign", campaign), \
                 patch.object(resource_curve.elasticity, "LOCK_PATH", lock_path), \
                 patch.object(resource_curve.fcntl, "flock", flock), \
                 patch.object(sys, "argv", [
                     "queue_position_causal.py", "--owner-root", str(owner), "--out", str(out),
                     "--table-fixture", str(fixture),
                 ]):
                queue_position_causal.main()
                campaign.assert_called_once_with(
                    queue_position_causal.CANDIDATE_IMAGE, out, owner_root=owner, ensure_fixture=fixture,
                    base_profile="hosting-8gib-fixed-workers-1", counts=(2,),
                )
                self.assertEqual(resource_curve.FROZEN_IMAGE, queue_position_causal.CANDIDATE_IMAGE)
                self.assertEqual(resource_curve.RATES, (2.0, 4.0, 8.0))
                self.assertTrue(lock_path.exists())
                self.assertEqual(flock.call_args.args[1], resource_curve.fcntl.LOCK_EX | resource_curve.fcntl.LOCK_NB)

    def test_rejects_an_image_override(self) -> None:
        with patch.object(sys, "argv", [
            "queue_position_causal.py", "--owner-root", "/owner", "--out", "/out", "--table-fixture", "/fixture",
            "--api-image", "other-image",
        ]):
            with self.assertRaises(SystemExit):
                queue_position_causal.main()


if __name__ == "__main__":
    unittest.main()
