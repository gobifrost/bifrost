"""Windows-style ``name (n).ext`` candidates for files.move rename mode."""

from __future__ import annotations

import os
import re
from collections.abc import Iterable


def _split(path: str) -> tuple[str, str, str]:
    directory, _, name = path.rpartition("/")
    stem, ext = os.path.splitext(name)
    return directory, stem, ext


def _join(directory: str, name: str) -> str:
    return f"{directory}/{name}" if directory else name


def numbered_name(path: str, n: int) -> str:
    """``a/report.pdf`` → ``a/report (n).pdf``; ``.env`` → ``.env (n)``."""
    directory, stem, ext = _split(path)
    return _join(directory, f"{stem} ({n}){ext}")


def candidate_prefix(path: str) -> str:
    """Relative path prefix every candidate of ``path`` starts with."""
    directory, stem, _ = _split(path)
    return _join(directory, f"{stem} (")


def matching_candidates(path: str, names: Iterable[str]) -> dict[int, str]:
    """Map ``n`` to each base name in ``names`` that is exactly ``stem (n)ext``."""
    _, stem, ext = _split(path)
    pattern = re.compile(rf"^{re.escape(stem)} \(([1-9]\d*)\){re.escape(ext)}$")
    found: dict[int, str] = {}
    for name in names:
        match = pattern.match(name)
        if match is not None:
            found[int(match.group(1))] = name
    return found
