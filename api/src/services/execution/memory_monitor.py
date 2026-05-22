"""
Memory monitoring for execution throttling.

Provides system memory checks to prevent OOM conditions by delaying
subprocess spawning when available memory is low.
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Track if we've warned about missing /proc/meminfo (only warn once)
_warned_no_proc_meminfo = False


def get_available_memory_mb() -> int:
    """
    Get available system memory in MB.

    Reads /proc/meminfo (Linux/Docker). Returns -1 if unavailable.

    Returns:
        Available memory in MB, or -1 if unable to determine.
    """
    global _warned_no_proc_meminfo

    proc_meminfo = Path("/proc/meminfo")

    if not proc_meminfo.exists():
        if not _warned_no_proc_meminfo:
            logger.warning(
                "/proc/meminfo not found - memory throttling disabled. "
                "This is expected on macOS local development."
            )
            _warned_no_proc_meminfo = True
        return -1

    try:
        with open(proc_meminfo) as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    # Format: "MemAvailable:    12345678 kB"
                    parts = line.split()
                    if len(parts) >= 2:
                        kb = int(parts[1])
                        return kb // 1024  # Convert to MB
        # MemAvailable not found (older kernels)
        logger.warning("MemAvailable not found in /proc/meminfo")
        return -1
    except (OSError, ValueError) as e:
        logger.warning(f"Failed to read /proc/meminfo: {e}")
        return -1


_warned_no_cgroup = False


def get_cgroup_memory() -> tuple[int, int]:
    """
    Read working-set and max memory from cgroup v2.

    The "current" value returned here is the **working set** — anonymous memory
    plus active file cache, parsed from `memory.stat`. This matches kubelet's
    working-set definition (and therefore `kubectl top`) and reflects the memory
    that actually determines OOM-eviction risk. Inactive file cache is excluded
    because the kernel can evict it under pressure without affecting the
    workload — counting it inflates apparent usage by ~2x in practice.

    Returns:
        Tuple of (working_set_bytes, max_bytes). `working_set_bytes` is -1 only
        when `memory.stat` is unreadable. `max_bytes` is -1 when `memory.max` is
        unset ("max") or unreadable — callers that need a hard limit (e.g.
        admission control) should treat that as "unknown".
    """
    global _warned_no_cgroup

    cgroup_stat = Path("/sys/fs/cgroup/memory.stat")
    cgroup_max = Path("/sys/fs/cgroup/memory.max")

    if not cgroup_stat.exists():
        if not _warned_no_cgroup:
            logger.warning(
                "cgroup v2 memory files not found - cgroup admission disabled. "
                "This is expected on macOS local development."
            )
            _warned_no_cgroup = True
        return (-1, -1)

    try:
        anon = 0
        active_file = 0
        seen_anon = False
        seen_active_file = False
        with open(cgroup_stat) as f:
            for line in f:
                key, _, value = line.partition(" ")
                if key == "anon":
                    anon = int(value.strip())
                    seen_anon = True
                elif key == "active_file":
                    active_file = int(value.strip())
                    seen_active_file = True
                if seen_anon and seen_active_file:
                    break
        if not seen_anon or not seen_active_file:
            logger.warning(
                "cgroup memory.stat missing anon/active_file keys - "
                "working-set calculation unavailable"
            )
            return (-1, -1)
        current = anon + active_file
    except (OSError, ValueError) as e:
        logger.warning(f"Failed to read cgroup memory.stat: {e}")
        return (-1, -1)

    limit = -1
    if cgroup_max.exists():
        try:
            with open(cgroup_max) as f:
                max_raw = f.read().strip()
            if max_raw == "max":
                if not _warned_no_cgroup:
                    logger.warning(
                        "cgroup memory.max is 'max' (no limit) - "
                        "cgroup admission disabled. Set a memory limit on the container."
                    )
                    _warned_no_cgroup = True
            else:
                limit = int(max_raw)
        except (OSError, ValueError) as e:
            logger.warning(f"Failed to read cgroup memory.max: {e}")

    return (current, limit)


def has_sufficient_memory_cgroup(threshold: float = 0.85) -> bool:
    """
    Check if container has enough memory headroom to fork a new process.

    Args:
        threshold: Maximum memory usage ratio (0.0-1.0). Default 0.85 (85%).

    Returns:
        True if usage is at or below threshold, or if cgroup files unavailable.
        False if memory pressure exceeds threshold.
    """
    current, limit = get_cgroup_memory()

    if current < 0 or limit < 0 or limit == 0:
        return True  # Permissive when unable to check or no limit set

    ratio = current / limit
    if ratio > threshold:
        logger.warning(
            f"Memory pressure: {ratio:.1%} usage ({current // (1024*1024)}MB / "
            f"{limit // (1024*1024)}MB) exceeds {threshold:.0%} threshold"
        )
        return False

    return True


def has_sufficient_memory(threshold_mb: int = 300) -> bool:
    """
    Check if system has enough memory to start a new subprocess.

    Args:
        threshold_mb: Minimum available memory required (default 300MB)

    Returns:
        True if memory is sufficient or if unable to check (non-Linux).
        False if available memory is below threshold.
    """
    available = get_available_memory_mb()

    # If we can't determine memory, assume it's OK (don't block on macOS dev)
    if available < 0:
        return True

    return available >= threshold_mb
