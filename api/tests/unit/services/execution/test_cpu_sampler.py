"""Unit tests for the workflow child CPU sampler."""

import io

import pytest

from src.services.execution.cpu_sampler import (
    CPUSampler,
    calculate_cpu_cores,
    parse_proc_stat_line,
    read_proc_stat,
    read_proc_status_rss,
    _ProcSample,
)


class TestParseProcStatLine:
    def test_simple_process_name(self) -> None:
        line = "123 (python) S 1 123 123 0 -1 4194560 1234 0 0 0 100 50 0 0 20 0 1 0 0 0 0 0 0"
        sample = parse_proc_stat_line(line)
        assert sample is not None
        assert sample.comm == "python"
        assert sample.utime == 100
        assert sample.stime == 50

    def test_process_name_with_spaces(self) -> None:
        line = "123 (python worker process) S 1 123 123 0 -1 4194560 1234 0 0 0 100 50 0 0 20 0 1 0 0 0 0 0 0"
        sample = parse_proc_stat_line(line)
        assert sample is not None
        assert sample.comm == "python worker process"
        assert sample.utime == 100
        assert sample.stime == 50

    def test_process_name_with_parentheses(self) -> None:
        line = "123 (python (3.11)) S 1 123 123 0 -1 4194560 1234 0 0 0 100 50 0 0 20 0 1 0 0 0 0 0 0"
        sample = parse_proc_stat_line(line)
        assert sample is not None
        assert sample.comm == "python (3.11)"
        assert sample.utime == 100
        assert sample.stime == 50

    def test_invalid_line_returns_none(self) -> None:
        assert parse_proc_stat_line("") is None
        assert parse_proc_stat_line("no parentheses here") is None
        assert parse_proc_stat_line("123 (python) S") is None


class TestCalculateCpuCores:
    def test_typical_interval(self) -> None:
        # 200 jiffies over 2 seconds at 100 ticks/sec = 1.0 cores
        assert calculate_cpu_cores(100, 100, 2.0, 100) == pytest.approx(1.0)

    def test_zero_delta_seconds_returns_none(self) -> None:
        assert calculate_cpu_cores(100, 0, 0.0, 100) is None

    def test_zero_ticks_returns_none(self) -> None:
        assert calculate_cpu_cores(100, 0, 1.0, 0) is None

    def test_multi_core_usage(self) -> None:
        # 400 jiffies over 1 second at 100 ticks/sec = 4.0 cores
        assert calculate_cpu_cores(400, 0, 1.0, 100) == pytest.approx(4.0)


class TestReadProcStatusRss:
    def test_parses_vmrss_line(self, monkeypatch) -> None:
        def _fake_open(path: str, *_args, **_kwargs):
            assert "status" in path
            return io.StringIO("VmRSS:   12345 kB\nOther: 1 kB\n")

        monkeypatch.setattr("builtins.open", _fake_open)
        assert read_proc_status_rss(1) == 12345 * 1024

    def test_missing_vmrss_returns_none(self, monkeypatch) -> None:
        def _fake_open(_path, *_args, **_kwargs):
            return io.StringIO("VmHWM:   12345 kB\n")

        monkeypatch.setattr("builtins.open", _fake_open)
        assert read_proc_status_rss(1) is None

    def test_missing_proc_entry_returns_none(self, monkeypatch) -> None:
        def _fake_open(_path, *_args, **_kwargs):
            raise FileNotFoundError()

        monkeypatch.setattr("builtins.open", _fake_open)
        assert read_proc_status_rss(999999) is None


class TestCPUSampler:
    def test_peak_tracks_maximum(self, monkeypatch) -> None:
        samples = [
            _make_sample(100, 0),
            _make_sample(300, 0),
            _make_sample(450, 0),
        ]
        monkeypatch.setattr(
            "src.services.execution.cpu_sampler.read_proc_stat",
            lambda _pid: samples.pop(0),
        )
        sampler = CPUSampler(pid=1, clock_ticks=100)
        sampler.set_baseline(0.0, _make_sample(0, 0))
        sampler.sample(1.0)  # 100 jiffies -> 1.0 core
        sampler.sample(2.0)  # 200 jiffies -> 2.0 cores
        sampler.sample(3.0)  # 150 jiffies -> 1.5 cores
        assert sampler.peak_cpu_cores == pytest.approx(2.0)

    def test_exits_before_valid_interval_keeps_none(self) -> None:
        sampler = CPUSampler(pid=1, clock_ticks=100)
        sampler.set_baseline(0.0, _make_sample(0, 0))
        assert sampler.peak_cpu_cores is None

    def test_missing_proc_entry_is_non_fatal(self, monkeypatch) -> None:
        def _fake_open(_path, *_args, **_kwargs):
            raise FileNotFoundError()

        monkeypatch.setattr("builtins.open", _fake_open)
        assert read_proc_stat(999999) is None
        sampler = CPUSampler(pid=999999, clock_ticks=100)
        sampler.set_baseline(0.0)
        assert sampler.sample(1.0) is None

    def test_tracks_peak_process_rss(self, monkeypatch) -> None:
        stat_samples = [
            _make_sample(100, 0),
            _make_sample(200, 0),
        ]
        rss_samples = [10_000 * 1024, 12_000 * 1024, 9_000 * 1024]
        monkeypatch.setattr(
            "src.services.execution.cpu_sampler.read_proc_stat",
            lambda _pid: stat_samples.pop(0),
        )
        monkeypatch.setattr(
            "src.services.execution.cpu_sampler.read_proc_status_rss",
            lambda _pid: rss_samples.pop(0),
        )
        sampler = CPUSampler(pid=1, clock_ticks=100)
        sampler.set_baseline(0.0, _make_sample(0, 0))
        sampler.sample(1.0)
        sampler.sample(2.0)
        assert sampler.peak_process_rss_bytes == 12_000 * 1024

    def test_missing_rss_does_not_invalidate_cpu_sample(self, monkeypatch) -> None:
        stat_samples = [
            _make_sample(100, 0),
            _make_sample(200, 0),
        ]
        monkeypatch.setattr(
            "src.services.execution.cpu_sampler.read_proc_stat",
            lambda _pid: stat_samples.pop(0),
        )
        monkeypatch.setattr(
            "src.services.execution.cpu_sampler.read_proc_status_rss",
            lambda _pid: None,
        )
        sampler = CPUSampler(pid=1, clock_ticks=100)
        sampler.set_baseline(0.0, _make_sample(0, 0))
        sampler.sample(1.0)
        cores = sampler.sample(2.0)
        assert cores == pytest.approx(1.0)
        assert sampler.peak_cpu_cores == pytest.approx(1.0)
        assert sampler.peak_process_rss_bytes is None


def _make_sample(utime: int, stime: int) -> _ProcSample:
    return _ProcSample(comm="test", utime=utime, stime=stime)
