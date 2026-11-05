"""Shared Cloudflare-derived ranking over already-authorized capabilities.

Adapted from Cloudflare Agents ``connectors/search.ts`` at commit
``321814b6c13f72cff82161e7eb20312574ac9b7a`` under its MIT license. See
``LICENSES/cloudflare-codemode-MIT.txt``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from math import floor
from typing import Any

SEARCH_RESULT_LIMIT = 50

_FIELD_WEIGHTS = {
    "path": 12,
    "connector": 8,
    "method": 10,
    "description": 5,
}
_CAMEL_BOUNDARY = re.compile(r"([a-z0-9])([A-Z])")
_SEPARATORS = re.compile(r"[_./:-]+")
_TOKEN_SEPARATOR = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class CapabilitySearchItem:
    """One currently callable sandbox function in Cloudflare search terms."""

    path: str
    connector: str
    method: str
    description: str | None = None


@dataclass(frozen=True)
class _PreparedField:
    raw: str
    tokens: tuple[str, ...]


def rank_capabilities(
    query: str, items: Iterable[CapabilitySearchItem]
) -> list[dict[str, Any]]:
    """Rank the caller-provided authorized view without imposing a transport cap."""
    return sorted(
        (
            result
            for item in items
            if (result := _score_match(item, query)) is not None
        ),
        key=lambda result: (-result["score"], result["path"]),
    )


def search_capabilities(
    query: str, items: Iterable[CapabilitySearchItem]
) -> dict[str, Any]:
    """Return the bounded discovery projection used inside CodeMode."""
    ranked = rank_capabilities(query, items)
    total = len(ranked)
    return {
        "results": ranked[:SEARCH_RESULT_LIMIT],
        "total": total,
        "truncated": total > SEARCH_RESULT_LIMIT,
    }


def _score_match(item: CapabilitySearchItem, query: str) -> dict[str, Any] | None:
    normalized_query = _normalize_search_text(query)
    query_tokens = _tokenize(query)
    if not normalized_query or not query_tokens:
        return None
    first_query_token = next(iter(query_tokens), None)

    fields = (
        _score_field(normalized_query, query_tokens, _prepare_field(item.path), _FIELD_WEIGHTS["path"]),
        _score_field(
            normalized_query,
            query_tokens,
            _prepare_field(item.connector),
            _FIELD_WEIGHTS["connector"],
        ),
        _score_field(normalized_query, query_tokens, _prepare_field(item.method), _FIELD_WEIGHTS["method"]),
        _score_field(
            normalized_query,
            query_tokens,
            _prepare_field(item.description or ""),
            _FIELD_WEIGHTS["description"],
        ),
    )
    matched_tokens = set().union(*(field[1] for field in fields))
    score = sum(field[0] for field in fields)
    exact_phrase = any(field[2] for field in fields)
    if not matched_tokens:
        return None

    coverage = len(matched_tokens) / len(query_tokens)
    minimum_coverage = 1 if len(query_tokens) <= 2 else 0.6
    if coverage < minimum_coverage and not exact_phrase:
        return None
    score += 25 if coverage == 1 else floor(coverage * 10 + 0.5)

    path_tokens = _tokenize(item.path)
    method_tokens = _tokenize(item.method)
    first_path_token = next(iter(path_tokens), None)
    first_method_token = next(iter(method_tokens), None)
    if first_path_token == first_query_token or (
        first_method_token == first_query_token
    ):
        score += 8
    if _normalize_search_text(item.path) == normalized_query or _normalize_search_text(item.method) == normalized_query:
        score += 20

    result: dict[str, Any] = {
        "path": item.path,
        "connector": item.connector,
        "method": item.method,
        "kind": "method",
        "score": score,
    }
    if item.description is not None:
        result["description"] = item.description
    return result


def _score_field(
    query: str, query_tokens: tuple[str, ...], field: _PreparedField, weight: int
) -> tuple[int, set[str], bool]:
    if not field.raw:
        return 0, set(), False

    score = 0
    matched_tokens: set[str] = set()
    exact_phrase = bool(query) and query in field.raw
    if query:
        if field.raw == query:
            score += weight * 14
        elif field.raw.startswith(query):
            score += weight * 9
        elif exact_phrase:
            score += weight * 6

    for token in query_tokens:
        if token in field.tokens:
            score += weight * 4
            matched_tokens.add(token)
        elif any(candidate.startswith(token) or token.startswith(candidate) for candidate in field.tokens):
            score += weight * 2
            matched_tokens.add(token)
        elif token in field.raw:
            score += weight
            matched_tokens.add(token)
    return score, matched_tokens, exact_phrase


def _prepare_field(value: str) -> _PreparedField:
    return _PreparedField(raw=_normalize_search_text(value), tokens=_tokenize(value))


def _normalize_search_text(value: str) -> str:
    return _SEPARATORS.sub(" ", _CAMEL_BOUNDARY.sub(r"\1 \2", value)).lower().strip()


def _tokenize(value: str) -> tuple[str, ...]:
    return tuple(token for token in _TOKEN_SEPARATOR.split(_normalize_search_text(value)) if token)
