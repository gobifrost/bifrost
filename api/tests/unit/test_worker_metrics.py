"""Tests for worker metrics persistence and heartbeat enhancement."""

from unittest.mock import patch
from datetime import datetime, timezone


class TestHeartbeatCgroupData:
    """Tests for cgroup memory data in heartbeat payload."""

    def test_heartbeat_includes_cgroup_memory(self):
        """Heartbeat should include memory_current_bytes and memory_max_bytes."""
        from src.services.execution.process_pool import ProcessPoolManager

        pool = ProcessPoolManager.__new__(ProcessPoolManager)
        pool.worker_id = "test-worker"
        pool.processes = {}
        pool.max_workers = 10
        pool._started_at = datetime.now(timezone.utc)
        pool._requirements_installed = 0
        pool._requirements_total = 0
        pool.heartbeat_interval_seconds = 10

        with patch(
            "src.services.execution.process_pool.get_cgroup_memory",
            return_value=(4_000_000_000, 8_000_000_000),
        ):
            heartbeat = pool._build_heartbeat()

        assert heartbeat["memory_current_bytes"] == 4_000_000_000
        assert heartbeat["memory_max_bytes"] == 8_000_000_000

    def test_heartbeat_cgroup_unavailable(self):
        """When cgroup is unavailable, heartbeat should have -1 values."""
        from src.services.execution.process_pool import ProcessPoolManager

        pool = ProcessPoolManager.__new__(ProcessPoolManager)
        pool.worker_id = "test-worker"
        pool.processes = {}
        pool.max_workers = 10
        pool._started_at = datetime.now(timezone.utc)
        pool._requirements_installed = 0
        pool._requirements_total = 0
        pool.heartbeat_interval_seconds = 10

        with patch(
            "src.services.execution.process_pool.get_cgroup_memory",
            return_value=(-1, -1),
        ):
            heartbeat = pool._build_heartbeat()

        assert heartbeat["memory_current_bytes"] == -1
        assert heartbeat["memory_max_bytes"] == -1

    def test_heartbeat_cgroup_no_limit(self):
        """When container has no memory limit, current should be reported and max should be -1."""
        from src.services.execution.process_pool import ProcessPoolManager

        pool = ProcessPoolManager.__new__(ProcessPoolManager)
        pool.worker_id = "test-worker"
        pool.processes = {}
        pool.max_workers = 10
        pool._started_at = datetime.now(timezone.utc)
        pool._requirements_installed = 0
        pool._requirements_total = 0
        pool.heartbeat_interval_seconds = 10

        with patch(
            "src.services.execution.process_pool.get_cgroup_memory",
            return_value=(322_834_432, -1),
        ):
            heartbeat = pool._build_heartbeat()

        assert heartbeat["memory_current_bytes"] == 322_834_432
        assert heartbeat["memory_max_bytes"] == -1

    def test_fork_memory_mb_uses_private_dirty(self):
        """Per-fork memory_mb should reflect private-dirty (USS), not COW-inflated RSS."""
        from src.services.execution.process_pool import ProcessPoolManager, ProcessState

        pool = ProcessPoolManager.__new__(ProcessPoolManager)
        pool.worker_id = "test-worker"
        pool._started_at = datetime.now(timezone.utc)
        pool._requirements_installed = 0
        pool._requirements_total = 0
        pool.heartbeat_interval_seconds = 10

        fake_proc = type(
            "P",
            (),
            {
                "pid": 1234,
                "id": "p1",
                "state": ProcessState.BUSY,
                "uptime_seconds": 1.0,
                "executions_completed": 0,
                "current_execution": None,
            },
        )()
        pool.processes = {"p1": fake_proc}

        with (
            patch(
                "src.services.execution.process_pool.get_cgroup_memory",
                return_value=(-1, -1),
            ),
            patch(
                "src.services.execution.process_pool._get_private_dirty_kb",
                return_value=65_536,  # 64 MB
            ),
        ):
            heartbeat = pool._build_heartbeat()

        assert heartbeat["processes"][0]["private_dirty_kb"] == 65_536
        assert heartbeat["processes"][0]["memory_mb"] == 64.0

    def test_fork_memory_mb_falls_back_to_rss(self):
        """When private-dirty is unavailable, fall back to RSS via psutil."""
        from src.services.execution.process_pool import ProcessPoolManager, ProcessState

        pool = ProcessPoolManager.__new__(ProcessPoolManager)
        pool.worker_id = "test-worker"
        pool._started_at = datetime.now(timezone.utc)
        pool._requirements_installed = 0
        pool._requirements_total = 0
        pool.heartbeat_interval_seconds = 10

        fake_proc = type(
            "P",
            (),
            {
                "pid": 1234,
                "id": "p1",
                "state": ProcessState.BUSY,
                "uptime_seconds": 1.0,
                "executions_completed": 0,
                "current_execution": None,
            },
        )()
        pool.processes = {"p1": fake_proc}

        with (
            patch(
                "src.services.execution.process_pool.get_cgroup_memory",
                return_value=(-1, -1),
            ),
            patch(
                "src.services.execution.process_pool._get_private_dirty_kb",
                return_value=-1,
            ),
            patch.object(
                ProcessPoolManager, "_get_process_memory", return_value=123.0
            ),
        ):
            heartbeat = pool._build_heartbeat()

        assert heartbeat["processes"][0]["memory_mb"] == 123.0


class TestMetricsDownsampling:
    """Tests for metrics query downsampling."""

    def test_range_to_timedelta(self):
        """Verify range string parsing."""
        from datetime import timedelta

        range_map = {
            "1h": timedelta(hours=1),
            "6h": timedelta(hours=6),
            "24h": timedelta(hours=24),
            "7d": timedelta(days=7),
        }
        for range_str, expected in range_map.items():
            unit = range_str[-1]
            value = int(range_str[:-1])
            if unit == "h":
                result = timedelta(hours=value)
            else:
                result = timedelta(days=value)
            assert result == expected
