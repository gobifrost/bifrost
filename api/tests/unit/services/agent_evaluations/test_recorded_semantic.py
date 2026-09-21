"""Recorded semantic judge B2 contracts."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from shared.agent_recorded_judge import (
    _usage_from_response,
    assertion_hash,
    execute_recorded_semantic_judge,
    prepare_recorded_judge_request,
)
from src.services.llm import LLMResponse
from src.services.llm.base import LLMConfig

pytestmark = pytest.mark.asyncio


PROFILE_ID = "00000000-0000-0000-0000-000000000001"


def _assertion(requirements: list[str] | None = None, *, threshold: float = 0.7):
    params = {
        "rubric": "The answer resolves the ticket.",
        "prompt_version": "recorded-v1",
        "threshold": threshold,
        "judge_snapshot": {
            "profile_id": PROFILE_ID,
            "provider": "openai",
            "model": "judge-v1",
            "endpoint": None,
            "openai_transport": None,
            "anthropic_prompt_cache_supported": None,
            "default_max_tokens": None,
            "extra_params": {},
            "prompt_version": "recorded-v1",
        },
    }
    if requirements is not None:
        params["recorded_evidence_requirements"] = requirements
    return {"type": "llm_judge", "params": params}


def _evidence():
    return {
        "terminal_status": "completed",
        "output": {"answer": "reset your password"},
        "tool_calls": [
            {"name": "update", "arguments": {"id": "1"}, "sequence": 2},
            {"name": "read", "arguments": {"id": "1"}, "sequence": 1},
        ],
        "delegation": {"children": []},
        "real_tool_executions": 0,
        "usage": {"latency_ms": 12},
    }


async def test_recorded_judge_requires_explicit_complete_declared_evidence():
    missing_requirements = prepare_recorded_judge_request(
        _assertion(None), _evidence(), {"terminal_status": True}
    )
    assert missing_requirements.insufficient_outcome["reason"] == "recorded_evidence_requirements_missing"

    incomplete = prepare_recorded_judge_request(
        _assertion(["terminal_status", "usage.tokens"]),
        _evidence(),
        {"terminal_status": True, "usage.tokens": False},
    )
    assert incomplete.insufficient_outcome["reason"] == "incomplete_evidence"
    assert "usage.tokens" in incomplete.insufficient_outcome["detail"]


async def test_recorded_judge_projection_contains_only_declared_dimensions():
    prepared = prepare_recorded_judge_request(
        _assertion(["terminal_status", "tool_calls", "usage.latency_ms"]),
        _evidence(),
        {"terminal_status": True, "tool_calls": True, "usage.latency_ms": True},
    )
    payload = prepared.request_payload
    assert payload is not None
    assert payload["evidence"] == {
        "terminal_status": "completed",
        "tool_calls": ["read", "update"],
        "tool_call_counts": {"read": 1, "update": 1},
        "usage": {"latency_ms": 12},
    }


async def test_recorded_semantic_judge_scores_and_preserves_parse_error_usage(monkeypatch):
    config = LLMConfig(provider="openai", model="judge-v1", api_key="test")
    responses = [
        LLMResponse(
            content='{"sufficient_evidence": true, "score": 0.8, "rationale": "ok"}',
            input_tokens=10,
            output_tokens=4,
            provider_cost=Decimal("0.0002"),
        ),
        LLMResponse(
            content="not json",
            input_tokens=11,
            output_tokens=5,
            cache_read_tokens=2,
            provider_cost=Decimal("0.0003"),
        ),
    ]

    async def fake_config(_session, *, profile_id, **_kwargs):
        assert str(profile_id) == PROFILE_ID
        return config

    class FakeClient:
        def __init__(self, resolved):
            assert resolved is config

        async def complete(self, *_args, **_kwargs):
            return responses.pop(0)

    monotonic_values = iter([100.0, 100.125, 200.0, 200.5])

    monkeypatch.setattr("src.services.llm.factory.get_llm_config", fake_config)
    monkeypatch.setattr("src.services.llm.pydantic_client.PydanticAIClient", FakeClient)
    monkeypatch.setattr("shared.agent_recorded_judge.monotonic", lambda: next(monotonic_values))
    prepared = prepare_recorded_judge_request(
        _assertion(["terminal_status"]),
        _evidence(),
        {"terminal_status": True},
    )
    passed = await execute_recorded_semantic_judge(
        assertion=_assertion(["terminal_status"]),
        request_payload=prepared.request_payload,
    )
    assert passed.outcome["outcome"] == "passed"
    assert passed.usage["provider_cost"] == Decimal("0.0002")
    assert passed.usage["duration_ms"] == 125

    errored = await execute_recorded_semantic_judge(
        assertion=_assertion(["terminal_status"]),
        request_payload=prepared.request_payload,
    )
    assert errored.outcome["outcome"] == "error"
    assert errored.outcome["reason"] == "judge_invalid_response"
    assert errored.usage["input_tokens"] == 11
    assert errored.usage["cache_read_tokens"] == 2
    assert errored.usage["duration_ms"] == 500




async def test_recorded_semantic_usage_rejects_negative_token_counts():
    assert (
        _usage_from_response(
            LLMResponse(content='{"ok": true}', input_tokens=-1, output_tokens=3),
            duration_ms=125,
        )
        is None
    )
    assert (
        _usage_from_response(
            LLMResponse(content='{"ok": true}', input_tokens=3, output_tokens=-1),
            duration_ms=125,
        )
        is None
    )

async def test_recorded_semantic_judge_invalid_score_and_provider_failure(monkeypatch):
    config = LLMConfig(provider="openai", model="judge-v1", api_key="test")

    async def fake_config(_session, *, profile_id, **_kwargs):
        return config

    class BadScoreClient:
        def __init__(self, _resolved):
            pass

        async def complete(self, *_args, **_kwargs):
            return LLMResponse(
                content='{"sufficient_evidence": true, "score": true, "rationale": "bad"}',
                input_tokens=1,
                output_tokens=1,
            )

    monkeypatch.setattr("src.services.llm.factory.get_llm_config", fake_config)
    monkeypatch.setattr("src.services.llm.pydantic_client.PydanticAIClient", BadScoreClient)
    prepared = prepare_recorded_judge_request(
        _assertion(["terminal_status"]), _evidence(), {"terminal_status": True}
    )
    invalid = await execute_recorded_semantic_judge(
        assertion=_assertion(["terminal_status"]), request_payload=prepared.request_payload
    )
    assert invalid.outcome["reason"] == "judge_invalid_response"
    assert invalid.usage["input_tokens"] == 1

    async def drifted_config(_session, *, profile_id, **_kwargs):
        return LLMConfig(provider="openai", model="changed", api_key="test")

    monkeypatch.setattr("src.services.llm.factory.get_llm_config", drifted_config)
    provider = await execute_recorded_semantic_judge(
        assertion=_assertion(["terminal_status"]), request_payload=prepared.request_payload
    )
    assert provider.outcome["reason"] == "judge_provider_error"
    assert provider.usage is None
    assert provider.unobserved_reason == "provider_error"


async def test_recorded_semantic_resume_is_per_assertion_and_preserves_terminal_outcomes():
    from shared.agent_recorded_admission import _merge_existing_recorded_semantic_outcomes

    fresh = [
        {"type": "llm_judge", "outcome": "pending_judge"},
        {"type": "llm_judge", "outcome": "pending_judge"},
        {"type": "llm_judge", "outcome": "pending_judge"},
    ]
    assertions = [
        _assertion(["terminal_status"]),
        _assertion(["terminal_status", "output"]),
        _assertion(["output"]),
    ]
    existing = [
        {
            "type": "llm_judge",
            "outcome": "passed",
            "judge_execution_state": "completed",
            "actual": 0.9,
            "assertion_index": 0,
            "assertion_hash": assertion_hash(assertions[0]),
        },
        {
            "type": "llm_judge",
            "outcome": "pending_judge",
            "judge_execution_state": "started",
            "accounting_attempt_id": str(uuid4()),
            "assertion_index": 1,
            "assertion_hash": assertion_hash(assertions[1]),
        },
        {
            "type": "llm_judge",
            "outcome": "insufficient_evidence",
            "reason": "incomplete_evidence",
            "assertion_index": 2,
            "assertion_hash": assertion_hash(assertions[2]),
        },
    ]

    merged, changed = _merge_existing_recorded_semantic_outcomes(
        fresh, existing, assertions
    )

    assert changed is True
    assert merged[0] is existing[0]
    assert merged[1]["outcome"] == "error"
    assert merged[1]["reason"] == "judge_attempt_ambiguous"
    assert merged[2] is existing[2]
