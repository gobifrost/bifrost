"""Recorded-outcome semantics: admission gates, fail-closed aggregation."""

from __future__ import annotations

import copy

import pytest

from shared.agent_recorded_evaluation import (
    aggregate_recorded_evaluation,
    aggregate_recorded_pair,
    evaluate_recorded_assertions,
)
from src.services.agent_evaluations.assertions import AssertionDefinitionError


def _evidence(**over) -> dict:
    base = {
        "terminal_status": "completed",
        "output": {"answer": "reset your password", "ticket_id": "ticket-0001"},
        "tool_calls": [
            {"name": "get_ticket", "arguments": {"id": "ticket-0001"}, "sequence": 0},
            {"name": "update_ticket", "arguments": {"id": "ticket-0001"}, "sequence": 1},
        ],
        "simulator_state": {"ticket": {"ticket-0001": {"status": "resolved"}}},
        "delegation": {"children": []},
        "usage": {
            "iterations": 2,
            "tokens": 400,
            "cost_usd": 0.01,
            "latency_ms": 1200,
        },
        "real_tool_executions": 0,
    }
    base.update(over)
    return base


def _complete(**over) -> dict:
    base = {
        "terminal_status": True,
        "output": True,
        "tool_calls": True,
        "tool_order": True,
        "tool_arguments": True,
        "delegation": True,
        "real_tool_executions": True,
        "usage.iterations": True,
        "usage.tokens": True,
        "usage.cost_usd": True,
        "usage.latency_ms": True,
    }
    base.update(over)
    return base


def _judge_assertion() -> dict:
    return {
        "type": "llm_judge",
        "params": {
            "rubric": "The answer is helpful.",
            "prompt_version": "3",
            "threshold": 0.7,
            "judge_snapshot": {
                "profile_id": "00000000-0000-0000-0000-000000000001",
                "provider": "openai",
                "model": "judge-v1",
                "endpoint": None,
                "openai_transport": None,
                "prompt_version": "3",
            },
        },
    }


def _run(assertions, evidence=None, completeness=None, applicability="applicable"):
    return evaluate_recorded_assertions(
        assertions,
        evidence if evidence is not None else _evidence(),
        completeness=completeness if completeness is not None else _complete(),
        applicability=applicability,
    )


def test_forbidden_tool_cannot_pass_an_incomplete_trace():
    outcomes = _run(
        [{"type": "forbidden_tool", "params": {"tool": "delete_ticket"}}],
        _evidence(tool_calls=[]),
        _complete(tool_calls=False),
    )
    assert outcomes[0]["outcome"] == "insufficient_evidence"
    assert outcomes[0]["passed"] is False


def test_tool_not_called_cannot_pass_a_missing_trace():
    evidence = _evidence()
    del evidence["tool_calls"]
    outcomes = _run(
        [{"type": "tool_not_called", "params": {"tool": "delete_ticket"}}],
        evidence,
        _complete(tool_calls=False),
    )
    assert outcomes[0]["outcome"] == "insufficient_evidence"


def test_explicit_empty_complete_trace_passes_absence():
    outcomes = _run(
        [
            {"type": "tool_not_called", "params": {"tool": "delete_ticket"}},
            {"type": "forbidden_tool", "params": {"tool": "delete_ticket"}},
        ],
        _evidence(tool_calls=[]),
        _complete(),
    )
    assert [o["outcome"] for o in outcomes] == ["passed", "passed"]
    assert all(o["passed"] for o in outcomes)


def test_true_completeness_with_missing_field_fails_closed():
    evidence = _evidence()
    del evidence["tool_calls"]
    outcomes = _run(
        [{"type": "tool_called", "params": {"tool": "get_ticket"}}],
        evidence,
        _complete(tool_calls=True),
    )
    assert outcomes[0]["outcome"] == "insufficient_evidence"
    assert outcomes[0]["passed"] is False


def test_unknown_applicability_is_insufficient_not_pass():
    outcomes = _run(
        [{"type": "terminal_status", "params": {"status": "completed"}}],
        applicability="unknown",
    )
    assert outcomes[0]["outcome"] == "insufficient_evidence"
    assert outcomes[0]["passed"] is False
    assert outcomes[0]["actual"] is None


def test_false_applicability_skips_evidence_entirely():
    outcomes = _run(
        [{"type": "terminal_status", "params": {"status": "completed"}}],
        _evidence(),
        _complete(terminal_status=False),
        applicability="not_applicable",
    )
    assert outcomes[0]["outcome"] == "not_applicable"
    assert outcomes[0]["passed"] is False


def test_definitions_validate_even_for_inapplicable_inputs():
    with pytest.raises(AssertionDefinitionError, match="unknown type"):
        _run(
            [{"type": "vibes", "params": {}}],
            applicability="not_applicable",
        )
    with pytest.raises(AssertionDefinitionError, match="unknown type"):
        _run([{"type": "vibes", "params": {}}], applicability="unknown")


def test_absent_simulator_state_is_insufficient():
    evidence = _evidence()
    del evidence["simulator_state"]
    outcomes = _run(
        [{"type": "simulator_state", "params": {"path": "ticket", "equals": "x"}}],
        evidence,
    )
    assert outcomes[0]["outcome"] == "insufficient_evidence"
    assert "not proof" in outcomes[0]["detail"]


def test_present_simulator_state_is_still_unsupported():
    outcomes = _run(
        [
            {
                "type": "simulator_state",
                "params": {
                    "path": "ticket.ticket-0001.status",
                    "equals": "resolved",
                },
            }
        ],
    )
    assert outcomes[0]["outcome"] == "insufficient_evidence"


def test_pending_judge_is_never_a_pass():
    outcomes = _run([_judge_assertion()])
    assert outcomes[0]["outcome"] == "pending_judge"
    assert outcomes[0]["passed"] is False
    assert outcomes[0]["actual"] == "pending"


def test_non_terminal_recorded_status_cannot_pass():
    for status in ("queued", "running", "sleeping", "waiting_child"):
        outcomes = _run(
            [{"type": "terminal_status", "params": {"status": status}}],
            _evidence(terminal_status=status),
        )
        assert outcomes[0]["outcome"] == "insufficient_evidence", status


def test_recorded_historical_failure_evaluates_normally():
    outcomes = _run(
        [{"type": "terminal_status", "params": {"status": "failed"}}],
        _evidence(terminal_status="failed"),
    )
    assert outcomes[0]["outcome"] == "passed"
    assert outcomes[0]["passed"] is True


def test_failed_plus_insufficient_stays_failed_and_incomplete():
    pair = aggregate_recorded_pair(
        [
            {"outcome": "failed"},
            {"outcome": "insufficient_evidence"},
        ]
    )
    assert pair["outcome"] == "failed"
    assert pair["complete"] is False


def test_precedence_error_over_failed_over_pending_over_insufficient():
    assert (
        aggregate_recorded_pair([{"outcome": "failed"}, {"outcome": "error"}])[
            "outcome"
        ]
        == "error"
    )
    assert (
        aggregate_recorded_pair(
            [{"outcome": "failed"}, {"outcome": "pending_judge"}]
        )["outcome"]
        == "failed"
    )
    assert (
        aggregate_recorded_pair(
            [{"outcome": "pending_judge"}, {"outcome": "insufficient_evidence"}]
        )["outcome"]
        == "pending_judge"
    )


def test_mixed_pass_and_not_applicable_passes_with_a_pass_present():
    pair = aggregate_recorded_pair(
        [{"outcome": "passed"}, {"outcome": "not_applicable"}]
    )
    assert pair["outcome"] == "passed"
    assert pair["complete"] is True


def test_all_not_applicable_cannot_green_gate():
    pair = aggregate_recorded_pair(
        [{"outcome": "not_applicable"}, {"outcome": "not_applicable"}]
    )
    assert pair["outcome"] == "not_applicable"
    evaluation = aggregate_recorded_evaluation([pair])
    assert evaluation["gate_passed"] is False
    assert evaluation["all_inapplicable"] is True


def test_empty_assertion_set_is_insufficient_never_vacuous_pass():
    pair = aggregate_recorded_pair([])
    assert pair["outcome"] == "insufficient_evidence"
    assert pair["complete"] is False
    evaluation = aggregate_recorded_evaluation([])
    assert evaluation["gate_passed"] is False
    assert evaluation["complete"] is False


def test_gate_requires_a_passing_pair_and_clean_counts():
    passing = aggregate_recorded_pair([{"outcome": "passed"}])
    skipped = aggregate_recorded_pair([{"outcome": "not_applicable"}])
    evaluation = aggregate_recorded_evaluation([passing, skipped])
    assert evaluation["gate_passed"] is True
    assert evaluation["complete"] is True
    assert evaluation["counts"]["passed"] == 1
    assert evaluation["counts"]["not_applicable"] == 1


def test_gate_fails_when_any_pair_is_not_clean():
    passing = aggregate_recorded_pair([{"outcome": "passed"}])
    failing = aggregate_recorded_pair([{"outcome": "failed"}])
    evaluation = aggregate_recorded_evaluation([passing, failing])
    assert evaluation["gate_passed"] is False
    assert evaluation["complete"] is True


@pytest.mark.parametrize(
    "usage_over",
    [
        {"tokens": True},
        {"tokens": float("nan")},
        {"tokens": float("inf")},
        {"tokens": -5},
    ],
)
def test_budgets_reject_bool_nan_inf_negative(usage_over):
    outcomes = _run(
        [{"type": "max_tokens", "params": {"limit": 1000}}],
        _evidence(usage={**_evidence()["usage"], **usage_over}),
    )
    assert outcomes[0]["outcome"] == "insufficient_evidence"
    assert outcomes[0]["passed"] is False


def test_budgets_reject_missing_usage_key():
    usage = dict(_evidence()["usage"])
    del usage["tokens"]
    outcomes = _run(
        [{"type": "max_tokens", "params": {"limit": 1000}}],
        _evidence(usage=usage),
    )
    assert outcomes[0]["outcome"] == "insufficient_evidence"


def test_valid_budget_evaluates_normally():
    outcomes = _run([{"type": "max_tokens", "params": {"limit": 1000}}])
    assert outcomes[0]["outcome"] == "passed"


def test_explicit_null_output_is_recorded_null_not_absent():
    outcomes = _run(
        [{"type": "output_path", "params": {"path": "answer"}}],
        _evidence(output=None),
        _complete(),
    )
    assert outcomes[0]["outcome"] == "failed"
    assert outcomes[0]["passed"] is False


def test_tool_order_needs_separate_order_completeness():
    outcomes = _run(
        [{"type": "tool_order", "params": {"tools": ["get_ticket", "update_ticket"]}}],
        _evidence(),
        _complete(tool_order=False),
    )
    assert outcomes[0]["outcome"] == "insufficient_evidence"


def test_tool_args_needs_argument_completeness():
    outcomes = _run(
        [
            {
                "type": "tool_args",
                "params": {"tool": "get_ticket", "args": {"id": "ticket-0001"}},
            }
        ],
        _evidence(),
        _complete(tool_arguments=False),
    )
    assert outcomes[0]["outcome"] == "insufficient_evidence"


def test_missing_real_tool_count_is_insufficient_never_zero():
    evidence = _evidence()
    del evidence["real_tool_executions"]
    outcomes = _run(
        [{"type": "no_real_tools", "params": {}}],
        evidence,
        _complete(real_tool_executions=True),
    )
    assert outcomes[0]["outcome"] == "insufficient_evidence"


def test_present_zero_real_tool_count_passes():
    outcomes = _run([{"type": "no_real_tools", "params": {}}])
    assert outcomes[0]["outcome"] == "passed"


@pytest.mark.parametrize("bad_count", [True, -1, "0"])
def test_malformed_real_tool_count_is_insufficient(bad_count):
    outcomes = _run(
        [{"type": "no_real_tools", "params": {}}],
        _evidence(real_tool_executions=bad_count),
    )
    assert outcomes[0]["outcome"] == "insufficient_evidence"


def test_inputs_are_never_mutated():
    assertions = [{"type": "tool_called", "params": {"tool": "get_ticket"}}]
    evidence = _evidence()
    completeness = _complete()
    before = (copy.deepcopy(assertions), copy.deepcopy(evidence), copy.deepcopy(completeness))
    _run(assertions, evidence, completeness)
    assert (assertions, evidence, completeness) == before


def test_redaction_survives_recorded_failures():
    outcomes = _run(
        [{"type": "output_path", "params": {"path": "auth", "equals": {"token": "different-secret"}}}],
        _evidence(output={"auth": {"token": "live-secret"}}),
    )
    assert outcomes[0]["outcome"] == "failed"
    assert outcomes[0]["actual"] == {"token": "[REDACTED]"}


def test_gated_outcomes_echo_no_evidence_values():
    outcomes = _run(
        [{"type": "tool_called", "params": {"tool": "get_ticket"}}],
        _evidence(output={"auth": {"token": "live-secret"}}),
        _complete(tool_calls=False),
        applicability="applicable",
    )
    assert outcomes[0]["outcome"] == "insufficient_evidence"
    assert outcomes[0]["actual"] is None


def test_evaluated_outcomes_keep_codes_and_journal_references():
    reference = {"run_id": "child-run", "sequence": 7, "kind": "tool_result"}
    evidence = _evidence(
        tool_calls=[
            {
                "name": "read",
                "arguments": {},
                "sequence": 0,
                "journal_references": [reference],
            }
        ]
    )
    outcomes = _run([{"type": "tool_called", "params": {"tool": "read"}}], evidence)
    assert outcomes[0]["outcome"] == "passed"
    assert outcomes[0]["code"] == "tool_called"
    assert outcomes[0]["type"] == "tool_called"
    assert outcomes[0]["evidence_references"] == [reference]
    assert "assertion" not in outcomes[0]


def test_nested_completeness_mapping_is_accepted():
    outcomes = evaluate_recorded_assertions(
        [{"type": "max_tokens", "params": {"limit": 1000}}],
        _evidence(),
        completeness={"usage": {"tokens": True}},
        applicability="applicable",
    )
    assert outcomes[0]["outcome"] == "passed"


def test_invalid_applicability_and_completeness_rejected():
    with pytest.raises(ValueError, match="applicability"):
        _run(
            [{"type": "tool_called", "params": {"tool": "x"}}],
            applicability="sometimes",
        )
    with pytest.raises(ValueError, match="completeness"):
        evaluate_recorded_assertions(
            [{"type": "tool_called", "params": {"tool": "x"}}],
            _evidence(),
            completeness=["tool_calls"],
            applicability="applicable",
        )


def test_fully_observed_failure_is_complete():
    pair = aggregate_recorded_pair([{"outcome": "failed"}])
    assert pair["outcome"] == "failed"
    assert pair["complete"] is True
    rollup = aggregate_recorded_evaluation([pair])
    assert rollup["complete"] is True
    assert rollup["gate_passed"] is False


def test_incomplete_pair_cannot_green_gate_even_with_pass_label():
    assert aggregate_recorded_evaluation(
        [{"outcome": "passed", "complete": False}]
    )["gate_passed"] is False


@pytest.mark.parametrize("applicability", ["applicable", "unknown", "not_applicable"])
def test_entire_result_redacts_assertion_secrets(applicability):
    import json

    result = evaluate_recorded_assertions(
        [{"type": "tool_args", "params": {"tool": "get_ticket", "args": {"password": "private-value"}}}],
        {"tool_calls": [{"name": "get_ticket", "arguments": {"password": "private-value"}, "sequence": 1}]},
        completeness={"tool_calls": True, "tool_arguments": True},
        applicability=applicability,
    )
    assert "private-value" not in json.dumps(result)


def test_unusable_status_does_not_echo_untrusted_evidence():
    import json

    result = evaluate_recorded_assertions(
        [{"type": "terminal_status", "params": {"status": "completed"}}],
        {"terminal_status": "private-status-value"},
        completeness={"terminal_status": True}, applicability="applicable",
    )
    assert "private-status-value" not in json.dumps(result)
