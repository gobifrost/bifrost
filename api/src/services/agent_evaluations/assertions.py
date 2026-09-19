"""Deterministic evaluation assertions over synthetic run evidence.

Every assertion produces a stable code, pass/fail, redacted
expected/actual values, and evidence journal sequence IDs. Exact and
predicate failures are authoritative; semantic (LLM-judge) scoring is
explicitly labeled nondeterministic and never gates a core v1 suite.

Malformed assertions fail at case save time via :func:`validate_assertions`,
never at result time.
"""

from __future__ import annotations

from typing import Any, Callable

from src.services.agent_evaluations.simulator_models import redact_value

KNOWN_ASSERTION_TYPES = frozenset(
    {
        "terminal_status",
        "output_schema",
        "output_path",
        "tool_called",
        "tool_not_called",
        "forbidden_tool",
        "tool_count",
        "tool_order",
        "tool_args",
        "simulator_state",
        "delegation_tree",
        "max_iterations",
        "max_tokens",
        "max_cost_usd",
        "max_latency_ms",
        "no_real_tools",
        "llm_judge",
    }
)


class AssertionDefinitionError(Exception):
    """An assertion is malformed and must be fixed at case save time."""


def validate_assertions(assertions: list[dict[str, Any]]) -> None:
    """Fail closed on malformed assertions before a case version freezes."""
    for index, assertion in enumerate(assertions):
        if not isinstance(assertion, dict):
            raise AssertionDefinitionError(f"Assertion {index} must be an object.")
        atype = assertion.get("type")
        if atype not in KNOWN_ASSERTION_TYPES:
            raise AssertionDefinitionError(
                f"Assertion {index} has unknown type {atype!r}."
            )
        params = assertion.get("params", {})
        if not isinstance(params, dict):
            raise AssertionDefinitionError(
                f"Assertion {index} params must be an object."
            )
        _require_params(index, atype, params)


def _require_params(index: int, atype: str, params: dict[str, Any]) -> None:
    required: dict[str, tuple[str, ...]] = {
        "terminal_status": ("status",),
        "output_path": ("path",),
        "tool_called": ("tool",),
        "tool_not_called": ("tool",),
        "forbidden_tool": ("tool",),
        "tool_count": ("tool", "count"),
        "tool_order": ("tools",),
        "tool_args": ("tool",),
        "simulator_state": ("path",),
        "llm_judge": ("model", "prompt_version", "threshold"),
    }
    for key in required.get(atype, ()):
        if key not in params:
            raise AssertionDefinitionError(
                f"Assertion {index} ({atype}) is missing required param {key!r}."
            )


def _get_path(value: Any, path: str) -> Any:
    current = value
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def _outcome(
    code: str,
    atype: str,
    passed: bool,
    *,
    label: str | None = None,
    expected: Any = None,
    actual: Any = None,
    evidence_sequences: list[int] | None = None,
    detail: str | None = None,
) -> dict[str, Any]:
    return {
        "code": code,
        "type": atype,
        "label": label,
        "passed": passed,
        "expected": redact_value(expected),
        "actual": redact_value(actual),
        "evidence_sequences": list(evidence_sequences or []),
        "detail": detail,
    }


JudgeFn = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]


def evaluate_assertions(
    assertions: list[dict[str, Any]],
    evidence: dict[str, Any],
    *,
    judge_fn: JudgeFn | None = None,
) -> list[dict[str, Any]]:
    """Evaluate validated assertions against finished-run evidence.

    ``evidence`` keys: ``terminal_status``, ``output``, ``tool_calls``
    (ordered ``{name, arguments, sequence}``), ``simulator_state``,
    ``delegation`` (``{children: [...]}``), ``usage`` (``iterations``,
    ``tokens``, ``cost_usd``, ``latency_ms``), ``real_tool_executions``,
    ``malformed_output``.
    """
    validate_assertions(assertions)
    outcomes = []
    for assertion in assertions:
        atype = assertion["type"]
        params = assertion.get("params", {})
        label = assertion.get("label")
        evaluator = _EVALUATORS[atype]
        if atype == "llm_judge":
            outcomes.append(evaluator(params, evidence, label, judge_fn))
        else:
            outcomes.append(evaluator(params, evidence, label))
    return outcomes


def _ev_terminal_status(params, evidence, label):
    expected = params["status"]
    actual = evidence.get("terminal_status")
    return _outcome(
        "terminal_status", "terminal_status", actual == expected,
        label=label, expected=expected, actual=actual,
    )


def _ev_output_schema(params, evidence, label):
    from src.services.tool_schema import validate_arguments_against_schema

    schema = params.get("schema") or evidence.get("output_schema") or {}
    output = evidence.get("output")
    if evidence.get("malformed_output"):
        return _outcome(
            "output_schema", "output_schema", False, label=label,
            expected="schema-valid output", actual="malformed output",
        )
    issues, schema_error = validate_arguments_against_schema(
        schema, output if isinstance(output, dict) else {}
    )
    passed = not issues and not schema_error and isinstance(output, dict)
    return _outcome(
        "output_schema", "output_schema", passed, label=label,
        expected="schema-valid output",
        actual=(output if passed else (schema_error or issues)),
    )


def _ev_output_path(params, evidence, label):
    actual = _get_path(evidence.get("output"), params["path"])
    if "equals" in params:
        passed = actual == params["equals"]
        expected: Any = params["equals"]
    elif "contains" in params:
        passed = params["contains"] in (actual or [])
        expected = params["contains"]
    else:
        passed = actual is not None
        expected = "present value"
    return _outcome(
        "output_path", "output_path", passed, label=label,
        expected=expected, actual=actual,
    )


def _tool_sequences(evidence, tool: str) -> list[int]:
    return [
        call.get("sequence", -1)
        for call in evidence.get("tool_calls", [])
        if call.get("name") == tool
    ]


def _ev_tool_called(params, evidence, label):
    seqs = _tool_sequences(evidence, params["tool"])
    return _outcome(
        "tool_called", "tool_called", len(seqs) > 0, label=label,
        expected=params["tool"], actual=f"{len(seqs)} call(s)",
        evidence_sequences=seqs,
    )


def _ev_tool_not_called(params, evidence, label, *, code="tool_not_called"):
    seqs = _tool_sequences(evidence, params["tool"])
    return _outcome(
        code, params.get("_type", code), len(seqs) == 0, label=label,
        expected=f"{params['tool']} not called",
        actual=f"{len(seqs)} call(s)", evidence_sequences=seqs,
    )


def _ev_forbidden_tool(params, evidence, label):
    return _ev_tool_not_called(
        dict(params, _type="forbidden_tool"), evidence, label, code="forbidden_tool"
    )


def _ev_tool_count(params, evidence, label):
    seqs = _tool_sequences(evidence, params["tool"])
    return _outcome(
        "tool_count", "tool_count", len(seqs) == params["count"], label=label,
        expected=params["count"], actual=len(seqs), evidence_sequences=seqs,
    )


def _ev_tool_order(params, evidence, label):
    expected_order: list[str] = params["tools"]
    actual_order = [call.get("name") for call in evidence.get("tool_calls", [])]
    if params.get("exact"):
        passed = actual_order == expected_order
    else:
        positions = []
        cursor = 0
        for tool in expected_order:
            try:
                found = actual_order.index(tool, cursor)
            except ValueError:
                passed = False
                break
            positions.append(found)
            cursor = found + 1
        else:
            passed = True
    seqs = [call.get("sequence", -1) for call in evidence.get("tool_calls", [])]
    return _outcome(
        "tool_order", "tool_order", passed, label=label,
        expected=expected_order, actual=actual_order, evidence_sequences=seqs,
    )


def _ev_tool_args(params, evidence, label):
    tool = params["tool"]
    expected_args = params.get("args", {})
    matches = [
        call for call in evidence.get("tool_calls", []) if call.get("name") == tool
    ]
    if not matches:
        return _outcome(
            "tool_args", "tool_args", False, label=label,
            expected=expected_args, actual="tool never called",
        )
    passed = any(
        all(
            _get_path(call.get("arguments", {}), path) == value
            for path, value in expected_args.items()
        )
        for call in matches
    )
    seqs = [call.get("sequence", -1) for call in matches]
    return _outcome(
        "tool_args", "tool_args", passed, label=label,
        expected=expected_args,
        actual=[call.get("arguments") for call in matches],
        evidence_sequences=seqs,
    )


def _ev_simulator_state(params, evidence, label):
    actual = _get_path(evidence.get("simulator_state", {}), params["path"])
    passed = actual == params.get("equals") if "equals" in params else actual is not None
    return _outcome(
        "simulator_state", "simulator_state", passed, label=label,
        expected=params.get("equals", "present value"), actual=actual,
    )


def _ev_delegation_tree(params, evidence, label):
    children = (evidence.get("delegation") or {}).get("children", [])
    if "min_children" in params and len(children) < params["min_children"]:
        return _outcome(
            "delegation_tree", "delegation_tree", False, label=label,
            expected=f">= {params['min_children']} children",
            actual=f"{len(children)} children",
        )
    if "max_children" in params and len(children) > params["max_children"]:
        return _outcome(
            "delegation_tree", "delegation_tree", False, label=label,
            expected=f"<= {params['max_children']} children",
            actual=f"{len(children)} children",
        )
    if "agents" in params:
        actual_agents = sorted(c.get("agent_name") for c in children)
        passed = actual_agents == sorted(params["agents"])
        return _outcome(
            "delegation_tree", "delegation_tree", passed, label=label,
            expected=sorted(params["agents"]), actual=actual_agents,
        )
    return _outcome(
        "delegation_tree", "delegation_tree", True, label=label,
        expected="delegation constraints hold",
        actual=f"{len(children)} children",
    )


def _budget_checker(key: str, code: str):
    def _check(params, evidence, label):
        usage = evidence.get("usage", {})
        actual = usage.get(key)
        limit = params.get("limit", params.get("max"))
        if limit is None or actual is None:
            return _outcome(code, code, False, label=label,
                            expected=f"{key} <= {limit}", actual=actual,
                            detail="missing usage or limit")
        try:
            passed = float(actual) <= float(limit)
        except (TypeError, ValueError):
            passed = False
        return _outcome(code, code, passed, label=label,
                        expected=f"{key} <= {limit}", actual=actual)

    _check.__name__ = f"_ev_{code}"
    return _check


def _ev_no_real_tools(params, evidence, label):
    count = evidence.get("real_tool_executions", 0)
    return _outcome(
        "no_real_tools", "no_real_tools", count == 0, label=label,
        expected=0, actual=count,
    )


def _ev_llm_judge(params, evidence, label, judge_fn):
    if judge_fn is None:
        return _outcome(
            "llm_judge", "llm_judge", False, label=label,
            expected=f"score >= {params['threshold']}",
            actual="judge not configured",
            detail="nondeterministic: llm_judge requires an explicit judge",
        )
    verdict = judge_fn(params, evidence)
    score = verdict.get("score")
    passed = score is not None and float(score) >= float(params["threshold"])
    return {
        **_outcome(
            "llm_judge", "llm_judge", passed, label=label,
            expected=f"score >= {params['threshold']} (nondeterministic)",
            actual=score,
            detail=verdict.get("rationale"),
        ),
        "judge_model": params["model"],
        "judge_prompt_version": params["prompt_version"],
        "judge_usage": verdict.get("usage", {}),
        "nondeterministic": True,
    }


_EVALUATORS = {
    "terminal_status": _ev_terminal_status,
    "output_schema": _ev_output_schema,
    "output_path": _ev_output_path,
    "tool_called": _ev_tool_called,
    "tool_not_called": _ev_tool_not_called,
    "forbidden_tool": _ev_forbidden_tool,
    "tool_count": _ev_tool_count,
    "tool_order": _ev_tool_order,
    "tool_args": _ev_tool_args,
    "simulator_state": _ev_simulator_state,
    "delegation_tree": _ev_delegation_tree,
    "max_iterations": _budget_checker("iterations", "max_iterations"),
    "max_tokens": _budget_checker("tokens", "max_tokens"),
    "max_cost_usd": _budget_checker("cost_usd", "max_cost_usd"),
    "max_latency_ms": _budget_checker("latency_ms", "max_latency_ms"),
    "no_real_tools": _ev_no_real_tools,
    "llm_judge": _ev_llm_judge,
}
