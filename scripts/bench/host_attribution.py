#!/usr/bin/env python3
"""Read-only, bounded host cgroup attribution companion for benchmark evidence.

It observes selected disjoint cgroup-v2 leaves only.  It is not a complete
system-accounting tool and never reads process command lines or environments.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import signal
import time
from pathlib import Path


CGROUP_ROOT = Path("/sys/fs/cgroup")
_STOP = False


def _stop(_signum: int, _frame: object) -> None:
    global _STOP
    _STOP = True


def _flat(path: Path) -> dict[str, int]:
    values: dict[str, int] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, *rest = line.split()
        if len(rest) == 1 and rest[0].isdigit():
            values[key] = int(rest[0])
    return values


def _io(path: Path, device: str) -> tuple[int, int]:
    bytes_, ios = 0, 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.split()[0] != device:
            continue
        fields = dict(item.split("=", 1) for item in line.split()[1:] if "=" in item)
        bytes_ += int(fields.get("rbytes", 0)) + int(fields.get("wbytes", 0))
        ios += int(fields.get("rios", 0)) + int(fields.get("wios", 0))
    return bytes_, ios


def candidates(root: Path = CGROUP_ROOT, include_user: bool = False) -> list[Path]:
    """Disjoint leaves: Docker scopes and QEMU scopes, optionally user slices."""
    found = [
        *sorted((root / "system.slice").glob("docker-*.scope")),
        *sorted((root / "machine.slice").glob("machine-qemu*.scope")),
    ]
    if include_user:
        found.extend(sorted((root / "user.slice").glob("user-*.slice")))
    return [path for path in found if path.is_dir()]


def snapshot(root: Path = CGROUP_ROOT, include_user: bool = False, device: str = "259:0") -> dict:
    groups, missing = {}, []
    for path in candidates(root, include_user):
        try:
            cpu = _flat(path / "cpu.stat").get("usage_usec")
            bytes_, ios = _io(path / "io.stat", device)
            if cpu is None:
                raise ValueError("cpu.stat lacks usage_usec")
            groups[str(path.relative_to(root))] = {
                "inode": path.stat().st_ino,
                "cpu_usec": cpu,
                "io_bytes": bytes_,
                "io_ios": ios,
            }
        except (OSError, ValueError) as error:
            missing.append(
                {
                    "path": str(path.relative_to(root)),
                    "reason": f"{type(error).__name__}: {error}",
                }
            )
    return {
        "epoch_s": time.time(),
        "observer_pid": os.getpid(),
        "observer_cpu_process_s": time.process_time(),
        "io_device": device,
        "scope": "selected-disjoint-cgroup-v2-leaves",
        "residual": "unmeasured; not complete system attribution",
        "groups": groups,
        "missing": missing,
    }


def delta(before: dict, after: dict) -> dict:
    """Deltas only for unchanged cgroup identities; resets/deletions stay explicit."""
    values, coverage = {}, {
        "new": [],
        "deleted": [],
        "identity_changed": [],
        "counter_reset": [],
        "missing": [item["path"] for item in after["missing"]],
    }
    prior, current = before["groups"], after["groups"]
    missing = set(coverage["missing"])
    for name in sorted(set(current) - set(prior)):
        coverage["new"].append(name)
    for name in sorted(set(prior) - set(current) - missing):
        coverage["deleted"].append(name)
    for name in sorted(set(prior) & set(current)):
        old, new = prior[name], current[name]
        if old["inode"] != new["inode"]:
            coverage["identity_changed"].append(name)
            continue
        changes = {key: new[key] - old[key] for key in ("cpu_usec", "io_bytes", "io_ios")}
        if any(value < 0 for value in changes.values()):
            coverage["counter_reset"].append(name)
            continue
        values[name] = changes
    return {
        "from_epoch_s": before["epoch_s"],
        "to_epoch_s": after["epoch_s"],
        "deltas": values,
        "coverage": coverage,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--duration-s", type=float, required=True)
    parser.add_argument("--interval-s", type=float, default=5.0)
    parser.add_argument("--include-user", action="store_true")
    parser.add_argument("--device", required=True, help="declared physical disk major:minor; avoids stacked-device double counting")
    args = parser.parse_args()
    if not all(math.isfinite(value) and value > 0 for value in (args.duration_s, args.interval_s)):
        raise ValueError("duration and interval must be finite positive seconds")
    if re.fullmatch(r"[0-9]+:[0-9]+", args.device) is None:
        raise ValueError("device must be a numeric major:minor ID")
    descriptor = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    started = time.monotonic()
    next_sample = started
    previous = None
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        while not _STOP and time.monotonic() - started < args.duration_s:
            current = snapshot(include_user=args.include_user, device=args.device)
            record = {
                "snapshot": current,
                "delta": None if previous is None else delta(previous, current),
            }
            output.write(json.dumps(record, allow_nan=False) + "\n")
            output.flush()
            os.fsync(output.fileno())
            previous = current
            next_sample += args.interval_s
            remaining = started + args.duration_s - time.monotonic()
            time.sleep(min(max(0.0, next_sample - time.monotonic()), max(0.0, remaining)))
        final = {
            "final_status": "signal" if _STOP else "completed",
            "ended_epoch_s": time.time(),
        }
        output.write(json.dumps(final, allow_nan=False) + "\n")
        output.flush()
        os.fsync(output.fileno())


if __name__ == "__main__":
    main()
