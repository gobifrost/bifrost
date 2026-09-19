"""PlatformJob orchestration tests: planning, batching, idempotency, scoring."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from src.jobs.platform.agent_evaluation import (
    AGENT_EVALUATION_SUITE_DEFINITION,
    AgentEvaluationSuitePayload,
    _in_flight,
    _started_keys,
)
from src.jobs.platform.registry import get_platform_job_definition
from src.models.orm.agent_evaluations import (
    AgentEvaluationExecution,
    AgentEvaluationResult,
)
from src.services.agent_evaluations.executions import (
    apply_terminal_event,
    build_dedupe_key,
    create_execution_objects,
    finalize_execution,
    next_batch,
    plan_work_items,
    unfinished_run_ids,
)


def _evidence(**over) -> dict:
    base = {
        "terminal_status": "completed",
        "output": {"answer": "ok"},
        "tool_calls": [],
        "simulator_state": {},
        "delegation": {"children": []},
        "usage": {"iterations": 1, "tokens": 10},
        "real_tool_executions": 0,
    }
    base.update(over)
    return base


def _result(**over) -> AgentEvaluationResult:
    kwargs = dict(
        case_version=1,
        repetition_index=0,
        status="pending",
        assertion_results=[
            {"type": "terminal_status", "params": {"status": "completed"}},
            {"type": "no_real_tools", "params": {}},
        ],
    )
    kwargs.update(over)
    return AgentEvaluationResult(**kwargs)


def test_payload_carries_execution_id_only():
    payload = AgentEvaluationSuitePayload(execution_id=uuid4())
    assert set(payload.model_dump()) == {"execution_id"}


def test_registry_dispatch_and_policy():
    definition = get_platform_job_definition("agent.evaluation_suite")
    assert definition is AGENT_EVALUATION_SUITE_DEFINITION
    assert definition.payload_version == 1
    assert definition.payload_model is AgentEvaluationSuitePayload
    assert definition.policy.timeout_seconds == 30 * 60
    assert definition.policy.max_attempts == 2
    assert definition.policy.retry_on_runner_loss is True
    assert definition.policy.allow_running_cancellation is True


def test_dedupe_key_separates_baseline_and_candidate():
    suite_id = uuid4()
    assert build_dedupe_key(suite_id, 1, None) != build_dedupe_key(
        suite_id, 1, uuid4()
    )
    assert build_dedupe_key(suite_id, 1, None) != build_dedupe_key(suite_id, 2, None)


def test_plan_orders_filter_and_expands_sides():
    cases = [
        {"id": "b", "version": 1, "position": 1, "enabled": True, "accepted": True, "repetitions": 1},
        {"id": "a", "version": 2, "position": 0, "enabled": True, "accepted": True, "repetitions": 2},
        {"id": "skip", "version": 1, "position": 2, "enabled": False, "accepted": True, "repetitions": 1},
        {"id": "draft", "version": 1, "position": 3, "enabled": True, "accepted": False, "repetitions": 1},
    ]
    planned = plan_work_items(cases, include_candidate=True)
    assert [i["case_id"] for i in planned] == ["a", "a", "a", "a", "b", "b"]
    assert [i["side"] for i in planned[:4]] == [
        "baseline", "candidate", "baseline", "candidate",
    ]
    baseline_only = plan_work_items(cases, include_candidate=False)
    assert {i["side"] for i in baseline_only} == {"baseline"}
    assert len(baseline_only) == 3


def test_next_batch_respects_ceiling_and_started():
    planned = [
        {"case_id": "a", "case_version": 1, "repetition_index": 0, "side": "baseline"},
        {"case_id": "a", "case_version": 1, "repetition_index": 0, "side": "candidate"},
        {"case_id": "b", "case_version": 1, "repetition_index": 0, "side": "baseline"},
    ]
    started = {("a", 1, 0, "baseline")}
    batch = next_batch(planned, started, in_flight=0, ceiling=2)
    assert [i["side"] for i in batch] == ["candidate", "baseline"]
    assert next_batch(planned, started, in_flight=2, ceiling=2) == []


def test_baseline_only_terminal_scores_passed():
    result = _result()
    run_id = uuid4()
    assert (
        apply_terminal_event(
            result, side="baseline", run_id=run_id,
            status="completed", evidence=_evidence(),
        )
        is True
    )
    assert result.status == "passed"
    assert result.baseline_run_id == run_id
    assert all(o["side"] == "baseline" for o in result.assertion_results)
    # Duplicate delivery is a no-op.
    assert (
        apply_terminal_event(
            result, side="baseline", run_id=run_id,
            status="completed", evidence=_evidence(),
        )
        is False
    )


def test_baseline_candidate_pair_compares_and_regresses():
    result = _result()
    base_id, cand_id = uuid4(), uuid4()
    apply_terminal_event(
        result, side="baseline", run_id=base_id,
        status="completed", evidence=_evidence(), expects_candidate=True,
    )
    assert result.status == "running"
    apply_terminal_event(
        result, side="candidate", run_id=cand_id,
        status="failed", evidence=_evidence(terminal_status="failed"),
        expects_candidate=True,
    )
    assert result.status == "failed"
    assert result.comparison["verdict"] == "regression"
    assert result.comparison["regressions"] == ["terminal_status"]


def test_runner_loss_rerun_is_deterministic():
    first = _result()
    second = _result()
    run_id = uuid4()
    apply_terminal_event(
        first, side="baseline", run_id=run_id,
        status="completed", evidence=_evidence(),
    )
    apply_terminal_event(
        second, side="baseline", run_id=run_id,
        status="completed", evidence=_evidence(),
    )
    assert first.status == second.status == "passed"
    assert first.assertion_results == second.assertion_results


def test_finalize_counters_and_status():
    execution = AgentEvaluationExecution(suite_version=1, status="running")
    summary = finalize_execution(
        execution,
        [_result(status="passed"), _result(status="failed"), _result(status="pending")],
    )
    assert summary == {
        "total": 3, "completed": 2, "passed": 1, "failed": 1, "status": "running",
    }
    assert execution.completed_cases == 2
    all_done = finalize_execution(
        execution, [_result(status="passed"), _result(status="passed")]
    )
    assert all_done["status"] == "succeeded"
    assert execution.status == "succeeded"


def test_unfinished_run_ids_skip_terminal_results():
    done = _result(status="passed", baseline_run_id=uuid4(), candidate_run_id=uuid4())
    active_id = uuid4()
    active = _result(status="running", baseline_run_id=active_id)
    assert unfinished_run_ids([done, active]) == [active_id]


def test_create_execution_objects_freezes_and_dedupes():
    suite = SimpleNamespace(id=uuid4(), version=3)
    candidate_id = uuid4()
    case = SimpleNamespace(
        id=uuid4(), version=1, position=0, enabled=True, accepted=True,
        repetitions=2, assertions=[{"type": "no_real_tools", "params": {}}],
    )
    execution, results, planned = create_execution_objects(
        suite=suite,
        candidate_id=candidate_id,
        baseline_agent_id=uuid4(),
        cases=[case],
        include_candidate=True,
        repetitions_override=None,
        created_by="tester",
        execution_id=uuid4(),
    )
    assert execution.suite_version == 3
    assert execution.dedupe_key == build_dedupe_key(suite.id, 3, candidate_id)
    assert len(results) == 2
    assert len(planned) == 4
    assert results[0].assertion_results == [
        {"definition": {"type": "no_real_tools", "params": {}}}
    ]


def test_started_keys_and_in_flight():
    results = [
        _result(status="running", baseline_run_id=uuid4(), case_id=uuid4()),
        _result(
            status="passed",
            baseline_run_id=uuid4(),
            candidate_run_id=uuid4(),
            case_id=uuid4(),
        ),
    ]
    assert len(_started_keys(results)) == 3
    assert _in_flight(results) == 1


def test_partial_failure_and_cancellation_paths():
    result = _result()
    apply_terminal_event(
        result, side="baseline", run_id=uuid4(),
        status="failed", evidence=_evidence(terminal_status="failed"),
    )
    assert result.status == "failed"
    error_result = _result()
    apply_terminal_event(
        error_result, side="baseline", run_id=uuid4(),
        status="error", evidence=_evidence(),
    )
    assert error_result.status == "error"
