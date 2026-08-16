"""
Memory sampler: polls a running container's VmRSS + cgroup memory.stat
fields at a configurable interval and streams samples to a CSV file.

Default target: the long-lived api container in the current worktree's
test stack (bifrost-test-<hash>-api-1). Can also target the dev api or
any named container via --container.

Meant to run alongside a test run:

  # terminal 1: start sampler in background, writes CSV
  python scripts/memory_sampler.py --out /tmp/bifrost/memory-trace.csv &

  # terminal 2: run the test suite
  ./test.sh e2e
  ./test.sh client e2e

  # terminal 1: Ctrl-C or kill the sampler
  # then: python scripts/memory_sampler_correlate.py (next script)

Columns per row:
  ts_unix                 — sample timestamp (float seconds since epoch)
  rss_kb                  — /proc/1/status VmRSS (resident set size)
  vmpeak_kb               — /proc/1/status VmPeak (high-water mark)
  cgroup_bytes            — memory.current (what K8s OOM killer sees)
  anon_bytes              — memory.stat anon (anonymous pages, counts against limit)
  inactive_anon_bytes     — memory.stat inactive_anon (arena cache territory)
  active_anon_bytes       — memory.stat active_anon (in-use anonymous)
  heap_bytes              — sum of /proc/1/maps [heap] regions
  anon_mapping_bytes      — sum of /proc/1/maps anonymous regions (virtual)
  anon_mapping_count      — count of anon mappings (arena count proxy)

Heap/map fields are more expensive to read than /proc/1/status so they
sample less often (every Nth tick, controlled by --maps-every).
"""
from __future__ import annotations

import argparse
import csv
import signal
import subprocess
import sys
import time
from pathlib import Path


# Inline script we pass to python3 inside the container. It prints a single
# CSV row to stdout per invocation, with ALL fields. We call this via
# `docker exec` each tick. The shell inside the container has python3, so we
# don't need to copy any script file.
_PROBE = r"""
import sys, os
def _read_status():
    rss = vmpeak = 0
    try:
        with open('/proc/1/status') as f:
            for line in f:
                k = line.split(':', 1)[0]
                if k == 'VmRSS':
                    rss = int(line.split()[1])
                elif k == 'VmPeak':
                    vmpeak = int(line.split()[1])
    except OSError:
        pass
    return rss, vmpeak

def _read_cgroup():
    cur = 0
    for p in ('/sys/fs/cgroup/memory.current',
              '/sys/fs/cgroup/memory/memory.usage_in_bytes'):
        try:
            with open(p) as f:
                cur = int(f.read().strip())
                break
        except OSError:
            continue
    anon = inactive = active = 0
    for p in ('/sys/fs/cgroup/memory.stat',
              '/sys/fs/cgroup/memory/memory.stat'):
        try:
            with open(p) as f:
                for line in f:
                    k, v = line.split()
                    if k == 'anon':
                        anon = int(v)
                    elif k == 'inactive_anon':
                        inactive = int(v)
                    elif k == 'active_anon':
                        active = int(v)
                break
        except OSError:
            continue
    return cur, anon, inactive, active

def _read_maps():
    heap = anon_map = 0
    count = 0
    try:
        with open('/proc/1/maps') as f:
            for line in f:
                parts = line.split(None, 5)
                if len(parts) < 6:
                    parts.append('')
                r = parts[0].split('-')
                size = int(r[1], 16) - int(r[0], 16)
                path = parts[5].strip()
                if path == '[heap]':
                    heap += size
                elif not path:
                    anon_map += size
                    count += 1
    except OSError:
        pass
    return heap, anon_map, count

rss, vmpeak = _read_status()
cg, anon, inactive, active = _read_cgroup()
if os.environ.get('SAMPLE_MAPS') == '1':
    heap, amap, acount = _read_maps()
else:
    heap = amap = acount = -1
print(f'{rss},{vmpeak},{cg},{anon},{inactive},{active},{heap},{amap},{acount}')
"""


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--container",
        help="Container name (default: auto-detect the test api container "
        "for the current worktree)",
    )
    p.add_argument(
        "--out",
        default="/tmp/bifrost/memory-trace.csv",
        help="CSV output path (default: /tmp/bifrost/memory-trace.csv)",
    )
    p.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Seconds between /proc/1/status samples (default: 1.0)",
    )
    p.add_argument(
        "--maps-every",
        type=int,
        default=10,
        help="Read /proc/1/maps once every N ticks (default: 10 — maps "
        "read is ~5x more expensive than /proc/1/status)",
    )
    return p.parse_args()


def _detect_container() -> str:
    """Find the test api container for the current worktree."""
    # Same project-name derivation as test.sh: hash of worktree path.
    try:
        out = subprocess.check_output(
            ["docker", "ps", "--format", "{{.Names}}"],
            text=True,
        )
    except subprocess.CalledProcessError:
        sys.exit("Failed to list docker containers")
    candidates = [n for n in out.splitlines() if n.endswith("-api-1") and "bifrost" in n]
    if not candidates:
        sys.exit(
            "No bifrost api container found running. Start one with "
            "`./test.sh stack up` or `./debug.sh` and pass --container."
        )
    # Prefer the bifrost-test-* one (backend e2e) over the bifrost-dev-*
    # one, since backend e2e is the more comprehensive surface.
    test_apis = [c for c in candidates if "-test-" in c]
    if test_apis:
        return test_apis[0]
    return candidates[0]


def main() -> int:
    args = _parse_args()
    container = args.container or _detect_container()
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"Sampling container: {container}", file=sys.stderr)
    print(f"Writing to: {out_path}", file=sys.stderr)
    print(f"Interval: {args.interval}s (maps every {args.maps_every} ticks)",
          file=sys.stderr)

    stopping = False

    def _stop(_signum, _frame) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    fields = [
        "ts_unix", "rss_kb", "vmpeak_kb", "cgroup_bytes",
        "anon_bytes", "inactive_anon_bytes", "active_anon_bytes",
        "heap_bytes", "anon_mapping_bytes", "anon_mapping_count",
    ]

    with out_path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(fields)
        f.flush()

        tick = 0
        while not stopping:
            tick += 1
            now = time.time()
            sample_maps = tick % args.maps_every == 0
            env = ["-e", "SAMPLE_MAPS=1"] if sample_maps else []
            try:
                proc = subprocess.run(
                    ["docker", "exec", *env, container,
                     "python3", "-c", _PROBE],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
            except subprocess.TimeoutExpired:
                # Container unresponsive — record the gap but keep going.
                writer.writerow([f"{now:.3f}", -1, -1, -1, -1, -1, -1, -1, -1, -1])
                f.flush()
                time.sleep(args.interval)
                continue
            if proc.returncode != 0:
                # ./test.sh stack reset stops+starts the api container mid-
                # run. The old container exits; a NEW container comes up
                # under the same NAME. docker exec by name should resolve
                # to the new one once it's healthy — so we just record a
                # gap and retry with a slightly longer sleep.
                err = proc.stderr.strip()[:200]
                if "is not running" in err or "No such container" in err:
                    writer.writerow([f"{now:.3f}", -1, -1, -1, -1, -1, -1, -1, -1, -1])
                    f.flush()
                    # Back off to 3x the normal interval during reset —
                    # typically ~10s before the new container accepts
                    # exec. Without backoff we spam docker with failing
                    # calls while the container is coming up.
                    time.sleep(max(args.interval * 3, 2.0))
                    continue
                print(f"docker exec failed (rc={proc.returncode}): {err}",
                      file=sys.stderr)
                break

            parts = proc.stdout.strip().split(",")
            if len(parts) != 9:
                print(f"unexpected probe output: {proc.stdout!r}", file=sys.stderr)
                time.sleep(args.interval)
                continue
            writer.writerow([f"{now:.3f}", *parts])
            f.flush()

            # Sleep minus the time we already spent on docker exec.
            elapsed = time.time() - now
            remaining = args.interval - elapsed
            if remaining > 0:
                time.sleep(remaining)

    print(f"\nStopped. Samples written to {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

# Fix for issue #603: safe input handling
