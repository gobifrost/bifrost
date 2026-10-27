from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cluster  # noqa: E402

SUMMARY = {
    "pods": [
        {
            "podRef": {"name": "bifrost-api-abc-1", "namespace": "bifrost-local", "uid": "u1"},
            "containers": [{
                "name": "api",
                "memory": {"workingSetBytes": 400 * 1024 * 1024, "rssBytes": 380 * 1024 * 1024},
                "cpu": {"usageNanoCores": 25_000_000},
            }],
        },
        {
            "podRef": {"name": "bifrost-worker-9f-x", "namespace": "bifrost-local", "uid": "u2"},
            "containers": [{"name": "worker", "memory": {"workingSetBytes": 85 * 1024 * 1024}, "cpu": {}}],
        },
        {
            "podRef": {"name": "coredns-1", "namespace": "kube-system", "uid": "u3"},
            "containers": [{"name": "coredns", "memory": {"workingSetBytes": 1}, "cpu": {}}],
        },
    ]
}


class StatsSummaryTest(unittest.TestCase):
    def test_only_namespace_containers_with_roles(self) -> None:
        rows = cluster.parse_stats_summary(SUMMARY, "bifrost-local")
        self.assertEqual([row["pod"] for row in rows], ["bifrost-api-abc-1", "bifrost-worker-9f-x"])
        api, worker = rows
        self.assertEqual(api["role"], "api")
        self.assertEqual(api["working_set_mib"], 400.0)
        self.assertEqual(api["rss_mib"], 380.0)
        self.assertEqual(api["cpu_millicores"], 25.0)
        self.assertEqual(worker["role"], "worker")
        self.assertEqual(worker["rss_mib"], 0.0)

    def test_roles(self) -> None:
        self.assertEqual(cluster.role_for_pod("bench-driver"), "bench")
        self.assertEqual(cluster.role_for_pod("postgres-0"), "infra")


if __name__ == "__main__":
    unittest.main()
