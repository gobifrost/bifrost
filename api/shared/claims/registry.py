"""Org-level claim registry helpers: load, dependency graph, cycle detection."""

from __future__ import annotations

from typing import Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.contracts.claims import CustomClaim as CustomClaimDTO
from src.models.orm.custom_claims import CustomClaim as CustomClaimORM


def load_org_claims(db: Session, organization_id: UUID) -> dict[str, CustomClaimDTO]:
    rows = db.execute(
        select(CustomClaimORM).where(CustomClaimORM.organization_id == organization_id)
    ).scalars().all()
    return {r.name: CustomClaimDTO.model_validate(r) for r in rows}


def referenced_claim_names(where: object | None) -> set[str]:
    """Walk an Expr-shaped node and collect every {claims: <name>} reference."""
    found: set[str] = set()
    # Unwrap Expr / RootModel so the walker sees the underlying dict.
    node = getattr(where, "root", where)
    _walk(node, found)
    return found


def _walk(node: object, found: set[str]) -> None:
    if isinstance(node, dict):
        if set(node.keys()) == {"claims"} and isinstance(node["claims"], str):
            found.add(node["claims"])
            return
        for v in node.values():
            _walk(v, found)
        return
    if isinstance(node, list):
        for v in node:
            _walk(v, found)


def claim_dependency_graph(claims: Iterable[CustomClaimDTO]) -> dict[str, set[str]]:
    """Build adjacency: claim_name -> set of other claim names it references."""
    graph: dict[str, set[str]] = {}
    for c in claims:
        graph[c.name] = referenced_claim_names(c.query.where if c.query else None)
    return graph


def find_cycle(graph: dict[str, set[str]]) -> list[str] | None:
    """Return a cycle path if any, else None."""
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {n: WHITE for n in graph}
    parent: dict[str, str | None] = {n: None for n in graph}

    def dfs(u: str) -> list[str] | None:
        color[u] = GRAY
        for v in graph.get(u, ()):
            if v not in color:
                continue  # reference to a name that doesn't exist (caught elsewhere)
            if color[v] == GRAY:
                # reconstruct cycle
                cycle = [v, u]
                while parent[u] is not None and parent[u] != v:
                    u = parent[u]  # type: ignore[assignment]
                    cycle.append(u)
                cycle.append(v)
                return list(reversed(cycle))
            if color[v] == WHITE:
                parent[v] = u
                found = dfs(v)
                if found:
                    return found
        color[u] = BLACK
        return None

    for node in graph:
        if color[node] == WHITE:
            cycle = dfs(node)
            if cycle:
                return cycle
    return None
