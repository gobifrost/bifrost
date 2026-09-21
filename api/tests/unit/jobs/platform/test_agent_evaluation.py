"""PlatformJob orchestration tests: planning, batching, idempotency, scoring."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select

from src.jobs.platform.agent_evaluation import (
    AGENT_EVALUATION_SUITE_DEFINITION,
    AgentEvaluationSuitePayload,
    _dispatch_case_run,
    _started_keys,
    cancel_evaluation_execution,
    reconcile_agent_evaluation_jobs,
    run_agent_evaluation_suite,
)
from src.jobs.platform.base import PlatformJobCancelled
from src.jobs.platform.registry import get_platform_job_definition
from src.models.orm.agent_evaluations import (
    AgentEvaluationExecution,
    AgentEvaluationResult,
    AgentEvaluationCase,
    AgentEvaluationSuite,
    AgentCandidateSnapshot,
    AgentSimulationSession,
    AgentSimulationToolRecord,
)
from src.models.orm.agent_runs import AgentRun, AgentRunJournalEntry
from src.models.orm.agents import Agent
from src.models.orm.ai_usage import AIUsage
from src.models.orm.platform_jobs import PlatformJob
from src.services.agent_evaluations.evidence import (
    load_persisted_evaluation_evidence,
)
from src.services.agent_evaluations.executions import (
    apply_terminal_event,
    build_dedupe_key,
    create_execution_objects,
    finalize_execution,
    next_batch,
    plan_result_work_items,
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


def test_synthetic_semantic_job_policy_has_no_runner_loss_retry():
    from shared.models import SyntheticSemanticJudgePayload
    from src.jobs.platform.agent_synthetic_judge import (
        SYNTHETIC_SEMANTIC_JUDGE_DEFINITION,
    )

    definition = get_platform_job_definition("agent.evaluation_synthetic_semantic")

    assert definition is SYNTHETIC_SEMANTIC_JUDGE_DEFINITION
    assert definition.payload_model is SyntheticSemanticJudgePayload
    assert definition.policy.max_attempts == 1
    assert definition.policy.retry_on_runner_loss is False
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


def test_durable_result_plan_preserves_each_repetition_and_side():
    case_id = uuid4()
    results = [
        _result(case_id=case_id, case_version=2, repetition_index=0),
        _result(case_id=case_id, case_version=2, repetition_index=1),
    ]
    planned = plan_result_work_items(results, include_candidate=True)
    assert [(item["repetition_index"], item["side"]) for item in planned] == [
        (0, "baseline"), (0, "candidate"), (1, "baseline"), (1, "candidate"),
    ]


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


def test_terminal_event_cannot_replace_admitted_side_run():
    result = _result(baseline_run_id=uuid4())
    assert (
        apply_terminal_event(
            result,
            side="baseline",
            run_id=uuid4(),
            status="completed",
            evidence=_evidence(),
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
        name="frozen", input={"task": "x"}, fixture={"entities": {}},
        simulator_policy={}, expected_tools=["get_ticket"], forbidden_tools=["delete_ticket"],
        output_schema={"type": "object"}, scoring_policy={},
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
        {"definition": {"type": "no_real_tools", "params": {}}},
        {"definition": {"type": "tool_called", "params": {"tool": "get_ticket"}}},
        {"definition": {"type": "forbidden_tool", "params": {"tool": "delete_ticket"}}},
    ]
    assert execution.case_definitions[0]["input"] == {"task": "x"}


def test_started_keys_include_only_admitted_sides():
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


def _synthetic_snapshot(agent_id) -> dict:
    return {
        "format_version": 1,
        "agent_id": str(agent_id),
        "agent_name": "evaluation-agent",
        "system_prompt": "Be deterministic.",
        "model": {},
        "tools": [{"name": "get_ticket", "parameters": {"type": "object"}}],
        "delegated_agents": [],
        "system_tools": [],
        "limits": {},
        "evaluation": {"mode": "evaluation_synthetic", "evaluation_only": True},
    }


async def _persist_terminal_side(
    db_session,
    *,
    run: AgentRun,
    result: AgentEvaluationResult,
    case: AgentEvaluationCase,
    state: dict,
) -> None:
    db_session.add(run)
    await db_session.flush()
    result.baseline_run_id = run.id
    result.status = "running"
    db_session.add(
        AgentSimulationSession(
            execution_id=result.execution_id,
            result_id=result.id,
            case_id=case.id,
            case_version=case.version,
            side="baseline",
            run_id=run.id,
            root_run_id=run.id,
            fixture={"version": 1, "entities": {}},
            tool_schemas={"get_ticket": {"type": "object"}},
            state=state,
            initial_state_hash="initial",
            final_state_hash="final",
        )
    )
    await db_session.flush()
    simulation = await db_session.scalar(
        select(AgentSimulationSession).where(
            AgentSimulationSession.result_id == result.id,
            AgentSimulationSession.side == "baseline",
        )
    )
    assert simulation is not None
    db_session.add(
        AgentSimulationToolRecord(
            session_id=simulation.id,
            sequence=0,
            operation_id=f"{run.id}:provider-call-1",
            tool_name="get_ticket",
            arguments={"id": "ticket-1"},
            result={"id": "ticket-1", "status": "resolved"},
            state_hash="final",
        )
    )
    db_session.add(
        AIUsage(
            provider="openai",
            model="test-model",
            input_tokens=10,
            output_tokens=15,
            provider_cost=Decimal("0.004"),
            cost=Decimal("0.004"),
            duration_ms=75,
            agent_run_id=run.id,
            sequence=1,
        )
    )


@pytest.mark.asyncio
async def test_scheduler_reconciles_persisted_evidence_dispatches_repetition_and_finishes_job(
    db_session, monkeypatch: pytest.MonkeyPatch
):
    """The production scheduler scores durable rows, not empty placeholders."""
    from src.jobs import rabbitmq
    from src.services import platform_jobs

    monkeypatch.setattr(rabbitmq, "publish_message", AsyncMock())
    monkeypatch.setattr(platform_jobs, "publish_platform_job_update", AsyncMock())
    agent = Agent(
        name=f"Evaluation scheduler {uuid4().hex}",
        system_prompt="Be deterministic.",
        created_by="test@example.com",
    )
    db_session.add(agent)
    await db_session.flush()
    suite = AgentEvaluationSuite(
        agent_id=agent.id,
        name=f"scheduler-{uuid4().hex}",
        status="published",
        version=1,
    )
    db_session.add(suite)
    await db_session.flush()
    assertions = [
        {"type": "terminal_status", "params": {"status": "completed"}},
        {"type": "tool_called", "params": {"tool": "get_ticket"}},
        {
            "type": "simulator_state",
            "params": {"path": "entities.ticket.ticket-1.status", "equals": "resolved"},
        },
        {"type": "max_cost_usd", "params": {"limit": 0.01}},
        {"type": "max_latency_ms", "params": {"limit": 200}},
        {"type": "no_real_tools", "params": {}},
    ]
    case = AgentEvaluationCase(
        suite_id=suite.id,
        name="persisted evidence",
        accepted=True,
        repetitions=2,
        assertions=assertions,
        fixture={"version": 1, "entities": {}},
    )
    db_session.add(case)
    await db_session.flush()
    snapshot = _synthetic_snapshot(agent.id)
    job = PlatformJob(
        job_type="agent.evaluation_suite",
        payload_version=1,
        payload={},
        requested_by_user_id=str(uuid4()),
        requested_by_email="test@example.com",
        requested_by_name="Test",
        title="Evaluation scheduler test",
        status="waiting",
    )
    db_session.add(job)
    await db_session.flush()
    candidate = AgentCandidateSnapshot(
        base_agent_id=agent.id,
        overlays={},
        snapshot=snapshot,
    )
    db_session.add(candidate)
    await db_session.flush()
    execution, results, _ = create_execution_objects(
        suite=suite,
        candidate_id=candidate.id,
        baseline_agent_id=agent.id,
        cases=[case],
        include_candidate=True,
        repetitions_override=None,
        created_by="test@example.com",
        baseline_snapshot=snapshot,
        candidate_snapshot=snapshot,
    )
    execution.platform_job_id = job.id
    execution.status = "waiting"
    db_session.add(execution)
    db_session.add_all(results)
    await db_session.flush()
    started_at = datetime(2026, 9, 19, tzinfo=timezone.utc)
    first_run = AgentRun(
        id=uuid4(),
        agent_id=agent.id,
        trigger_type="evaluation_synthetic",
        status="completed",
        output={"answer": "resolved"},
        iterations_used=2,
        tokens_used=25,
        duration_ms=75,
        started_at=started_at,
        completed_at=started_at + timedelta(milliseconds=120),
        root_run_id=None,
        execution_snapshot=snapshot,
        correlation={"evaluation_mode": "evaluation_synthetic"},
    )
    await _persist_terminal_side(
        db_session,
        run=first_run,
        result=results[0],
        case=case,
        state={"version": 1, "entities": {"ticket": {"ticket-1": {"status": "resolved"}}}},
    )
    first_run.root_run_id = first_run.id
    child_run = AgentRun(
        id=uuid4(),
        agent_id=agent.id,
        trigger_type="evaluation_synthetic",
        status="completed",
        output={"answer": "delegated"},
        iterations_used=1,
        tokens_used=5,
        duration_ms=75,
        parent_run_id=first_run.id,
        root_run_id=first_run.id,
        execution_snapshot=snapshot,
        correlation={"evaluation_mode": "evaluation_synthetic"},
    )
    db_session.add(child_run)
    await db_session.flush()
    db_session.add(
        AgentRunJournalEntry(
            run_id=first_run.id,
            sequence=1,
            kind="delegation",
            data={"child_run_id": str(child_run.id)},
        )
    )
    db_session.add(
        AgentRunJournalEntry(
            run_id=first_run.id,
            sequence=2,
            kind="tool_result",
            data={"tool_call_id": "provider-call-1"},
        )
    )
    db_session.add(
        AIUsage(
            provider="openai", model="test-model", input_tokens=2,
            output_tokens=3, provider_cost=Decimal("0.004"),
            cost=Decimal("0.004"), duration_ms=75,
            agent_run_id=child_run.id, sequence=1,
        )
    )
    await db_session.commit()

    try:
        persisted_evidence = await load_persisted_evaluation_evidence(
            db_session, first_run.id
        )
        delegated = persisted_evidence["delegation"]["children"][0]
        assert delegated["run_id"] == str(child_run.id)
        assert delegated["agent_name"] == "evaluation-agent"
        assert delegated["journal_references"] == [
            {
                "run_id": str(first_run.id),
                "sequence": 1,
                "kind": "delegation",
            }
        ]
        assert persisted_evidence["tool_calls"][0]["journal_references"] == [
            {
                "run_id": str(first_run.id),
                "sequence": 2,
                "kind": "tool_result",
            }
        ]
        assert Decimal(persisted_evidence["usage"]["cost_usd"]) == Decimal("0.008")
        assert persisted_evidence["usage"]["latency_ms"] == 120
        assert persisted_evidence["usage"]["provider_latency_ms"] == 150
        assert await reconcile_agent_evaluation_jobs() >= 1
        await db_session.refresh(results[0])
        await db_session.refresh(results[1])
        assert results[0].status == "running"
        assert results[0].candidate_run_id is not None
        assert results[1].baseline_run_id is not None
        assert results[1].candidate_run_id is not None

        admitted_sides = [
            (results[0], "candidate", results[0].candidate_run_id),
            (results[1], "baseline", results[1].baseline_run_id),
            (results[1], "candidate", results[1].candidate_run_id),
        ]
        for result, side, run_id in admitted_sides:
            assert run_id is not None
            run = await db_session.get(AgentRun, run_id)
            assert run is not None
            run.status = "completed"
            run.output = {"answer": "resolved"}
            run.iterations_used = 2
            run.tokens_used = 25
            run.duration_ms = 75
            simulation = await db_session.scalar(
                select(AgentSimulationSession).where(
                    AgentSimulationSession.result_id == result.id,
                    AgentSimulationSession.side == side,
                )
            )
            assert simulation is not None
            simulation.state = {
                "version": 1,
                "entities": {"ticket": {"ticket-1": {"status": "resolved"}}},
            }
            simulation.final_state_hash = f"final-{run.id}"
            db_session.add(
                AgentSimulationToolRecord(
                    session_id=simulation.id,
                    sequence=0,
                    operation_id=f"{run.id}:provider-call-1",
                    tool_name="get_ticket",
                    arguments={"id": "ticket-1"},
                    result={"id": "ticket-1", "status": "resolved"},
                    state_hash=simulation.final_state_hash,
                )
            )
            db_session.add(
                AIUsage(
                    provider="openai", model="test-model", input_tokens=10,
                    output_tokens=15, provider_cost=Decimal("0.004"),
                    cost=Decimal("0.004"), duration_ms=75,
                    agent_run_id=run.id, sequence=1,
                )
            )
        await db_session.commit()

        assert await reconcile_agent_evaluation_jobs() >= 1
        await db_session.refresh(execution)
        await db_session.refresh(results[0])
        await db_session.refresh(results[1])
        await db_session.refresh(job)
        assert results[0].status == "passed"
        assert results[1].status == "passed"
        assert execution.status == "succeeded"
        assert job.status == "succeeded"
    finally:
        await db_session.execute(
            delete(AgentEvaluationExecution).where(
                AgentEvaluationExecution.id == execution.id
            )
        )
        await db_session.execute(delete(AgentRun).where(AgentRun.agent_id == agent.id))
        await db_session.execute(delete(AgentEvaluationCase).where(AgentEvaluationCase.id == case.id))
        await db_session.execute(delete(AgentEvaluationSuite).where(AgentEvaluationSuite.id == suite.id))
        await db_session.execute(
            delete(AgentCandidateSnapshot).where(AgentCandidateSnapshot.id == candidate.id)
        )
        await db_session.execute(delete(PlatformJob).where(PlatformJob.id == job.id))
        await db_session.execute(delete(Agent).where(Agent.id == agent.id))
        await db_session.commit()


@pytest.mark.asyncio
async def test_studio_cancellation_calls_runtime_root_and_descendant_helpers(
    db_session, monkeypatch: pytest.MonkeyPatch
):
    """Studio delegates AgentRun cancellation; it never writes run state itself."""
    from src.core.cache import redis_client
    from src.core import redis_client as signals_client
    from src.services.agent_runtime import delegation, run_store, types as runtime_types

    agent = Agent(
        name=f"Evaluation cancellation {uuid4().hex}",
        system_prompt="Be deterministic.",
        created_by="test@example.com",
    )
    db_session.add(agent)
    await db_session.flush()
    suite = AgentEvaluationSuite(
        agent_id=agent.id,
        name=f"cancellation-{uuid4().hex}",
        status="published",
        version=1,
    )
    db_session.add(suite)
    await db_session.flush()
    case = AgentEvaluationCase(suite_id=suite.id, name="cancel", assertions=[])
    db_session.add(case)
    await db_session.flush()
    execution = AgentEvaluationExecution(
        suite_id=suite.id,
        suite_version=suite.version,
        baseline_agent_id=agent.id,
        baseline_snapshot=_synthetic_snapshot(agent.id),
        status="running",
    )
    db_session.add(execution)
    await db_session.flush()
    root = AgentRun(
        id=uuid4(),
        agent_id=agent.id,
        trigger_type="evaluation_synthetic",
        status="running",
        root_run_id=None,
    )
    child = AgentRun(
        id=uuid4(),
        agent_id=agent.id,
        trigger_type="delegation",
        status="queued",
        parent_run_id=root.id,
        root_run_id=root.id,
    )
    root.root_run_id = root.id
    db_session.add(root)
    await db_session.flush()
    db_session.add_all(
        (
            child,
            AgentEvaluationResult(
                execution_id=execution.id,
                case_id=case.id,
                case_version=case.version,
                repetition_index=0,
                baseline_run_id=root.id,
                status="running",
                assertion_results=[],
            ),
        )
    )
    await db_session.commit()

    requested: list = []
    cascaded: list = []
    notified: list = []
    flags: list[str] = []
    cancellation_entered = asyncio.Event()
    cancellation_release = asyncio.Event()

    async def fake_request_cancellation(_session, run_id, *, error):
        requested.append((run_id, error))
        if len(requested) > 1:
            raise runtime_types.InvalidTransitionError("already terminal")
        cancellation_entered.set()
        await cancellation_release.wait()
        return SimpleNamespace(id=run_id, status="cancelling")

    async def fake_cascade(_session_factory, _redis, run_id):
        cascaded.append(run_id)
        return 1

    async def fake_notify(_session_factory, run_id):
        notified.append(run_id)
        return True

    class _RedisClient:
        async def set_agent_run_cancel_flag(self, run_id: str):
            flags.append(run_id)

    @asynccontextmanager
    async def fake_get_redis():
        yield object()

    monkeypatch.setattr(run_store, "request_cancellation", fake_request_cancellation)
    monkeypatch.setattr(delegation, "cascade_cancel", fake_cascade)
    monkeypatch.setattr(delegation, "notify_parent_of_completion", fake_notify)
    monkeypatch.setattr(signals_client, "get_redis_client", lambda: _RedisClient())
    monkeypatch.setattr(redis_client, "get_redis", fake_get_redis)

    cancellation_task = None
    try:
        cancellation_task = asyncio.create_task(cancel_evaluation_execution(execution.id))
        await cancellation_entered.wait()
        await db_session.refresh(execution)
        assert execution.status == "cancelled"
        assert execution.completed_at is None

        # This independent transaction races while runtime cancellation is
        # paused. The persisted cancellation fence must block late admission.
        from src.core.database import get_db_context

        async with get_db_context() as competing_db:
            competing_execution = await competing_db.get(
                AgentEvaluationExecution, execution.id
            )
            competing_result = await competing_db.scalar(
                select(AgentEvaluationResult).where(
                    AgentEvaluationResult.execution_id == execution.id
                )
            )
            assert competing_execution is not None
            assert competing_result is not None
            assert (
                await _dispatch_case_run(
                    competing_db,
                    execution=competing_execution,
                    result=competing_result,
                    item={
                        "case_id": str(case.id),
                        "case_version": case.version,
                        "repetition_index": 0,
                        "side": "baseline",
                    },
                )
                is None
            )

        cancellation_release.set()
        assert await cancellation_task == 1
        assert requested == [(root.id, "Evaluation execution cancelled")]
        assert cascaded == [root.id]
        assert notified == []
        assert flags == [str(root.id)]
        await db_session.refresh(execution)
        await db_session.refresh(root)
        await db_session.refresh(child)
        assert execution.status == "cancelled"
        assert execution.completed_at is not None
        assert root.status == "running"
        assert child.status == "queued"

        # A terminal result root can still own unfinished children. The root
        # transition is an idempotent no-op, but its descendant cascade must
        # still run on this crash-recovery pass.
        root.status = "completed"
        await db_session.commit()
        assert await cancel_evaluation_execution(execution.id) == 0
        assert cascaded == [root.id, root.id]
        await db_session.refresh(execution)
        assert execution.completed_at is not None
    finally:
        cancellation_release.set()
        if cancellation_task is not None and not cancellation_task.done():
            await cancellation_task
        await db_session.execute(
            delete(AgentEvaluationExecution).where(
                AgentEvaluationExecution.id == execution.id
            )
        )
        await db_session.execute(delete(AgentRun).where(AgentRun.id.in_((root.id, child.id))))
        await db_session.execute(delete(AgentEvaluationCase).where(AgentEvaluationCase.id == case.id))
        await db_session.execute(delete(AgentEvaluationSuite).where(AgentEvaluationSuite.id == suite.id))
        await db_session.execute(delete(Agent).where(Agent.id == agent.id))
        await db_session.commit()


@pytest.mark.asyncio
async def test_shared_platform_job_cancel_reconciles_studio_execution(
    db_session, monkeypatch: pytest.MonkeyPatch
):
    """Canonical PlatformJob cancellation reaches Studio without its route."""
    from src.services import platform_jobs

    monkeypatch.setattr(platform_jobs, "publish_platform_job_update", AsyncMock())
    agent = Agent(
        name=f"Shared cancellation {uuid4().hex}",
        system_prompt="Be deterministic.",
        created_by="test@example.com",
    )
    db_session.add(agent)
    await db_session.flush()
    suite = AgentEvaluationSuite(
        agent_id=agent.id,
        name=f"shared-cancel-{uuid4().hex}",
        status="published",
        version=1,
    )
    db_session.add(suite)
    await db_session.flush()
    case = AgentEvaluationCase(suite_id=suite.id, name="pending", assertions=[])
    db_session.add(case)
    await db_session.flush()
    job = PlatformJob(
        job_type="agent.evaluation_suite",
        payload_version=1,
        payload={},
        requested_by_user_id=str(uuid4()),
        requested_by_email="test@example.com",
        requested_by_name="Test",
        title="Shared cancellation test",
        status="waiting",
    )
    db_session.add(job)
    await db_session.flush()
    execution = AgentEvaluationExecution(
        suite_id=suite.id,
        suite_version=suite.version,
        baseline_agent_id=agent.id,
        baseline_snapshot=_synthetic_snapshot(agent.id),
        platform_job_id=job.id,
        status="waiting",
    )
    db_session.add(execution)
    await db_session.flush()
    db_session.add(
        AgentEvaluationResult(
            execution_id=execution.id,
            case_id=case.id,
            case_version=case.version,
            repetition_index=0,
            status="pending",
            assertion_results=[],
        )
    )
    await db_session.commit()

    try:
        await platform_jobs.request_platform_job_cancel(db_session, job)
        assert await reconcile_agent_evaluation_jobs() >= 0
        await db_session.refresh(job)
        await db_session.refresh(execution)
        assert job.status == "cancelled"
        assert execution.status == "cancelled"
        assert execution.completed_at is not None
    finally:
        await db_session.execute(
            delete(AgentEvaluationExecution).where(
                AgentEvaluationExecution.id == execution.id
            )
        )
        await db_session.execute(delete(AgentEvaluationCase).where(AgentEvaluationCase.id == case.id))
        await db_session.execute(delete(AgentEvaluationSuite).where(AgentEvaluationSuite.id == suite.id))
        await db_session.execute(delete(PlatformJob).where(PlatformJob.id == job.id))
        await db_session.execute(delete(Agent).where(Agent.id == agent.id))
        await db_session.commit()


@pytest.mark.asyncio
async def test_dispatcher_does_not_revive_execution_cancelled_after_initial_fence(
    db_session,
):
    """A cancellation between the first probe and report wins durably."""
    from src.core.database import get_db_context

    agent = Agent(
        name=f"Dispatcher cancellation race {uuid4().hex}",
        system_prompt="Be deterministic.",
        created_by="test@example.com",
    )
    db_session.add(agent)
    await db_session.flush()
    suite = AgentEvaluationSuite(
        agent_id=agent.id,
        name=f"dispatcher-race-{uuid4().hex}",
        status="published",
        version=1,
    )
    db_session.add(suite)
    await db_session.flush()
    case = AgentEvaluationCase(
        suite_id=suite.id,
        name="pending dispatch",
        assertions=[],
    )
    db_session.add(case)
    await db_session.flush()
    execution, results, _ = create_execution_objects(
        suite=suite,
        candidate_id=None,
        baseline_agent_id=agent.id,
        cases=[case],
        include_candidate=False,
        repetitions_override=None,
        created_by="test@example.com",
        baseline_snapshot=_synthetic_snapshot(agent.id),
    )
    db_session.add(execution)
    db_session.add_all(results)
    await db_session.commit()

    class _CancellingContext:
        async def report(self, *_args, **_kwargs) -> None:
            async with get_db_context() as competing_db:
                competing_execution = await competing_db.scalar(
                    select(AgentEvaluationExecution)
                    .where(AgentEvaluationExecution.id == execution.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                assert competing_execution is not None
                competing_execution.status = "cancelled"
                competing_execution.completed_at = None

    try:
        with pytest.raises(PlatformJobCancelled):
            await run_agent_evaluation_suite(
                _CancellingContext(),
                AgentEvaluationSuitePayload(execution_id=execution.id),
            )
        await db_session.refresh(execution)
        await db_session.refresh(results[0])
        assert execution.status == "cancelled"
        assert execution.completed_at is not None
        assert results[0].baseline_run_id is None
    finally:
        await db_session.execute(
            delete(AgentEvaluationExecution).where(
                AgentEvaluationExecution.id == execution.id
            )
        )
        await db_session.execute(
            delete(AgentEvaluationCase).where(AgentEvaluationCase.id == case.id)
        )
        await db_session.execute(
            delete(AgentEvaluationSuite).where(AgentEvaluationSuite.id == suite.id)
        )
        await db_session.execute(delete(Agent).where(Agent.id == agent.id))
        await db_session.commit()


def _semantic_assertion(profile_id) -> dict:
    return {
        "type": "llm_judge",
        "params": {
            "rubric": "Helpful answer",
            "prompt_version": "1",
            "threshold": 0.7,
            "judge_snapshot": {
                "profile_id": str(profile_id),
                "provider": "openai",
                "model": "judge-v1",
                "endpoint": None,
                "openai_transport": None,
                "anthropic_prompt_cache_supported": None,
                "default_max_tokens": None,
                "extra_params": {},
            },
        },
    }


async def _semantic_db_fixture(db_session, seed_user, *, child_status="running"):
    from src.jobs.platform.base import PlatformJobContext

    profile_id = uuid4()
    assertion = _semantic_assertion(profile_id)
    suite = AgentEvaluationSuite(
        name=f"synthetic-semantic-{uuid4().hex}",
        status="published",
        version=1,
    )
    db_session.add(suite)
    await db_session.flush()
    case = AgentEvaluationCase(
        suite_id=suite.id,
        name="semantic case",
        assertions=[assertion],
    )
    parent = PlatformJob(
        job_type="agent.evaluation_suite",
        payload_version=1,
        payload={},
        requested_by_user_id=str(seed_user.id),
        requested_by_email=seed_user.email,
        requested_by_name=seed_user.name or "Seed User",
        title="Semantic parent",
        status="waiting",
    )
    db_session.add_all([case, parent])
    await db_session.flush()
    execution = AgentEvaluationExecution(
        suite_id=suite.id,
        suite_version=1,
        platform_job_id=parent.id,
        status="running",
        total_cases=1,
        baseline_snapshot={},
    )
    db_session.add(execution)
    await db_session.flush()
    child_id = uuid4()
    result = AgentEvaluationResult(
        execution_id=execution.id,
        case_id=case.id,
        case_version=case.version,
        repetition_index=0,
        status="running",
        assertion_results=[
            {"type": "terminal_status", "code": "terminal_status", "passed": True, "side": "baseline"},
            {"type": "llm_judge", "code": "llm_judge", "actual": "pending", "side": "baseline"},
        ],
        comparison={
            "semantic_judge_job_id": str(child_id),
            "_semantic_pending": {
                "definitions": [assertion],
                "baseline_evidence": {"output": {"answer": "ok"}},
                "candidate_evidence": None,
            },
        },
    )
    db_session.add(result)
    await db_session.flush()
    lease_token = uuid4()
    child = PlatformJob(
        id=child_id,
        job_type="agent.evaluation_synthetic_semantic",
        payload_version=1,
        payload={"execution_id": str(execution.id), "result_id": str(result.id)},
        dedupe_key=f"synthetic-semantic-result:{result.id}",
        resource_lock_key=f"agent-evaluation-semantic:{execution.id}",
        organization_id=suite.org_id,
        requested_by_user_id=str(seed_user.id),
        requested_by_email=seed_user.email,
        requested_by_name=seed_user.name or "Seed User",
        resource_type="agent_evaluation",
        resource_id=str(execution.id),
        title="Semantic child",
        status=child_status,
        lease_token=lease_token,
        lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    db_session.add(child)
    await db_session.commit()
    context = PlatformJobContext(
        job_id=child.id,
        lease_token=lease_token,
        organization_id=suite.org_id,
        requested_by_user_id=str(seed_user.id),
        requested_by_email=seed_user.email,
        requested_by_name=seed_user.name or "Seed User",
    )
    return SimpleNamespace(
        suite=suite,
        suite_id=suite.id,
        case=case,
        case_id=case.id,
        case_version=case.version,
        parent=parent,
        parent_id=parent.id,
        execution=execution,
        execution_id=execution.id,
        result=result,
        result_id=result.id,
        child=child,
        child_id=child.id,
        context=context,
        profile_id=profile_id,
        tracked_run_ids=[],
    )


async def _semantic_child_job_ids_for_fixture(db_session, fixture) -> list:
    rows = (
        await db_session.execute(
            select(PlatformJob.id).where(
                PlatformJob.job_type == "agent.evaluation_synthetic_semantic",
                PlatformJob.payload["execution_id"].as_string() == str(fixture.execution_id),
                PlatformJob.payload["result_id"].as_string() == str(fixture.result_id),
            )
        )
    ).scalars().all()
    ids = set(rows)
    ids.add(fixture.child_id)
    return list(ids)


async def _cleanup_semantic_fixture(db_session, fixture):
    from src.models.orm.ai_usage import AIUsageAttempt

    await db_session.rollback()
    child_ids = await _semantic_child_job_ids_for_fixture(db_session, fixture)
    if child_ids:
        await db_session.execute(delete(AIUsage).where(AIUsage.platform_job_id.in_(child_ids)))
        await db_session.execute(
            delete(AIUsageAttempt).where(AIUsageAttempt.platform_job_id.in_(child_ids))
        )
    tracked_run_ids = list(getattr(fixture, "tracked_run_ids", []) or [])
    await db_session.execute(
        delete(AgentEvaluationExecution).where(AgentEvaluationExecution.id == fixture.execution_id)
    )
    if tracked_run_ids:
        await db_session.execute(delete(AgentRun).where(AgentRun.id.in_(tracked_run_ids)))
    await db_session.execute(delete(AgentEvaluationCase).where(AgentEvaluationCase.id == fixture.case_id))
    await db_session.execute(delete(AgentEvaluationSuite).where(AgentEvaluationSuite.id == fixture.suite_id))
    await db_session.execute(delete(PlatformJob).where(PlatformJob.id.in_([*child_ids, fixture.parent_id])))
    await db_session.commit()
    remaining_child_jobs = await db_session.scalar(
        select(func.count())
        .select_from(PlatformJob)
        .where(
            PlatformJob.job_type == "agent.evaluation_synthetic_semantic",
            PlatformJob.payload["execution_id"].as_string() == str(fixture.execution_id),
            PlatformJob.payload["result_id"].as_string() == str(fixture.result_id),
        )
    )
    remaining_usage = await db_session.scalar(
        select(func.count()).select_from(AIUsage).where(AIUsage.platform_job_id.in_(child_ids))
    )
    remaining_attempts = await db_session.scalar(
        select(func.count()).select_from(AIUsageAttempt).where(AIUsageAttempt.platform_job_id.in_(child_ids))
    )
    assert remaining_child_jobs == 0
    assert remaining_usage == 0
    assert remaining_attempts == 0


@pytest.mark.asyncio
async def test_synthetic_semantic_handler_rejects_mismatched_linkage(db_session, seed_user):
    from shared.models import SyntheticSemanticJudgePayload
    from src.jobs.platform.agent_synthetic_judge import run_synthetic_semantic_judge
    from src.jobs.platform.base import PlatformJobFailure

    fixture = await _semantic_db_fixture(db_session, seed_user)
    try:
        fixture.result.comparison = {
            **fixture.result.comparison,
            "semantic_judge_job_id": str(uuid4()),
        }
        await db_session.commit()

        with pytest.raises(PlatformJobFailure) as exc:
            await run_synthetic_semantic_judge(
                fixture.context,
                SyntheticSemanticJudgePayload(
                    execution_id=fixture.execution.id,
                    result_id=fixture.result.id,
                ),
            )
        assert exc.value.code == "semantic_job_link_mismatch"
    finally:
        await _cleanup_semantic_fixture(db_session, fixture)


@pytest.mark.asyncio
async def test_synthetic_semantic_terminal_child_missing_verdict_settles_without_replay(
    db_session, seed_user, monkeypatch: pytest.MonkeyPatch
):
    from shared.agent_synthetic_judge import reconcile_synthetic_semantic_child

    fixture = await _semantic_db_fixture(db_session, seed_user, child_status="failed")
    called = False

    async def fail_if_provider_called(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("provider must not be replayed")

    monkeypatch.setattr(
        "shared.agent_synthetic_judge._execute_provider_call",
        fail_if_provider_called,
    )
    try:
        assert await reconcile_synthetic_semantic_child(db_session, fixture.result) is True
        await db_session.commit()
        await db_session.refresh(fixture.result)
        assert called is False
        assert fixture.result.assertion_results[1]["judge_execution_state"] == "ambiguous"
        assert fixture.result.assertion_results[1]["reason"] == "judge_attempt_ambiguous"
    finally:
        await _cleanup_semantic_fixture(db_session, fixture)


@pytest.mark.asyncio
async def test_synthetic_semantic_child_payload_mismatch_not_reused_for_cancel(
    db_session, seed_user
):
    from shared.agent_synthetic_judge import valid_semantic_child_job_id_for_result

    fixture = await _semantic_db_fixture(db_session, seed_user)
    try:
        fixture.child.payload = {"execution_id": str(fixture.execution.id), "result_id": str(uuid4())}
        await db_session.commit()
        await db_session.refresh(fixture.result)

        assert await valid_semantic_child_job_id_for_result(db_session, fixture.result) is None
    finally:
        await _cleanup_semantic_fixture(db_session, fixture)


@pytest.mark.asyncio
async def test_synthetic_semantic_cancellation_after_response_records_usage_without_stale_verdict(
    db_session, seed_user, monkeypatch: pytest.MonkeyPatch
):
    from shared.models import SyntheticSemanticJudgePayload
    from src.core.database import get_db_context
    from src.jobs.platform.agent_synthetic_judge import run_synthetic_semantic_judge
    from src.models.orm.ai_usage import AIUsageAttempt
    from src.services.llm.base import LLMConfig, LLMResponse

    fixture = await _semantic_db_fixture(db_session, seed_user)

    async def fake_get_llm_config(_db, *, profile_id):
        assert profile_id == fixture.profile_id
        return LLMConfig(
            provider="openai",
            model="judge-v1",
            api_key="test",
            endpoint=None,
            openai_transport=None,
            anthropic_prompt_cache_supported=None,
            default_max_tokens=None,
            extra_params={},
        )

    class FakeClient:
        def __init__(self, _config):
            pass

        async def complete(self, *_args, **_kwargs):
            async with get_db_context() as competing_db:
                parent = await competing_db.get(PlatformJob, fixture.parent.id)
                assert parent is not None
                parent.cancel_requested_at = datetime.now(timezone.utc)
                parent.status = "cancel_requested"
                await competing_db.commit()
            return LLMResponse(
                content='{"score": 0.9, "rationale": "ok"}',
                input_tokens=10,
                output_tokens=4,
                provider_cost=Decimal("0.01"),
            )

    monkeypatch.setattr("src.services.llm.factory.get_llm_config", fake_get_llm_config)
    monkeypatch.setattr("src.services.llm.pydantic_client.PydanticAIClient", FakeClient)
    try:
        result = await run_synthetic_semantic_judge(
            fixture.context,
            SyntheticSemanticJudgePayload(
                execution_id=fixture.execution.id,
                result_id=fixture.result.id,
            ),
        )
        assert result["completed"] == 0
        await db_session.refresh(fixture.result)
        semantic = fixture.result.assertion_results[1]
        assert semantic["judge_execution_state"] == "started"
        assert semantic["actual"] == "pending"
        attempt = await db_session.scalar(
            select(AIUsageAttempt).where(AIUsageAttempt.platform_job_id == fixture.child.id)
        )
        assert attempt is not None
        usage = await db_session.scalar(select(AIUsage).where(AIUsage.usage_attempt_id == attempt.id))
        assert usage is not None
        assert usage.input_tokens == 10
        assert usage.output_tokens == 4
    finally:
        await _cleanup_semantic_fixture(db_session, fixture)


@pytest.mark.asyncio
async def test_synthetic_semantic_marker_mismatch_blocks_paid_start(
    db_session, seed_user, monkeypatch: pytest.MonkeyPatch
):
    from shared.models import SyntheticSemanticJudgePayload
    from src.jobs.platform.agent_synthetic_judge import run_synthetic_semantic_judge
    from src.models.orm.ai_usage import AIUsageAttempt

    fixture = await _semantic_db_fixture(db_session, seed_user)
    try:
        outcomes = list(fixture.result.assertion_results)
        outcomes[1] = {
            **outcomes[1],
            "assertion_hash": "stale",
            "request_fingerprint": "stale",
        }
        fixture.result.assertion_results = outcomes
        await db_session.commit()

        async def fail_if_provider_called(*_args, **_kwargs):
            raise AssertionError("provider must not start after frozen marker mismatch")

        monkeypatch.setattr(
            "shared.agent_synthetic_judge._execute_provider_call",
            fail_if_provider_called,
        )
        result = await run_synthetic_semantic_judge(
            fixture.context,
            SyntheticSemanticJudgePayload(
                execution_id=fixture.execution.id,
                result_id=fixture.result.id,
            ),
        )
        assert result["completed"] == 0
        assert (
            await db_session.scalar(
                select(AIUsageAttempt).where(AIUsageAttempt.platform_job_id == fixture.child.id)
            )
            is None
        )
    finally:
        await _cleanup_semantic_fixture(db_session, fixture)


@pytest.mark.asyncio
async def test_synthetic_semantic_handler_requires_existing_child_link(db_session, seed_user):
    from shared.models import SyntheticSemanticJudgePayload
    from src.jobs.platform.agent_synthetic_judge import run_synthetic_semantic_judge
    from src.jobs.platform.base import PlatformJobFailure

    fixture = await _semantic_db_fixture(db_session, seed_user)
    try:
        comparison = dict(fixture.result.comparison)
        comparison.pop("semantic_judge_job_id", None)
        fixture.result.comparison = comparison
        await db_session.commit()

        with pytest.raises(PlatformJobFailure) as exc:
            await run_synthetic_semantic_judge(
                fixture.context,
                SyntheticSemanticJudgePayload(
                    execution_id=fixture.execution.id,
                    result_id=fixture.result.id,
                ),
            )
        assert exc.value.code == "semantic_job_link_mismatch"
        await db_session.refresh(fixture.result)
        assert "semantic_judge_job_id" not in fixture.result.comparison
    finally:
        await _cleanup_semantic_fixture(db_session, fixture)


@pytest.mark.asyncio
async def test_synthetic_semantic_actual_child_payload_mismatch_cancels_fence(
    db_session, seed_user
):
    from shared.models import SyntheticSemanticJudgePayload
    from src.jobs.platform.agent_synthetic_judge import run_synthetic_semantic_judge
    from src.jobs.platform.base import PlatformJobCancelled

    fixture = await _semantic_db_fixture(db_session, seed_user)
    try:
        fixture.child.payload = {
            "execution_id": str(fixture.execution.id),
            "result_id": str(uuid4()),
        }
        await db_session.commit()

        with pytest.raises(PlatformJobCancelled):
            await run_synthetic_semantic_judge(
                fixture.context,
                SyntheticSemanticJudgePayload(
                    execution_id=fixture.execution.id,
                    result_id=fixture.result.id,
                ),
            )
    finally:
        await _cleanup_semantic_fixture(db_session, fixture)


@pytest.mark.asyncio
async def test_synthetic_semantic_terminal_execution_blocks_paid_start(
    db_session, seed_user, monkeypatch: pytest.MonkeyPatch
):
    from shared.models import SyntheticSemanticJudgePayload
    from src.jobs.platform.agent_synthetic_judge import run_synthetic_semantic_judge
    from src.jobs.platform.base import PlatformJobFailure
    from src.models.orm.ai_usage import AIUsageAttempt

    fixture = await _semantic_db_fixture(db_session, seed_user)
    try:
        fixture.execution.status = "succeeded"
        await db_session.commit()

        async def fail_if_provider_called(*_args, **_kwargs):
            raise AssertionError("provider must not start for terminal execution")

        monkeypatch.setattr(
            "shared.agent_synthetic_judge._execute_provider_call",
            fail_if_provider_called,
        )
        with pytest.raises(PlatformJobFailure) as exc:
            await run_synthetic_semantic_judge(
                fixture.context,
                SyntheticSemanticJudgePayload(
                    execution_id=fixture.execution.id,
                    result_id=fixture.result.id,
                ),
            )
        assert exc.value.code == "semantic_job_parent_not_active"
        assert (
            await db_session.scalar(
                select(AIUsageAttempt).where(AIUsageAttempt.platform_job_id == fixture.child.id)
            )
            is None
        )
    finally:
        await _cleanup_semantic_fixture(db_session, fixture)


@pytest.mark.asyncio
async def test_synthetic_semantic_started_row_missing_paid_identity_blocks_verdict(
    db_session, seed_user, monkeypatch: pytest.MonkeyPatch
):
    from shared.models import SyntheticSemanticJudgePayload
    from src.jobs.platform.agent_synthetic_judge import run_synthetic_semantic_judge
    from src.services.llm.base import LLMConfig, LLMResponse

    fixture = await _semantic_db_fixture(db_session, seed_user)

    async def fake_get_llm_config(_db, *, profile_id):
        assert profile_id == fixture.profile_id
        return LLMConfig(
            provider="openai",
            model="judge-v1",
            api_key="test",
            endpoint=None,
            openai_transport=None,
            anthropic_prompt_cache_supported=None,
            default_max_tokens=None,
            extra_params={},
        )

    class FakeClient:
        def __init__(self, _config):
            pass

        async def complete(self, *_args, **_kwargs):
            return LLMResponse(
                content='{"score": 0.9, "rationale": "ok"}',
                input_tokens=5,
                output_tokens=2,
                provider_cost=Decimal("0.01"),
            )

    monkeypatch.setattr("src.services.llm.factory.get_llm_config", fake_get_llm_config)
    monkeypatch.setattr("src.services.llm.pydantic_client.PydanticAIClient", FakeClient)
    try:
        outcomes = list(fixture.result.assertion_results)
        outcomes[1] = {
            **outcomes[1],
            "judge_execution_state": "started",
            "actual": "pending",
        }
        outcomes[1].pop("assertion_index", None)
        outcomes[1].pop("assertion_hash", None)
        outcomes[1].pop("request_fingerprint", None)
        fixture.result.assertion_results = outcomes
        await db_session.commit()

        result = await run_synthetic_semantic_judge(
            fixture.context,
            SyntheticSemanticJudgePayload(
                execution_id=fixture.execution.id,
                result_id=fixture.result.id,
            ),
        )
        assert result["completed"] == 0
        await db_session.refresh(fixture.result)
        semantic = fixture.result.assertion_results[1]
        assert semantic["judge_execution_state"] == "started"
        assert semantic["actual"] == "pending"
    finally:
        await _cleanup_semantic_fixture(db_session, fixture)


@pytest.mark.asyncio
async def test_synthetic_semantic_full_flow_defers_finalization_until_child_completes(
    db_session, seed_user, monkeypatch: pytest.MonkeyPatch
):
    from shared.agent_synthetic_judge import semantic_child_job_id
    from shared.models import SyntheticSemanticJudgePayload
    from src.jobs.platform.agent_synthetic_judge import run_synthetic_semantic_judge
    from src.services import platform_jobs
    from src.services.llm.base import LLMConfig, LLMResponse

    monkeypatch.setattr(platform_jobs, "publish_platform_job_update", AsyncMock())
    fixture = await _semantic_db_fixture(db_session, seed_user, child_status="queued")
    try:
        source_run = AgentRun(trigger_type="evaluation_synthetic", status="completed")
        db_session.add(source_run)
        await db_session.flush()
        fixture.tracked_run_ids.append(source_run.id)
        fixture.execution.case_definitions = [
            {
                "id": str(fixture.case_id),
                "version": fixture.case_version,
                "position": 0,
                "enabled": True,
                "accepted": True,
                "repetitions": 1,
            }
        ]
        comparison = dict(fixture.result.comparison)
        comparison.pop("semantic_judge_job_id", None)
        fixture.result.comparison = comparison
        fixture.result.baseline_run_id = source_run.id
        fixture.result.status = "passed"
        fixture.execution.status = "running"
        await db_session.delete(fixture.child)
        await db_session.commit()

        assert await reconcile_agent_evaluation_jobs() >= 0
        await db_session.refresh(fixture.execution)
        await db_session.refresh(fixture.parent)
        await db_session.refresh(fixture.result)
        assert fixture.execution.status == "running"
        assert fixture.parent.status == "waiting"
        child_id = semantic_child_job_id(fixture.result)
        assert child_id is not None
        child = await db_session.get(PlatformJob, child_id)
        assert child is not None
        assert child.status == "queued"

        lease_token = uuid4()
        child.status = "running"
        child.lease_token = lease_token
        child.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
        await db_session.commit()
        context = fixture.context.__class__(
            job_id=child.id,
            lease_token=lease_token,
            organization_id=fixture.suite.org_id,
            requested_by_user_id=str(seed_user.id),
            requested_by_email=seed_user.email,
            requested_by_name=seed_user.name or "Seed User",
        )

        async def fake_get_llm_config(_db, *, profile_id):
            assert profile_id == fixture.profile_id
            return LLMConfig(
                provider="openai",
                model="judge-v1",
                api_key="test",
                endpoint=None,
                openai_transport=None,
                anthropic_prompt_cache_supported=None,
                default_max_tokens=None,
                extra_params={},
            )

        class FakeClient:
            def __init__(self, _config):
                pass

            async def complete(self, *_args, **_kwargs):
                return LLMResponse(
                    content='{"score": 0.9, "rationale": "ok"}',
                    input_tokens=5,
                    output_tokens=2,
                    provider_cost=Decimal("0.01"),
                )

        monkeypatch.setattr("src.services.llm.factory.get_llm_config", fake_get_llm_config)
        monkeypatch.setattr("src.services.llm.pydantic_client.PydanticAIClient", FakeClient)
        handled = await run_synthetic_semantic_judge(
            context,
            SyntheticSemanticJudgePayload(
                execution_id=fixture.execution.id,
                result_id=fixture.result.id,
            ),
        )
        assert handled["completed"] == 1
        child.status = "succeeded"
        child.result = handled
        await db_session.commit()

        assert await reconcile_agent_evaluation_jobs() >= 0
        await db_session.refresh(fixture.execution)
        await db_session.refresh(fixture.parent)
        assert fixture.execution.status == "succeeded"
        assert fixture.parent.status == "succeeded"
    finally:
        await _cleanup_semantic_fixture(db_session, fixture)


@pytest.mark.asyncio
async def test_synthetic_semantic_reconcile_enqueue_is_idempotent_for_duplicate_delivery(
    db_session, seed_user, monkeypatch: pytest.MonkeyPatch
):
    from shared.agent_synthetic_judge import semantic_child_job_id
    from src.services import platform_jobs

    monkeypatch.setattr(platform_jobs, "publish_platform_job_update", AsyncMock())

    async def no_follow_up_dispatch(*_args, **_kwargs):
        return 0

    monkeypatch.setattr(
        "src.jobs.platform.agent_evaluation._dispatch_follow_up_batch",
        no_follow_up_dispatch,
    )
    fixture = await _semantic_db_fixture(db_session, seed_user, child_status="queued")
    try:
        comparison = dict(fixture.result.comparison)
        comparison.pop("semantic_judge_job_id", None)
        fixture.result.comparison = comparison
        fixture.result.status = "passed"
        fixture.execution.status = "running"
        await db_session.delete(fixture.child)
        await db_session.commit()

        assert await reconcile_agent_evaluation_jobs() >= 0
        await db_session.refresh(fixture.result)
        first_child_id = semantic_child_job_id(fixture.result)
        assert first_child_id is not None
        assert await reconcile_agent_evaluation_jobs() >= 0
        await db_session.refresh(fixture.result)
        assert semantic_child_job_id(fixture.result) == first_child_id
        child_count = await db_session.scalar(
            select(func.count())
            .select_from(PlatformJob)
            .where(
                PlatformJob.job_type == "agent.evaluation_synthetic_semantic",
                PlatformJob.payload["execution_id"].as_string() == str(fixture.execution.id),
                PlatformJob.payload["result_id"].as_string() == str(fixture.result.id),
            )
        )
        assert child_count == 1
    finally:
        await _cleanup_semantic_fixture(db_session, fixture)


@pytest.mark.asyncio
async def test_synthetic_semantic_scheduler_dispatches_remaining_cases_while_child_active(
    db_session, seed_user, monkeypatch: pytest.MonkeyPatch
):
    from shared.agent_synthetic_judge import semantic_child_job_id
    from src.services import platform_jobs

    monkeypatch.setattr(platform_jobs, "publish_platform_job_update", AsyncMock())
    dispatched: list[dict] = []

    async def fake_dispatch(db, *, execution, result, item):
        dispatched.append(item)
        run = AgentRun(trigger_type="evaluation_synthetic", status="queued")
        db.add(run)
        await db.flush()
        fixture.tracked_run_ids.append(run.id)
        result.baseline_run_id = run.id
        if result.status == "pending":
            result.status = "running"
        await db.commit()
        return result.baseline_run_id

    monkeypatch.setattr(
        "src.jobs.platform.agent_evaluation._dispatch_case_run",
        fake_dispatch,
    )
    fixture = await _semantic_db_fixture(db_session, seed_user, child_status="queued")
    try:
        second_case = AgentEvaluationCase(
            suite_id=fixture.suite.id,
            name="remaining semantic case",
            assertions=[],
        )
        db_session.add(second_case)
        await db_session.flush()
        second_case_id = second_case.id
        second_result = AgentEvaluationResult(
            execution_id=fixture.execution.id,
            case_id=second_case.id,
            case_version=second_case.version,
            repetition_index=0,
            status="pending",
            assertion_results=[],
        )
        db_session.add(second_result)
        await db_session.flush()
        second_result_id = second_result.id
        fixture.execution.case_definitions = [
            {
                "id": str(fixture.case.id),
                "version": fixture.case.version,
                "position": 0,
                "enabled": True,
                "accepted": True,
                "repetitions": 1,
            },
            {
                "id": str(second_case.id),
                "version": second_case.version,
                "position": 1,
                "enabled": True,
                "accepted": True,
                "repetitions": 1,
            },
        ]
        source_run = AgentRun(trigger_type="evaluation_synthetic", status="completed")
        db_session.add(source_run)
        await db_session.flush()
        fixture.tracked_run_ids.append(source_run.id)
        comparison = dict(fixture.result.comparison)
        comparison.pop("semantic_judge_job_id", None)
        fixture.result.comparison = comparison
        fixture.result.baseline_run_id = source_run.id
        fixture.result.status = "passed"
        await db_session.delete(fixture.child)
        await db_session.commit()

        assert await reconcile_agent_evaluation_jobs() >= 0
        await db_session.refresh(fixture.result)
        await db_session.refresh(second_result)
        assert semantic_child_job_id(fixture.result) is not None
        assert second_result.baseline_run_id is not None
        assert str(second_case.id) in {item["case_id"] for item in dispatched}
    finally:
        await db_session.rollback()
        if "second_result_id" in locals():
            await db_session.execute(
                delete(AgentEvaluationResult).where(AgentEvaluationResult.id == second_result_id)
            )
        if "second_case_id" in locals():
            await db_session.execute(delete(AgentEvaluationCase).where(AgentEvaluationCase.id == second_case_id))
        await _cleanup_semantic_fixture(db_session, fixture)
