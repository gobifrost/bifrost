"""Pure per-line matching for source search.

User patterns run on the ``regex`` engine (Python ``re`` syntax) with a
per-line timeout, so a catastrophic-backtracking pattern fails fast instead of
pinning a worker thread.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Protocol

import regex

MAX_LINE_CHARS = 400
LINE_MATCH_TIMEOUT_S = 1.0


class RegexTimeout(ValueError):
    """A pattern took longer than LINE_MATCH_TIMEOUT_S on one line."""


@dataclass(frozen=True)
class LineHit:
    line: int
    column: int
    text: str
    context_before: list[str] = field(default_factory=list)
    context_after: list[str] = field(default_factory=list)


def build_matcher(query: str, is_regex: bool, case_sensitive: bool) -> "regex.Pattern[str]":
    flags = 0 if case_sensitive else regex.IGNORECASE
    try:
        return regex.compile(query if is_regex else regex.escape(query), flags | regex.V0)
    except regex.error as exc:
        raise ValueError(f"Invalid regex pattern: {exc}") from exc


def window_line(line: str, column: int) -> str:
    """Keep very long (e.g. minified) lines small, centred near the match."""
    if len(line) <= MAX_LINE_CHARS:
        return line
    start = max(0, column - MAX_LINE_CHARS // 4)
    end = start + MAX_LINE_CHARS
    return ("…" if start else "") + line[start:end] + ("…" if end < len(line) else "")


class _Matcher(Protocol):
    def finditer(self, string: str, *, timeout: float) -> Iterator[Any]:
        """Iterate matches in one line, raising TimeoutError past ``timeout``."""
        raise NotImplementedError


def _finditer(matcher: _Matcher, line: str) -> Iterator[Any]:
    try:
        yield from matcher.finditer(line, timeout=LINE_MATCH_TIMEOUT_S)
    except TimeoutError as exc:
        raise RegexTimeout(
            f"The pattern took too long on a single line (over {LINE_MATCH_TIMEOUT_S:g}s), "
            "likely from catastrophic backtracking; simplify it (avoid overlapping alternatives "
            "such as (a|a)+ or nested quantifiers)"
        ) from exc


def iter_line_hits(
    content: str,
    matcher: _Matcher,
    context_lines: int,
    *,
    after: tuple[int, int] | None = None,
) -> Iterator[LineHit]:
    """Yield hits in (line, column) order, lazily, strictly after ``after``.

    Callers take only what a page needs, so a dense or huge file costs one
    page of hit objects, and resuming from a cursor starts at its line.
    """
    lines = content.replace("\r\n", "\n").split("\n")
    start = after[0] - 1 if after else 0
    for idx in range(max(start, 0), len(lines)):
        line = lines[idx]
        for m in _finditer(matcher, line):
            if after is not None and (idx + 1, m.start()) <= after:
                if m.start() == m.end():
                    break
                continue
            yield LineHit(
                line=idx + 1,
                column=m.start(),
                text=window_line(line, m.start()),
                context_before=[window_line(x, 0) for x in lines[max(0, idx - context_lines):idx]],
                context_after=[window_line(x, 0) for x in lines[idx + 1:idx + 1 + context_lines]],
            )
            if m.start() == m.end():
                break  # a zero-width match counts once per line, like grep


def count_matches(content: str, matcher: _Matcher) -> tuple[int, int]:
    """Return ``(match_count, first_line)`` without building hit objects."""
    count = first = 0
    for idx, line in enumerate(content.replace("\r\n", "\n").split("\n")):
        for m in _finditer(matcher, line):
            count += 1
            first = first or idx + 1
            if m.start() == m.end():
                break
    return count, first
