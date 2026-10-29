#!/usr/bin/env python3
"""Collect host-side cgroup-v2 memory samples for running Docker workers."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Container:
    requested: str
    host_pid: int
    container_id: str
    name: str
    image: str


def _read_int(path: Path) -> int | None:
    try:
        value = path.read_text(encoding="utf-8").strip()
        return None if value == "max" else int(value)
    except (OSError, ValueError):
        return None


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def inspect_container(requested: str) -> Container:
    command = [
        "docker", "inspect", "--format",
        "{{.State.Pid}}\t{{.Id}}\t{{.Name}}\t{{.Image}}", requested,
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    fields = (result.stdout or "").strip().split("\t") if result.returncode == 0 else []
    if len(fields) != 4:
        raise RuntimeError("container_inspect_failed")
    try:
        host_pid = int(fields[0])
    except ValueError as exc:
        raise RuntimeError("container_not_running") from exc
    if host_pid <= 0:
        raise RuntimeError("container_not_running")
    return Container(requested, host_pid, fields[1], fields[2], fields[3])


def _cgroup_dir(host_pid: int, *, proc_root: Path, cgroup_root: Path) -> Path | None:
    for line in (_read_text(proc_root / str(host_pid) / "cgroup") or "").splitlines():
        fields = line.split(":", 2)
        if len(fields) == 3 and fields[1] == "":
            return cgroup_root / fields[2].lstrip("/")
    return None


def _memory(cgroup: Path) -> dict[str, Any]:
    values = {"anon": None, "file": None, "inactive_file": None, "kernel": None}
    for line in (_read_text(cgroup / "memory.stat") or "").splitlines():
        key, _, raw_value = line.partition(" ")
        if key in values:
            try:
                values[key] = int(raw_value)
            except ValueError:
                # Ignore malformed cgroup statistics and retain the missing value.
                pass
    current, inactive = _read_int(cgroup / "memory.current"), values["inactive_file"]
    return {
        "current_bytes": current,
        "peak_bytes": _read_int(cgroup / "memory.peak"),
        "max_bytes": _read_int(cgroup / "memory.max"),
        "stat": {f"{key}_bytes": value for key, value in values.items()},
        "working_set_estimate_bytes": (
            None if current is None or inactive is None else max(0, current - inactive)
        ),
    }


def _cpu(cgroup: Path) -> dict[str, int]:
    return {
        key: int(value)
        for line in (_read_text(cgroup / "cpu.stat") or "").splitlines()
        for key, _, value in [line.partition(" ")]
        if key in {"usage_usec", "user_usec", "system_usec"} and value.isdigit()
    }


def _process(pid: int, *, proc_root: Path) -> dict[str, int | str | None]:
    process = proc_root / str(pid)
    status = _read_text(process / "status")
    values: dict[str, int | str | None] = {
        "pid": pid,
        "comm": _read_text(process / "comm"),
        "status": None,
        "rss_bytes": None,
        "pss_bytes": None,
    }
    if status is not None:
        for line in status.splitlines():
            key, _, raw_value = line.partition(":")
            if key == "State":
                values["status"] = raw_value.strip() or None
            elif key == "VmRSS":
                try:
                    values["rss_bytes"] = int(raw_value.split()[0]) * 1024
                except (IndexError, ValueError):
                    # The optional VmRSS value may be absent or malformed.
                    pass
    for line in (_read_text(process / "smaps_rollup") or "").splitlines():
        key, _, raw_value = line.partition(":")
        if key == "Pss":
            try:
                values["pss_bytes"] = int(raw_value.split()[0]) * 1024
            except (IndexError, ValueError):
                # The optional Pss value may be absent or malformed.
                pass
    return values


def sample_container(
    container: Container,
    *,
    proc_root: Path = Path("/proc"),
    cgroup_root: Path = Path("/sys/fs/cgroup"),
) -> dict[str, Any]:
    cgroup = _cgroup_dir(container.host_pid, proc_root=proc_root, cgroup_root=cgroup_root)
    if cgroup is None:
        return {"cgroup_path": None, "memory": None, "processes": []}
    process_ids = _read_text(cgroup / "cgroup.procs") or ""
    pids = sorted({int(pid) for pid in process_ids.splitlines() if pid.isdigit()})
    return {
        "cgroup_path": str(cgroup),
        "memory": _memory(cgroup),
        "cpu": _cpu(cgroup),
        "processes": [_process(pid, proc_root=proc_root) for pid in pids],
    }


def collect(
    containers: list[Container], *, duration_seconds: float, interval_seconds: float
) -> dict[str, Any]:
    started_at = datetime.now(timezone.utc).isoformat()
    started = time.monotonic()
    samples: list[dict[str, Any]] = []
    while True:
        elapsed = time.monotonic() - started
        samples.append(
            {
                "elapsed_seconds": elapsed,
                "containers": [sample_container(container) for container in containers],
            }
        )
        remaining = duration_seconds - elapsed
        if remaining <= 0:
            break
        time.sleep(min(interval_seconds, remaining))
    return {
        "context": "host-side", "started_at": started_at,
        "duration_seconds": duration_seconds,
        "interval_seconds": interval_seconds,
        "method": "proc-cgroup-v2",
        "units": "bytes",
        "containers": [
            {
                "id": container.container_id,
                "name": container.name,
                "image": container.image,
                "host_pid": container.host_pid,
            }
            for container in containers
        ],
        "samples": samples,
    }


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", action="append", required=True)
    parser.add_argument("--duration-seconds", type=float, default=30)
    parser.add_argument("--interval-seconds", type=float, default=1)
    args = parser.parse_args()
    if not 0.05 <= args.duration_seconds <= 600:
        parser.error("--duration-seconds must be between 0.05 and 600")
    if not 0.05 <= args.interval_seconds <= min(60, args.duration_seconds):
        parser.error("--interval-seconds must be between 0.05 and the duration")
    return args


def main() -> None:
    args = _arguments()
    containers = [inspect_container(requested) for requested in args.container]
    result = collect(
        containers,
        duration_seconds=args.duration_seconds,
        interval_seconds=args.interval_seconds,
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
