"""Lazy, request-scoped resolution of Custom Claims for the calling user."""

from __future__ import annotations

from typing import Any

from src.models.contracts.claims import CustomClaim


def resolve_claim(claim: CustomClaim, user: Any, db: Any) -> Any:
    """Resolve a claim for the calling user; cache on `user.claims[<name>]`.

    Returns list for type=list, scalar or None for type=scalar.
    """
    cache = _get_or_init_cache(user)
    if claim.name in cache:
        return cache[claim.name]

    rows = _run_claim_query(claim, user, db)
    values = [row.get(claim.query.select) for row in rows]
    result = values if claim.type == "list" else (values[0] if values else None)
    cache[claim.name] = result
    return result


def _get_or_init_cache(user: Any) -> dict:
    cache = getattr(user, "claims", None)
    if cache is None:
        cache = {}
        setattr(user, "claims", cache)  # principal must be mutable; raises if not
    return cache


def _run_claim_query(claim: CustomClaim, user: Any, db: Any) -> list[dict]:
    """Dispatch to ``shared.claims.runner.run`` (kept as a seam for tests)."""
    from shared.claims import runner  # local import to avoid circular at module load

    return runner.run(claim, user, db)
