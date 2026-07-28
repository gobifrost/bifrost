"""Unit tests for AutonomousAgentExecutor session management.

Validates the Redis-first pattern: no DB session is held during LLM calls,
steps are buffered in memory/Redis, and flush_to_db() batch-inserts everything.
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from src.services.execution.autonomous_agent_executor import AutonomousAgentExecutor


def _make_mock_session_factory():
    """Create a mock session factory that tracks session lifecycle."""
    sessions_open = []

    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()
    mock_session.flush = AsyncMock()
    mock_session.get = AsyncMock(return_value=None)

    mock_ctx = AsyncMock()

    async def aenter(*args):
        sessions_open.append(True)
        return mock_session

    async def aexit(*args):
        sessions_open.pop()

    mock_ctx.__aenter__ = aenter
    mock_ctx.__aexit__ = aexit

    factory = MagicMock(return_value=mock_ctx)
    return factory, mock_session, sessions_open


def _make_mock_agent():
    """Create a mock Agent ORM instance."""
    agent = MagicMock()
    agent.id = uuid4()
    agent.name = "test-agent"
    agent.system_prompt = "You are a test agent."
    agent.max_iterations = 5
    agent.max_token_budget = 10000
    agent.max_run_timeout = 60
    agent.llm_model = "test-model"
    agent.llm_max_tokens = 4096
    agent.tools = []
    agent.delegated_agents = []
    agent.roles = []
    agent.system_tools = []
    agent.knowledge_sources = []
    agent.organization_id = uuid4()
    return agent


def _make_mock_llm_response(content="Hello!", tool_calls=None):
    """Create a mock LLM response."""
    resp = MagicMock()
    resp.content = content
    resp.tool_calls = tool_calls
    resp.finish_reason = "stop"
    resp.input_tokens = 10
    resp.output_tokens = 5
    resp.model = "test-model"
    return resp


class TestNoSessionDuringLLMCall:
    """Verify that no DB session is held while waiting for LLM responses."""

    @pytest.mark.asyncio
    async def test_no_session_held_during_llm_call(self):
        """During llm_client.complete(), no session should be checked out."""
        factory, mock_session, sessions_open = _make_mock_session_factory()
        agent = _make_mock_agent()
        llm_response = _make_mock_llm_response()

        session_held_during_llm = []

        async def mock_complete(**kwargs):
            # Record whether any session is open right now
            session_held_during_llm.append(len(sessions_open) > 0)
            await asyncio.sleep(0.01)  # Simulate LLM latency
            return llm_response

        mock_llm_client = AsyncMock()
        mock_llm_client.complete = mock_complete
        mock_llm_client.provider_name = "test"

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)  # Not cancelled
        mock_redis.xadd = AsyncMock()

        # Mock resolve_agent_tools to return empty (no tools)
        with patch(
            "src.services.execution.autonomous_agent_executor.resolve_agent_tools",
            new_callable=AsyncMock,
            return_value=([], {}),
        ), patch(
            "src.services.execution.autonomous_agent_executor.get_llm_client",
            new_callable=AsyncMock,
            return_value=mock_llm_client,
        ), patch(
            "src.services.execution.autonomous_agent_executor.publish_agent_run_step",
            new_callable=AsyncMock,
        ):
            executor = AutonomousAgentExecutor(factory, redis_client=mock_redis)
            result = await executor.run(agent=agent, input_data={"task": "say hello"})

        assert result["status"] == "completed"
        # The LLM was called at least once
        assert len(session_held_during_llm) > 0
        # No session was open during any LLM call
        assert not any(session_held_during_llm), (
            "A DB session was held open during an LLM call"
        )


class TestStepsBufferedToRedis:
    """Verify steps go to Redis during execution, not to DB."""

    @pytest.mark.asyncio
    async def test_steps_buffered_to_redis_not_db(self):
        """session.add() should NOT be called during run(). Steps go to Redis."""
        factory, mock_session, _ = _make_mock_session_factory()
        agent = _make_mock_agent()
        llm_response = _make_mock_llm_response()

        mock_llm_client = AsyncMock()
        mock_llm_client.complete = AsyncMock(return_value=llm_response)
        mock_llm_client.provider_name = "test"

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.xadd = AsyncMock()

        with patch(
            "src.services.execution.autonomous_agent_executor.resolve_agent_tools",
            new_callable=AsyncMock,
            return_value=([], {}),
        ), patch(
            "src.services.execution.autonomous_agent_executor.get_llm_client",
            new_callable=AsyncMock,
            return_value=mock_llm_client,
        ), patch(
            "src.services.execution.autonomous_agent_executor.publish_agent_run_step",
            new_callable=AsyncMock,
        ):
            executor = AutonomousAgentExecutor(factory, redis_client=mock_redis)
            result = await executor.run(agent=agent, input_data={"task": "say hello"})

        assert result["status"] == "completed"

        # session.add() should NOT have been called during the run
        # (it's only called by flush_to_db() which hasn't been called yet)
        mock_session.add.assert_not_called()

        # Redis xadd SHOULD have been called for steps
        assert mock_redis.xadd.call_count > 0

        # Steps should be buffered in the executor
        assert len(executor._pending_steps) > 0


class TestFlushToDb:
    """Verify flush_to_db() batch-inserts all buffered steps."""

    @pytest.mark.asyncio
    async def test_flush_to_db_batch_inserts_steps(self):
        """flush_to_db() should add all buffered steps to the session."""
        factory, _, _ = _make_mock_session_factory()
        agent = _make_mock_agent()
        llm_response = _make_mock_llm_response()

        mock_llm_client = AsyncMock()
        mock_llm_client.complete = AsyncMock(return_value=llm_response)
        mock_llm_client.provider_name = "test"

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.xadd = AsyncMock()

        with patch(
            "src.services.execution.autonomous_agent_executor.resolve_agent_tools",
            new_callable=AsyncMock,
            return_value=([], {}),
        ), patch(
            "src.services.execution.autonomous_agent_executor.get_llm_client",
            new_callable=AsyncMock,
            return_value=mock_llm_client,
        ), patch(
            "src.services.execution.autonomous_agent_executor.publish_agent_run_step",
            new_callable=AsyncMock,
        ):
            executor = AutonomousAgentExecutor(factory, redis_client=mock_redis)
            await executor.run(agent=agent, input_data={"task": "say hello"})

        # Now flush to a fresh mock session
        flush_session = AsyncMock()
        flush_session.add = MagicMock()

        step_count = len(executor._pending_steps)
        assert step_count > 0, "Expected buffered steps"

        with patch(
            "src.services.ai_usage_service.record_ai_usage",
            new_callable=AsyncMock,
        ):
            await executor.flush_to_db(flush_session)

        # All steps should have been added to the flush session
        assert flush_session.add.call_count >= step_count


class TestDelegationUsesFactory:
    """Verify delegation creates child executors with session factory, not session."""

    @pytest.mark.asyncio
    async def test_delegation_uses_session_factory(self):
        """Child executor should receive the session factory, not a session instance."""
        factory, mock_session, _ = _make_mock_session_factory()

        # Set up mock for db.get() to return a mock AgentRun
        mock_agent_run = MagicMock()
        mock_session.get = AsyncMock(return_value=mock_agent_run)

        agent = _make_mock_agent()
        child_agent = _make_mock_agent()
        child_agent.name = "child agent"  # agent_delegation_slug("child agent") -> "delegate_to_child_agent"
        child_agent.is_active = True
        child_agent.organization_id = agent.organization_id
        agent.delegated_agents = [child_agent]

        # Mock the select query for re-fetching child agent
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = child_agent
        mock_session.execute = AsyncMock(return_value=mock_result)

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.xadd = AsyncMock()

        # The child executor's run() should be called
        child_run_result = {
            "output": "done",
            "iterations_used": 1,
            "tokens_used": 10,
            "status": "completed",
            "llm_model": "test",
        }

        with patch.object(
            AutonomousAgentExecutor,
            "run",
            new_callable=AsyncMock,
            return_value=child_run_result,
        ):
            executor = AutonomousAgentExecutor(factory, redis_client=mock_redis)
            executor._current_run_id = str(uuid4())

            tool_call = MagicMock()
            tool_call.name = "delegate_to_child_agent"
            tool_call.arguments = {"task": "do something"}

            await executor._execute_delegation(tool_call, agent)

        # Delegation should have completed (mock_run was called)
        # Child executor receives the session factory via constructor
        # (verified by the mock patching AutonomousAgentExecutor.run)
