"""Unit tests for per-turn knowledge retrieval limits."""

import pytest

from src.services.knowledge.search_budget import (
    MAX_KNOWLEDGE_RESULTS,
    MAX_KNOWLEDGE_SEARCHES_PER_TURN,
    KnowledgeSearchBudget,
    clamp_knowledge_result_limit,
    knowledge_search_rejection_payload,
)


@pytest.mark.parametrize(
    ("requested", "expected"),
    [
        (-10, 1),
        (0, 1),
        (1, 1),
        (MAX_KNOWLEDGE_RESULTS, MAX_KNOWLEDGE_RESULTS),
        (10, MAX_KNOWLEDGE_RESULTS),
        ("3", 3),
        ("invalid", MAX_KNOWLEDGE_RESULTS),
        (None, MAX_KNOWLEDGE_RESULTS),
    ],
)
def test_clamp_knowledge_result_limit(requested, expected):
    assert clamp_knowledge_result_limit(requested) == expected


def test_duplicate_query_is_rejected_after_normalization():
    budget = KnowledgeSearchBudget()

    first = budget.reserve("  Contact   Roles ")
    duplicate = budget.reserve("contact roles")

    assert first.allowed is True
    assert duplicate.allowed is False
    assert duplicate.rejection == "duplicate_query"
    assert duplicate.searches_used == 1
    assert duplicate.searches_remaining == (
        MAX_KNOWLEDGE_SEARCHES_PER_TURN - 1
    )
    payload = knowledge_search_rejection_payload(duplicate)
    assert payload["search_skipped"] is True
    assert payload["documents"] == []
    assert "already searched" in str(payload["message"])


def test_budget_rejects_fourth_unique_query_and_resets():
    budget = KnowledgeSearchBudget()

    for index in range(MAX_KNOWLEDGE_SEARCHES_PER_TURN):
        decision = budget.reserve(f"query {index}")
        assert decision.allowed is True

    rejected = budget.reserve("one query too many")
    assert rejected.allowed is False
    assert rejected.rejection == "search_budget_exhausted"
    assert rejected.searches_remaining == 0
    assert budget.exhausted is True

    budget.reset()
    assert budget.exhausted is False
    assert budget.searches_used == 0
    assert budget.reserve("one query too many").allowed is True


def test_search_knowledge_tool_schema_advertises_runtime_limit():
    from src.services.mcp_server.server import get_system_tools

    tool = next(
        item for item in get_system_tools() if item["id"] == "search_knowledge"
    )
    limit_schema = tool["parameters"]["properties"]["limit"]

    assert limit_schema["minimum"] == 1
    assert limit_schema["maximum"] == MAX_KNOWLEDGE_RESULTS
    assert limit_schema["default"] == MAX_KNOWLEDGE_RESULTS
