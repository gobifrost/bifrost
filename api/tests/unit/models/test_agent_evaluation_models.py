"""Model tests for the Agent Evaluation Studio persistence (Task 1).

Pure-Python ORM assertions: defaults, uniqueness, indexes, relationships,
and JSON round-trips. No live database required.
"""

from __future__ import annotations

from src.models.orm.agent_evaluations import (
    AgentCandidateSnapshot,
    AgentEvaluationCase,
    AgentEvaluationExecution,
    AgentEvaluationResult,
    AgentEvaluationSuite,
    AgentSimulationSession,
    AgentSimulationToolRecord,
)


def _unique_names(model) -> set[str]:
    return {
        c.name
        for c in model.__table__.constraints
        if c.__class__.__name__ == "UniqueConstraint"
    }


def _index_names(model) -> set[str]:
    return {i.name for i in model.__table__.indexes}


def test_suite_defaults_and_constraints():
    columns = AgentEvaluationSuite.__table__.c
    assert columns["status"].default.arg == "draft"
    assert columns["version"].default.arg == 1
    assert "uq_eval_suites_org_name_version" in _unique_names(AgentEvaluationSuite)
    indexes = _index_names(AgentEvaluationSuite)
    assert {"ix_eval_suites_org_id", "ix_eval_suites_agent_id", "ix_eval_suites_status"} <= indexes


def test_case_version_uniqueness_and_defaults():
    columns = AgentEvaluationCase.__table__.c
    assert columns["version"].default.arg == 1
    assert columns["position"].default.arg == 0
    assert columns["enabled"].default.arg is True
    assert columns["accepted"].default.arg is True
    assert columns["repetitions"].default.arg == 1
    assert columns["provenance"].default.arg == "manual"
    assert "uq_eval_cases_suite_name_version" in _unique_names(AgentEvaluationCase)
    indexes = _index_names(AgentEvaluationCase)
    assert {"ix_eval_cases_suite_id", "ix_eval_cases_suite_position"} <= indexes


def test_candidate_snapshot_is_evaluation_only():
    columns = AgentCandidateSnapshot.__table__.c
    assert columns["evaluation_only"].default.arg is True
    candidate = AgentCandidateSnapshot(overlays={}, snapshot={})
    assert candidate.snapshot_hash is None
    indexes = _index_names(AgentCandidateSnapshot)
    assert {
        "ix_eval_candidates_org_id",
        "ix_eval_candidates_base_agent_id",
        "ix_eval_candidates_snapshot_hash",
    } <= indexes


def test_execution_projection_and_active_dedupe():
    columns = AgentEvaluationExecution.__table__.c
    assert columns["status"].default.arg == "queued"
    assert columns["total_cases"].default.arg == 0
    assert columns["completed_cases"].default.arg == 0
    execution = AgentEvaluationExecution(suite_version=1)
    assert execution.platform_job_id is None
    partial = [
        i
        for i in AgentEvaluationExecution.__table__.indexes
        if i.name == "uq_eval_executions_active_dedupe"
    ]
    assert len(partial) == 1
    assert partial[0].unique is True
    assert "ix_eval_executions_platform_job_id" in _index_names(
        AgentEvaluationExecution
    )


def test_result_repetition_uniqueness_and_links():
    columns = AgentEvaluationResult.__table__.c
    assert columns["status"].default.arg == "pending"
    assert columns["repetition_index"].default.arg == 0
    result = AgentEvaluationResult(case_version=1)
    assert result.baseline_run_id is None
    assert result.candidate_run_id is None
    assert (
        "uq_eval_results_execution_case_repetition"
        in _unique_names(AgentEvaluationResult)
    )


def test_simulation_session_and_tool_records():
    columns = AgentSimulationSession.__table__.c
    assert columns["version"].default.arg == 0
    session = AgentSimulationSession(case_version=1, state={})
    assert session.initial_state_hash is None
    record = AgentSimulationToolRecord(sequence=0, tool_name="get_ticket")
    assert record.state_hash is None
    assert (
        "uq_sim_tool_records_session_sequence"
        in _unique_names(AgentSimulationToolRecord)
    )


def test_contract_models_round_trip():
    from src.models.contracts.agent_evaluations import (
        CandidateCreate,
        EvaluationAssertion,
        EvaluationCaseCreate,
        EvaluationExecutionCreate,
        EvaluationSuiteCreate,
    )

    assertion = EvaluationAssertion(type="tool_called", params={"tool": "get_ticket"})
    case = EvaluationCaseCreate(
        name="lookup",
        fixture={"entities": {"tickets": {}}},
        assertions=[assertion],
        provenance="generated",
    )
    assert case.assertions[0].type == "tool_called"
    suite = EvaluationSuiteCreate(name="nightly")
    assert suite.name == "nightly"
    candidate = CandidateCreate(
        base_agent_id="00000000-0000-0000-0000-000000000000",
        overlays={"system_prompt": "Be brief."},
    )
    assert candidate.overlays.system_prompt == "Be brief."
    execution = EvaluationExecutionCreate(
        suite_id="00000000-0000-0000-0000-000000000000"
    )
    assert execution.candidate_id is None

    import pytest

    with pytest.raises(Exception):
        EvaluationAssertion(type="made_up_type", params={}, bogus_field=True)
