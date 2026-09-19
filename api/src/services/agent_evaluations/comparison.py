"""Baseline-versus-candidate comparison.

Evidence-first deltas: assertion regressions/improvements, tool-trajectory
differences, output differences, and usage deltas. Subjective prose never
becomes a silent pass/fail gate — the verdict is a deterministic function
of the evaluated assertion outcomes.
"""

from __future__ import annotations

from typing import Any

from src.services.agent_evaluations.simulator_models import redact_value


def compare_runs(
    baseline_outcomes: list[dict[str, Any]],
    candidate_outcomes: list[dict[str, Any]],
    *,
    baseline_tools: list[str] | None = None,
    candidate_tools: list[str] | None = None,
    baseline_output: Any = None,
    candidate_output: Any = None,
    baseline_usage: dict[str, Any] | None = None,
    candidate_usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Deterministic baseline/candidate delta over stored outcomes."""
    base = _index_outcomes(baseline_outcomes)
    cand = _index_outcomes(candidate_outcomes)
    repeated = {name for name, occurrence in set(base) | set(cand) if occurrence > 1}
    regressions: list[str] = []
    improvements: list[str] = []
    unchanged_failures: list[str] = []
    for key in sorted(set(base) | set(cand)):
        b_passed = base.get(key, {}).get("passed")
        c_passed = cand.get(key, {}).get("passed")
        name, occurrence = key
        label = f"{name}#{occurrence}" if name in repeated else name
        if b_passed is True and c_passed is False:
            regressions.append(label)
        elif b_passed is False and c_passed is True:
            improvements.append(label)
        elif b_passed is False and c_passed is False:
            unchanged_failures.append(label)
    trajectory = _trajectory_differences(baseline_tools or [], candidate_tools or [])
    output_diffs = _output_differences(baseline_output, candidate_output)
    usage_delta = _usage_delta(baseline_usage or {}, candidate_usage or {})
    if regressions and improvements:
        verdict = "mixed"
    elif regressions:
        verdict = "regression"
    elif improvements:
        verdict = "improvement"
    else:
        verdict = "unchanged"
    return {
        "regressions": regressions,
        "improvements": improvements,
        "unchanged_failures": unchanged_failures,
        "tool_trajectory_differences": trajectory,
        "output_differences": output_diffs,
        "usage_delta": usage_delta,
        "verdict": verdict,
    }


def _outcome_key(outcome: dict[str, Any]) -> str:
    label = outcome.get("label")
    return f"{outcome.get('code')}:{label}" if label else str(outcome.get("code"))


def _index_outcomes(
    outcomes: list[dict[str, Any]],
) -> dict[tuple[str, int], dict[str, Any]]:
    # Both sides evaluate the same frozen ordered assertion list. A type or
    # optional label is not a unique assertion identity: retain each occurrence.
    counts: dict[str, int] = {}
    indexed = {}
    for outcome in outcomes:
        name = _outcome_key(outcome)
        counts[name] = counts.get(name, 0) + 1
        indexed[(name, counts[name])] = outcome
    return indexed


def _trajectory_differences(
    baseline_tools: list[str], candidate_tools: list[str]
) -> list[dict[str, Any]]:
    differences = []
    max_len = max(len(baseline_tools), len(candidate_tools))
    for position in range(max_len):
        left = baseline_tools[position] if position < len(baseline_tools) else None
        right = candidate_tools[position] if position < len(candidate_tools) else None
        if left != right:
            differences.append(
                {"position": position, "baseline": left, "candidate": right}
            )
    return differences


def _output_differences(baseline: Any, candidate: Any) -> list[dict[str, Any]]:
    if baseline == candidate:
        return []
    return [
        {"baseline": redact_value(baseline), "candidate": redact_value(candidate)}
    ]


def _usage_delta(
    baseline: dict[str, Any], candidate: dict[str, Any]
) -> dict[str, Any]:
    delta = {}
    for key in sorted(set(baseline) | set(candidate)):
        left, right = baseline.get(key), candidate.get(key)
        if isinstance(left, (int, float)) and isinstance(right, (int, float)):
            delta[key] = {"baseline": left, "candidate": right, "delta": right - left}
        else:
            delta[key] = {"baseline": left, "candidate": right}
    return delta
