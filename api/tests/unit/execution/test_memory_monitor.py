"""Shared cgroup accounting and its current admission consumers."""

from pathlib import Path
from unittest.mock import mock_open, patch

import pytest

from src.services.execution import memory_monitor
from src.services.execution.memory_monitor import (
    effective_memory_sample,
    get_cgroup_memory,
    memory_pressure_ratio,
)
from src.services.memory_requests import measure_budget, under_pressure


@pytest.fixture
def cgroup(tmp_path, monkeypatch):
    """Read actual counter files without depending on the test container's cgroup."""
    monkeypatch.setattr(memory_monitor, "Path", lambda path: tmp_path / Path(path).name)
    (tmp_path / "memory.current").write_text("950000000\n")
    (tmp_path / "memory.stat").write_text(
        "anon 400000000\nfile 500000000\nkernel 50000000\n"
        "active_file 100000000\ninactive_file 400000000\n"
    )
    (tmp_path / "memory.max").write_text("1000000000\n")
    return tmp_path


def test_working_set_retains_charges_beyond_anon_and_active_file(cgroup):
    # 950 MB total - 400 MB inactive cache = 550 MB, not the old 500 MB.
    assert get_cgroup_memory() == (550_000_000, 1_000_000_000)


@pytest.mark.parametrize("missing", ["memory.current", "memory.stat"])
def test_missing_counter_preserves_unknown_sample(cgroup, missing):
    (cgroup / missing).unlink()
    assert get_cgroup_memory() == (-1, -1)


@pytest.mark.parametrize("counter,value", [
    ("memory.current", "invalid"),
    ("memory.current", "-1"),
    ("memory.stat", "inactive_file invalid"),
    ("memory.stat", "inactive_file -1"),
    ("memory.stat", "anon 100"),
])
def test_invalid_counter_preserves_unknown_sample(cgroup, counter, value):
    (cgroup / counter).write_text(value)
    assert get_cgroup_memory() == (-1, -1)


def test_read_error_preserves_unknown_sample(cgroup):
    with patch("builtins.open", side_effect=OSError("Permission denied")):
        assert get_cgroup_memory() == (-1, -1)


@pytest.mark.parametrize("limit", ["max", "invalid", None])
def test_unknown_or_unbounded_limit_preserves_working_set(cgroup, limit):
    if limit is None:
        (cgroup / "memory.max").unlink()
    else:
        (cgroup / "memory.max").write_text(limit)
    assert get_cgroup_memory() == (550_000_000, -1)


def test_independently_sampled_counters_cannot_report_negative_usage(cgroup):
    (cgroup / "memory.current").write_text("100")
    (cgroup / "memory.stat").write_text("inactive_file 200")
    assert get_cgroup_memory() == (0, 1_000_000_000)


def test_charged_memory_reaches_existing_pressure_and_budget_consumers(cgroup):
    (cgroup / "memory.current").write_text("900000000")
    (cgroup / "memory.stat").write_text(
        "anon 700000000\nactive_file 100000000\ninactive_file 20000000\n"
    )
    # The old 800 MB sample missed the configured 850 MB pressure threshold.
    assert effective_memory_sample() == (880_000_000, 1_000_000_000)
    assert memory_pressure_ratio() == .88
    assert under_pressure(.85)
    assert not under_pressure(.90), "the caller's configured threshold still applies"
    budget = measure_budget(.85)
    assert budget.baseline_bytes == 880_000_000
    assert budget.budget_bytes == 0


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
