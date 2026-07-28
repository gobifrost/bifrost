"""Unit tests for per-turn knowledge retrieval limits."""

import json

import pytest

from src.services.knowledge.search_budget import (
    MAX_KNOWLEDGE_EVIDENCE_CHARS_PER_TURN,
    MAX_KNOWLEDGE_METADATA_CHARS,
    MAX_KNOWLEDGE_RESULTS,
    MAX_KNOWLEDGE_SEARCHES_PER_TURN,
    KnowledgeSearchBudget,
    clamp_knowledge_result_limit,
    compact_knowledge_metadata,
    knowledge_search_rejection_payload,
    select_novel_knowledge_evidence,
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
    assert budget.evidence_chars_used == 0
    assert budget.reserve("one query too many").allowed is True


def test_evidence_is_deduplicated_across_different_queries():
    budget = KnowledgeSearchBudget()

    first = select_novel_knowledge_evidence(
        budget,
        [
            ("chunk-1", {"content": "Technical POC settings"}),
            ("chunk-2", {"content": "Billing POC settings"}),
        ],
    )
    second = select_novel_knowledge_evidence(
        budget,
        [
            ("chunk-1", {"content": "Technical POC settings"}),
            ("chunk-3", {"content": "Contact type configuration"}),
        ],
    )

    assert [doc["content"] for doc in first.documents] == [
        "Technical POC settings",
        "Billing POC settings",
    ]
    assert [doc["content"] for doc in second.documents] == [
        "Contact type configuration"
    ]
    assert second.omitted_duplicates == 1
    assert second.omitted_for_budget == 0
    assert second.evidence_chars_used <= MAX_KNOWLEDGE_EVIDENCE_CHARS_PER_TURN


def test_evidence_envelope_rejects_payload_that_would_overflow():
    budget = KnowledgeSearchBudget(max_evidence_chars=512)

    selection = select_novel_knowledge_evidence(
        budget,
        [
            ("fits", {"content": "a" * 400}),
            ("overflows", {"content": "b" * 400}),
        ],
    )

    assert len(selection.documents) == 1
    assert selection.omitted_for_budget == 1
    assert selection.evidence_chars_used <= 512
    assert selection.evidence_chars_remaining >= 0


def test_compact_metadata_keeps_grounding_and_drops_transport_noise():
    metadata = {
        "image_uuids": [f"image-{index}" for index in range(1_000)],
        "custom_category": "users",
        "title": "Site Contacts",
        "parent_slug": "site-contacts",
    }

    compacted = compact_knowledge_metadata(metadata)

    assert compacted == {
        "title": "Site Contacts",
        "parent_slug": "site-contacts",
        "custom_category": "users",
    }
    assert len(json.dumps(compacted)) <= MAX_KNOWLEDGE_METADATA_CHARS


def test_full_evidence_envelope_rejects_another_search_and_resets():
    budget = KnowledgeSearchBudget(max_evidence_chars=512)
    assert budget.claim_evidence("chunk", 512) == "accepted"

    rejected = budget.reserve("another query")

    assert rejected.allowed is False
    assert rejected.rejection == "evidence_budget_exhausted"
    assert budget.exhausted is True
    payload = knowledge_search_rejection_payload(rejected)
    assert payload["reason"] == "evidence_budget_exhausted"
    assert payload["evidence_chars_remaining"] == 0

    budget.reset()
    assert budget.evidence_chars_used == 0
    assert budget.reserve("another query").allowed is True


def test_search_knowledge_tool_schema_advertises_runtime_limit():
    from src.services.mcp_server.server import get_system_tools

    tool = next(
        item for item in get_system_tools() if item["id"] == "search_knowledge"
    )
    limit_schema = tool["parameters"]["properties"]["limit"]

    assert limit_schema["minimum"] == 1
    assert limit_schema["maximum"] == MAX_KNOWLEDGE_RESULTS
    assert limit_schema["default"] == MAX_KNOWLEDGE_RESULTS
