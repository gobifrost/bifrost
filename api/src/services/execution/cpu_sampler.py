"""Sample CPU and process memory for workflow child processes.

The pool's 1-second monitor loop uses this to estimate peak CPU cores for
BUSY workflow children. Values are sampled, not instantaneous absolute peaks.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)


def get_clock_ticks() -> int:
    """Return system clock ticks per second for /proc/stat CPU math."""
    return os.sysconf("SC_CLK_TCK")


@dataclass(frozen=True)
class _ProcSample:
    """Parsed CPU sample from /proc/{pid}/stat."""

    comm: str
    utime: int
    stime: int


def parse_proc_stat_line(line: str) -> _ProcSample | None:
    """Parse a single /proc/{pid}/stat line.

    The process name (comm) is enclosed in parentheses and may contain spaces
    and parentheses, so we locate the first '(' and last ')' rather than
    splitting naively.

    Returns None if the line is malformed or unreadable.
    """
    if not line or line.strip() == "":
        return None

    first_paren = line.find("(")
    last_paren = line.rfind(")")
    if first_paren == -1 or last_paren == -1 or last_paren <= first_paren:
        return None

    comm = line[first_paren + 1:last_paren]
    remainder = line[last_paren + 1:].strip()
    fields = remainder.split()
    # After comm, the fields are: state ppid pgrp session tty_nr tpgid flags
    # minflt cminflt majflt cmajflt utime stime cutime cstime ...
    # utime is index 11, stime is index 12 in the remainder.
    if len(fields) < 13:
        return None

    try:
        utime = int(fields[11])
        stime = int(fields[12])
    except (ValueError, IndexError):
        return None

    return _ProcSample(comm=comm, utime=utime, stime=stime)


def read_proc_stat(pid: int) -> _ProcSample | None:
    """Read and parse /proc/{pid}/stat. Returns None if unavailable."""
    try:
        with open(f"/proc/{pid}/stat") as f:
            return parse_proc_stat_line(f.read())
    except (OSError, ValueError) as e:
        logger.debug(f"could not read /proc/{pid}/stat: {e}")
        return None


def read_proc_status_rss(pid: int) -> int | None:
    """Read current RSS in bytes from /proc/{pid}/status.

    Returns None if the entry is unavailable or the VmRSS line cannot be
    parsed. The caller treats None as "unknown" rather than zero usage.
    """
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    parts = line.split()
                    if len(parts) >= 2:
                        return int(parts[1]) * 1024  # kB to bytes
    except (OSError, ValueError) as e:
        logger.debug(f"could not read /proc/{pid}/status VmRSS: {e}")
    return None


def calculate_cpu_cores(
    delta_utime: int,
    delta_stime: int,
    delta_seconds: float,
    ticks: int,
) -> float | None:
    """Convert a jiffies delta and wall-clock delta to CPU core usage.

    Returns None when the delta is not positive (no valid interval).
    """
    if ticks <= 0 or delta_seconds <= 0:
        return None
    return round((delta_utime + delta_stime) / ticks / delta_seconds, 4)


@dataclass
class CPUSampler:
    """Track sampled peak CPU cores and RSS for one process.

    Baseline is set at dispatch; subsequent samples update the running peaks.
    If the process exits before a valid CPU interval, peak_cpu_cores stays
    null. RSS is sampled independently each interval; the highest observed
    value is kept as peak_process_rss_bytes.
    """

    pid: int
    clock_ticks: int
    peak_cpu_cores: float | None = None
    peak_process_rss_bytes: int | None = None
    _baseline_utime: int | None = None
    _baseline_stime: int | None = None
    _baseline_time: float | None = None

    def _sample_rss(self) -> None:
        rss = read_proc_status_rss(self.pid)
        if rss is not None and (
            self.peak_process_rss_bytes is None or rss > self.peak_process_rss_bytes
        ):
            self.peak_process_rss_bytes = rss

    def set_baseline(self, now: float, sample: _ProcSample | None = None) -> None:
        """Record the starting CPU time for this child."""
        self._sample_rss()
        if sample is None:
            sample = read_proc_stat(self.pid)
        if sample is None:
            return
        self._baseline_utime = sample.utime
        self._baseline_stime = sample.stime
        self._baseline_time = now

    def sample(self, now: float) -> float | None:
        """Sample CPU usage and RSS, updating both running peaks.

        Returns the interval's core usage (not the peak). RSS is sampled
        each call; missing RSS readings are ignored so the CPU interval
        remains valid even when /proc/{pid}/status is transiently unreadable.
        """
        self._sample_rss()
        if self._baseline_utime is None or self._baseline_time is None:
            self.set_baseline(now)
            return None

        current = read_proc_stat(self.pid)
        if current is None:
            return None

        delta_seconds = now - self._baseline_time
        cores = calculate_cpu_cores(
            current.utime - self._baseline_utime,
            current.stime - self._baseline_stime,
            delta_seconds,
            self.clock_ticks,
        )

        # Advance baseline for the next interval so each sample measures growth
        # since the previous sample rather than since dispatch.
        self._baseline_utime = current.utime
        self._baseline_stime = current.stime
        self._baseline_time = now

        if cores is not None:
            if self.peak_cpu_cores is None or cores > self.peak_cpu_cores:
                self.peak_cpu_cores = cores

        return cores
