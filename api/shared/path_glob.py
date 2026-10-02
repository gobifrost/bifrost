"""ripgrep/gitignore-style path globs for source search.

* ``*`` and ``?`` never cross ``/``; ``**`` does. ``{a,b}`` alternates; ``[...]``
  / ``[!...]`` are classes. Matching is case-sensitive.
* A pattern with no ``/`` (other than a trailing one) matches at any depth.
  A leading ``/`` anchors to the root. A trailing ``/`` matches a directory.
* A pattern matching a directory also matches everything below it.
* Exclusion globs (a leading ``!``) are rejected rather than silently matching
  nothing.
"""

from __future__ import annotations

import re

_META = set("*?[{")


def _expand_braces(pattern: str) -> list[str]:
    start = pattern.find("{")
    if start == -1:
        if "}" in pattern:
            raise ValueError(f"unbalanced '}}' in glob {pattern!r}")
        return [pattern]
    depth = 0
    end = start
    for end in range(start, len(pattern)):
        if pattern[end] == "{":
            depth += 1
        elif pattern[end] == "}":
            depth -= 1
            if depth == 0:
                break
    if depth != 0:
        raise ValueError(f"unbalanced '{{' in glob {pattern!r}")
    body, parts, level, last = pattern[start + 1:end], [], 0, 0
    for i, ch in enumerate(body):
        if ch == "{":
            level += 1
        elif ch == "}":
            level -= 1
        elif ch == "," and level == 0:
            parts.append(body[last:i])
            last = i + 1
    parts.append(body[last:])
    head, tail = pattern[:start], pattern[end + 1:]
    return [x for p in parts for x in _expand_braces(head + p + tail)]


def _translate(pattern: str) -> str:
    out: list[str] = []
    i, n = 0, len(pattern)
    while i < n:
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        elif pattern[i] == "[":
            close = pattern.find("]", i + 2)
            if close == -1:
                out.append(re.escape("["))
                i += 1
                continue
            body = pattern[i + 1:close]
            if body.startswith("!"):
                body = "^" + body[1:]
            escaped = body.replace("\\", "\\\\")
            out.append(f"[{escaped}]")
            i = close + 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return "".join(out)


def compile_glob(pattern: str) -> re.Pattern[str]:
    """Compile a source-search glob into an anchored regex over repo-relative paths.

    Raises ValueError for exclusion globs (``!pattern``) and malformed globs.
    """
    if pattern.startswith("!"):
        raise ValueError(
            "Exclusion globs ('!pattern') are not supported; pass the files to "
            "include instead, e.g. 'api/**/*.py'"
        )
    alternatives = []
    for alt in _expand_braces(pattern):
        anchored = alt.startswith("/")
        alt = alt.strip("/")
        body = _translate(alt)
        if not anchored and "/" not in alt:
            body = "(?:.*/)?" + body
        alternatives.append(body)
    try:
        return re.compile(r"\A(?:" + "|".join(alternatives) + r")\Z")
    except re.error as exc:
        raise ValueError(f"Invalid glob {pattern!r}: {exc}") from exc


def glob_matches(compiled: re.Pattern[str], path: str) -> bool:
    """True when ``path`` or any of its ancestor directories matches."""
    parts = path.split("/")
    return any(compiled.match("/".join(parts[:k])) for k in range(len(parts), 0, -1))


def glob_literal_prefix(pattern: str) -> str:
    """The anchored directory prefix every match must start with, or ``""``."""
    alt = pattern.lstrip("/")
    if "{" in alt or ("/" not in alt.rstrip("/") and not pattern.startswith("/")):
        return ""
    prefix: list[str] = []
    for ch in alt:
        if ch in _META:
            break
        prefix.append(ch)
    literal = "".join(prefix)
    return literal[: literal.rfind("/") + 1]
