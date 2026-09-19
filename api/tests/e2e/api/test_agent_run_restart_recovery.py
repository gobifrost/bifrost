"""Restart recovery: kill workers at committed boundaries, resume the same run.

Failure-injection coverage for the approved resume table. Each test runs
one attempt that dies like a SIGKILL (``KeyboardInterrupt`` escapes the
model/tool loop the way a dead container does), expires the lease, then
resumes the SAME AgentRun with a fresh worker and asserts the exact
expected recovery.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
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
from src.services.agent_runtime import run_store
from src.services.agent_runtime.resume import prepare_resume
from src.services.execution.autonomous_agent_executor import AutonomousAgentExecutor
from src.services.llm.base import LLMConfig, ToolDefinition

pytestmark = pytest.mark.asyncio


class WorkerDied(asyncio.CancelledError):
    """Simulated SIGKILL: escapes ``except Exception`` handlers without cleanup.

    ``CancelledError`` inherits ``BaseException``, so it propagates through
    the model/tool loop the way a dead container does — while remaining
    catchable by ``pytest.raises`` (unlike ``KeyboardInterrupt``, which tears
    down the pytest event loop itself).
    """


def _mock_agent():
    agent = MagicMock()
    agent.id = uuid4()
    agent.name = "Recovery Agent"
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
    agent.organization_id = None
    agent.is_active = True
    return agent


def _history_aware_model():
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
                        tool_name="test_tool", args={}, tool_call_id="call-1"
                    )
                ],
                model_name="fake",
            )
        return ModelResponse(parts=[TextPart(content="done")], model_name="fake")

    return FunctionModel(_fn)


def _redis():
    redis_mock = AsyncMock()
    redis_mock.get.return_value = None
    return redis_mock


def _executor_patches(dispatch):
    tool_id = uuid4()
    return (
        patch(
            "src.services.execution.autonomous_agent_executor.get_llm_config",
            new_callable=AsyncMock,
            return_value=LLMConfig(
                provider="openai", model="test-model", api_key="k"
            ),
        ),
        patch(
            "src.services.execution.autonomous_agent_executor.create_agent_model",
            return_value=_history_aware_model(),
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
                {"test_tool": tool_id},
            ),
        ),
        patch.object(AutonomousAgentExecutor, "_execute_tool", dispatch),
    )


async def _create_row(async_session_factory) -> UUID:
    async with async_session_factory() as session:
        run = AgentRun(trigger_type="test", status="queued")
        session.add(run)
        await session.commit()
        return run.id


async def _claim(async_session_factory, run_id: UUID) -> str:
    async with async_session_factory() as session:
        claimed = await run_store.claim_run(session, run_id, "worker")
        assert claimed.lease_token is not None
        return claimed.lease_token


async def _expire_lease(async_session_factory, run_id: UUID):
    async with async_session_factory() as session:
        run = await session.get(AgentRun, run_id)
        assert run is not None
        run.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await session.commit()


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


async def _load(async_session_factory, run_id: UUID) -> AgentRun:
    async with async_session_factory() as session:
        result = await session.execute(
            select(AgentRun).where(AgentRun.id == run_id)
        )
        return result.scalar_one()


async def _run_attempt(
    async_session_factory, run_id, token, dispatch, agent, resume_history=None
):
    """One worker attempt; resumes from decoded history when provided."""
    executor = AutonomousAgentExecutor(
        async_session_factory, redis_client=_redis()
    )
    get_llm, create_model, resolve_tools, execute_tool = _executor_patches(
        dispatch
    )
    with get_llm, create_model, resolve_tools, execute_tool:
        if resume_history is None:
            plan, _, unrecoverable = await prepare_resume(
                async_session_factory, run_id, token
            )
            assert unrecoverable is None
            assert plan is not None
            resume_history = plan.history or None
        return await executor.run(
            agent,
            input_data={"task": "go"},
            run_id=str(run_id),
            lease_token=token,
            resume_history=resume_history,
        )


class TestRestartRecovery:
    async def test_kill_before_model_request_restarts_cleanly(
        self, async_session_factory
    ):
        """No committed boundary: the next worker starts the same run over."""
        run_id = await _create_row(async_session_factory)
        try:
            token = await _claim(async_session_factory, run_id)
            dispatch = AsyncMock(return_value="tool-output")

            def _die_immediately(messages, info):
                raise WorkerDied()

            with pytest.raises(WorkerDied):
                executor = AutonomousAgentExecutor(
                    async_session_factory, redis_client=_redis()
                )
                get_llm, _, resolve_tools, execute_tool = _executor_patches(
                    dispatch
                )
                with (
                    get_llm,
                    patch(
                        "src.services.execution.autonomous_agent_executor.create_agent_model",
                        return_value=FunctionModel(_die_immediately),
                    ),
                    resolve_tools,
                    execute_tool,
                ):
                    await executor.run(
                        _mock_agent(),
                        input_data={"task": "go"},
                        run_id=str(run_id),
                        lease_token=token,
                    )

            await _expire_lease(async_session_factory, run_id)
            token2 = await _claim(async_session_factory, run_id)
            result = await _run_attempt(
                async_session_factory, run_id, token2, dispatch, _mock_agent()
            )
            assert result["status"] == "completed"
            assert result["output"] == "done"
            assert dispatch.await_count == 1
            reloaded = await _load(async_session_factory, run_id)
            assert reloaded.id == run_id
            assert reloaded.attempt == 2
        finally:
            await _cleanup(async_session_factory, run_id)

    async def test_kill_after_model_response_executes_planned_once(
        self, async_session_factory
    ):
        """Model response committed, tool never planned: execute exactly once."""
        run_id = await _create_row(async_session_factory)
        try:
            token = await _claim(async_session_factory, run_id)
            dispatch = AsyncMock(return_value="tool-output")
            with pytest.raises(WorkerDied):
                executor = AutonomousAgentExecutor(
                    async_session_factory, redis_client=_redis()
                )
                patches = _executor_patches(dispatch)
                with (
                    patches[0],
                    patches[1],
                    patches[2],
                    patches[3],
                    patch(
                        "src.services.agent_runtime.tool_invocations.plan_invocation",
                        side_effect=WorkerDied(),
                    ),
                ):
                    await executor.run(
                        _mock_agent(),
                        input_data={"task": "go"},
                        run_id=str(run_id),
                        lease_token=token,
                    )

            await _expire_lease(async_session_factory, run_id)
            token2 = await _claim(async_session_factory, run_id)
            result = await _run_attempt(
                async_session_factory, run_id, token2, dispatch, _mock_agent()
            )
            assert result["status"] == "completed"
            assert result["output"] == "done"
            assert dispatch.await_count == 1
            reloaded = await _load(async_session_factory, run_id)
            assert reloaded.attempt == 2
        finally:
            await _cleanup(async_session_factory, run_id)

    async def test_kill_after_planned_tool_executes_without_replan(
        self, async_session_factory
    ):
        """Tool planned but not started: the next worker executes it once."""
        run_id = await _create_row(async_session_factory)
        try:
            token = await _claim(async_session_factory, run_id)
            dispatch = AsyncMock(return_value="tool-output")
            with pytest.raises(WorkerDied):
                executor = AutonomousAgentExecutor(
                    async_session_factory, redis_client=_redis()
                )
                patches = _executor_patches(dispatch)
                with (
                    patches[0],
                    patches[1],
                    patches[2],
                    patches[3],
                    patch(
                        "src.services.agent_runtime.tool_invocations.mark_running",
                        side_effect=WorkerDied(),
                    ),
                ):
                    await executor.run(
                        _mock_agent(),
                        input_data={"task": "go"},
                        run_id=str(run_id),
                        lease_token=token,
                    )

            async with async_session_factory() as session:
                planned = (
                    await session.execute(
                        select(AgentToolInvocation).where(
                            AgentToolInvocation.run_id == run_id
                        )
                    )
                ).scalars().all()
                assert len(planned) == 1
                assert planned[0].state == "planned"

            await _expire_lease(async_session_factory, run_id)
            token2 = await _claim(async_session_factory, run_id)
            result = await _run_attempt(
                async_session_factory, run_id, token2, dispatch, _mock_agent()
            )
            assert result["status"] == "completed"
            assert dispatch.await_count == 1
        finally:
            await _cleanup(async_session_factory, run_id)

    async def test_kill_after_completed_tool_replays_stored_result(
        self, async_session_factory
    ):
        """Tool completed, next model request never issued: no re-execution."""
        run_id = await _create_row(async_session_factory)
        try:
            token = await _claim(async_session_factory, run_id)
            dispatch = AsyncMock(return_value="tool-output")

            real_checkpoint = AutonomousAgentExecutor._durable_checkpoint

            async def _die_after_tool_result(executor_self, **kwargs):
                if kwargs.get("journal_kind") == "tool_result":
                    raise WorkerDied()
                return await real_checkpoint(executor_self, **kwargs)

            with pytest.raises(WorkerDied):
                executor = AutonomousAgentExecutor(
                    async_session_factory, redis_client=_redis()
                )
                patches = _executor_patches(dispatch)
                with (
                    patches[0],
                    patches[1],
                    patches[2],
                    patches[3],
                    patch.object(
                        AutonomousAgentExecutor,
                        "_durable_checkpoint",
                        autospec=True,
                        side_effect=_die_after_tool_result,
                    ),
                ):
                    await executor.run(
                        _mock_agent(),
                        input_data={"task": "go"},
                        run_id=str(run_id),
                        lease_token=token,
                    )

            async with async_session_factory() as session:
                invocation = (
                    await session.execute(
                        select(AgentToolInvocation).where(
                            AgentToolInvocation.run_id == run_id
                        )
                    )
                ).scalar_one()
                assert invocation.state == "completed"

            await _expire_lease(async_session_factory, run_id)
            token2 = await _claim(async_session_factory, run_id)
            result = await _run_attempt(
                async_session_factory, run_id, token2, dispatch, _mock_agent()
            )
            assert result["status"] == "completed"
            assert result["output"] == "done"
            # Attempt 1 executed the tool; attempt 2 replayed the stored result.
            assert dispatch.await_count == 1
        finally:
            await _cleanup(async_session_factory, run_id)

    async def test_kill_during_external_write_goes_recovery_required(
        self, async_session_factory
    ):
        """Tool running at lease expiry with no hook: fail closed, no replay."""
        run_id = await _create_row(async_session_factory)
        try:
            token = await _claim(async_session_factory, run_id)
            dispatch = AsyncMock(side_effect=WorkerDied())
            with pytest.raises(WorkerDied):
                executor = AutonomousAgentExecutor(
                    async_session_factory, redis_client=_redis()
                )
                patches = _executor_patches(dispatch)
                with patches[0], patches[1], patches[2], patches[3]:
                    await executor.run(
                        _mock_agent(),
                        input_data={"task": "go"},
                        run_id=str(run_id),
                        lease_token=token,
                    )

            assert dispatch.await_count == 1
            await _expire_lease(async_session_factory, run_id)
            token2 = await _claim(async_session_factory, run_id)
            plan, _, unrecoverable = await prepare_resume(
                async_session_factory, run_id, token2
            )
            assert plan is None
            assert unrecoverable is not None
            assert "test_tool" in unrecoverable
            async with async_session_factory() as session:
                recovered = await run_store.mark_recovery_required(
                    session,
                    run_id,
                    token2,
                    reason=unrecoverable,
                    evidence={"attempt": 2},
                )
                assert recovered.status == "recovery_required"
            # No automatic replay happened after the uncertain boundary.
            assert dispatch.await_count == 1
        finally:
            await _cleanup(async_session_factory, run_id)
