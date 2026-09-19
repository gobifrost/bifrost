"""Durable timers: sleep suspends, due promotion wakes once, same run resumes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
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
from sqlalchemy.orm import selectinload

from src.models.orm.agent_runs import AgentRun, AgentRunJournalEntry
from src.models.orm.agents import Agent
from src.services.agent_runtime import run_store
from src.services.agent_runtime.resume import prepare_resume
from src.services.agent_runtime.timers import (
    MAX_TIMER_SECONDS,
    TIMER_FIRED_TEXT,
    TimerError,
    collect_timer_results,
    parse_timer_args,
    promote_due_timers,
)
from src.services.execution.autonomous_agent_executor import AutonomousAgentExecutor
from src.services.llm.base import LLMConfig, ToolDefinition

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _mock_rabbitmq_publish():
    """Keep durable queue nudges in-process.

    Due-timer promotion republishes woken runs via
    ``src.jobs.rabbitmq.publish_message``, including the direct
    ``promote_due_timers`` calls below. Every run in this file executes
    in-process, so a real publish would disturb the shared stack worker and
    pin the process-wide publisher pools to one test's function-scoped loop
    (see ``RabbitMQConnection.reset_pools``). Swallow the nudges.
    """
    with patch("src.jobs.rabbitmq.publish_message", new_callable=AsyncMock) as mock:
        yield mock

CALL_ID = "timer-1"


def _sleep_model():
    def _fn(messages, info):
        answered = any(
            isinstance(message, ModelRequest)
            and any(
                isinstance(part, ToolReturnPart)
                and part.tool_call_id == CALL_ID
                for part in message.parts
            )
            for message in messages
        )
        if not answered:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="sleep_until",
                        args={"seconds": 3600, "reason": "wait for report"},
                        tool_call_id=CALL_ID,
                    )
                ],
                model_name="fake",
            )
        return ModelResponse(
            parts=[TextPart(content="Report arrived; done.")],
            model_name="fake",
        )

    return FunctionModel(_fn)


def _mock_agent():
    from unittest.mock import MagicMock

    agent = MagicMock()
    agent.id = uuid4()
    agent.name = "Timer Agent"
    agent.system_prompt = "Wait when asked."
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


def _patches(model):
    sleep_def = ToolDefinition(
        name="sleep_until",
        description="Sleep",
        parameters={"type": "object", "properties": {}},
    )
    return (
        patch(
            "src.services.execution.autonomous_agent_executor.create_agent_model",
            return_value=model,
        ),
        patch(
            "src.services.execution.autonomous_agent_executor.get_llm_config",
            new_callable=AsyncMock,
            return_value=LLMConfig(
                provider="openai", model="test-model", api_key="k"
            ),
        ),
        patch(
            "src.services.execution.autonomous_agent_executor.resolve_agent_tools",
            new_callable=AsyncMock,
            return_value=([sleep_def], {}),
        ),
        patch(
            "src.services.execution.run_summarizer.enqueue_summarize",
            new_callable=AsyncMock,
        ),
        patch("src.jobs.rabbitmq.publish_message", new_callable=AsyncMock),
    )


async def _create_row(async_session_factory) -> UUID:
    async with async_session_factory() as session:
        run = AgentRun(trigger_type="test", status="queued")
        session.add(run)
        await session.commit()
        return run.id


async def _cleanup(async_session_factory, run_id: UUID):
    async with async_session_factory() as session:
        await session.execute(
            delete(AgentRunJournalEntry).where(
                AgentRunJournalEntry.run_id == run_id
            )
        )
        await session.execute(delete(AgentRun).where(AgentRun.id == run_id))
        await session.commit()


class TestTimerParsing:
    def test_seconds_and_wake_at(self):
        now = datetime.now(timezone.utc)
        wake = parse_timer_args({"seconds": 60, "reason": "x"}, now=now)
        assert wake == now + timedelta(seconds=60)
        wake2 = parse_timer_args(
            {"wake_at": (now + timedelta(hours=1)).isoformat(), "reason": "x"},
            now=now,
        )
        assert wake2 > now

    def test_rejects_bad_requests(self):
        now = datetime.now(timezone.utc)
        with pytest.raises(TimerError):
            parse_timer_args({"seconds": 60}, now=now)
        with pytest.raises(TimerError):
            parse_timer_args({"seconds": 0, "reason": "x"}, now=now)
        with pytest.raises(TimerError):
            parse_timer_args(
                {"seconds": MAX_TIMER_SECONDS + 1, "reason": "x"}, now=now
            )
        with pytest.raises(TimerError):
            parse_timer_args(
                {"wake_at": (now - timedelta(seconds=5)).isoformat(),
                 "reason": "x"},
                now=now,
            )
        with pytest.raises(TimerError):
            parse_timer_args(
                {"seconds": 60, "wake_at": now.isoformat(), "reason": "x"},
                now=now,
            )


class TestDurableTimer:
    async def test_sleep_wake_resume_same_run(self, async_session_factory):
        run_id = await _create_row(async_session_factory)
        try:
            async with async_session_factory() as session:
                claimed = await run_store.claim_run(session, run_id, "worker")
                lease_token = claimed.lease_token
                assert lease_token is not None

            executor = AutonomousAgentExecutor(
                async_session_factory, redis_client=None
            )
            patches = _patches(_sleep_model())
            with (
                patches[0],
                patches[1],
                patches[2],
                patches[3],
                patches[4],
            ):
                suspended = await executor.run(
                    _mock_agent(),
                    input_data={"task": "wait"},
                    run_id=str(run_id),
                    lease_token=lease_token,
                )
            assert suspended["status"] == "suspended"

            async with async_session_factory() as session:
                row = await session.get(AgentRun, run_id)
                assert row is not None
                assert row.status == "sleeping"
                assert row.lease_token is None
                assert row.wake_at is not None
                assert row.wake_at > datetime.now(timezone.utc)

            # Early scans are no-ops.
            assert await promote_due_timers(
                async_session_factory, datetime.now(timezone.utc)
            ) == []
            async with async_session_factory() as session:
                row = await session.get(AgentRun, run_id)
                assert row is not None
                assert row.status == "sleeping"

            # Due promotion wakes once with one fired entry.
            due = datetime.now(timezone.utc) + timedelta(seconds=3601)
            first = await promote_due_timers(async_session_factory, due)
            assert first == [run_id]
            assert await promote_due_timers(async_session_factory, due) == []
            async with async_session_factory() as session:
                row = await session.get(AgentRun, run_id)
                assert row is not None
                assert row.status == "running"
                assert row.wake_at is None
                results, pending = await collect_timer_results(session, run_id)
                assert results == {CALL_ID: TIMER_FIRED_TEXT}
                assert pending == set()

            # The same run resumes with the deterministic fired result.
            async with async_session_factory() as session:
                resumed_claim = await run_store.claim_run(
                    session, run_id, "worker-b"
                )
                resume_token = resumed_claim.lease_token
                assert resume_token is not None
            plan, _, unrecoverable = await prepare_resume(
                async_session_factory, run_id, resume_token
            )
            assert unrecoverable is None
            assert plan is not None
            assert plan.deferred_results == {CALL_ID: TIMER_FIRED_TEXT}

            resumed = AutonomousAgentExecutor(
                async_session_factory, redis_client=None
            )
            patches = _patches(_sleep_model())
            with (
                patches[0],
                patches[1],
                patches[2],
                patches[3],
                patches[4],
            ):
                result = await resumed.run(
                    _mock_agent(),
                    input_data={"task": "wait"},
                    run_id=str(run_id),
                    lease_token=resume_token,
                    resume_history=plan.history or None,
                    deferred_results=plan.deferred_results or None,
                )
            assert result["status"] == "completed"
            assert result["output"] == "Report arrived; done."
        finally:
            await _cleanup(async_session_factory, run_id)

    async def test_sleeping_run_cancelled_through_api(
        self, e2e_client, platform_admin, db_session, async_session_factory
    ):
        response = e2e_client.post(
            "/api/agents",
            json={
                "name": f"Sleep Agent {uuid4().hex[:8]}",
                "description": "Timer cancel test",
                "system_prompt": "Wait when asked.",
                "channels": ["chat"],
                "access_level": "authenticated",
                "organization_id": None,
            },
            headers=platform_admin.headers,
        )
        assert response.status_code == 201, response.text
        agent_doc = response.json()
        try:
            async with async_session_factory() as session:
                result = await session.execute(
                    select(Agent)
                    .options(
                        selectinload(Agent.tools),
                        selectinload(Agent.delegated_agents),
                        selectinload(Agent.roles),
                    )
                    .where(Agent.id == UUID(agent_doc["id"]))
                )
                agent = result.scalar_one()

            run_id = await _create_row(async_session_factory)
            async with async_session_factory() as session:
                target = await session.get(AgentRun, run_id)
                assert target is not None
                target.agent_id = agent.id
                await session.commit()
                claimed = await run_store.claim_run(session, run_id, "worker")
                lease_token = claimed.lease_token
                assert lease_token is not None

            executor = AutonomousAgentExecutor(
                async_session_factory, redis_client=None
            )
            patches = _patches(_sleep_model())
            with (
                patches[0],
                patches[1],
                patches[2],
                patches[3],
                patches[4],
            ):
                suspended = await executor.run(
                    agent,
                    input_data={"task": "wait"},
                    run_id=str(run_id),
                    lease_token=lease_token,
                )
            assert suspended["status"] == "suspended"

            cancelled = e2e_client.post(
                f"/api/agent-runs/{run_id}/cancel",
                headers=platform_admin.headers,
            )
            assert cancelled.status_code == 200, cancelled.text
            assert cancelled.json()["status"] == "cancelled"
            async with async_session_factory() as session:
                row = await session.get(AgentRun, run_id)
                assert row is not None
                assert row.status == "cancelled"
            await _cleanup(async_session_factory, run_id)
        finally:
            e2e_client.delete(
                f"/api/agents/{agent_doc['id']}",
                headers=platform_admin.headers,
            )
