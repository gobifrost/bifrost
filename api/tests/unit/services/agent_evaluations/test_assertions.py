"""Assertion and comparison tests: stable codes, redaction, deterministic deltas."""

from __future__ import annotations

import pytest

from src.services.agent_evaluations.assertions import (
    AssertionDefinitionError,
    evaluate_assertions,
    freeze_semantic_judges,
    validate_assertions,
)
from src.services.agent_evaluations.comparison import compare_runs


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


def test_terminal_status_and_tool_assertions():
    outcomes = evaluate_assertions(
        [
            {"type": "terminal_status", "params": {"status": "completed"}},
            {"type": "tool_called", "params": {"tool": "get_ticket"}},
            {"type": "tool_not_called", "params": {"tool": "delete_ticket"}},
            {"type": "forbidden_tool", "params": {"tool": "delete_ticket"}},
            {"type": "tool_count", "params": {"tool": "get_ticket", "count": 1}},
            {"type": "tool_order", "params": {"tools": ["get_ticket", "update_ticket"]}},
            {
                "type": "tool_args",
                "params": {"tool": "get_ticket", "args": {"id": "ticket-0001"}},
            },
        ],
        _evidence(),
    )
    assert [o["code"] for o in outcomes] == [
        "terminal_status",
        "tool_called",
        "tool_not_called",
        "forbidden_tool",
        "tool_count",
        "tool_order",
        "tool_args",
    ]
    assert all(o["passed"] for o in outcomes)
    assert outcomes[1]["evidence_sequences"] == [0]


def test_output_budget_state_delegation_no_real_tools():
    outcomes = evaluate_assertions(
        [
            {
                "type": "output_schema",
                "params": {
                    "schema": {
                        "type": "object",
                        "properties": {"answer": {"type": "string"}},
                        "required": ["answer"],
                    }
                },
            },
            {"type": "output_path", "params": {"path": "ticket_id", "equals": "ticket-0001"}},
            {"type": "simulator_state", "params": {"path": "ticket.ticket-0001.status", "equals": "resolved"}},
            {"type": "delegation_tree", "params": {"max_children": 0}},
            {"type": "max_iterations", "params": {"limit": 5}},
            {"type": "max_tokens", "params": {"limit": 1000}},
            {"type": "max_cost_usd", "params": {"limit": 1.0}},
            {"type": "max_latency_ms", "params": {"limit": 60000}},
            {"type": "no_real_tools", "params": {}},
        ],
        _evidence(),
    )
    assert all(o["passed"] for o in outcomes), outcomes


def test_failures_carry_redacted_evidence():
    outcomes = evaluate_assertions(
        [
            {"type": "terminal_status", "params": {"status": "completed"}},
            {"type": "tool_called", "params": {"tool": "get_ticket"}},
        ],
        _evidence(
            terminal_status="failed",
            tool_calls=[
                {
                    "name": "get_ticket",
                    "arguments": {"id": "x", "api_token": "live-secret"},
                    "sequence": 3,
                }
            ],
        ),
    )
    by_code = {o["code"]: o for o in outcomes}
    assert by_code["terminal_status"]["passed"] is False
    assert by_code["terminal_status"]["actual"] == "failed"
    assert by_code["tool_called"]["passed"] is True
    assert by_code["tool_called"]["evidence_sequences"] == [3]


def test_secrets_redacted_in_failure_evidence():
    outcomes = evaluate_assertions(
        [
            {
                "type": "tool_args",
                "params": {"tool": "get_ticket", "args": {"id": "other"}},
            }
        ],
        _evidence(),
    )
    assert outcomes[0]["passed"] is False
    actual_args = outcomes[0]["actual"][0]
    assert actual_args["id"] == "ticket-0001"


def test_secret_leak_in_output_path_is_redacted():
    outcomes = evaluate_assertions(
        [{"type": "output_path", "params": {"path": "auth"}}],
        _evidence(output={"auth": {"token": "live-secret"}}),
    )
    assert outcomes[0]["actual"] == {"token": "[REDACTED]"}


def test_malformed_assertions_fail_at_save_time():
    with pytest.raises(AssertionDefinitionError, match="unknown type"):
        validate_assertions([{"type": "vibes", "params": {}}])
    with pytest.raises(AssertionDefinitionError, match="missing required param"):
        validate_assertions([{"type": "tool_called", "params": {}}])
    with pytest.raises(AssertionDefinitionError, match="must be an object"):
        validate_assertions(["nope"])
    with pytest.raises(AssertionDefinitionError):
        evaluate_assertions([{"type": "tool_count", "params": {"tool": "x"}}], _evidence())


def test_llm_judge_uses_a_frozen_snapshot_and_is_nondeterministic():
    assertion = [
        {
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
    ]
    pending = evaluate_assertions(assertion, _evidence())
    assert pending[0]["actual"] == "pending"


def test_semantic_judge_profile_cannot_be_frozen_by_non_admin():
    import asyncio

    with pytest.raises(AssertionDefinitionError, match="platform administrator"):
        asyncio.get_event_loop().run_until_complete(
            freeze_semantic_judges(
                None,
                [{"type": "llm_judge", "params": {
                    "rubric": "helpful", "prompt_version": "1", "threshold": 0.5,
                    "judge_profile_id": "00000000-0000-0000-0000-000000000001",
                }}],
                is_superuser=False,
            )
        )


def test_comparison_verdicts():
    passing = [{"code": "tool_called", "passed": True}]
    failing = [{"code": "tool_called", "passed": False}]
    assert compare_runs(passing, failing)["verdict"] == "regression"
    assert compare_runs(passing, failing)["regressions"] == ["tool_called"]
    assert compare_runs(failing, passing)["verdict"] == "improvement"
    assert compare_runs(failing, failing)["verdict"] == "unchanged"
    assert compare_runs(failing, failing)["unchanged_failures"] == ["tool_called"]
    mixed = compare_runs(
        [{"code": "a", "passed": True}, {"code": "b", "passed": False}],
        [{"code": "a", "passed": False}, {"code": "b", "passed": True}],
    )
    assert mixed["verdict"] == "mixed"


@pytest.mark.parametrize("definition", [
    {"type": "output_path", "params": {"path": None}},
    {"type": "tool_count", "params": {"tool": "read", "count": -1}},
    {"type": "tool_args", "params": {"tool": "read", "args": []}},
    {"type": "tool_order", "params": {"tools": "read"}},
    {"type": "max_tokens", "params": {"limit": "unbounded"}},
])
def test_malformed_assertion_params_are_rejected_before_execution(definition):
    with pytest.raises(AssertionDefinitionError):
        validate_assertions([definition])


def test_contains_assertion_fails_on_scalar_output_instead_of_crashing():
    outcomes = evaluate_assertions(
        [{"type": "output_path", "params": {"path": "answer", "contains": "yes"}}],
        _evidence(output={"answer": 42}),
    )
    assert outcomes[0]["passed"] is False


def test_tool_assertions_link_to_the_correct_run_and_journal_sequence():
    reference = {"run_id": "child-run", "sequence": 7, "kind": "tool_result"}
    outcomes = evaluate_assertions(
        [{"type": "tool_called", "params": {"tool": "read"}}],
        _evidence(tool_calls=[{
            "name": "read", "sequence": 0, "journal_references": [reference],
        }]),
    )
    assert outcomes[0]["evidence_references"] == [reference]


def test_comparison_trajectory_output_usage():
    result = compare_runs(
        [{"code": "tool_called", "passed": True}],
        [{"code": "tool_called", "passed": True}],
        baseline_tools=["get_ticket", "update_ticket"],
        candidate_tools=["get_ticket", "delete_ticket"],
        baseline_output={"answer": "a"},
        candidate_output={"answer": "b"},
        baseline_usage={"tokens": 100, "iterations": 2},
        candidate_usage={"tokens": 150, "iterations": 2},
    )
    assert result["verdict"] == "unchanged"
    assert result["tool_trajectory_differences"] == [
        {"position": 1, "baseline": "update_ticket", "candidate": "delete_ticket"}
    ]
    assert result["usage_delta"]["tokens"] == {
        "baseline": 100,
        "candidate": 150,
        "delta": 50,
    }
    assert result["output_differences"][0]["candidate"] == {"answer": "b"}
