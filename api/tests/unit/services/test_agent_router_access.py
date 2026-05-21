"""Access-control regression tests for chat agent routing."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest
import pytest_asyncio

from src.models.enums import AgentAccessLevel
from src.models.orm.agents import Agent
from src.models.orm.organizations import Organization
from src.models.orm.users import User
from src.services.agent_router import AgentRouter


@pytest_asyncio.fixture
async def seeded_router_data(db_session):
    now = datetime.now(timezone.utc)
    org = Organization(
        id=uuid4(),
        name=f"Org {uuid4().hex[:8]}",
        created_by="test@example.com",
        created_at=now,
        updated_at=now,
    )
    user = User(
        id=uuid4(),
        email=f"user_{uuid4().hex[:8]}@example.com",
        name="Org User",
        is_active=True,
        is_superuser=False,
        is_verified=True,
        is_registered=True,
        organization_id=org.id,
        created_at=now,
        updated_at=now,
    )
    restricted = Agent(
        id=uuid4(),
        name="Platform Assistant",
        description="Privileged helper",
        system_prompt="You can manage the platform.",
        channels=["chat"],
        access_level=AgentAccessLevel.ROLE_BASED,
        organization_id=None,
        is_active=True,
        knowledge_sources=[],
        system_tools=["create_agent"],
        created_by="admin@example.com",
        created_at=now,
        updated_at=now,
    )
    allowed = Agent(
        id=uuid4(),
        name="Help Desk",
        description="General support",
        system_prompt="You help users.",
        channels=["chat"],
        access_level=AgentAccessLevel.AUTHENTICATED,
        organization_id=None,
        is_active=True,
        knowledge_sources=[],
        system_tools=[],
        created_by="admin@example.com",
        created_at=now,
        updated_at=now,
    )
    db_session.add_all([org, user, restricted, allowed])
    await db_session.commit()

    yield user, restricted, allowed

    await db_session.delete(restricted)
    await db_session.delete(allowed)
    await db_session.delete(user)
    await db_session.delete(org)
    await db_session.commit()


@pytest.mark.asyncio
async def test_parse_mention_ignores_inaccessible_role_based_agent(
    seeded_router_data, async_session_factory
):
    user, _restricted, allowed = seeded_router_data
    router = AgentRouter(async_session_factory)

    assert await router.parse_mention("@[Platform Assistant] help", user) is None

    mentioned = await router.parse_mention("@[Help Desk] help", user)
    assert mentioned is not None
    assert mentioned.id == allowed.id


@pytest.mark.asyncio
async def test_auto_routing_cannot_select_inaccessible_agent(
    seeded_router_data, async_session_factory, monkeypatch
):
    user, _restricted, _allowed = seeded_router_data
    router = AgentRouter(async_session_factory)

    class _RouterLLM:
        async def complete(self, messages):
            return type("Response", (), {"content": "Platform Assistant"})()

    async def _get_router_llm(_session):
        return _RouterLLM()

    monkeypatch.setattr("src.services.agent_router.get_llm_client", _get_router_llm)

    assert await router.route_message("manage platform agents", user=user) is None
