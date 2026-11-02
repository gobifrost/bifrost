from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import deploy  # noqa: E402
import envelope  # noqa: E402
import run  # noqa: E402


MIB = 1024**2


class HostingSizingProfileTest(unittest.TestCase):
    """Benchmark-only hosting arms keep one full workload and gate contract."""

    profiles = {
        "hosting-4gb-fixed-workers-1": {"memory": "4G", "workers": 1, "warm": 1, "worker_cpu": "500m", "worker_mib": 512},
        "hosting-4gb-fixed-workers-2": {"memory": "4G", "workers": 2, "warm": 2, "worker_cpu": "500m", "worker_mib": 512},
        "hosting-4gb-elastic-warm0-max2": {"memory": "4G", "workers": 2, "warm": 0, "worker_cpu": "500m", "worker_mib": 512},
        "hosting-4gb-elastic-warm1-max2": {"memory": "4G", "workers": 2, "warm": 1, "worker_cpu": "500m", "worker_mib": 512},
        "hosting-8gib-fixed-workers-1": {"memory": "8Gi", "workers": 1, "warm": 1, "worker_cpu": "250m", "worker_mib": 1024},
        "hosting-8gib-fixed-workers-2": {"memory": "8Gi", "workers": 2, "warm": 2, "worker_cpu": "250m", "worker_mib": 1024},
        "hosting-8gib-fixed-workers-4": {"memory": "8Gi", "workers": 4, "warm": 4, "worker_cpu": "250m", "worker_mib": 1024},
        "hosting-8gib-fixed-workers-2-cpu500m-bridge": {"memory": "8Gi", "workers": 2, "warm": 2, "worker_cpu": "500m", "worker_mib": 1024},
        "hosting-8gib-elastic-warm0-max4": {"memory": "8Gi", "workers": 4, "warm": 0, "worker_cpu": "250m", "worker_mib": 1024},
        "hosting-8gib-elastic-warm1-max4": {"memory": "8Gi", "workers": 4, "warm": 1, "worker_cpu": "250m", "worker_mib": 1024},
    }

    def test_profiles_keep_the_full_capacity_workload_and_gates(self) -> None:
        reference = run.load_profile("m-capacity")
        retained = (
            "capacity", "seed", "classes", "sequential", "burst", "burst_rounds",
            "timeout_s", "settle_s", "idle_s", "idle_db_s", "fake_llm_latency_ms",
            "env", "table_docs",
        )
        for name in self.profiles:
            with self.subTest(profile=name):
                profile = run.load_profile(name)
                for key in retained:
                    self.assertEqual(profile[key], reference[key], key)

    def test_profiles_declare_the_requested_components_and_worker_policy(self) -> None:
        expected_common = {
            "api": {"replicas": 2, "cpu": "750m", "memory": "512Mi"},
            "client": {"replicas": 1, "cpu": "50m", "memory": "64Mi"},
            "redis": {"cpu": "100m", "memory": "64Mi"},
            "pooler": {"cpu": "100m", "memory": "128Mi"},
        }
        for name, expected in self.profiles.items():
            with self.subTest(profile=name):
                profile = run.load_profile(name)
                self.assertEqual(profile["quota"]["cpu"], "4")
                self.assertEqual(profile["quota"]["memory"], expected["memory"])
                for component, values in expected_common.items():
                    self.assertEqual(profile["components"][component], values)
                self.assertEqual(profile["components"]["postgres"], {
                    "cpu": "750m",
                    "memory": "768Mi" if expected["memory"] == "4G" else "1536Mi",
                    "storage": "20Gi",
                })
                self.assertEqual(profile["worker_cpu"], expected["worker_cpu"])
                self.assertEqual(profile["worker_policy"], {
                    "warm_workers": expected["warm"],
                    "max_workers": expected["workers"],
                    "worker_memory_mib": expected["worker_mib"],
                    "largest_worker_mib": expected["worker_mib"],
                    "idle_seconds": 300,
                })

    def test_patched_api_and_pooler_resources_match_the_profile_effective_requests(self) -> None:
        for name in self.profiles:
            with self.subTest(profile=name):
                profile = run.load_profile(name)
                patches = deploy.tenant_patches(profile, "127.0.0.1")
                by_target = {tuple(sorted(patch["target"].items())): json.loads(patch["patch"]) for patch in patches}
                api = by_target[(("kind", "Deployment"), ("name", "bifrost-api"))]
                api_resources = [operation["value"] for operation in api if operation["path"].endswith("/resources")]
                expected_api = {
                    "requests": {"cpu": "750m", "memory": "512Mi"},
                    "limits": {"cpu": "750m", "memory": "512Mi"},
                }
                self.assertEqual(api_resources, [expected_api, expected_api])
                pooler = by_target[(("kind", "Pooler"), ("name", "bifrost-db-pooler"))]
                self.assertEqual(pooler, [{
                    "op": "replace", "path": "/spec/template/spec/containers/0/resources", "value": {
                        "requests": {"cpu": "100m", "memory": "128Mi"},
                        "limits": {"cpu": "100m", "memory": "128Mi"},
                    },
                }])
                self.assertEqual(deploy.fixed_components(profile)["pooler"], envelope.Resources(100, 128 * MIB))

    def test_maximum_occupancy_fits_with_required_headroom(self) -> None:
        for name, expected in self.profiles.items():
            with self.subTest(profile=name):
                profile = run.load_profile(name)
                occupancy = envelope.max_occupancy(
                    deploy.fixed_components(profile), deploy.worker_resources(profile), expected["workers"],
                )
                headroom = envelope.check_fits(occupancy, {
                    "requests.cpu": profile["quota"]["cpu"], "limits.cpu": profile["quota"]["cpu"],
                    "requests.memory": profile["quota"]["memory"], "limits.memory": profile["quota"]["memory"],
                })
                self.assertGreaterEqual(headroom.cpu_m, 500)
                self.assertGreaterEqual(headroom.memory, (512 if expected["memory"] == "4G" else 1024) * MIB)
                deploy.check_profile(profile)

    def test_bridge_matches_the_regular_four_worker_cpu_total(self) -> None:
        regular = run.load_profile("hosting-8gib-fixed-workers-4")
        bridge = run.load_profile("hosting-8gib-fixed-workers-2-cpu500m-bridge")
        self.assertEqual(
            envelope.parse_cpu(regular["worker_cpu"]) * regular["worker_policy"]["max_workers"],
            envelope.parse_cpu(bridge["worker_cpu"]) * bridge["worker_policy"]["max_workers"],
        )


if __name__ == "__main__":
    unittest.main()
