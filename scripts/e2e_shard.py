#!/usr/bin/env python3
"""Print the e2e test files for one shard of an N-shard split.

Usage:
    scripts/e2e_shard.py --shard-id 1 --total 2

Allocates files to shards by sorted-order round-robin, weighted by an
optional weights file (see WEIGHTS below). Stable: adding a new file only
moves files in shards downstream of the insertion point.

Print one path per line on stdout, suitable for piping into ./test.sh.
"""

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
API_ROOT = REPO_ROOT / "api"
TESTS_ROOT = API_ROOT / "tests" / "e2e"

# Files measured to dominate runtime (>= 5s each, summed across all tests
# in the file). Bin-packed first to keep wall-clock balanced. Numbers are
# rough seconds from a CI run; used only for ordering, not exact math.
# Weights are from May 2026; refresh from a --durations=50 run when shards
# drift past 3 min spread.
#
# Paths are relative to api/ (i.e., tests/e2e/...) to match what test.sh
# passes to pytest inside the container (CWD=/app, mounted from ./api/).
WEIGHTS = {
    "tests/e2e/api/test_executions.py": 70,
    "tests/e2e/platform/test_large_file_memory.py": 35,
    "tests/e2e/platform/test_worker_memory.py": 32,
    "tests/e2e/api/test_misc.py": 20,
    "tests/e2e/platform/test_fork_pool.py": 12,
    "tests/e2e/api/test_applications.py": 70,
    "tests/e2e/platform/test_cli_apps_replace.py": 25,
    "tests/e2e/platform/test_cli_apps.py": 18,
}


def collect_test_files() -> list[str]:
    # Paths are relative to api/ so they are valid as pytest args inside the
    # test-runner container (CWD=/app, which maps to ./api/ on the host).
    out = []
    for p in sorted(TESTS_ROOT.rglob("test_*.py")):
        rel = p.relative_to(API_ROOT)
        out.append(str(rel))
    return out


def split(files: list[str], total: int) -> list[list[str]]:
    """Bin-pack heavies first, then round-robin the rest."""
    shards: list[list[str]] = [[] for _ in range(total)]
    weights: list[int] = [0] * total

    heavy = sorted(
        ((WEIGHTS[f], f) for f in files if f in WEIGHTS),
        reverse=True,
    )
    light = sorted(f for f in files if f not in WEIGHTS)

    # Heavies: largest into the lightest shard.
    for w, f in heavy:
        i = min(range(total), key=lambda i: weights[i])
        shards[i].append(f)
        weights[i] += w

    # Light files: assume each ~equal. Round-robin by sorted order keeps
    # related files together and is stable across additions.
    for idx, f in enumerate(light):
        i = idx % total
        shards[i].append(f)

    for s in shards:
        s.sort()
    return shards


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shard-id", type=int, required=True, help="1-indexed shard id")
    ap.add_argument("--total", type=int, required=True, help="total shard count")
    args = ap.parse_args()

    if args.shard_id < 1 or args.shard_id > args.total:
        print(f"shard-id must be in 1..{args.total}", file=sys.stderr)
        return 2

    files = collect_test_files()
    if not files:
        print(f"no test files found under {TESTS_ROOT}", file=sys.stderr)
        return 1

    shards = split(files, args.total)
    for f in shards[args.shard_id - 1]:
        print(f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
