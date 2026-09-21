"""Recorded-outcome assertion semantics over stored agent-run evidence.

This module wraps the deterministic assertion evaluators in
``src.services.agent_evaluations.assertions`` with an explicit
completeness/applicability admission boundary for *recorded* evidence:
stored runs evaluated after the fact, where trace dimensions may be
absent.

Two inputs the deterministic evaluators cannot infer are required:

- ``applicability``: whether the test applies to the recorded run
  (``applicable`` / ``not_applicable`` / ``unknown``). Unknown
  applicability can never pass; known-false applicability yields
  ``not_applicable`` without touching evidence. Applicability is never
  inferred from absence of expected calls.
- ``completeness``: which evidence dimensions the recorded trace
  actually captured. Anything not explicitly marked complete is treated
  as incomplete, and absence can never prove a negative (a missing tool
  trace does not prove a forbidden tool was never called).

This slice never calls a semantic judge (``llm_judge`` stays
``pending_judge`` with no pass claim) and never synthesizes missing
evidence: no default output object, no zero real-tool count, no
simulator-state verdicts. Verdict aggregation is fail-closed so lost or
incomplete evidence can never green-gate. Test verdicts never touch
PlatformJob lifecycle: a job can finish successfully and still report
failed tests.
"""

from __future__ import annotations

import copy
import math
from typing import Any

from src.services.agent_evaluations.assertions import (
    evaluate_assertions,
    validate_assertions,
)
from src.services.agent_evaluations.simulator_models import redact_value
from src.services.agent_runtime import types as runtime_types

APPLICABILITY_VALUES = ("applicable", "not_applicable", "unknown")
"""Allowed test-level applicability values. ``applicability`` is required."""

RECORDED_OUTCOME_NAMES = (
    "passed",
    "failed",
    "not_applicable",
    "insufficient_evidence",
    "pending_judge",
    "error",
)
"""Every recorded outcome uses one of these ``outcome`` values."""

_REQUIRED_COMPLETENESS: dict[str, tuple[str, ...]] = {
    "terminal_status": ("terminal_status",),
    "output_schema": ("output",),
    "output_path": ("output",),
    "tool_called": ("tool_calls",),
    "tool_not_called": ("tool_calls",),
    "forbidden_tool": ("tool_calls",),
    "tool_count": ("tool_calls",),
    "tool_order": ("tool_calls", "tool_order"),
    "tool_args": ("tool_calls", "tool_arguments"),
    "delegation_tree": ("delegation",),
    "no_real_tools": ("real_tool_executions",),
    "max_iterations": ("usage.iterations",),
    "max_tokens": ("usage.tokens",),
    "max_cost_usd": ("usage.cost_usd",),
    "max_latency_ms": ("usage.latency_ms",),
}
"""Completeness dimensions each assertion type requires before evaluation."""

_BUDGET_USAGE_KEY = {
    "max_iterations": "iterations",
    "max_tokens": "tokens",
    "max_cost_usd": "cost_usd",
    "max_latency_ms": "latency_ms",
}
"""Assertion type to ``usage`` evidence key for budget assertions."""


def _completeness_flag(completeness: dict[str, Any], dotted: str) -> bool:
    """Return True only when ``dotted`` is explicitly marked complete."""
    if dotted in completeness:
        return completeness[dotted] is True
    current: Any = completeness
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return False
        current = current[part]
    return current is True


def _gate_outcome(
    assertion: dict[str, Any],
    outcome: str,
    *,
    reason: str,
    detail: str,
) -> dict[str, Any]:
    """Build a non-evaluated outcome that echoes no untrusted evidence."""
    params = assertion.get("params", {})
    return {
        "code": assertion.get("type"),
        "type": assertion.get("type"),
        "label": assertion.get("label"),
        "outcome": outcome,
        "passed": False,
        "expected": redact_value(copy.deepcopy(params)),
        "actual": None,
        "detail": detail,
        "reason": reason,
        "evidence_sequences": [],
        "evidence_references": [],
    }


def _tool_calls_shape_problem(evidence: dict[str, Any]) -> str | None:
    """Check the recorded tool trace is a structurally valid list."""
    if "tool_calls" not in evidence:
        return "recorded evidence is missing tool_calls"
    calls = evidence["tool_calls"]
    if not isinstance(calls, list):
        return "recorded tool_calls must be a list"
    for index, call in enumerate(calls):
        if not isinstance(call, dict):
            return f"recorded tool_calls[{index}] must be an object"
        name = call.get("name")
        if not isinstance(name, str) or not name.strip():
            return f"recorded tool_calls[{index}] must name a tool"
        sequence = call.get("sequence")
        if sequence is not None and (
            not isinstance(sequence, int) or isinstance(sequence, bool)
        ):
            return f"recorded tool_calls[{index}] has a non-integer sequence"
        arguments = call.get("arguments")
        if arguments is not None and not isinstance(arguments, dict):
            return f"recorded tool_calls[{index}] has non-object arguments"
    return None


def _evidence_shape_problem(atype: str, evidence: dict[str, Any]) -> str | None:
    """Fail closed when a complete flag is contradicted by the evidence."""
    if atype == "terminal_status":
        if "terminal_status" not in evidence:
            return "recorded evidence is missing terminal_status"
        return None
    if atype in ("output_schema", "output_path"):
        if "output" not in evidence:
            return "recorded evidence is missing output"
        return None
    if atype in (
        "tool_called",
        "tool_not_called",
        "forbidden_tool",
        "tool_count",
        "tool_order",
    ):
        return _tool_calls_shape_problem(evidence)
    if atype == "tool_args":
        problem = _tool_calls_shape_problem(evidence)
        if problem is not None:
            return problem
        for index, call in enumerate(evidence["tool_calls"]):
            if not isinstance(call.get("arguments"), dict):
                return (
                    f"recorded tool_calls[{index}] is missing arguments "
                    "claimed complete upstream"
                )
        return None
    if atype == "delegation_tree":
        delegation = evidence.get("delegation")
        if not isinstance(delegation, dict) or not isinstance(
            delegation.get("children"), list
        ):
            return "recorded evidence must carry delegation with a children list"
        return None
    if atype == "no_real_tools":
        count = evidence.get("real_tool_executions")
        if (
            count is None and "real_tool_executions" not in evidence
        ) or not _is_recorded_count(count):
            return (
                "recorded evidence must carry real_tool_executions "
                "as a nonnegative integer"
            )
        return None
    if atype in _BUDGET_USAGE_KEY:
        usage = evidence.get("usage")
        key = _BUDGET_USAGE_KEY[atype]
        if not isinstance(usage, dict) or key not in usage:
            return f"recorded evidence is missing usage.{key}"
        return None
    return None


def _is_recorded_count(value: Any) -> bool:
    """Counts are nonnegative integers; bools are never counts."""
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and value >= 0
    )


def _is_usable_budget_value(value: Any) -> bool:
    """Budget evidence must be a finite nonnegative number, never a bool."""
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


def _recorded_validity_problem(
    atype: str, evidence: dict[str, Any]
) -> str | None:
    """Check recorded values are adjudicable, without judging them."""
    if atype == "terminal_status":
        recorded = evidence.get("terminal_status")
        if not isinstance(recorded, str) or recorded not in runtime_types.TERMINAL_STATUSES:
            return (
                "recorded status is not terminal "
                "(queued/running/sleeping/waiting runs cannot complete a pass)"
            )
        return None
    if atype in _BUDGET_USAGE_KEY:
        value = evidence.get("usage", {}).get(_BUDGET_USAGE_KEY[atype])
        if not _is_usable_budget_value(value):
            return (
                f"recorded usage.{_BUDGET_USAGE_KEY[atype]} must be a "
                "finite nonnegative number"
            )
        return None
    return None


def _pending_judge_outcome(assertion: dict[str, Any]) -> dict[str, Any]:
    """Defer semantic judges: pending, never a pass, never evaluated here."""
    params = assertion.get("params", {})
    snapshot = params.get("judge_snapshot") or {}
    return {
        "code": "llm_judge",
        "type": "llm_judge",
        "label": assertion.get("label"),
        "outcome": "pending_judge",
        "passed": False,
        "expected": f"score >= {params.get('threshold')}",
        "actual": "pending",
        "detail": (
            "semantic judge requires a later durable job slice; "
            "this slice never calls a judge"
        ),
        "reason": "judge_pending",
        "evidence_sequences": [],
        "evidence_references": [],
        "authoritative": False,
        "nondeterministic": True,
        "judge_snapshot": redact_value(copy.deepcopy(snapshot)),
        "judge_rubric": redact_value(params.get("rubric")),
        "judge_threshold": params.get("threshold"),
    }


def _recorded_outcome_for_assertion(
    assertion: dict[str, Any],
    evidence: dict[str, Any],
    completeness: dict[str, Any],
) -> dict[str, Any]:
    """Gate one applicable assertion, then delegate to the exact evaluator."""
    atype = assertion["type"]
    if atype == "simulator_state":
        return _gate_outcome(
            assertion,
            "insufficient_evidence",
            reason="unsupported_recorded_state",
            detail=(
                "simulator_state is not supported for recorded evaluation: "
                "missing state is not proof the situation does not apply"
            ),
        )
    if atype == "llm_judge":
        return _pending_judge_outcome(assertion)
    required = _REQUIRED_COMPLETENESS[atype]
    incomplete = [
        dimension
        for dimension in required
        if not _completeness_flag(completeness, dimension)
    ]
    if incomplete:
        return _gate_outcome(
            assertion,
            "insufficient_evidence",
            reason="incomplete_evidence",
            detail=(
                "recorded evidence is incomplete for dimensions: "
                + ", ".join(incomplete)
            ),
        )
    problem = _evidence_shape_problem(atype, evidence)
    if problem is not None:
        return _gate_outcome(
            assertion,
            "insufficient_evidence",
            reason="missing_evidence",
            detail=f"recorded evidence contradicts its completeness claim: {problem}",
        )
    problem = _recorded_validity_problem(atype, evidence)
    if problem is not None:
        return _gate_outcome(
            assertion,
            "insufficient_evidence",
            reason="unusable_evidence",
            detail=problem,
        )
    try:
        evaluated = evaluate_assertions([assertion], evidence)[0]
    except Exception as exc:
        return _gate_outcome(
            assertion,
            "error",
            reason="evaluator_error",
            detail=(
                "deterministic evaluator failed with "
                f"{type(exc).__name__}; evaluator execution errors are "
                "distinct from recorded historical failures"
            ),
        )
    recorded = dict(evaluated)
    recorded["outcome"] = "passed" if evaluated["passed"] else "failed"
    recorded["reason"] = "deterministic"
    return recorded


def evaluate_recorded_assertions(
    assertions: list[dict[str, Any]],
    evidence: dict[str, Any],
    *,
    completeness: dict[str, Any],
    applicability: str,
) -> list[dict[str, Any]]:
    """Evaluate assertions against recorded evidence with explicit admission.

    ``applicability`` is required: ``applicable`` evaluates normally,
    ``not_applicable`` returns ``not_applicable`` for every assertion, and
    ``unknown`` returns ``insufficient_evidence`` for every assertion.
    Definitions are validated even for inapplicable inputs. Inputs are
    never mutated.
    """
    if applicability not in APPLICABILITY_VALUES:
        raise ValueError(
            f"applicability must be one of {APPLICABILITY_VALUES}, "
            f"got {applicability!r}"
        )
    if not isinstance(completeness, dict):
        raise ValueError("completeness must map evidence dimensions to booleans")
    validate_assertions(assertions)
    if applicability == "not_applicable":
        return [
            _gate_outcome(
                assertion,
                "not_applicable",
                reason="test_not_applicable",
                detail=(
                    "test applicability is known false; recorded evidence "
                    "was not evaluated"
                ),
            )
            for assertion in assertions
        ]
    if applicability == "unknown":
        return [
            _gate_outcome(
                assertion,
                "insufficient_evidence",
                reason="unknown_applicability",
                detail=(
                    "test applicability is unknown; applicability must be "
                    "established upstream, never inferred from absent calls"
                ),
            )
            for assertion in assertions
        ]
    return [
        _recorded_outcome_for_assertion(assertion, evidence, completeness)
        for assertion in assertions
    ]


def aggregate_recorded_pair(
    outcomes: list[dict[str, Any]],
) -> dict[str, Any]:
    """Aggregate one test's assertion outcomes to a pair verdict.

    Precedence: error > failed > pending_judge > insufficient_evidence >
    all-not-applicable > passed. Pending, error, or insufficient evidence
    always makes ``complete`` False, even when a known failure already
    decides the outcome. An empty assertion set is insufficient, never a
    vacuous pass. Mixed pass plus explicit not_applicable passes only
    when at least one assertion passed and nothing else is outstanding.
    """
    counts = dict.fromkeys(RECORDED_OUTCOME_NAMES, 0)
    for outcome in outcomes or []:
        name = outcome.get("outcome") if isinstance(outcome, dict) else None
        if name not in counts:
            raise ValueError(f"unknown recorded outcome {name!r}")
        counts[name] += 1
    total = sum(counts.values())
    if total == 0:
        return {
            "outcome": "insufficient_evidence",
            "complete": False,
            "counts": counts,
            "total": 0,
        }
    complete = not any(counts[name] for name in ("error", "pending_judge", "insufficient_evidence"))
    for name in ("error", "failed", "pending_judge", "insufficient_evidence"):
        if counts[name]:
            return {
                "outcome": name,
                "complete": complete,
                "counts": counts,
                "total": total,
            }
    if counts["not_applicable"] == total:
        return {
            "outcome": "not_applicable",
            "complete": True,
            "counts": counts,
            "total": total,
        }
    return {
        "outcome": "passed",
        "complete": True,
        "counts": counts,
        "total": total,
    }


def aggregate_recorded_evaluation(
    pairs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Aggregate pair verdicts to an evaluation gate without job lifecycle.

    ``gate_passed`` requires at least one applicable passing pair and no
    failure, error, pending judge, or insufficient evidence anywhere.
    Lost or incomplete evidence never collapses into a green total. Pair
    dictionaries come from :func:`aggregate_recorded_pair`; pairs (not
    assertions) are counted.
    """
    pair_list = list(pairs or [])
    counts = dict.fromkeys(RECORDED_OUTCOME_NAMES, 0)
    for pair in pair_list:
        name = pair.get("outcome") if isinstance(pair, dict) else None
        if name not in counts:
            raise ValueError(f"unknown recorded pair outcome {name!r}")
        counts[name] += 1
    total = len(pair_list)
    bad = (
        counts["failed"]
        + counts["error"]
        + counts["pending_judge"]
        + counts["insufficient_evidence"]
    )
    return {
        "counts": counts,
        "total": total,
        "all_inapplicable": total > 0 and counts["not_applicable"] == total,
        "complete": total > 0
        and all(pair.get("complete") is True for pair in pair_list),
        "gate_passed": total > 0 and counts["passed"] > 0 and bad == 0
        and all(pair.get("complete") is True for pair in pair_list),
    }
