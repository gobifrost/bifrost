"""Host-side contention and saturation counters for benchmark validity.

Read from the host (no ``kubectl exec``, so measurement never spends tenant
CPU): ``/proc/stat``, ``/proc/diskstats``, PSI, and the cgroups of the Kind node
containers and of every benchmark pod under them. A window is the delta of two
snapshots; ``invalid_reasons`` applies the predeclared rules in
docs/plans/2026-10-29-capacity-benchmark-methodology.md §5.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

CGROUP_ROOT = Path("/sys/fs/cgroup")
CLOCK_TICKS = os.sysconf("SC_CLK_TCK")
SECTOR_BYTES = 512
RECLAIM_KEYS = ("pgscan_kswapd", "pgscan_direct")
LIMIT_EVENTS = ("high", "max", "oom")


def node_cgroup(container: str) -> Path:
    """The cgroup of a Kind node's Docker container."""
    container_id = subprocess.run(
        ["docker", "inspect", "--format", "{{.Id}}", container], capture_output=True, text=True, check=True,
    ).stdout.strip()
    path = CGROUP_ROOT / "system.slice" / f"docker-{container_id}.scope"
    if not path.is_dir():
        raise RuntimeError(f"cgroup for {container} not found at {path}")
    return path


def pod_cgroup(node: Path, uid: str) -> Path | None:
    """A pod's cgroup under the Kind node (any QoS class); None once the pod is gone."""
    name = f"pod{uid.replace('-', '_')}.slice"
    kubepods = node / "kubelet.slice" / "kubelet-kubepods.slice"
    for parent in (kubepods, kubepods / "kubelet-kubepods-burstable.slice", kubepods / "kubelet-kubepods-besteffort.slice"):
        for prefix in ("kubelet-kubepods-", "kubelet-kubepods-burstable-", "kubelet-kubepods-besteffort-"):
            path = parent / f"{prefix}{name}"
            if path.is_dir():
                return path
    return None


def parse_flat(text: str) -> dict[str, int]:
    """``key value`` lines (cpu.stat, memory.stat, memory.events)."""
    values: dict[str, int] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("-").isdigit():
            values[parts[0]] = int(parts[1])
    return values


def parse_pressure(text: str) -> dict[str, int]:
    """PSI ``some``/``full`` cumulative stall time in microseconds."""
    totals: dict[str, int] = {}
    for line in text.splitlines():
        kind, *fields = line.split()
        totals[kind] = int(next(field for field in fields if field.startswith("total=")).split("=")[1])
    return totals


def parse_io_stat(text: str, device_id: str) -> dict[str, int]:
    """Bytes and I/Os for one device (``major:minor``) from a cgroup ``io.stat``.

    One device only: a stacked device (LVM over NVMe) appears once per layer,
    so summing lines would count each I/O twice.
    """
    totals = {"bytes": 0, "ios": 0}
    for line in text.splitlines():
        if not line.startswith(f"{device_id} "):
            continue
        fields = dict(field.split("=") for field in line.split()[1:] if "=" in field)
        totals["bytes"] += int(fields.get("rbytes", 0)) + int(fields.get("wbytes", 0))
        totals["ios"] += int(fields.get("rios", 0)) + int(fields.get("wios", 0))
    return totals


def parse_proc_stat(text: str) -> dict[str, int]:
    """Host busy and total CPU jiffies from the aggregate ``cpu`` line."""
    fields = [int(value) for value in text.splitlines()[0].split()[1:]]
    idle = fields[3] + (fields[4] if len(fields) > 4 else 0)
    return {"busy": sum(fields[:8]) - idle, "total": sum(fields[:8])}


def parse_diskstats(text: str, device: str) -> dict[str, int]:
    """Bytes, I/Os and I/O milliseconds for one whole device from ``/proc/diskstats``."""
    totals = {"bytes": 0, "ios": 0, "io_ms": 0}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) >= 14 and fields[2] == device:
            totals["ios"] += int(fields[3]) + int(fields[7])
            totals["bytes"] += (int(fields[5]) + int(fields[9])) * SECTOR_BYTES
            totals["io_ms"] += int(fields[6]) + int(fields[10])
    return totals


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def cgroup_counters(path: Path) -> dict[str, dict[str, int]]:
    memory = parse_flat(_read(path / "memory.stat"))
    current = int(_read(path / "memory.current").strip())
    return {
        "cpu": parse_flat(_read(path / "cpu.stat")),
        "memory_events": parse_flat(_read(path / "memory.events")),
        "memory": {
            "current": current,
            "lifetime_peak": int(_read(path / "memory.peak").strip()),
            "working_set": max(0, current - memory["inactive_file"]),
            **{key: memory[key] for key in ("anon", "file", "shmem", "kernel")},
        },
    }


def snapshot(nodes: dict[str, Path], pods: dict[str, Path], disk: dict[str, str]) -> dict:
    """One reading of every counter; ``nodes`` maps a label (``bench``, ``preserved``) to a node cgroup."""
    taken = {
        "at": time.time(),
        "host_cpu": parse_proc_stat(_read(Path("/proc/stat"))),
        "host_memory_pressure": parse_pressure(_read(Path("/proc/pressure/memory"))),
        "disk": parse_diskstats(_read(Path("/proc/diskstats")), disk["name"]),
        "nodes": {},
        "pods": {},
    }
    for label, path in nodes.items():
        counters = cgroup_counters(path)
        counters["memory_stat"] = {key: value for key, value in parse_flat(_read(path / "memory.stat")).items() if key in RECLAIM_KEYS}
        counters["memory_events_local"] = parse_flat(_read(path / "memory.events.local"))
        ancestor = parse_flat(_read(path.parent / "memory.events.local"))
        for key in LIMIT_EVENTS:
            counters["memory_events_local"][key] = counters["memory_events_local"].get(key, 0) + ancestor.get(key, 0)
        counters["memory_pressure"] = parse_pressure(_read(path / "memory.pressure"))
        counters["io"] = parse_io_stat(_read(path / "io.stat"), disk["id"])
        taken["nodes"][label] = counters
    for name, path in pods.items():
        if path.is_dir():
            taken["pods"][name] = cgroup_counters(path)
    return taken


def window(before: dict, after: dict) -> dict:
    """Rates and event deltas between two snapshots."""
    seconds = after["at"] - before["at"]
    host_busy = (after["host_cpu"]["busy"] - before["host_cpu"]["busy"]) / CLOCK_TICKS / seconds
    nodes: dict[str, dict] = {}
    for label, now in after["nodes"].items():
        then = before["nodes"][label]
        nodes[label] = {
            "cpu_cores": (now["cpu"]["usage_usec"] - then["cpu"]["usage_usec"]) / 1e6 / seconds,
            "io_bytes": now["io"]["bytes"] - then["io"]["bytes"],
            "io_ios": now["io"]["ios"] - then["io"]["ios"],
            "reclaim_pages": sum(now["memory_stat"].get(key, 0) - then["memory_stat"].get(key, 0) for key in RECLAIM_KEYS),
            "memory_pressure_pct": (now["memory_pressure"]["some"] - then["memory_pressure"]["some"]) / 1e6 / seconds * 100,
            "local_limit_events": sum(now["memory_events_local"].get(key, 0) - then["memory_events_local"].get(key, 0) for key in LIMIT_EVENTS),
            "memory_mib": now["memory"]["current"] / 2**20,
        }
    pods: dict[str, dict] = {}
    for name, now in after["pods"].items():
        then = before["pods"].get(name)
        if then is None:
            continue
        periods = now["cpu"].get("nr_periods", 0) - then["cpu"].get("nr_periods", 0)
        pods[name] = {
            "cpu_cores": (now["cpu"]["usage_usec"] - then["cpu"]["usage_usec"]) / 1e6 / seconds,
            "throttled_ratio": (now["cpu"].get("nr_throttled", 0) - then["cpu"].get("nr_throttled", 0)) / periods if periods else 0.0,
            "limit_events": sum(now["memory_events"].get(key, 0) - then["memory_events"].get(key, 0) for key in LIMIT_EVENTS),
            "oom_kills": now["memory_events"].get("oom_kill", 0) - then["memory_events"].get("oom_kill", 0),
            "memory_mib": now["memory"]["current"] / 2**20,
            "memory_bytes": dict(now["memory"]),
        }
    bench = nodes.get("bench", {})
    disk_bytes = after["disk"]["bytes"] - before["disk"]["bytes"]
    disk_ios = after["disk"]["ios"] - before["disk"]["ios"]
    io_ms = after["disk"]["io_ms"] - before["disk"]["io_ms"]
    return {
        "at": after["at"],
        "seconds": seconds,
        "host_cpu_cores": host_busy,
        "external_cpu_cores": host_busy - bench.get("cpu_cores", 0.0),
        "disk_bytes": disk_bytes,
        "disk_ios": disk_ios,
        "disk_interval_latency_ms": io_ms / disk_ios if disk_ios else 0.0,
        "external_io_bytes_share": max(0, disk_bytes - bench.get("io_bytes", 0)) / disk_bytes if disk_bytes else 0.0,
        "external_io_ios_share": max(0, disk_ios - bench.get("io_ios", 0)) / disk_ios if disk_ios else 0.0,
        "host_memory_pressure_pct": (after["host_memory_pressure"]["some"] - before["host_memory_pressure"]["some"]) / 1e6 / seconds * 100,
        "nodes": nodes,
        "pods": pods,
    }


def assess(intervals: list[dict], infra_pods: set[str], limits: dict | None = None) -> dict:
    """External contention over a measured window, from consecutive interval deltas.

    ``invalid`` lists reasons that invalidate the window; ``warnings`` lists
    unproven signals that are reported but do not.
    """
    limits = {"external_cpu_cores": 1.5, "external_cpu_time_share": 0.10, "external_io_share": 0.20,
              "external_io_floor_bytes_s": 5e6, "external_io_floor_ios_s": 200,
              "infra_throttled_ratio": 0.05, "memory_pressure_pct": 1.0, **(limits or {})}
    invalid: list[str] = []
    warnings: list[str] = []
    if not intervals:
        return {"invalid": ["no host samples"], "warnings": []}
    busy = sum(1 for interval in intervals if interval["external_cpu_cores"] > limits["external_cpu_cores"])
    if busy / len(intervals) > limits["external_cpu_time_share"]:
        invalid.append(f"external CPU above {limits['external_cpu_cores']} cores in {busy}/{len(intervals)} intervals")
    disk_bytes = sum(interval["disk_bytes"] for interval in intervals)
    disk_ios = sum(interval["disk_ios"] for interval in intervals)
    external_bytes = sum(interval["external_io_bytes_share"] * interval["disk_bytes"] for interval in intervals)
    external_ios = sum(interval["external_io_ios_share"] * interval["disk_ios"] for interval in intervals)
    seconds = sum(interval["seconds"] for interval in intervals)
    heavy_bytes = disk_bytes and external_bytes / disk_bytes > limits["external_io_share"] and external_bytes / seconds > limits["external_io_floor_bytes_s"]
    heavy_ios = disk_ios and external_ios / disk_ios > limits["external_io_share"] and external_ios / seconds > limits["external_io_floor_ios_s"]
    if heavy_bytes or heavy_ios:
        invalid.append("external disk I/O above its share of the window")
    pod_events = sum(pod["limit_events"] for interval in intervals for pod in interval["pods"].values())
    nodes = [interval["nodes"].get("bench", {}) for interval in intervals]
    local_events = sum(node.get("local_limit_events", 0) for node in nodes)
    reclaim = sum(node.get("reclaim_pages", 0) for node in nodes)
    pressured = any(node.get("memory_pressure_pct", 0.0) > limits["memory_pressure_pct"] for node in nodes)
    if local_events and (reclaim or pressured):
        invalid.append("memory limit pressure at the Kind node or an ancestor")
    elif not pod_events and reclaim and pressured:
        invalid.append("memory pressure from above the benchmark's limits")
    elif not pod_events and (reclaim or pressured):
        warnings.append("possible external memory pressure (unproven)")
    for interval in intervals:
        for name in infra_pods & interval["pods"].keys():
            pod = interval["pods"][name]
            if pod["throttled_ratio"] > limits["infra_throttled_ratio"] or pod["limit_events"]:
                invalid.append(f"infrastructure pod {name} saturated")
    return {"invalid": sorted(set(invalid)), "warnings": warnings}
