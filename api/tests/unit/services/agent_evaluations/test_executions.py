"""Scored usage persistence: per-side usage survives scoring in comparison JSON."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.services.agent_evaluations.executions import apply_terminal_event


def _result() -> SimpleNamespace:
    return SimpleNamespace(
        baseline_run_id=None,
        candidate_run_id=None,
        comparison=None,
        status="pending",
        assertion_results=[],
        tokens_used=None,
        turns_used=None,
        duration_ms=None,
        cost_usd=None,
        simulator_state_hash=None,
        error=None,
    )


def _evidence(usage: dict) -> dict:
    return {
        "terminal_status": "completed",
        "output": {"answer": "ok"},
        "tool_calls": [],
        "usage": usage,
    }


def _baseline_usage() -> dict:
    return {
        "iterations": 2,
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_read_tokens": 40,
        "cache_write_tokens": 10,
        "cache_hit_fraction": 0.4,
        "tokens": 150,
        "latency_ms": 1200,
    }


def test_baseline_only_result_preserves_baseline_usage():
    result = _result()

    advanced = apply_terminal_event(
        result,
        side="baseline",
        run_id=uuid4(),
        status="completed",
        evidence=_evidence(_baseline_usage()),
    )

    assert advanced is True
    assert result.status == "passed"
    assert result.comparison["usage"] == {
        "baseline": _baseline_usage(),
        "candidate": None,
    }
    # Existing model rollups keep working from the same evidence.
    assert result.tokens_used == 150
    assert result.turns_used == 2
    assert result.duration_ms == 1200


def test_paired_result_preserves_both_sides_and_usage_delta():
    result = _result()
    baseline_id, candidate_id = uuid4(), uuid4()
    candidate_usage = {
        "iterations": 3,
        "input_tokens": 200,
        "output_tokens": 60,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "cache_hit_fraction": 0.0,
        "tokens": 260,
        "latency_ms": 1500,
    }

    assert apply_terminal_event(
        result,
        side="baseline",
        run_id=baseline_id,
        status="completed",
        evidence=_evidence(_baseline_usage()),
        expects_candidate=True,
    ) is True
    # Baseline alone must wait for its pair in a candidate execution.
    assert result.status == "running"
    assert "usage" not in (result.comparison or {})
    assert apply_terminal_event(
        result,
        side="candidate",
        run_id=candidate_id,
        status="completed",
        evidence=_evidence(candidate_usage),
        expects_candidate=True,
    ) is True

    assert result.status == "passed"
    assert result.comparison["usage"] == {
        "baseline": _baseline_usage(),
        "candidate": candidate_usage,
    }
    delta = result.comparison["usage_delta"]
    assert delta["input_tokens"] == {
        "baseline": 100,
        "candidate": 200,
        "delta": 100,
    }
    assert delta["cache_hit_fraction"]["baseline"] == pytest.approx(0.4)
    assert delta["cache_hit_fraction"]["candidate"] == pytest.approx(0.0)
    assert delta["cache_hit_fraction"]["delta"] == pytest.approx(-0.4)
