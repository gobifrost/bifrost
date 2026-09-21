from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from shared.agent_reviews import (
    AgentReviewServiceError,
    parse_review_response,
    usage_from_response,
)
from src.services.llm.base import LLMResponse


def test_parse_review_response_accepts_strict_shape():
    run_id = uuid4()

    summary, findings = parse_review_response(
        {
            "summary": "Two issues found",
            "findings": [
                {
                    "kind": "problem",
                    "description": "The agent missed the SLA.",
                    "expected_behavior": "Escalate the SLA breach.",
                    "evidence_markdown": "Run evidence cites the missed SLA.",
                    "source_run_ids": [str(run_id)],
                }
            ],
        },
        selected_run_ids=[run_id],
    )

    assert summary == "Two issues found"
    assert findings[0].ordinal == 0
    assert findings[0].finding_kind == "problem"
    assert findings[0].source_run_ids == [run_id]


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"summary": "ok", "findings": "not-list"},
        {"summary": "x" * 2001, "findings": []},
        {"summary": "ok", "findings": [{"kind": "bad", "description": "d", "source_run_ids": []}]},
        {"summary": "ok", "findings": [{"kind": "problem", "description": "", "source_run_ids": []}]},
    ],
)
def test_parse_review_response_rejects_malformed_or_oversized(payload):
    with pytest.raises(AgentReviewServiceError) as exc:
        parse_review_response(payload, selected_run_ids=[uuid4()])
    assert exc.value.code == "invalid_review_response"


def test_parse_review_response_rejects_foreign_or_duplicate_source_ids():
    allowed = uuid4()
    foreign = uuid4()
    for source_ids in ([str(foreign)], [str(allowed), str(allowed)]):
        with pytest.raises(AgentReviewServiceError) as exc:
            parse_review_response(
                {
                    "summary": None,
                    "findings": [
                        {
                            "kind": "opportunity",
                            "description": "Improve evidence handling.",
                            "source_run_ids": source_ids,
                        }
                    ],
                },
                selected_run_ids=[allowed],
            )
        assert exc.value.code == "invalid_review_response"


def test_usage_from_response_reads_direct_llmresponse_fields_and_duration():
    response = LLMResponse(
        content='{"summary": null, "findings": []}',
        input_tokens=12,
        output_tokens=5,
        cache_read_tokens=2,
        cache_write_tokens=1,
        provider_cost=Decimal("0.00012345"),
    )

    usage = usage_from_response(response, duration_ms=42)

    assert usage == {
        "input_tokens": 12,
        "output_tokens": 5,
        "cache_read_tokens": 2,
        "cache_write_tokens": 1,
        "provider_cost": Decimal("0.00012345"),
        "duration_ms": 42,
    }


@pytest.mark.parametrize(
    "response",
    [
        LLMResponse(input_tokens=None, output_tokens=1),
        LLMResponse(input_tokens=1, output_tokens=None),
        LLMResponse(input_tokens=-1, output_tokens=1),
        LLMResponse(input_tokens=True, output_tokens=1),
        LLMResponse(input_tokens=1, output_tokens=1, cache_read_tokens=-1),
    ],
)
def test_usage_from_response_rejects_missing_or_invalid_counts(response):
    assert usage_from_response(response, duration_ms=1) is None
