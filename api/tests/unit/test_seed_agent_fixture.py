"""Regression coverage for durable cleanup of the shared Agent fixture."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select

from src.models.orm.agent_runs import AgentRun
from src.models.orm.agents import Agent
from src.models.orm.users import User
from tests.unit.conftest import seed_agent, seed_user


@pytest.mark.asyncio
async def test_seed_agent_removes_a_committed_agent_from_a_fresh_session(
    async_session_factory,
) -> None:
    """A committing child must not leave the shared Agent fixture durable."""
    async with async_session_factory() as setup:
        fixture = seed_agent.__wrapped__(setup, async_session_factory)
        agent = await anext(fixture)
        run = AgentRun(
            id=uuid4(),
            agent_id=agent.id,
            trigger_type="test",
            status="completed",
            iterations_used=0,
            tokens_used=0,
            created_at=datetime.now(timezone.utc),
        )
        setup.add(run)
        await setup.commit()
        agent_id = agent.id
        run_id = run.id
        # Hold a row lock in the source fixture session. Cleanup must first
        # release it before the separate durable deletion can proceed.
        agent.name = "uncommitted fixture update"
        await setup.flush()

        with pytest.raises(StopAsyncIteration):
            await anext(fixture)

    async with async_session_factory() as verify:
        assert await verify.get(Agent, agent_id) is None
        assert (
            await verify.execute(select(AgentRun.id).where(AgentRun.id == run_id))
        ).scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_seed_user_removes_a_committed_user_from_a_fresh_session(
    async_session_factory,
) -> None:
    """A committed fixture user cannot survive a later uncommitted update."""
    async with async_session_factory() as setup:
        fixture = seed_user.__wrapped__(setup, async_session_factory)
        user = await anext(fixture)
        await setup.commit()
        user_id = user.id
        user.name = "uncommitted fixture update"
        await setup.flush()

        with pytest.raises(StopAsyncIteration):
            await anext(fixture)

    async with async_session_factory() as verify:
        assert await verify.get(User, user_id) is None
