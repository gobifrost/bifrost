#!/usr/bin/env python3
"""Require external GitHub Actions to be pinned to full commit SHAs."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path


FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)
USES_RE = re.compile(r"^\s*(?:-\s*)?uses:\s*(?P<value>.+?)\s*$")
WORKFLOW_SUFFIXES = {".yml", ".yaml"}


@dataclass(frozen=True)
class Violation:
    path: Path
    line_number: int
    action: str
    reason: str

    def format(self) -> str:
        return f"{self.path}:{self.line_number}: {self.reason}: {self.action}"


def _strip_inline_comment(value: str) -> str:
    quote: str | None = None
    for index, char in enumerate(value):
        if char in {"'", '"'}:
            if quote == char:
                quote = None
            elif quote is None:
                quote = char
        elif char == "#" and quote is None:
            return value[:index].strip()
    return value.strip()


def _strip_optional_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1].strip()
    return value


def _is_local_or_non_github_action(action: str) -> bool:
    return action.startswith(("./", "../", "docker://"))


def find_unpinned_actions(paths: list[Path]) -> list[Violation]:
    violations: list[Violation] = []
    for path in _iter_workflow_files(paths):
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            match = USES_RE.match(line)
            if not match:
                continue

            action = _strip_optional_quotes(_strip_inline_comment(match.group("value")))
            if _is_local_or_non_github_action(action):
                continue

            if "@" not in action:
                violations.append(Violation(path, line_number, action, "external action is not pinned"))
                continue

            ref = action.rsplit("@", 1)[1]
            if not FULL_SHA_RE.fullmatch(ref):
                violations.append(
                    Violation(
                        path,
                        line_number,
                        action,
                        "external action must use a full 40-character commit SHA",
                    )
                )

    return violations


def _iter_workflow_files(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if path.is_file() and path.suffix in WORKFLOW_SUFFIXES:
            files.append(path)
        elif path.is_dir():
            files.extend(
                child
                for child in path.rglob("*")
                if child.is_file() and child.suffix in WORKFLOW_SUFFIXES
            )
    return sorted(files)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check that external GitHub Actions are pinned to full commit SHAs."
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        default=[Path(".github/workflows"), Path(".github/actions")],
        help="Workflow files or directories to scan.",
    )
    args = parser.parse_args(argv)

    violations = find_unpinned_actions(args.paths)
    if not violations:
        return 0

    print("Found GitHub Actions that are not pinned to full commit SHAs:", file=sys.stderr)
    for violation in violations:
        print(f"  {violation.format()}", file=sys.stderr)
    print(
        "\nUse a full commit SHA and keep the readable version as a comment, for example:\n"
        "  uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
