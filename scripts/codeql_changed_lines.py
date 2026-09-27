#!/usr/bin/env python3
"""Print added/edited line ranges since origin/main for local CodeQL PR checks."""

import json
import re
import subprocess
from pathlib import Path


def changed_lines(diff: str) -> dict[str, list[list[int]]]:
    ranges: dict[str, list[list[int]]] = {}
    path: str | None = None
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            path = line[6:]
        elif line.startswith("+++ /dev/null"):
            path = None
        elif line.startswith("@@ ") and path is not None:
            match = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", line)
            if match:
                start = int(match.group(1))
                count = int(match.group(2) or "1")
                if count:
                    ranges.setdefault(path, []).append([start, start + count - 1])
    return ranges


def main() -> None:
    diff = subprocess.run(
        ["git", "diff", "--no-ext-diff", "--unified=0", "origin/main", "--"],
        check=True, capture_output=True, text=True,
    ).stdout
    ranges = changed_lines(diff)
    root = Path(subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"], text=True,
    ).strip())
    for path in subprocess.check_output(
        ["git", "ls-files", "--others", "--exclude-standard"], text=True,
    ).splitlines():
        if path not in ranges and (root / path).is_file():
            count = len((root / path).read_bytes().splitlines())
            if count:
                ranges[path] = [[1, count]]
    print(json.dumps(ranges))


if __name__ == "__main__":
    main()
