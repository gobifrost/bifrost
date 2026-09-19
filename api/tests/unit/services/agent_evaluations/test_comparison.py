"""Comparison keeps distinct assertions even when they share a type/label."""

from src.services.agent_evaluations.comparison import compare_runs


def test_repeated_assertion_types_preserve_each_regression():
    baseline = [
        {"code": "tool_called", "passed": True, "expected": "read_ticket"},
        {"code": "tool_called", "passed": True, "expected": "update_ticket"},
    ]
    candidate = [
        {**baseline[0], "passed": False},
        baseline[1],
    ]

    result = compare_runs(baseline, candidate)

    assert result["verdict"] == "regression"
    assert result["regressions"] == ["tool_called#1"]
