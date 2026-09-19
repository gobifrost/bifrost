"""Synthetic evaluations run through the durable agent engine.

- Synthetic rows admit with ``evaluation_synthetic`` mode, frozen candidate
  snapshots, and bounded correlation; production enqueue rejects the mode.
- Non-engine tools dispatch through the case simulator; engine-owned
  delegation/timer primitives keep durable semantics; children stay synthetic.
- Checkpoints, journal, output contracts, and completion events behave like
  production; timers resolve deterministically under a configurable cap.
- Zero real tool executors are ever called.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import FunctionModel
from sqlalchemy import delete, select

from src.models.orm.agent_runs import (
    AgentRun,
    AgentRunCheckpoint,
    AgentRunJournalEntry,
    AgentRunStep,
    AgentToolInvocation,
)
from src.models.orm.agents import Agent, AgentDelegation
from src.services.agent_evaluations.candidates import build_candidate_snapshot
from src.services.agent_evaluations.runner import (
    EVALUATION_SYNTHETIC_MODE,
    SYNTHETIC_TIMER_FIRED_TEXT,
    EngineToolSignal,
    SimulatorToolRouter,
    SyntheticRunnerError,
    admit_synthetic_run,
    assert_no_production_trigger,
    attach_synthetic,
    build_synthetic_correlation,
    collect_assertion_evidence,
    is_engine_tool,
    is_synthetic_correlation,
    seed_side_fixtures,
    validate_synthetic_timer,
)
from src.services.agent_evaluations.simulator import Simulator
from src.services.agent_runtime import run_store
from src.services.agent_runtime.execution_snapshot import (
    is_synthetic_snapshot,
    synthetic_snapshot_from_candidate,
)
from src.services.execution.autonomous_agent_executor import AutonomousAgentExecutor
from src.services.execution.agent_run_service import enqueue_agent_run
from src.services.llm.base import LLMConfig, ToolCallRequest, ToolDefinition

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _mock_rabbitmq_publish():
    """Keep durable queue nudges in-process.

    Synthetic runs (including delegated children) publish a RabbitMQ nudge
    via ``src.jobs.rabbitmq.publish_message``. Every run in this file
    executes in-process against the simulator, so a real publish would
    disturb the shared stack worker and pin the process-wide publisher pools
    to one test's function-scoped loop (see
    ``RabbitMQConnection.reset_pools``). Swallow the nudges.
    """
    with patch("src.jobs.rabbitmq.publish_message", new_callable=AsyncMock) as mock:
        yield mock


# -----------------------------------------------------------------------------
# Fixtures and scripted models
# -----------------------------------------------------------------------------


def _fixture(**over) -> dict:
    base = {
        "version": 1,
        "entities": {"ticket": {"ticket-0001": {"id": "ticket-0001", "title": "VPN"}}},
        "allowed_tools": ["get_ticket"],
        "rules": [],
    }
    base.update(over)
    return base


def _schemas() -> dict:
    return {
        "get_ticket": {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
        }
    }


def _candidate_snapshot(**over) -> dict:
    from src.models.contracts.agent_evaluations import CandidateOverlay

    agent_id = uuid4()
    snapshot = build_candidate_snapshot(
        base_agent_id=agent_id,
        base_agent_name="support",
        base_agent_updated_at="2026-09-18T00:00:00+00:00",
        base_system_prompt="Be helpful.",
        base_model={"profile_id": None, "llm_max_tokens": 1000},
        base_tools=[{"name": "get_ticket", "target_id": str(uuid4())}],
        base_delegated_agents=[],
        base_system_tools=[],
        base_limits={
            "max_iterations": 10,
            "max_token_budget": 5000,
            "max_run_timeout": 600,
        },
        overlays=CandidateOverlay(),
    )
    snapshot.update(over)
    return snapshot


def _correlation(**over) -> dict:
    corr = build_synthetic_correlation(
        suite_id=uuid4(), case_id=uuid4(), execution_id=uuid4(), side="baseline"
    )
    corr.update(over)
    return corr


def _tool_calling_model(final_text: str = "done"):
    def _fn(messages, info):
        answered = any(
            isinstance(message, ModelRequest)
            and any(isinstance(part, ToolReturnPart) for part in message.parts)
            for message in messages
        )
        if not answered:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="get_ticket",
                        args={"id": "ticket-0001"},
                        tool_call_id="call-1",
                    )
                ],
                model_name="fake",
            )
        return ModelResponse(parts=[TextPart(content=final_text)], model_name="fake")

    return FunctionModel(_fn)


def _redis():
    redis_mock = AsyncMock()
    redis_mock.get.return_value = None
    return redis_mock


def _mock_agent():
    agent = MagicMock()
    agent.id = uuid4()
    agent.name = "Support Agent"
    agent.system_prompt = "Be helpful."
    agent.tools = []
    agent.system_tools = []
    agent.knowledge_sources = []
    agent.delegated_agents = []
    agent.roles = []
    agent.max_iterations = 5
    agent.max_token_budget = 5000
    agent.max_run_timeout = 600
    agent.llm_profile_id = None
    agent.llm_max_tokens = None
    agent.organization_id = None
    agent.is_active = True
    return agent


def _executor_patches(model):
    tool_id = uuid4()
    return (
        patch(
            "src.services.execution.autonomous_agent_executor.get_llm_config",
            new_callable=AsyncMock,
            return_value=LLMConfig(provider="openai", model="test-model", api_key="k"),
        ),
        patch(
            "src.services.execution.autonomous_agent_executor.create_agent_model",
            return_value=model,
        ),
        patch(
            "src.services.execution.autonomous_agent_executor.resolve_agent_tools",
            new_callable=AsyncMock,
            return_value=(
                [
                    ToolDefinition(
                        name="get_ticket",
                        description="Fetch a ticket",
                        parameters=_schemas()["get_ticket"],
                    )
                ],
                {"get_ticket": tool_id},
            ),
        ),
    )


async def _cleanup(async_session_factory, run_id: UUID):
    async with async_session_factory() as session:
        for model in (
            AgentRunJournalEntry,
            AgentRunCheckpoint,
            AgentRunStep,
            AgentToolInvocation,
        ):
            await session.execute(delete(model).where(model.run_id == run_id))
        await session.execute(delete(AgentRun).where(AgentRun.id == run_id))
        await session.commit()


# -----------------------------------------------------------------------------
# Mode, correlation, and fixture guards (no DB)
# -----------------------------------------------------------------------------


def test_engine_tool_classification():
    assert is_engine_tool("delegate_agents") is True
    assert is_engine_tool("sleep_until") is True
    assert is_engine_tool("delegate_to_billing") is True
    assert is_engine_tool("get_ticket") is False


def test_correlation_is_bounded_and_synthetic():
    corr = _correlation()
    assert is_synthetic_correlation(corr) is True
    assert is_synthetic_correlation({"evaluation_mode": "production"}) is False
    assert is_synthetic_correlation(None) is False
    assert_no_production_trigger(EVALUATION_SYNTHETIC_MODE, corr)


def test_production_trigger_keys_rejected():
    with pytest.raises(SyntheticRunnerError, match="production trigger keys"):
        assert_no_production_trigger(
            EVALUATION_SYNTHETIC_MODE, _correlation(ticket_id="t-1")
        )
    with pytest.raises(SyntheticRunnerError, match="trigger_type"):
        assert_no_production_trigger("event", _correlation())


def test_side_fixtures_are_independent_frozen_copies():
    baseline, candidate = seed_side_fixtures(_fixture())
    assert baseline == candidate
    assert baseline is not candidate
    baseline["entities"]["ticket"]["ticket-0001"]["title"] = "mutated"
    assert candidate["entities"]["ticket"]["ticket-0001"]["title"] == "VPN"


async def test_production_enqueue_rejects_synthetic_trigger():
    with pytest.raises(ValueError, match="reserved for the Evaluation service"):
        await enqueue_agent_run(
            agent_id=None, trigger_type=EVALUATION_SYNTHETIC_MODE, input_data={}
        )


def test_synthetic_snapshot_entry_point_requires_candidate_marker():
    snapshot = synthetic_snapshot_from_candidate(_candidate_snapshot())
    assert is_synthetic_snapshot(snapshot) is True
    assert snapshot["format_version"] == 1
    assert [t["name"] for t in snapshot["tools"]] == ["get_ticket"]
    with pytest.raises(Exception, match="evaluation-marked"):
        synthetic_snapshot_from_candidate({"format_version": 1})


# -----------------------------------------------------------------------------
# Router behavior (no DB)
# -----------------------------------------------------------------------------


async def test_router_sends_non_engine_tools_to_simulator():
    router = SimulatorToolRouter(Simulator(_fixture(), tool_schemas=_schemas()))
    result = await router.route("get_ticket", {"id": "ticket-0001"}, "call-1")
    assert "VPN" in result
    assert router.real_calls == 0
    assert router.journal[0]["tool_name"] == "get_ticket"


async def test_router_raises_engine_signal_for_owned_tools():
    router = SimulatorToolRouter(Simulator(_fixture(), tool_schemas=_schemas()))
    with pytest.raises(EngineToolSignal):
        await router.route("sleep_until", {}, "call-9")
    with pytest.raises(EngineToolSignal):
        await router.route("delegate_to_billing", {}, "call-9")
    assert router.journal == []


async def test_router_proof_hook_fires_when_bypassed():
    async def _boom(name, args, tool_call_id):
        raise AssertionError("real executor must never run")

    router = SimulatorToolRouter(
        Simulator(_fixture(), tool_schemas=_schemas()), real_executor=_boom
    )
    with pytest.raises(AssertionError, match="never run"):
        await router.route_real("get_ticket", {}, "call-1")
    assert router.real_calls == 1


async def test_timer_cap_rejects_long_sleeps():
    from datetime import datetime, timezone

    now = datetime(2026, 9, 18, tzinfo=timezone.utc)
    wake = validate_synthetic_timer(
        {"seconds": 60, "reason": "poll again soon"}, max_seconds=300, now=now
    )
    assert wake > now
    with pytest.raises(SyntheticRunnerError, match="synthetic maximum"):
        validate_synthetic_timer(
            {"seconds": 3600, "reason": "long poll"}, max_seconds=300, now=now
        )


async def test_executor_sleep_resolves_deterministically_without_waiting():
    executor = AutonomousAgentExecutor(MagicMock(), redis_client=_redis())
    router = SimulatorToolRouter(Simulator(_fixture(), tool_schemas=_schemas()))
    attach_synthetic(executor, router, timer_max_seconds=300)
    result = await executor._execute_sleep(
        ToolCallRequest(
            id="t1", name="sleep_until",
            arguments={"seconds": 60, "reason": "poll again soon"},
        ),
        _mock_agent(),
    )
    assert result == SYNTHETIC_TIMER_FIRED_TEXT
    with pytest.raises(Exception, match="synthetic maximum"):
        await executor._execute_sleep(
            ToolCallRequest(
                id="t2", name="sleep_until",
                arguments={"seconds": 900, "reason": "long poll"},
            ),
            _mock_agent(),
        )


# -----------------------------------------------------------------------------
# Durable engine integration (DB-backed)
# -----------------------------------------------------------------------------


async def test_synthetic_run_uses_simulator_checkpoints_journal_and_contract(
    async_session_factory,
):
    run_id = uuid4()
    candidate = _candidate_snapshot()
    correlation = _correlation()
    router = SimulatorToolRouter(
        Simulator(_fixture(), tool_schemas=_schemas()), correlation=correlation
    )
    async with async_session_factory() as session:
        await admit_synthetic_run(
            session,
            candidate_snapshot=candidate,
            case_input={"task": "summarize ticket-0001"},
            output_schema=None,
            correlation=correlation,
            run_id=run_id,
        )
        await session.commit()
    try:
        async with async_session_factory() as session:
            claimed = await run_store.claim_run(session, run_id, "worker")
            token = claimed.lease_token
            assert token is not None
        executor = AutonomousAgentExecutor(
            async_session_factory, redis_client=_redis()
        )
        attach_synthetic(executor, router)
        get_llm, create_model, resolve_tools = _executor_patches(
            _tool_calling_model()
        )
        with get_llm, create_model, resolve_tools:
            from src.services.agent_runtime.resume import prepare_resume

            plan, _, unrecoverable = await prepare_resume(
                async_session_factory, run_id, token
            )
            assert unrecoverable is None
            result = await executor.run(
                _mock_agent(),
                input_data={"task": "summarize ticket-0001"},
                run_id=str(run_id),
                lease_token=token,
                resume_history=(plan.history if plan else None),
                correlation=correlation,
            )
        assert result["status"] == "completed"
        assert router.real_calls == 0
        assert len(router.journal) >= 1
        async with async_session_factory() as session:
            reloaded = await session.get(AgentRun, run_id)
            assert reloaded is not None
            assert reloaded.trigger_type == EVALUATION_SYNTHETIC_MODE
            assert reloaded.correlation["evaluation_side"] == "baseline"
            assert is_synthetic_snapshot(reloaded.execution_snapshot) is True
            checkpoints = (
                await session.execute(
                    select(AgentRunCheckpoint).where(
                        AgentRunCheckpoint.run_id == run_id
                    )
                )
            ).scalars().all()
            assert len(checkpoints) >= 1
            kinds = {
                entry.kind
                for entry in (
                    await session.execute(
                        select(AgentRunJournalEntry).where(
                            AgentRunJournalEntry.run_id == run_id
                        )
                    )
                ).scalars().all()
            }
            assert "tool_call" in kinds
            assert "tool_result" in kinds
    finally:
        await _cleanup(async_session_factory, run_id)


async def test_committed_synthetic_tool_result_never_reexecutes(
    async_session_factory,
):
    run_id = uuid4()
    calls = 0
    simulator = Simulator(_fixture(), tool_schemas=_schemas())
    original_call = simulator.call

    def _counting_call(name, args):
        nonlocal calls
        calls += 1
        return original_call(name, args)

    simulator.call = _counting_call  # type: ignore[method-assign]
    router = SimulatorToolRouter(simulator)
    async with async_session_factory() as session:
        await admit_synthetic_run(
            session,
            candidate_snapshot=_candidate_snapshot(),
            case_input={"task": "x"},
            output_schema=None,
            correlation=_correlation(),
            run_id=run_id,
        )
        await session.commit()
        claimed = await run_store.claim_run(session, run_id, "worker")
        token = claimed.lease_token
        assert token is not None
    try:
        executor = AutonomousAgentExecutor(
            async_session_factory, redis_client=_redis()
        )
        attach_synthetic(executor, router)
        executor._durable_lease_token = token
        first = await executor._execute_tool_durable(
            "get_ticket", {"id": "ticket-0001"}, "call-1",
            run_id=str(run_id), agent=_mock_agent(),
        )
        second = await executor._execute_tool_durable(
            "get_ticket", {"id": "ticket-0001"}, "call-1",
            run_id=str(run_id), agent=_mock_agent(),
        )
        assert first == second
        assert calls == 1
    finally:
        await _cleanup(async_session_factory, run_id)


async def test_output_contract_failure_marks_run_contract_failed(
    async_session_factory,
):
    run_id = uuid4()
    router = SimulatorToolRouter(Simulator(_fixture(), tool_schemas=_schemas()))
    async with async_session_factory() as session:
        await admit_synthetic_run(
            session,
            candidate_snapshot=_candidate_snapshot(),
            case_input={"task": "x"},
            output_schema={
                "type": "object",
                "properties": {"answer": {"type": "string"}},
                "required": ["answer"],
            },
            correlation=_correlation(),
            run_id=run_id,
        )
        await session.commit()
        claimed = await run_store.claim_run(session, run_id, "worker")
        token = claimed.lease_token
        assert token is not None
    try:
        executor = AutonomousAgentExecutor(
            async_session_factory, redis_client=_redis()
        )
        attach_synthetic(executor, router)
        get_llm, create_model, resolve_tools = _executor_patches(
            _tool_calling_model(final_text="not json at all, forever")
        )
        with get_llm, create_model, resolve_tools:
            from src.services.agent_runtime.resume import prepare_resume

            plan, _, _ = await prepare_resume(
                async_session_factory, run_id, token
            )
            result = await executor.run(
                _mock_agent(),
                input_data={"task": "x"},
                output_schema={
                    "type": "object",
                    "properties": {"answer": {"type": "string"}},
                    "required": ["answer"],
                },
                run_id=str(run_id),
                lease_token=token,
                resume_history=(plan.history if plan else None),
            )
        assert result["status"] == "contract_failed"
        assert router.real_calls == 0
    finally:
        await _cleanup(async_session_factory, run_id)


async def test_delegation_child_of_synthetic_parent_stays_synthetic(
    async_session_factory, db_session
):
    parent = Agent(
        name=f"Synthetic Parent {uuid4().hex[:8]}",
        system_prompt="Delegate.",
        created_by="test",
    )
    child = Agent(
        name=f"Synthetic Child {uuid4().hex[:8]}",
        system_prompt="Answer.",
        created_by="test",
    )
    db_session.add_all([parent, child])
    await db_session.commit()
    db_session.add(
        AgentDelegation(parent_agent_id=parent.id, child_agent_id=child.id)
    )
    await db_session.commit()

    @asynccontextmanager
    async def _factory():
        yield db_session

    try:
        loaded = (
            await db_session.execute(
                select(Agent)
                .options()
                .where(Agent.id == parent.id)
            )
        ).scalar_one()
        await db_session.refresh(loaded, ["tools", "delegated_agents"])
        router = SimulatorToolRouter(
            Simulator(_fixture(), tool_schemas=_schemas()),
            correlation=_correlation(),
        )
        executor = AutonomousAgentExecutor(_factory, redis_client=_redis())
        attach_synthetic(executor, router)
        seen_routers = []

        async def _fake_sub_run(inner_self, *args, **kwargs):
            seen_routers.append(inner_self._synthetic_router)
            return {
                "output": "child answer",
                "status": "completed",
                "iterations_used": 1,
                "tokens_used": 10,
                "llm_model": "fake",
            }

        with (
            patch.object(
                AutonomousAgentExecutor, "run", autospec=True, side_effect=_fake_sub_run
            ),
            patch(
                "src.services.execution.run_summarizer.enqueue_summarize",
                new_callable=AsyncMock,
            ),
        ):
            outcome = await executor.run_delegation(
                parent_agent=loaded,
                tool_call=ToolCallRequest(
                    id="tc1",
                    name=f"delegate_to_{child.name.lower().replace(' ', '_')}",
                    arguments={"task": "Answer briefly"},
                ),
                caller={"user_id": None, "email": None, "name": None},
            )
        assert outcome.status == "completed"
        assert seen_routers and seen_routers[0] is router
        persisted = await db_session.get(AgentRun, outcome.child_run_id)
        assert persisted is not None
        assert persisted.trigger_type == EVALUATION_SYNTHETIC_MODE
        assert is_synthetic_correlation(persisted.correlation) is True
    finally:
        if "outcome" in locals():
            await _cleanup(async_session_factory, outcome.child_run_id)
        await db_session.execute(
            delete(AgentDelegation).where(
                AgentDelegation.parent_agent_id == parent.id
            )
        )
        await db_session.execute(delete(Agent).where(Agent.id == parent.id))
        await db_session.execute(delete(Agent).where(Agent.id == child.id))
        await db_session.commit()


def test_collect_evidence_projects_router_journal():
    router = SimulatorToolRouter(Simulator(_fixture(), tool_schemas=_schemas()))
    router.journal.append(
        {
            "sequence": 0,
            "kind": "tool_result",
            "tool_name": "get_ticket",
            "tool_call_id": "call-1",
            "arguments": {"id": "ticket-0001"},
            "result": {"id": "ticket-0001"},
        }
    )
    evidence = collect_assertion_evidence(
        terminal_status="completed",
        output={"answer": "ok"},
        router=router,
        simulator_state={"ticket": {}},
        usage={"iterations": 2, "tokens": 10},
    )
    assert evidence["tool_calls"][0]["name"] == "get_ticket"
    assert evidence["real_tool_executions"] == 0
    assert evidence["usage"]["tokens"] == 10


def test_candidate_only_tools_do_not_touch_base_agent():
    from src.models.contracts.agent_evaluations import CandidateOverlay

    tool_id = uuid4()
    candidate = build_candidate_snapshot(
        base_agent_id=uuid4(),
        base_agent_name="support",
        base_agent_updated_at="2026-09-18T00:00:00+00:00",
        base_system_prompt="Be helpful.",
        base_model={"profile_id": None, "llm_max_tokens": 1000},
        base_tools=[],
        base_delegated_agents=[],
        base_system_tools=[],
        base_limits={"max_iterations": 5},
        overlays=CandidateOverlay(tool_ids=[tool_id]),
        overlay_tool_definitions=[
            {"name": "escalate_ticket", "target_id": str(tool_id)}
        ],
    )
    snapshot = synthetic_snapshot_from_candidate(candidate)
    assert [t["name"] for t in snapshot["tools"]] == ["escalate_ticket"]
