from __future__ import annotations

import sys
import unittest
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import hostmetrics  # noqa: E402


def _interval(external_cpu: float = 0.2, reclaim: int = 0, pressure: float = 0.0, pod_events: int = 0, local_events: int = 0,
              infra_throttle: float = 0.0, external_io: float = 0.0, disk_bytes: int = 100_000_000) -> dict:
    return {
        "seconds": 5.0, "external_cpu_cores": external_cpu, "disk_bytes": disk_bytes, "disk_ios": disk_bytes // 10_000,
        "external_io_bytes_share": external_io, "external_io_ios_share": external_io,
        "nodes": {"bench": {"reclaim_pages": reclaim, "memory_pressure_pct": pressure, "local_limit_events": local_events}},
        "pods": {
            "bench-tenant/api": {"limit_events": pod_events, "throttled_ratio": 0.5},
            "bench-infra/bench-driver": {"limit_events": 0, "throttled_ratio": infra_throttle},
        },
    }


INFRA = {"bench-infra/bench-driver"}


class ParseTest(unittest.TestCase):
    def test_parsers(self) -> None:
        self.assertEqual(hostmetrics.parse_flat("usage_usec 10\nnr_throttled 2\n"), {"usage_usec": 10, "nr_throttled": 2})
        self.assertEqual(
            hostmetrics.parse_pressure("some avg10=0.00 avg60=0.00 avg300=0.00 total=18\nfull avg10=0.00 avg60=0.00 avg300=0.00 total=9\n"),
            {"some": 18, "full": 9},
        )
        self.assertEqual(hostmetrics.parse_io_stat("259:0 rbytes=10 wbytes=5 rios=1 wios=2 dbytes=0 dios=0\n252:0 rbytes=10 wbytes=5 rios=1 wios=2\n", "259:0"),
                         {"bytes": 15, "ios": 3})
        self.assertEqual(hostmetrics.parse_proc_stat("cpu  10 0 5 80 5 0 0 0 0 0\ncpu0 1 0 0 0\n"), {"busy": 15, "total": 100})
        line = " 259 0 nvme0n1 10 0 100 7 20 0 300 9 0 0 0 0 0 0 0 0 0\n 259 1 nvme0n1p1 1 0 1 1 1 0 1 1 0 0 0\n"
        self.assertEqual(hostmetrics.parse_diskstats(line, "nvme0n1"), {"bytes": 400 * 512, "ios": 30, "io_ms": 16})

    def test_memory_charge_keeps_reclaimable_cache_separate_from_working_set(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "cpu.stat").write_text("usage_usec 100\n")
            (root / "memory.events").write_text("oom_kill 0\n")
            (root / "memory.current").write_text("1000\n")
            (root / "memory.peak").write_text("1500\n")
            (root / "memory.stat").write_text(
                "anon 500\nfile 400\nshmem 100\nkernel 100\ninactive_file 300\n"
            )
            counters = hostmetrics.cgroup_counters(root)
        self.assertEqual(counters["memory"], {
            "current": 1000, "lifetime_peak": 1500, "working_set": 700,
            "anon": 500, "file": 400, "shmem": 100, "kernel": 100,
        })


class AssessTest(unittest.TestCase):
    def test_quiet_window_is_valid(self) -> None:
        self.assertEqual(hostmetrics.assess([_interval()] * 10, INFRA), {"invalid": [], "warnings": []})

    def test_tenant_throttling_is_a_result_not_contention(self) -> None:
        self.assertEqual(hostmetrics.assess([_interval(pod_events=3)] * 10, INFRA)["invalid"], [])

    def test_sustained_external_cpu_invalidates(self) -> None:
        intervals = [_interval(external_cpu=3.0)] * 2 + [_interval()] * 8
        self.assertIn("external CPU", hostmetrics.assess(intervals, INFRA)["invalid"][0])
        self.assertEqual(hostmetrics.assess([_interval(external_cpu=3.0)] + [_interval()] * 9, INFRA)["invalid"], [])

    def test_memory_pressure_needs_corroboration(self) -> None:
        both = hostmetrics.assess([_interval(reclaim=50, pressure=5.0)] * 3, INFRA)
        self.assertEqual(both["invalid"], ["memory pressure from above the benchmark's limits"])
        for single in (_interval(reclaim=50), _interval(pressure=5.0)):
            result = hostmetrics.assess([single] * 3, INFRA)
            self.assertEqual(result["invalid"], [])
            self.assertEqual(result["warnings"], ["possible external memory pressure (unproven)"])

    def test_pod_limit_events_explain_reclaim(self) -> None:
        self.assertEqual(hostmetrics.assess([_interval(reclaim=50, pressure=5.0, pod_events=1)] * 3, INFRA), {"invalid": [], "warnings": []})

    def test_node_or_ancestor_limit_pressure_invalidates_when_the_benchmark_is_affected(self) -> None:
        self.assertEqual(hostmetrics.assess([_interval(reclaim=50, local_events=1)] * 3, INFRA)["invalid"],
                         ["memory limit pressure at the Kind node or an ancestor"])
        self.assertEqual(hostmetrics.assess([_interval(local_events=1)] * 3, INFRA), {"invalid": [], "warnings": []})

    def test_infra_saturation_and_external_io_invalidate(self) -> None:
        self.assertEqual(hostmetrics.assess([_interval(infra_throttle=0.2)], INFRA)["invalid"], ["infrastructure pod bench-infra/bench-driver saturated"])
        self.assertEqual(hostmetrics.assess([_interval(external_io=0.5)], INFRA)["invalid"], ["external disk I/O above its share of the window"])

    def test_trivial_external_io_does_not_invalidate_a_quiet_window(self) -> None:
        self.assertEqual(hostmetrics.assess([_interval(external_io=0.9, disk_bytes=2_000_000)], INFRA)["invalid"], [])


if __name__ == "__main__":
    unittest.main()
