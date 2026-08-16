#!/usr/bin/env python3
"""Require external GitHub Actions to be pinned to full commit SHAs."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)
USES_RE = re.compile(r"^\s*(?:-\s*)?uses:\s*(?P<value>.+?)\s*$")
WORKFLOW_SUFFIXES = {".yml", ".yaml"}
VERSION_COMMENT_RE = re.compile(r"#\s*(?P<version>v[0-9][^\s#]*)\s*$")


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


def find_mismatched_action_versions(
    paths: list[Path],
    resolve_version: Callable[[str, str], str],
) -> list[Violation]:
    """Verify each readable version comment resolves to the pinned commit."""
    violations: list[Violation] = []
    resolved: dict[tuple[str, str], str] = {}
    for path in _iter_workflow_files(paths):
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            match = USES_RE.match(line)
            if not match:
                continue

            action = _strip_optional_quotes(
                _strip_inline_comment(match.group("value"))
            )
            if _is_local_or_non_github_action(action) or "@" not in action:
                continue

            action_path, pinned_sha = action.rsplit("@", 1)
            if not FULL_SHA_RE.fullmatch(pinned_sha):
                continue

            version_match = VERSION_COMMENT_RE.search(line)
            if not version_match:
                violations.append(
                    Violation(
                        path,
                        line_number,
                        action,
                        "SHA-pinned external action needs a readable version comment",
                    )
                )
                continue

            parts = action_path.split("/")
            if len(parts) < 2:
                continue
            repository = "/".join(parts[:2])
            version = version_match.group("version")
            key = (repository, version)
            try:
                if key not in resolved:
                    resolved[key] = resolve_version(repository, version)
                resolved_sha = resolved[key]
            except (HTTPError, URLError, RuntimeError, ValueError) as exc:
                violations.append(
                    Violation(
                        path,
                        line_number,
                        action,
                        f"could not resolve {repository}@{version}: {exc}",
                    )
                )
                continue

            if resolved_sha.lower() != pinned_sha.lower():
                violations.append(
                    Violation(
                        path,
                        line_number,
                        action,
                        (
                            f"{repository}@{version} resolves to {resolved_sha}, "
                            f"not {pinned_sha}"
                        ),
                    )
                )

    return violations


def resolve_github_action_version(repository: str, version: str) -> str:
    """Resolve an action tag through GitHub's commits API."""
    url = (
        "https://api.github.com/repos/"
        f"{quote(repository, safe='/')}/commits/{quote(version, safe='')}"
    )
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "bifrost-action-pin-check",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    with urlopen(Request(url, headers=headers), timeout=15) as response:  # noqa: S310
        payload = json.load(response)
    sha = payload.get("sha")
    if not isinstance(sha, str) or not FULL_SHA_RE.fullmatch(sha):
        raise ValueError("GitHub returned no full commit SHA")
    return sha


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
    parser.add_argument(
        "--verify-versions",
        action="store_true",
        help="Resolve readable version comments and verify their pinned SHAs.",
    )
    args = parser.parse_args(argv)

    violations = find_unpinned_actions(args.paths)
    if args.verify_versions:
        violations.extend(
            find_mismatched_action_versions(
                args.paths,
                resolve_github_action_version,
            )
        )
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
