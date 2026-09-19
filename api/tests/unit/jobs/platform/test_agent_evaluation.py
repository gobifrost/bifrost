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
from sqlalchemy import delete, select

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
