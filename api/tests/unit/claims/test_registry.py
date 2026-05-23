"""Unit tests for the claims registry helpers."""
from src.models.contracts.claims import ClaimQuery, CustomClaim
from shared.claims.registry import (
    claim_dependency_graph,
    find_cycle,
    referenced_claim_names,
)
from uuid import uuid4


def _claim(name: str, where=None):
    return CustomClaim(
        id=uuid4(),
        organization_id=uuid4(),
        name=name,
        type="list",
        query=ClaimQuery(table="t", select="x", where=where),
    )


# referenced_claim_names

def test_referenced_claim_names_walks_nested():
    expr = {"and": [
        {"in": [{"row": "campus_id"}, {"claims": "allowed_campus_ids"}]},
        {"in": [{"row": "doc_type_id"}, {"claims": "allowed_doc_type_ids"}]},
    ]}
    assert referenced_claim_names(expr) == {"allowed_campus_ids", "allowed_doc_type_ids"}


def test_referenced_claim_names_no_refs():
    assert referenced_claim_names({"eq": [{"row": "x"}, "y"]}) == set()


def test_referenced_claim_names_none():
    assert referenced_claim_names(None) == set()


def test_referenced_claim_names_deeply_nested():
    expr = {"or": [
        {"and": [{"in": [{"row": "a"}, {"claims": "A"}]}]},
        {"in": [{"row": "b"}, {"claims": "B"}]},
    ]}
    assert referenced_claim_names(expr) == {"A", "B"}


# claim_dependency_graph

def test_dependency_graph_empty_for_claim_with_no_refs():
    c = _claim("a", where={"eq": [{"row": "u"}, "v"]})
    g = claim_dependency_graph([c])
    assert g == {"a": set()}


def test_dependency_graph_collects_refs():
    a = _claim("a", where={"in": [{"row": "x"}, {"claims": "b"}]})
    b = _claim("b")
    g = claim_dependency_graph([a, b])
    assert g == {"a": {"b"}, "b": set()}


# find_cycle

def test_find_cycle_returns_none_when_acyclic():
    g = {"a": {"b"}, "b": {"c"}, "c": set()}
    assert find_cycle(g) is None


def test_find_cycle_detects_self_loop():
    g = {"a": {"a"}}
    cycle = find_cycle(g)
    assert cycle is not None
    assert "a" in cycle


def test_find_cycle_detects_two_node_cycle():
    g = {"a": {"b"}, "b": {"a"}}
    cycle = find_cycle(g)
    assert cycle is not None
    assert set(cycle) >= {"a", "b"}


def test_find_cycle_detects_longer_cycle():
    g = {"a": {"b"}, "b": {"c"}, "c": {"a"}}
    cycle = find_cycle(g)
    assert cycle is not None
    assert set(cycle) >= {"a", "b", "c"}


def test_find_cycle_ignores_missing_refs():
    # A claim references an unknown name; not a cycle.
    g = {"a": {"nonexistent"}}
    assert find_cycle(g) is None
