"""Pure per-line matching for source search."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

MAX_LINE_CHARS = 400


@dataclass(frozen=True)
class LineHit:
    line: int
    column: int
    text: str
    context_before: list[str] = field(default_factory=list)
    context_after: list[str] = field(default_factory=list)


def build_matcher(query: str, is_regex: bool, case_sensitive: bool) -> re.Pattern[str]:
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        return re.compile(query if is_regex else re.escape(query), flags)
    except re.error as exc:
        raise ValueError(f"Invalid regex pattern: {exc}") from exc


def window_line(line: str, column: int) -> str:
    """Keep very long (e.g. minified) lines small, centred near the match."""
    if len(line) <= MAX_LINE_CHARS:
        return line
    start = max(0, column - MAX_LINE_CHARS // 4)
    end = start + MAX_LINE_CHARS
    return ("…" if start else "") + line[start:end] + ("…" if end < len(line) else "")


def match_lines(content: str, matcher: re.Pattern[str], context_lines: int) -> list[LineHit]:
    lines = content.replace("\r\n", "\n").split("\n")
    hits: list[LineHit] = []
    for idx, line in enumerate(lines):
        for m in matcher.finditer(line):
            hits.append(LineHit(
                line=idx + 1,
                column=m.start(),
                text=window_line(line, m.start()),
                context_before=[window_line(x, 0) for x in lines[max(0, idx - context_lines):idx]],
                context_after=[window_line(x, 0) for x in lines[idx + 1:idx + 1 + context_lines]],
            ))
            if m.start() == m.end():
                break  # a zero-width match counts once per line, like grep
    return hits
