"""Detect Solution workspaces so global _repo commands can refuse to run in them."""
from __future__ import annotations

import pathlib
import sys

DESCRIPTOR = "bifrost.solution.yaml"


def find_solution_root(start: pathlib.Path) -> pathlib.Path | None:
    cur = start.resolve()
    for parent in [cur, *cur.parents]:
        if (parent / DESCRIPTOR).is_file():
            return parent
    return None


def assert_not_solution_workspace(path: str, command: str) -> None:
    root = find_solution_root(pathlib.Path(path)) or find_solution_root(pathlib.Path.cwd())
    if root is not None:
        print(
            f"This is a Solution workspace ({root}). `bifrost {command}` targets the "
            f"global _repo workspace and is disabled here.\nUse `bifrost solution deploy`.",
            file=sys.stderr,
        )
        raise SystemExit(1)
