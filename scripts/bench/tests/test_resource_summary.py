from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import resource_summary  # noqa: E402
import search  # noqa: E402


def pod(cpu: float, memory: int) -> dict:
    return {"cpu_cores": cpu, "throttled_ratio": 0.25,
            "memory_bytes": {"current": memory, "working_set": memory - 1}}


class ResourceSummaryTest(unittest.TestCase):
    def test_component_cpu_is_time_weighted_and_role_memory_is_simultaneous(self) -> None:
        usage = resource_summary.summarize([
            {"at": 105, "seconds": 5, "pods": {"bench-tenant/bifrost-api-a@uid": pod(20, 100)}},
            {"at": 115, "seconds": 5, "pods": {
                "bench-tenant/bifrost-worker-a@uid1": pod(0.5, 30),
                "bench-tenant/bifrost-worker-b@uid2": pod(0.5, 10),
                "bench-infra/bench-driver@uid": pod(40, 10000)}},
            {"at": 120, "seconds": 5, "pods": {
                "bench-tenant/bifrost-worker-a@uid1": pod(0.1, 10),
                "bench-tenant/bifrost-worker-b@uid2": pod(0.1, 40)}},
        ], 100, (10, 20))
        self.assertEqual(set(usage["roles"]), {"worker"})
        worker = usage["roles"]["worker"]
        self.assertAlmostEqual(worker["mean_cpu_cores_observed"], 0.6)
        self.assertEqual(worker["charged_peak_bytes"], 50)
        self.assertEqual(worker["observed_window_fraction"], 1)
        self.assertEqual(worker["max_interval_cpu_cores"], 1)

    def test_missing_memory_and_partial_sampling_remain_explicit(self) -> None:
        usage = resource_summary.summarize([
            {"at": 115, "seconds": 5, "pods": {"bench-tenant/redis-0@uid": {"cpu_cores": 0.1, "throttled_ratio": 0}}},
        ], 100, (10, 20))["roles"]["redis"]
        self.assertIsNone(usage["charged_peak_bytes"])
        self.assertEqual(usage["observed_window_fraction"], 0.5)


class HostSamplerTest(unittest.TestCase):
    def test_sampler_exception_invalidates_the_run_and_reaps_thread(self) -> None:
        cluster = Mock()
        cluster.external_nodes.return_value = []
        with patch.object(search.hostmetrics, "node_cgroup", return_value=Path("/unused")), \
             patch.object(search.HostSampler, "_pods", side_effect=OSError("cgroup unavailable")):
            sampler = search.HostSampler(cluster, {})
            with self.assertRaisesRegex(RuntimeError, "sampler failed"):
                with sampler:
                    pass
        self.assertFalse(sampler._thread.is_alive())

    def test_same_name_replacement_keeps_distinct_counter_identity(self) -> None:
        cluster = Mock()
        cluster.external_nodes.return_value = []
        with patch.object(search.hostmetrics, "node_cgroup", return_value=Path("/unused")), \
             patch.object(search.hostmetrics, "pod_cgroup", return_value=Path("/pod")):
            sampler = search.HostSampler(cluster, {})
            cluster.bench_pods.return_value = [("bench-tenant", "redis-0", "first")]
            first = sampler._pods()
            cluster.bench_pods.return_value = [("bench-tenant", "redis-0", "second")]
            second = sampler._pods()
        self.assertFalse(first.keys() & second.keys())


if __name__ == "__main__":
    unittest.main()
