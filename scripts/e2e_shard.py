#!/usr/bin/env python3
"""Print the e2e test files for one shard of an N-shard split.

Usage:
    scripts/e2e_shard.py --shard-id 1 --total 3

Allocates files by source size so new and changed tests affect the split
without a manually maintained timing map. Each file stays on one shard.

Print one path per line on stdout, suitable for piping into ./test.sh.
"""

import argparse
import sys
from pathlib import Path
from typing import Mapping

REPO_ROOT = Path(__file__).resolve().parent.parent
API_ROOT = REPO_ROOT / "api"
TESTS_ROOT = API_ROOT / "tests" / "e2e"

def collect_test_files() -> list[str]:
    # Paths are relative to api/ so they are valid as pytest args inside the
    # test-runner container (CWD=/app, which maps to ./api/ on the host).
    out = []
    for p in sorted(TESTS_ROOT.rglob("test_*.py")):
        rel = p.relative_to(API_ROOT)
        out.append(str(rel))
    return out


def split(
    files: list[str], total: int, sizes: Mapping[str, int] | None = None
) -> list[list[str]]:
    """Place larger files first into the shard with the least source."""
    if sizes is None:
        sizes = {file: (API_ROOT / file).stat().st_size for file in files}
    shards: list[list[str]] = [[] for _ in range(total)]
    weights: list[int] = [0] * total
    for file in sorted(files, key=lambda file: (-sizes[file], file)):
        index = min(range(total), key=lambda i: (weights[i], len(shards[i]), i))
        shards[index].append(file)
        weights[index] += sizes[file]

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
