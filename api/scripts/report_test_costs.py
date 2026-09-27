"""Rank pytest JUnit case time by file without a maintained test map.

Usage: python3 api/scripts/report_test_costs.py /tmp/bifrost-<project>/test-results.xml
Case times are summed work, not suite or shard wall time.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path
from xml.etree import ElementTree


def _module(classname: str) -> str:
    parts = classname.split(".")
    for index, part in enumerate(parts):
        if part.startswith("test_"):
            return ".".join(parts[: index + 1])
    return classname


def summarize(path: Path) -> tuple[list[tuple[str, float, str]], list[tuple[str, float, int]]]:
    """Return case times and module totals from one JUnit result file."""
    cases: list[tuple[str, float, str]] = []
    modules: dict[str, list[float]] = defaultdict(list)
    for case in ElementTree.parse(path).iter("testcase"):
        seconds = float(case.get("time", "0"))
        module = _module(case.get("classname", ""))
        name = case.get("name", "")
        cases.append((f"{module}::{name}", seconds, module))
        modules[module].append(seconds)
    ranked = sorted(
        ((module, sum(times), len(times)) for module, times in modules.items()),
        key=lambda row: row[1],
        reverse=True,
    )
    return sorted(cases, key=lambda row: row[1], reverse=True), ranked


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("junit", type=Path, help="pytest --junitxml output")
    parser.add_argument("--top", type=int, default=15, help="rows per ranking")
    parser.add_argument("--slow", type=float, default=2.0, help="review threshold in seconds")
    args = parser.parse_args()
    cases, modules = summarize(args.junit)
    slow = [case for case in cases if case[1] >= args.slow]
    print(f"Cases: {len(cases)}; summed case time: {sum(case[1] for case in cases):.1f}s")
    print(f"Cases >= {args.slow:g}s: {len(slow)}; summed time: {sum(case[1] for case in slow):.1f}s")
    print("\nTop files (summed case time):")
    for module, seconds, count in modules[: args.top]:
        print(f"{seconds:7.1f}s  {count:4d}  {module}")
    print("\nTop cases:")
    for name, seconds, _ in cases[: args.top]:
        print(f"{seconds:7.1f}s  {name}")
    print("\nThese are summed case durations, not elapsed suite or shard time.")


if __name__ == "__main__":
    main()
