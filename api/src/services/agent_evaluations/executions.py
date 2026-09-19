"""Suite execution bookkeeping for the ``agent.evaluation_suite`` PlatformJob.

The PlatformJob owns execution; this module owns the feature projection:
frozen suite/candidate references, per-case result rows, idempotent terminal
event application, and deterministic finalization. Session wrappers mutate
already-loaded ORM objects so the logic cores stay pure and unit-testable
without a database.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

EVALUATION_CONCURRENCY_DEFAULT = 4
EVALUATION_CONCURRENCY_MAX = 16

TERMINAL_RESULT_STATUSES = frozenset({"passed", "failed", "error"})


def build_dedupe_key(
    suite_id: UUID, suite_version: int, candidate_id: UUID | None
) -> str:
    side = str(candidate_id) if candidate_id is not None else "baseline"
    return f"suite:{suite_id}:v{suite_version}:candidate:{side}"


def plan_work_items(
    cases: list[dict[str, Any]],
    *,
    include_candidate: bool,
    repetitions_override: int | None = None,
) -> list[dict[str, Any]]:
    """One item per enabled case x repetition x side, in suite order."""
    items = []
    for case in sorted(cases, key=lambda c: (c.get("position", 0), c.get("name", ""))):
        if not case.get("enabled", True) or not case.get("accepted", True):
            continue
        repetitions = repetitions_override or case.get("repetitions", 1) or 1
        for repetition in range(repetitions):
            items.append(_item(case, "baseline", repetition))
            if include_candidate:
                items.append(_item(case, "candidate", repetition))
    return items


def _item(case: dict[str, Any], side: str, repetition: int) -> dict[str, Any]:
    return {
        "case_id": case["id"],
        "case_version": case["version"],
        "repetition_index": repetition,
        "side": side,
    }


def next_batch(
    planned: list[dict[str, Any]],
    started_keys: set[tuple],
    in_flight: int,
    ceiling: int,
) -> list[dict[str, Any]]:
    """Bounded next dispatch: skip started items, respect the ceiling."""

    def _key(item: dict[str, Any]) -> tuple:
        return (item["case_id"], item["case_version"], item["repetition_index"], item["side"])

    batch = []
    for item in planned:
        if len(batch) + in_flight >= ceiling:
            break
        if _key(item) not in started_keys:
            batch.append(item)
    return batch


def plan_result_work_items(results: list, *, include_candidate: bool) -> list[dict[str, Any]]:
    """Rebuild dispatch work from durable result rows without losing repeats."""
    items = []
    for result in sorted(
        results,
        key=lambda row: (str(row.case_id), row.case_version, row.repetition_index),
    ):
        items.append(
            {
                "case_id": str(result.case_id),
                "case_version": result.case_version,
                "repetition_index": result.repetition_index,
                "side": "baseline",
            }
        )
        if include_candidate:
            items.append(
                {
                    "case_id": str(result.case_id),
                    "case_version": result.case_version,
                    "repetition_index": result.repetition_index,
                    "side": "candidate",
                }
            )
    return items


def apply_terminal_event(
    result,
    *,
    side: str,
    run_id: UUID,
    status: str,
    evidence: dict[str, Any] | None = None,
    expects_candidate: bool = False,
) -> bool:
    """Idempotently record one side of a case result.

    ``expects_candidate`` must reflect the execution (not the rows): in a
    candidate execution the baseline event must wait for its pair instead
    of scoring alone — including the event-before-dispatch race where the
    candidate run ID is not recorded yet.

    Returns True when this call advanced the result (caller should persist
    and consider dispatching more work); False for duplicate deliveries.
    """
    run_field = "baseline_run_id" if side == "baseline" else "candidate_run_id"
    recorded_run_id = getattr(result, run_field)
    if recorded_run_id is not None and recorded_run_id != run_id:
        # A late duplicate dispatch/event must never replace the side that was
        # admitted under the result's durable fence.
        return False
    if recorded_run_id == run_id and _side_recorded(result, side):
        return False
    if result.status in TERMINAL_RESULT_STATUSES:
        # A scored result is frozen: scoring pops the staged evidence out
        # of ``comparison`` (raw evidence never persists there), so any
        # later delivery for an already-final result is a duplicate.
        return False
    setattr(result, run_field, run_id)
    _store_side_evidence(result, side, status, evidence or {})
    if _both_sides_recorded(result, expects_candidate):
        _score_result(result)
    elif result.status not in TERMINAL_RESULT_STATUSES:
        result.status = "running"
    return True


def _side_recorded(result, side: str) -> bool:
    staging = (result.comparison or {})
    return f"_evidence_{side}" in staging


def _both_sides_recorded(result, expects_candidate: bool) -> bool:
    staging = result.comparison or {}
    has_baseline = "_evidence_baseline" in staging
    has_candidate = "_evidence_candidate" in staging
    if expects_candidate or result.candidate_run_id is not None or has_candidate:
        return has_baseline and has_candidate
    return has_baseline


def _store_side_evidence(result, side: str, status: str, evidence: dict) -> None:
    staging = dict(result.comparison or {})
    staging[f"_evidence_{side}"] = {"status": status, "evidence": evidence}
    result.comparison = staging


def _score_result(result) -> None:
    """Evaluate assertions per side and compare once both sides are in."""
    from src.services.agent_evaluations.assertions import evaluate_assertions
    from src.services.agent_evaluations.comparison import compare_runs
    from src.services.agent_evaluations.simulator_models import redact_value

    staging = dict(result.comparison or {})
    baseline_entry = staging.pop("_evidence_baseline", {})
    candidate_entry = staging.pop("_evidence_candidate", None)
    assertions = list(result.assertion_results or [])
    definitions = [
        a.get("definition", a) if isinstance(a, dict) else a for a in assertions
    ]
    evidence_errors = {
        side: entry["evidence"]["evidence_error"]
        for side, entry in (("baseline", baseline_entry), ("candidate", candidate_entry))
        if entry is not None and entry.get("evidence", {}).get("evidence_error")
    }
    if evidence_errors:
        # Incomplete evidence is an explicit infrastructure error, never a
        # passing empty assertion set or an indefinitely deferred result.
        result.status = "error"
        result.error = "Evaluation evidence could not be loaded."
        result.comparison = {**staging, "evidence_errors": evidence_errors}
        result.assertion_results = []
        return
    baseline_evidence = baseline_entry.get("evidence", {})
    baseline_outcomes = evaluate_assertions(definitions, baseline_evidence)
    if candidate_entry is None:
        result.assertion_results = [
            {**o, "side": "baseline"} for o in baseline_outcomes
        ]
        result.comparison = {**staging, "side": "baseline"}
    else:
        candidate_outcomes = evaluate_assertions(
            definitions, candidate_entry.get("evidence", {})
        )
        result.assertion_results = [
            {**o, "side": "baseline"} for o in baseline_outcomes
        ] + [{**o, "side": "candidate"} for o in candidate_outcomes]
        result.comparison = {
            **staging,
            **compare_runs(
                baseline_outcomes,
                candidate_outcomes,
                baseline_tools=_tool_names(baseline_evidence),
                candidate_tools=_tool_names(candidate_entry.get("evidence", {})),
                baseline_output=baseline_evidence.get("output"),
                candidate_output=candidate_entry.get("evidence", {}).get("output"),
                baseline_usage=baseline_evidence.get("usage"),
                candidate_usage=candidate_entry.get("evidence", {}).get("usage"),
            ),
        }
    semantic_definitions = [
        definition for definition in definitions
        if isinstance(definition, dict) and definition.get("type") == "llm_judge"
    ]
    if semantic_definitions:
        comparison = dict(result.comparison or {})
        comparison["_semantic_pending"] = {
            "definitions": semantic_definitions,
            # These immutable, redacted evidence snapshots are the exact
            # judge inputs. Never keep raw terminal output in durable Studio
            # comparison JSON while a semantic observation is pending.
            "baseline_evidence": redact_value(baseline_evidence),
            "candidate_evidence": (
                redact_value(candidate_entry.get("evidence", {}))
                if candidate_entry is not None
                else None
            ),
        }
        result.comparison = comparison
    # Preserve per-side usage under ``comparison["usage"]`` for evidence-first
    # views — including baseline-only results. The generic usage_delta above
    # already carries the counts/fraction; no result-table columns needed.
    baseline_usage = baseline_evidence.get("usage") or {}
    if candidate_entry is not None:
        candidate_usage: dict[str, Any] | None = (
            candidate_entry.get("evidence", {}).get("usage") or {}
        )
    else:
        candidate_usage = None
    comparison = dict(result.comparison or {})
    comparison["usage"] = {"baseline": baseline_usage, "candidate": candidate_usage}
    result.comparison = comparison
    failed = [
        o
        for o in result.assertion_results
        if isinstance(o, dict) and not o.get("passed", True)
    ]
    statuses = {baseline_entry.get("status")}
    if candidate_entry is not None:
        statuses.add(candidate_entry.get("status"))
    if statuses <= {"completed"} and not failed:
        result.status = "passed"
    elif "error" in statuses:
        result.status = "error"
    else:
        result.status = "failed"
    usage = baseline_evidence.get("usage") or {}
    if candidate_entry is not None:
        candidate_usage = candidate_entry.get("evidence", {}).get("usage") or {}
        usage = candidate_usage or usage
    result.tokens_used = _as_int(usage.get("tokens"))
    result.turns_used = _as_int(usage.get("iterations"))
    result.duration_ms = _as_int(usage.get("latency_ms"))
    result.cost_usd = (
        str(usage["cost_usd"]) if usage.get("cost_usd") is not None else None
    )
    result.simulator_state_hash = baseline_evidence.get("simulator_state_hash") or None


async def resolve_semantic_assertions(session, result) -> None:
    """Persist optional non-authoritative judge observations after scoring."""
    from src.services.agent_evaluations.assertions import (
        evaluate_assertions_async,
        execute_semantic_judge,
    )

    comparison = dict(result.comparison or {})
    pending = comparison.pop("_semantic_pending", None)
    if not pending:
        return
    definitions = list(pending["definitions"])
    sides = [("baseline", pending["baseline_evidence"])]
    if pending.get("candidate_evidence") is not None:
        sides.append(("candidate", pending["candidate_evidence"]))
    resolved = {}
    for side, evidence in sides:
        outcomes = await evaluate_assertions_async(
            definitions,
            evidence,
            judge_fn=lambda params, judge_evidence: execute_semantic_judge(
                session, params, judge_evidence
            ),
        )
        resolved[side] = outcomes
    queues = {side: iter(outcomes) for side, outcomes in resolved.items()}
    replaced = []
    for outcome in result.assertion_results or []:
        if outcome.get("type") == "llm_judge":
            side = outcome.get("side", "baseline")
            judged = next(queues[side])
            replaced.append({**judged, "side": side, "label": outcome.get("label")})
        else:
            replaced.append(outcome)
    result.assertion_results = replaced
    result.comparison = comparison


def _as_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _tool_names(evidence: dict[str, Any]) -> list[str]:
    return [c.get("name") for c in evidence.get("tool_calls", [])]


def finalize_execution(execution, results: list) -> dict[str, Any]:
    """Deterministic counters + terminal status from result rows."""
    total = len(results)
    completed = sum(1 for r in results if r.status in TERMINAL_RESULT_STATUSES)
    passed = sum(1 for r in results if r.status == "passed")
    failed = sum(1 for r in results if r.status in ("failed", "error"))
    execution.total_cases = total
    execution.completed_cases = completed
    execution.passed_cases = passed
    execution.failed_cases = failed
    if completed == total and total > 0:
        execution.status = "succeeded" if failed == 0 else "failed"
    elif completed < total:
        execution.status = "running"
    return {
        "total": total,
        "completed": completed,
        "passed": passed,
        "failed": failed,
        "status": execution.status,
    }


def unfinished_run_ids(results: list) -> list[UUID]:
    """Synthetic AgentRun IDs still needing cancellation."""
    run_ids = []
    for result in results:
        if result.status in TERMINAL_RESULT_STATUSES:
            continue
        for field in ("baseline_run_id", "candidate_run_id"):
            run_id = getattr(result, field, None)
            if run_id is not None:
                run_ids.append(run_id)
    return run_ids


def create_execution_objects(
    *,
    suite,
    candidate_id: UUID | None,
    baseline_agent_id: UUID | None,
    cases: list,
    include_candidate: bool,
    repetitions_override: int | None,
    created_by: str | None,
    baseline_snapshot: dict[str, Any] | None = None,
    candidate_snapshot: dict[str, Any] | None = None,
    execution_id: UUID | None = None,
):
    """Build the execution + pending result rows (caller persists)."""
    from uuid import uuid4

    from src.models.orm.agent_evaluations import (
        AgentEvaluationExecution,
        AgentEvaluationResult,
    )

    resolved_id = execution_id or uuid4()
    dedupe_key = build_dedupe_key(suite.id, suite.version, candidate_id)
    execution = AgentEvaluationExecution(
        id=resolved_id,
        suite_id=suite.id,
        suite_version=suite.version,
        candidate_id=candidate_id,
        baseline_agent_id=baseline_agent_id,
        status="queued",
        dedupe_key=dedupe_key,
        created_by=created_by,
        baseline_snapshot=baseline_snapshot or {},
        candidate_snapshot=candidate_snapshot,
        case_definitions=[_case_definition(case) for case in cases],
    )
    planned = plan_work_items(
        [
            {
                "id": str(c.id),
                "version": c.version,
                "position": c.position,
                "enabled": c.enabled,
                "accepted": c.accepted,
                "repetitions": c.repetitions,
            }
            for c in cases
        ],
        include_candidate=include_candidate,
        repetitions_override=repetitions_override,
    )
    results = []
    seen = set()
    for item in planned:
        key = (item["case_id"], item["case_version"], item["repetition_index"])
        if key in seen:
            continue
        seen.add(key)
        case = next(c for c in cases if str(c.id) == item["case_id"])
        results.append(
            AgentEvaluationResult(
                execution_id=resolved_id,
                case_id=case.id,
                case_version=item["case_version"],
                repetition_index=item["repetition_index"],
                status="pending",
                assertion_results=[
                    {"definition": dict(a)} if isinstance(a, dict) else a
                    for a in (
                        list(case.assertions or [])
                        + [{"type": "tool_called", "params": {"tool": tool}}
                           for tool in (case.expected_tools or [])]
                        + [{"type": "forbidden_tool", "params": {"tool": tool}}
                           for tool in (case.forbidden_tools or [])]
                    )
                ],
            )
        )
    return execution, results, planned


def _case_definition(case) -> dict[str, Any]:
    """Fully frozen case data used by dispatch and scoring after enqueue."""
    return {
        "id": str(case.id), "version": case.version, "name": case.name,
        "position": case.position, "input": dict(case.input or {}),
        "fixture": dict(case.fixture or {}),
        "simulator_policy": dict(case.simulator_policy or {}),
        "assertions": list(case.assertions or []),
        "expected_tools": list(case.expected_tools or []),
        "forbidden_tools": list(case.forbidden_tools or []),
        "output_schema": case.output_schema,
        "repetitions": case.repetitions,
    }
