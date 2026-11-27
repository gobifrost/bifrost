"""Pure summary compute contract tests."""
import json
from decimal import Decimal

import pytest

from shared.agent_summary_compute import (
    SummaryCompletion,
    SummaryComputeInput,
    compute_summary,
)


@pytest.mark.asyncio
async def test_compute_summary_builds_the_canonical_prompt_and_parses_one_completion():
    requests = []

    async def complete(request):
        requests.append(request)
        return SummaryCompletion(
            content=json.dumps(
                {
                    "asked": "a" * 500,
                    "did": "Used [exact_tool]",
                    "answered": "done",
                    "confidence": 1.4,
                    "confidence_reason": "evidence",
                    "metadata": {"ticket": 42, "ignored": {"nested": True}},
                }
            ),
            model="summary-model-revision",
            input_tokens=11,
            output_tokens=7,
            provider_cost=str(Decimal("0.00123")),
        )

    result = await compute_summary(
        SummaryComputeInput(
            agent_name="Ticket Agent",
            agent_system_prompt="Resolve the ticket.",
            run_input={"tool_call": "exact_tool"},
            run_output={"status": "done"},
            model="summary-model",
        ),
        complete,
    )

    assert len(requests) == 1
    assert requests[0].model == "summary-model"
    assert "TOOL MARKERS — STRICT RULE" in requests[0].messages[0].content
    assert '"agent_name": "Ticket Agent"' in requests[0].messages[1].content
    assert result.failure is None
    assert result.completion is not None
    assert result.completion.provider_cost == "0.00123"
    assert result.fields is not None
    assert result.fields.asked == "a" * 400
    assert result.fields.did == "Used [exact_tool]"
    assert result.fields.confidence == 1.0
    assert result.fields.metadata == {"ticket": "42"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content", "failure_kind", "error_fragment"),
    [
        ("", "empty_content", "empty content"),
        ("not json", "invalid_json", "Invalid JSON"),
        ('{"asked": "unfinished"', "invalid_json", "truncated"),
        ("[]", "not_object", "JSON object"),
    ],
)
async def test_compute_summary_preserves_completion_failure_classification(
    content, failure_kind, error_fragment
):
    async def complete(_request):
        return SummaryCompletion(content=content)

    result = await compute_summary(
        SummaryComputeInput("", "", {}, {}, "summary-model"), complete
    )

    assert result.fields is None
    assert result.failure is not None
    assert result.failure.kind == failure_kind
    assert error_fragment.lower() in result.failure.error.lower()


@pytest.mark.asyncio
async def test_compute_summary_returns_provider_failure_without_retrying():
    calls = 0

    async def complete(_request):
        nonlocal calls
        calls += 1
        raise RuntimeError("429 rate limited")

    result = await compute_summary(
        SummaryComputeInput("", "", {}, {}, "summary-model"), complete
    )

    assert calls == 1
    assert result.fields is None
    assert result.failure is not None
    assert result.failure.kind == "provider"
    assert "RuntimeError" in result.failure.error
    assert "429 rate limited" in result.failure.error
