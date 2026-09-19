"""Durable tool execution: planned/running/completed boundaries + reclaim."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from sqlalchemy import delete, select

from src.models.orm.agent_runs import (
    AgentRun,
    AgentRunCheckpoint,
    AgentRunJournalEntry,
    AgentRunStep,
    AgentToolInvocation,
)
from src.services.agent_runtime import run_store
from src.services.agent_runtime import types as rt
from src.services.agent_runtime.tool_invocations import (
    RECONCILIATION_REGISTRY,
    ReconcileDecision,
    ReconciliationRegistry,
    complete_invocation,
    durable_operation_id,
    fail_invocation,
    get_invocation,
    mark_running,
    plan_invocation,
    reclaim_in_flight,
)
from src.services.execution.autonomous_agent_executor import AutonomousAgentExecutor
from src.services.llm.base import LLMConfig, ToolDefinition

asyncio_mark = pytest.mark.asyncio


async def _create_run(async_session_factory) -> UUID:
    async with async_session_factory() as session:
        run = AgentRun(trigger_type="test", status="queued")
        session.add(run)
        await session.commit()
        return run.id


async def _claim(async_session_factory, run_id: UUID) -> str:
    async with async_session_factory() as session:
        claimed = await run_store.claim_run(session, run_id, "worker-a")
        assert claimed.lease_token is not None
        return claimed.lease_token


async def _cleanup(async_session_factory, run_id: UUID):
    async with async_session_factory() as session:
        for model in (AgentRunJournalEntry, AgentRunCheckpoint, AgentRunStep):
            await session.execute(delete(model).where(model.run_id == run_id))
        await session.execute(
            delete(AgentToolInvocation).where(
                AgentToolInvocation.run_id == run_id
            )
        )
        await session.execute(delete(AgentRun).where(AgentRun.id == run_id))
        await session.commit()


def _plan_kwargs(run_id, token, call_id="call-1", tool="lookup"):
    return {
        "run_id": run_id,
        "lease_token": token,
        "provider_tool_call_id": call_id,
        "tool_name": tool,
        "tool_version": "1",
        "tool_schema": {"type": "object"},
        "arguments": {"q": "x"},
    }


class TestInvocationLifecycle:
    @asyncio_mark
    async def test_operation_id_deterministic_and_replan_stable(
        self, async_session_factory
    ):
        run_id = await _create_run(async_session_factory)
        try:
            token = await _claim(async_session_factory, run_id)
            assert durable_operation_id(str(run_id), "call-1") == (
                durable_operation_id(str(run_id), "call-1")
            )
            async with async_session_factory() as session:
                first = await plan_invocation(
                    session, **_plan_kwargs(run_id, token)
                )
                assert first.state == "planned"
                assert first.operation_id == durable_operation_id(
                    str(run_id), "call-1"
                )
                second = await plan_invocation(
                    session, **_plan_kwargs(run_id, token)
                )
                assert second.operation_id == first.operation_id
                running = await mark_running(
                    session,
                    run_id=run_id,
                    lease_token=token,
                    provider_tool_call_id="call-1",
                )
                assert running.state == "running"
                done = await complete_invocation(
                    session,
                    run_id=run_id,
                    lease_token=token,
                    provider_tool_call_id="call-1",
                    result="ok",
                )
                assert done.state == "completed"
                assert done.result == {"text": "ok"}
        finally:
            await _cleanup(async_session_factory, run_id)

    @asyncio_mark
    async def test_stale_token_cannot_plan(self, async_session_factory):
        run_id = await _create_run(async_session_factory)
        try:
            await _claim(async_session_factory, run_id)
            async with async_session_factory() as session:
                with pytest.raises(rt.LeaseMismatchError):
                    await plan_invocation(
                        session, **_plan_kwargs(run_id, "stale-token")
                    )
        finally:
            await _cleanup(async_session_factory, run_id)


class TestReclaim:
    @asyncio_mark
    async def test_safe_retry_tool_resets_to_planned(self, async_session_factory):
        run_id = await _create_run(async_session_factory)
        try:
            token = await _claim(async_session_factory, run_id)
            async with async_session_factory() as session:
                await plan_invocation(
                    session, **_plan_kwargs(run_id, token, tool="search_knowledge")
                )
                await mark_running(
                    session,
                    run_id=run_id,
                    lease_token=token,
                    provider_tool_call_id="call-1",
                )
            report = await reclaim_in_flight(
                async_session_factory, run_id, token
            )
            assert report.unrecoverable_reason is None
            assert len(report.to_execute) == 1
            async with async_session_factory() as session:
                invocation = await get_invocation(session, run_id, "call-1")
                assert invocation is not None
                assert invocation.state == "planned"
        finally:
            await _cleanup(async_session_factory, run_id)

    @asyncio_mark
    async def test_side_effect_without_hook_fails_closed(
        self, async_session_factory
    ):
        run_id = await _create_run(async_session_factory)
        try:
            token = await _claim(async_session_factory, run_id)
            async with async_session_factory() as session:
                await plan_invocation(
                    session, **_plan_kwargs(run_id, token, tool="send_email")
                )
                await mark_running(
                    session,
                    run_id=run_id,
                    lease_token=token,
                    provider_tool_call_id="call-1",
                )
            report = await reclaim_in_flight(
                async_session_factory, run_id, token
            )
            assert report.to_execute == []
            assert report.recovered == []
            assert "send_email" in (report.unrecoverable_reason or "")
            async with async_session_factory() as session:
                invocation = await get_invocation(session, run_id, "call-1")
                assert invocation is not None
                assert invocation.state == "uncertain"
        finally:
            await _cleanup(async_session_factory, run_id)

    @asyncio_mark
    async def test_registered_hook_recovers_result(self, async_session_factory):
        registry = ReconciliationRegistry()

        async def _hook(_data):
            return ReconcileDecision.recovered({"ticket": 7})

        registry.register("create_ticket", _hook)
        run_id = await _create_run(async_session_factory)
        try:
            token = await _claim(async_session_factory, run_id)
            async with async_session_factory() as session:
                await plan_invocation(
                    session, **_plan_kwargs(run_id, token, tool="create_ticket")
                )
                await mark_running(
                    session,
                    run_id=run_id,
                    lease_token=token,
                    provider_tool_call_id="call-1",
                )
            report = await reclaim_in_flight(
                async_session_factory, run_id, token, registry=registry
            )
            assert report.unrecoverable_reason is None
            assert len(report.recovered) == 1
            async with async_session_factory() as session:
                invocation = await get_invocation(session, run_id, "call-1")
                assert invocation is not None
                assert invocation.state == "completed"
                assert invocation.result == {"ticket": 7}
        finally:
            await _cleanup(async_session_factory, run_id)

    @asyncio_mark
    async def test_global_registry_starts_empty(self):
        assert RECONCILIATION_REGISTRY.hook_for("send_email") is None


def _mock_agent():
    agent = MagicMock()
    agent.id = uuid4()
    agent.name = "Durable Agent"
    agent.system_prompt = "Do things."
    agent.tools = []
    agent.system_tools = []
    agent.knowledge_sources = []
    agent.delegated_agents = []
    agent.roles = []
    agent.max_iterations = 10
    agent.max_token_budget = 50000
    agent.llm_profile_id = None
    agent.llm_max_tokens = None
    agent.organization_id = uuid4()
    agent.is_active = True
    return agent


def _function_model():
    calls = {"count": 0}

    def _fn(messages, info):
        calls["count"] += 1
        if calls["count"] == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="test_tool",
                        args={},
                        tool_call_id="call-1",
                    )
                ],
                model_name="fake",
            )
        return ModelResponse(
            parts=[TextPart(content="done")], model_name="fake"
        )

    return FunctionModel(_fn)


class TestDurableExecutorBoundaries:
    @asyncio_mark
    async def test_model_and_tool_boundaries_commit_durably(
        self, async_session_factory
    ):
        run_id = await _create_run(async_session_factory)
        try:
            token = await _claim(async_session_factory, run_id)
            redis_mock = AsyncMock()
            redis_mock.get.return_value = None
            executor = AutonomousAgentExecutor(
                async_session_factory, redis_client=redis_mock
            )
            recorded_usage: list = []
            with (
                patch(
                    "src.services.execution.autonomous_agent_executor.get_llm_config",
                    new_callable=AsyncMock,
                    return_value=LLMConfig(
                        provider="openai", model="test-model", api_key="k"
                    ),
                ),
                patch(
                    "src.services.execution.autonomous_agent_executor.create_agent_model",
                    return_value=_function_model(),
                ),
                patch(
                    "src.services.execution.autonomous_agent_executor.resolve_agent_tools",
                    new_callable=AsyncMock,
                    return_value=(
                        [
                            ToolDefinition(
                                name="test_tool",
                                description="Test",
                                parameters={"type": "object", "properties": {}},
                            )
                        ],
                        {"test_tool": uuid4()},
                    ),
                ),
                patch.object(
                    AutonomousAgentExecutor,
                    "_execute_tool",
                    new_callable=AsyncMock,
                    return_value="tool-output",
                ),
                patch(
                    "src.services.ai_usage_service.record_ai_usage",
                    new_callable=AsyncMock,
                    side_effect=lambda **kwargs: recorded_usage.append(kwargs),
                ),
            ):
                result = await executor.run(
                    _mock_agent(),
                    input_data={"task": "go"},
                    run_id=str(run_id),
                    lease_token=token,
                )
            assert result["status"] == "completed"
            assert result["output"] == "done"
            # Durable mode: committed boundaries bypass the end-of-run
            # buffer; only pre-boundary request notices remain buffered.
            assert {s["type"] for s in executor._pending_steps} == {"llm_request"}
            assert executor._pending_ai_usage == []

            async with async_session_factory() as session:
                checkpoints = (
                    await session.execute(
                        select(AgentRunCheckpoint).where(
                            AgentRunCheckpoint.run_id == run_id
                        )
                    )
                ).scalars().all()
                assert len(checkpoints) >= 3
                journals = (
                    await session.execute(
                        select(AgentRunJournalEntry).where(
                            AgentRunJournalEntry.run_id == run_id
                        )
                    )
                ).scalars().all()
                kinds = {j.kind for j in journals}
                assert {"model_response", "tool_call", "tool_result"} <= kinds
                steps = (
                    await session.execute(
                        select(AgentRunStep)
                        .where(AgentRunStep.run_id == run_id)
                        .order_by(AgentRunStep.step_number)
                    )
                ).scalars().all()
                step_types = [s.type for s in steps]
                assert "llm_response" in step_types
                assert "tool_call" in step_types
                assert "tool_result" in step_types
                # Observable ordering preserved across projected steps.
                assert [s.step_number for s in steps] == sorted(
                    s.step_number for s in steps
                )
                invocation = await get_invocation(session, run_id, "call-1")
                assert invocation is not None
                assert invocation.state == "completed"
                assert invocation.result == {"text": "tool-output"}
            # One usage row per committed model boundary, distinct sequences.
            assert len(recorded_usage) == 2
            sequences = [call["sequence"] for call in recorded_usage]
            assert len(set(sequences)) == 2
        finally:
            await _cleanup(async_session_factory, run_id)

    @asyncio_mark
    async def test_usage_write_is_idempotent_on_recommit(
        self, async_session_factory
    ):
        from src.models.orm.ai_usage import AIUsage

        run_id = await _create_run(async_session_factory)
        try:
            token = await _claim(async_session_factory, run_id)
            assert token
            # A previously committed boundary already has its usage row.
            async with async_session_factory() as session:
                session.add(
                    AIUsage(
                        provider="openai",
                        model="test-model",
                        input_tokens=1,
                        output_tokens=1,
                        agent_run_id=run_id,
                        sequence=9,
                    )
                )
                await session.commit()
            redis_mock = AsyncMock()
            redis_mock.get.return_value = None
            executor = AutonomousAgentExecutor(
                async_session_factory, redis_client=redis_mock
            )
            executor._last_checkpoint_sequence = 9
            recorded: list = []
            with patch(
                "src.services.ai_usage_service.record_ai_usage",
                new_callable=AsyncMock,
                side_effect=lambda **kwargs: recorded.append(kwargs),
            ):
                await executor._persist_usage_durable(
                    agent=_mock_agent(),
                    run_id=str(run_id),
                    provider="openai",
                    model="test-model",
                    input_tokens=1,
                    output_tokens=1,
                )
            assert recorded == []
        finally:
            async with async_session_factory() as session:
                await session.execute(
                    delete(AIUsage).where(AIUsage.agent_run_id == run_id)
                )
                await session.commit()
            await _cleanup(async_session_factory, run_id)

    @asyncio_mark
    async def test_committed_result_reused_without_reexecution(
        self, async_session_factory
    ):
        run_id = await _create_run(async_session_factory)
        try:
            token = await _claim(async_session_factory, run_id)
            async with async_session_factory() as session:
                await plan_invocation(
                    session, **_plan_kwargs(run_id, token, tool="lookup")
                )
                await fail_invocation(
                    session,
                    run_id=run_id,
                    lease_token=token,
                    provider_tool_call_id="call-1",
                    error="boom",
                )
                await complete_invocation(
                    session,
                    run_id=run_id,
                    lease_token=token,
                    provider_tool_call_id="call-1",
                    result="stored-output",
                )
            executor = AutonomousAgentExecutor(
                async_session_factory, redis_client=AsyncMock()
            )
            executor._durable_lease_token = token
            with patch.object(
                AutonomousAgentExecutor,
                "_execute_tool",
                new_callable=AsyncMock,
                side_effect=AssertionError("must not re-execute"),
            ):
                reused = await executor._execute_tool_durable(
                    "lookup",
                    {"q": "x"},
                    "call-1",
                    run_id=str(run_id),
                    agent=_mock_agent(),
                )
            assert reused == "stored-output"
        finally:
            await _cleanup(async_session_factory, run_id)
