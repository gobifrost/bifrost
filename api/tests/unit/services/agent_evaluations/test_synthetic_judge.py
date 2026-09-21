"""Synthetic semantic judge planning helpers."""

from types import SimpleNamespace

import pytest
from uuid import uuid4

from shared.agent_synthetic_judge import (
    build_judge_plans,
    mark_plan_ambiguous,
    usage_from_response,
)


def _assertion() -> dict:
    return {
        "type": "llm_judge",
        "params": {
            "rubric": "Helpful answer",
            "prompt_version": "1",
            "threshold": 0.7,
            "judge_snapshot": {
                "profile_id": str(uuid4()),
                "provider": "openrouter",
                "model": "judge-v1",
                "endpoint": "https://openrouter.ai/api/v1",
                "openai_transport": None,
                "anthropic_prompt_cache_supported": None,
                "default_max_tokens": None,
                "extra_params": {},
            },
        },
    }


def _result(assertion: dict) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        case_id=uuid4(),
        case_version=1,
        repetition_index=0,
        assertion_results=[
            {
                "type": "terminal_status",
                "code": "terminal_status",
                "passed": True,
                "side": "baseline",
            },
            {
                "type": "llm_judge",
                "code": "llm_judge",
                "actual": "pending",
                "side": "baseline",
            },
        ],
        comparison={
            "_semantic_pending": {
                "definitions": [assertion],
                "baseline_evidence": {"output": {"answer": "ok"}},
                "candidate_evidence": None,
            }
        },
    )


def test_synthetic_judge_identity_is_bounded_and_canonicalizes_openrouter():
    assertion = _assertion()
    result = _result(assertion)
    plans = build_judge_plans(
        result,
        execution_id=uuid4(),
        result_id=result.id,
    )

    assert len(plans) == 1
    plan = plans[0]
    assert plan.provider == "openrouter"
    assert plan.idempotency_key.startswith("synthetic-semantic:")
    assert len(plan.idempotency_key) < 255
    assert len(plan.item_id) == 64


def test_mark_plan_ambiguous_preserves_non_authoritative_shape():
    assertion = _assertion()
    result = _result(assertion)
    plan = build_judge_plans(
        result,
        execution_id=uuid4(),
        result_id=result.id,
    )[0]

    mark_plan_ambiguous(result, plan, attempt_id=uuid4())

    exact, semantic = result.assertion_results
    assert exact["type"] == "terminal_status"
    assert semantic["type"] == "llm_judge"
    assert semantic["passed"] is False
    assert semantic["reason"] == "judge_attempt_ambiguous"
    assert semantic["authoritative"] is False
    assert semantic["nondeterministic"] is True


def test_usage_from_response_rejects_missing_or_bool_tokens():
    assert usage_from_response(SimpleNamespace(input_tokens=None, output_tokens=1)) is None
    assert usage_from_response(SimpleNamespace(input_tokens=True, output_tokens=1)) is None
    assert usage_from_response(
        SimpleNamespace(
            input_tokens=10,
            output_tokens=5,
            cache_read_tokens=2,
            cache_write_tokens=1,
            provider_cost="0.02",
        )
    ) == {
        "input_tokens": 10,
        "output_tokens": 5,
        "cache_read_tokens": 2,
        "cache_write_tokens": 1,
        "provider_cost": "0.02",
    }


def test_semantic_child_job_id_malformed_fails_closed():
    result = _result(_assertion())
    result.comparison["semantic_judge_job_id"] = "not-a-uuid"

    from shared.agent_synthetic_judge import semantic_child_job_id

    with pytest.raises(ValueError):
        semantic_child_job_id(result)


def test_mark_unavailable_settles_all_unresolved_and_preserves_terminal_errors():
    result = _result(_assertion())
    result.assertion_results = [
        {
            "type": "llm_judge",
            "code": "llm_judge",
            "actual": "pending",
            "side": "baseline",
            "judge_execution_state": "started",
        },
        {
            "type": "llm_judge",
            "code": "llm_judge",
            "actual": "pending",
            "side": "baseline",
        },
        {
            "type": "llm_judge",
            "code": "llm_judge",
            "actual": None,
            "side": "baseline",
            "reason": "judge_invalid_response",
            "judge_execution_state": "error",
        },
    ]

    from shared.agent_synthetic_judge import mark_unavailable

    assert mark_unavailable(result) is True

    assert [row["judge_execution_state"] for row in result.assertion_results] == [
        "ambiguous",
        "ambiguous",
        "error",
    ]
    assert result.assertion_results[2]["reason"] == "judge_invalid_response"


def test_usage_from_response_rejects_negative_tokens():
    assert usage_from_response(SimpleNamespace(input_tokens=-1, output_tokens=1)) is None
    assert usage_from_response(SimpleNamespace(input_tokens=1, output_tokens=-1)) is None
