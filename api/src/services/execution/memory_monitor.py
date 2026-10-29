"""
The container's memory, read from its cgroup (see src.services.memory_requests).
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

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
                "cgroup v2 memory files not found - executors size themselves "
                "from the host's memory. This is expected on macOS local development."
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
                        "cgroup memory.max is 'max' (no limit) - executors size "
                        "themselves from the host's memory."
                    )
                    _warned_no_cgroup = True
            else:
                limit = int(max_raw)
        except (OSError, ValueError) as e:
            logger.warning(f"Failed to read cgroup memory.max: {e}")

    return (current, limit)


def host_memory_bytes() -> int:
    """Return host physical memory without importing application settings."""
    with open("/proc/meminfo") as meminfo:
        for line in meminfo:
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) * 1024
    raise RuntimeError("/proc/meminfo has no MemTotal")


def effective_memory_sample() -> tuple[int, int]:
    """Use cgroup usage with the host limit when a cgroup is unbounded.

    This is the same policy used by ``memory_requests.measure_budget``. It
    keeps Compose and other unbounded installations runnable while preserving
    cgroup working-set telemetry whenever it is available.
    """
    current, limit = get_cgroup_memory()
    return max(current, 0), limit if limit > 0 else host_memory_bytes()


def memory_pressure_ratio() -> float:
    """Use host-used pressure when the cgroup has no finite limit."""
    current, limit = get_cgroup_memory()
    if current >= 0 and limit > 0:
        return current / limit
    values: dict[str, int] = {}
    with open("/proc/meminfo") as meminfo:
        for line in meminfo:
            key, _, rest = line.partition(":")
            values[key] = int(rest.split()[0]) * 1024
    return (values["MemTotal"] - values["MemAvailable"]) / values["MemTotal"]
