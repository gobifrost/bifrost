"""
Unit tests for memory_monitor module.
"""

from unittest.mock import patch, mock_open

from src.services.execution.memory_monitor import (
    effective_memory_sample,
    get_cgroup_memory,
    memory_pressure_ratio,
)


class TestGetCgroupMemory:
    """Tests for cgroup v2 memory reading (working-set semantics)."""

    # Realistic memory.stat excerpt — order intentionally interleaved.
    STAT_DATA = (
        "anon 400000000\n"
        "file 500000000\n"
        "kernel_stack 1048576\n"
        "active_anon 0\n"
        "inactive_anon 400000000\n"
        "active_file 100000000\n"
        "inactive_file 400000000\n"
    )

    def test_returns_anon_plus_active_file_as_working_set(self):
        """Should sum anon + active_file (kubelet working-set), excluding inactive_file."""
        with patch("builtins.open", side_effect=[
            mock_open(read_data=self.STAT_DATA)(),
            mock_open(read_data="1073741824\n")(),   # memory.max = 1GB
        ]):
            with patch("pathlib.Path.exists", return_value=True):
                current, limit = get_cgroup_memory()
                # 400MB anon + 100MB active_file = 500MB working set,
                # NOT 900MB (which would include the 400MB inactive_file cache).
                assert current == 500_000_000
                assert limit == 1073741824

    def test_returns_negative_when_cgroup_files_missing(self):
        """Should return (-1, -1) when cgroup files don't exist."""
        with patch("pathlib.Path.exists", return_value=False):
            current, limit = get_cgroup_memory()
            assert current == -1
            assert limit == -1

    def test_returns_current_with_negative_limit_when_memory_max_is_max(self):
        """Should return (working_set, -1) when memory.max is 'max' (no limit set)."""
        with patch("builtins.open", side_effect=[
            mock_open(read_data=self.STAT_DATA)(),
            mock_open(read_data="max\n")(),
        ]):
            with patch("pathlib.Path.exists", return_value=True):
                current, limit = get_cgroup_memory()
                assert current == 500_000_000
                assert limit == -1

    def test_handles_read_error_gracefully(self):
        """Should return (-1, -1) on read failure."""
        with patch("pathlib.Path.exists", return_value=True):
            with patch("builtins.open", side_effect=OSError("Permission denied")):
                current, limit = get_cgroup_memory()
                assert current == -1
                assert limit == -1

    def test_returns_negative_when_stat_missing_required_keys(self):
        """Should return (-1, -1) when memory.stat lacks anon or active_file."""
        # Only `anon`, no `active_file` — pre-v2-style stat or oddball runtime.
        bad_stat = "anon 400000000\nfile 500000000\n"
        with patch("builtins.open", mock_open(read_data=bad_stat)):
            with patch("pathlib.Path.exists", return_value=True):
                current, limit = get_cgroup_memory()
                assert current == -1
                assert limit == -1


def test_effective_sample_uses_host_limit_for_an_unbounded_cgroup(monkeypatch):
    monkeypatch.setattr(
        "src.services.execution.memory_monitor.get_cgroup_memory", lambda: (123, -1)
    )
    monkeypatch.setattr(
        "src.services.execution.memory_monitor.host_memory_bytes", lambda: 456
    )

    assert effective_memory_sample() == (123, 456)


def test_unbounded_pressure_uses_host_used_memory_not_container_working_set(monkeypatch):
    monkeypatch.setattr(
        "src.services.execution.memory_monitor.get_cgroup_memory", lambda: (123, -1)
    )
    meminfo = "MemTotal: 100 kB\nMemAvailable: 25 kB\n"
    with patch("builtins.open", mock_open(read_data=meminfo)):
        assert memory_pressure_ratio() == .75
