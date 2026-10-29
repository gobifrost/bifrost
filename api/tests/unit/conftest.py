"""Unit test fixtures shared across the unit suite."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import AsyncGenerator
from uuid import uuid4

import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.enums import AgentAccessLevel
from src.models.orm.agents import Agent
from src.models.orm.users import User


@pytest_asyncio.fixture
async def seed_agent(
    db_session: AsyncSession,
    async_session_factory,
) -> AsyncGenerator[Agent, None]:
    """Create a minimal global Agent row for tests that need an agent FK.

    Uses ``flush()`` (not ``commit()``) so the agent is visible to subsequent
    queries on the same session. Teardown also removes a row made durable by
    a test that commits its child rows.
    """
    agent = Agent(
        id=uuid4(),
        name=f"seed_agent_{uuid4().hex[:8]}",
        description="Seed agent for unit tests",
        system_prompt="You are a test agent.",
        channels=["chat"],
        access_level=AgentAccessLevel.ROLE_BASED,
        organization_id=None,
        is_active=True,
        knowledge_sources=[],
        system_tools=[],
        created_by="test@example.com",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(agent)
    await db_session.flush()
    agent_id = agent.id

    try:
        yield agent
    finally:
        # Individual tests legitimately commit children of this fixture. A
        # rollback of the test session cannot undo those rows, so delete from
        # a fresh transaction and make cleanup failures visible. Release the
        # source session first: an uncommitted update may otherwise lock the
        # Agent row and block the independent cleanup transaction.
        await db_session.rollback()
        async with async_session_factory() as cleanup:
            await cleanup.execute(delete(Agent).where(Agent.id == agent_id))
            await cleanup.commit()


@pytest_asyncio.fixture
async def seed_user(
    db_session: AsyncSession,
    async_session_factory,
) -> AsyncGenerator[User, None]:
    """Create a minimal User row for tests that need a user FK.

    Mirrors ``seed_agent`` semantics, including durable cleanup after a test
    commits rows that reference the fixture User.
    """
    # is_superuser=True so we satisfy the ck_users_org_requires_superuser
    # check constraint without needing to seed an Organization row.
    user = User(
        id=uuid4(),
        email=f"seed_user_{uuid4().hex[:8]}@example.com",
        name="Seed User",
        is_active=True,
        is_superuser=True,
        is_verified=True,
        is_registered=True,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(user)
    await db_session.flush()
    user_id = user.id

    try:
        yield user
    finally:
        # Match seed_agent: tests can commit rows that reference this User,
        # while a later uncommitted update still holds a lock in db_session.
        await db_session.rollback()
        async with async_session_factory() as cleanup:
            await cleanup.execute(delete(User).where(User.id == user_id))
            await cleanup.commit()
