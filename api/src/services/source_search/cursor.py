"""Opaque keyset cursors bound to one search's query and filters."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import asdict, dataclass

from src.models.contracts.editor import SearchRequest


@dataclass(frozen=True)
class SearchPosition:
    rank: int      # 0 = workspace, 1 = Solution source
    scope: str     # "" for workspace, the Solution install UUID otherwise
    path: str
    line: int      # 0 in files mode
    column: int
    seen: int      # results returned before this position, for "results 26-50"


def fingerprint(request: SearchRequest) -> str:
    """Identity of a search: every field except the page size and position."""
    body = request.model_dump(mode="json", exclude={"cursor", "limit"})
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:16]


def encode_cursor(fp: str, pos: SearchPosition) -> str:
    raw = json.dumps({"f": fp, **asdict(pos)}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(token: str, fp: str) -> SearchPosition:
    try:
        data = json.loads(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)))
        found = data.pop("f")
        pos = SearchPosition(**data)
    except Exception as exc:  # any malformed token is the caller's error
        raise ValueError("cursor is invalid — start the search again without cursor") from exc
    if found != fp:
        raise ValueError(
            "cursor does not belong to this search — start without cursor or repeat "
            "the original query and filters"
        )
    return pos
