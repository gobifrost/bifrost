"""Durable tool execution: planned/running/completed boundaries + reclaim."""

from __future__ import annotations

from decimal import Decimal
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
from pydantic_ai.usage import RunUsage
from sqlalchemy import delete, select

from src.core.secret_string import REDACTED, SecretString
from src.models.orm.agent_runs import (
    AgentRun,
    AgentRunCheckpoint,
    AgentRunJournalEntry,
    AgentRunStep,
    AgentToolInvocation,
)
from src.services.agent_runtime import run_store
from src.services.agent_runtime import AgentRunBudget
from src.services.agent_runtime import types as rt
from src.services.agent_runtime.checkpoint_codec import encode_messages
from src.services.agent_runtime.resume import prepare_resume
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

    @asyncio_mark
    async def test_secret_wrappers_are_redacted_before_invocation_persistence(
        self, async_session_factory
    ):
        run_id = await _create_run(async_session_factory)
        secret = SecretString("durable-secret-value")
        try:
            token = await _claim(async_session_factory, run_id)
            async with async_session_factory() as session:
                plan_kwargs = _plan_kwargs(
                    run_id,
                    token,
                    call_id="secret-call",
                )
                plan_kwargs["arguments"] = {"token": secret}
                await plan_invocation(
                    session,
                    **plan_kwargs,
                    secrets={secret.get_secret_value()},
                )
                await complete_invocation(
                    session,
                    run_id=run_id,
                    lease_token=token,
                    provider_tool_call_id="secret-call",
                    result={"token": secret},
                    secrets={secret.get_secret_value()},
                )
            async with async_session_factory() as session:
                invocation = await get_invocation(
                    session, run_id, "secret-call"
                )
                assert invocation is not None
                assert invocation.arguments == {"token": REDACTED}
                assert invocation.result == {"token": REDACTED}
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
                await run_store.commit_checkpoint(
                    session,
                    run_id,
                    token,
                    encode_messages(
                        [
                            ModelResponse(
                                parts=[
                                    ToolCallPart(
                                        tool_name="create_ticket",
                                        args={},
                                        tool_call_id="call-1",
                                    )
                                ]
                            )
                        ]
                    ),
                    journal_kind=rt.JOURNAL_MODEL_RESPONSE,
                )
            plan, _, unrecoverable = await prepare_resume(
                async_session_factory, run_id, token
            )
            assert unrecoverable is None
            assert plan is not None
            replay_request = plan.history[-1]
            assert isinstance(replay_request, ModelRequest)
            replay_part = replay_request.parts[0]
            assert isinstance(replay_part, ToolReturnPart)
            assert replay_part.content == '{"ticket": 7}'

            executor = AutonomousAgentExecutor(async_session_factory)
            executor._durable_lease_token = token
            assert await executor._execute_tool_durable(
                "create_ticket",
                {},
                "call-1",
                run_id=str(run_id),
                agent=_mock_agent(),
            ) == {"ticket": 7}
        finally:
            await _cleanup(async_session_factory, run_id)

    @asyncio_mark
    async def test_reconciliation_redacts_explicit_secret_output(
        self, async_session_factory
    ):
        registry = ReconciliationRegistry()

        async def _hook(_data):
            return ReconcileDecision.recovered(
                {"token": SecretString("reconciliation-secret")}
            )

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
            await reclaim_in_flight(
                async_session_factory, run_id, token, registry=registry
            )
            async with async_session_factory() as session:
                invocation = await get_invocation(session, run_id, "call-1")
                assert invocation is not None
                assert invocation.result == {"token": REDACTED}
                assert "reconciliation-secret" not in str(invocation.result)
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
    # These runs are global; usage rows must not reference a nonexistent org.
    agent.organization_id = None
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
                assert {
                    journal.data["tool_call_id"]
                    for journal in journals
                    if journal.kind in {"tool_call", "tool_result"}
                } == {"call-1"}
                model_boundaries = [
                    journal
                    for journal in journals
                    if journal.kind == "model_response"
                ]
                assert model_boundaries
                assert all("usage" in (journal.data or {}) for journal in model_boundaries)
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
    async def test_checkpoint_usage_evidence_recovers_once_after_crash_window(
        self, async_session_factory
    ):
        """A committed boundary can recreate AIUsage without another model call."""
        from src.models.orm.ai_usage import AIUsage

        run_id = await _create_run(async_session_factory)
        try:
            token = await _claim(async_session_factory, run_id)
            async with async_session_factory() as session:
                await run_store.commit_checkpoint(
                    session,
                    run_id,
                    token,
                    {"format_version": 1, "messages": []},
                    journal_kind="model_response",
                    journal_data={
                        "usage": {
                            "provider": "openai",
                            "model": "test-model",
                            "input_tokens": 13,
                            "output_tokens": 8,
                            "cache_read_tokens": 2,
                            "cache_write_tokens": 1,
                            "provider_cost": "0.00001000",
                            "duration_ms": 21,
                        }
                    },
                )

            redis_mock = AsyncMock()
            redis_mock.get.return_value = None
            executor = AutonomousAgentExecutor(
                async_session_factory, redis_client=redis_mock
            )
            recorded: list[dict] = []

            async def record_usage(**kwargs):
                recorded.append(kwargs)

            with patch(
                "src.services.ai_usage_service.record_ai_usage",
                new_callable=AsyncMock,
                side_effect=record_usage,
            ):
                await executor.recover_durable_usage(
                    agent=_mock_agent(), run_id=str(run_id)
                )
                # A later reclaim sees the projection and does not count it
                # again, even though it is replaying the same checkpoint.
                await executor.recover_durable_usage(
                    agent=_mock_agent(), run_id=str(run_id)
                )

            assert len(recorded) == 1
            assert recorded[0]["sequence"] == 1
            assert recorded[0]["input_tokens"] == 13
            assert recorded[0]["provider_cost"] == Decimal("0.00001000")
            async with async_session_factory() as session:
                usage_rows = (
                    await session.execute(
                        select(AIUsage).where(AIUsage.agent_run_id == run_id)
                    )
                ).scalars().all()
            assert len(usage_rows) == 1
            assert usage_rows[0].sequence == 1
            assert usage_rows[0].provider_cost == Decimal("0.00001000")
        finally:
            async with async_session_factory() as session:
                await session.execute(
                    delete(AIUsage).where(AIUsage.agent_run_id == run_id)
                )
                await session.commit()
            await _cleanup(async_session_factory, run_id)

    @asyncio_mark
    async def test_reclaimed_contract_correction_intent_never_calls_provider_twice(
        self, async_session_factory
    ):
        """A crash after correction intent spends the bounded retry once."""
        run_id = await _create_run(async_session_factory)
        try:
            token = await _claim(async_session_factory, run_id)
            async with async_session_factory() as session:
                await run_store.commit_checkpoint(
                    session,
                    run_id,
                    token,
                    {"format_version": 1, "messages": []},
                    journal_kind="validation",
                    journal_data={"correction_intent": True, "valid": False},
                )
            executor = AutonomousAgentExecutor(async_session_factory)
            executor._durable_lease_token = token
            runtime = MagicMock()
            runtime.run = AsyncMock()
            output, valid, errors, status = await executor._enforce_output_contract(
                "not json",
                {"type": "object"},
                runtime=runtime,
                usage=RunUsage(),
                budget=AgentRunBudget(max_requests=5, max_total_tokens=100),
                usage_start_requests=0,
                usage_start_tokens=0,
                run_id=str(run_id),
            )
            assert output == {"text": "not json"}
            assert valid is False
            assert errors
            assert status == "contract_failed"
            runtime.run.assert_not_awaited()
        finally:
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
