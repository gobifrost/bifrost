"""Lazy, request-scoped resolution of Custom Claims for the calling user."""

from __future__ import annotations

from typing import Any

from src.models.contracts.claims import CustomClaim


def resolve_claim(claim: CustomClaim, user: Any, db: Any) -> list | object | None:
    """Resolve a claim for the calling user; cache on `user.claims[<name>]`.

    Returns:
      - list[scalar] for `type == "list"` (empty list if no rows match)
      - scalar | None for `type == "scalar"` (None if no rows match)
    """
    cache = _get_or_init_cache(user)
    if claim.name in cache:
        return cache[claim.name]

    rows = _run_claim_query(claim, user, db)
    values = [row.get(claim.query.select) for row in rows]

    if claim.type == "list":
        result: object = values
    else:  # scalar
        result = values[0] if values else None

    cache[claim.name] = result
    return result


def _get_or_init_cache(user: Any) -> dict:
    cache = getattr(user, "claims", None)
    if cache is None:
        cache = {}
        try:
            setattr(user, "claims", cache)
        except AttributeError:
            # Fall back: principal is read-only — return a transient cache.
            return cache
    return cache


def _run_claim_query(claim: CustomClaim, user: Any, db: Any) -> list[dict]:
    """Run the claim's query against the source table as the calling user.

    Wired in Task 8 — for now this is the seam the tests monkeypatch.
    """
    raise NotImplementedError(
        "_run_claim_query is wired in shared/claims/runner.py — see Task 8"
    )
