from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.auth import UserPrincipal
from src.models.enums import AgentAccessLevel
from src.models.orm.agents import Agent, AgentRole, Conversation
from src.models.orm.organizations import Organization
from src.models.orm.users import Role, User, UserRole
from src.services.agent_executor import AgentExecutor
from src.services.agent_router import AgentRouter
from src.services.llm import LLMResponse


class SessionBoundAgentExecutor(AgentExecutor):
    def __init__(self, session: AsyncSession):
        super().__init__(lambda: None)  # type: ignore[arg-type]
        self._session = session

    @asynccontextmanager
    async def _db(self):
        yield self._session
        await self._session.flush()


class SessionBoundAgentRouter(AgentRouter):
    def __init__(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        org_id: UUID | None,
        is_superuser: bool = False,
    ):
        super().__init__(
            lambda: None,  # type: ignore[arg-type]
            user_id=user_id,
            org_id=org_id,
            is_superuser=is_superuser,
        )
        self._session = session

    @asynccontextmanager
    async def _db(self):
        yield self._session


def _now():
    return datetime.now(timezone.utc)


async def _make_org(db: AsyncSession, name: str) -> Organization:
    org = Organization(
        id=uuid4(),
        name=name,
        domain=f"{uuid4().hex[:8]}.example.com",
        created_by="test@example.com",
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(org)
    await db.flush()
    return org


async def _make_user(
    db: AsyncSession,
    *,
    org_id: UUID,
    email_prefix: str,
    is_superuser: bool = False,
) -> User:
    user = User(
        id=uuid4(),
        email=f"{email_prefix}-{uuid4().hex[:8]}@example.com",
        name=email_prefix,
        is_active=True,
        is_superuser=is_superuser,
        is_verified=True,
        is_registered=True,
        organization_id=org_id,
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(user)
    await db.flush()
    return user


async def _make_role(db: AsyncSession, user: User | None = None) -> Role:
    role = Role(
        id=uuid4(),
        name=f"role-{uuid4().hex[:8]}",
        created_by="test@example.com",
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(role)
    await db.flush()
    if user is not None:
        db.add(UserRole(user_id=user.id, role_id=role.id, assigned_by="test@example.com"))
        await db.flush()
    return role


async def _make_agent(
    db: AsyncSession,
    name: str,
    *,
    org_id: UUID | None,
    access_level: AgentAccessLevel = AgentAccessLevel.AUTHENTICATED,
    owner_user_id: UUID | None = None,
    role: Role | None = None,
) -> Agent:
    agent = Agent(
        id=uuid4(),
        name=name,
        description=f"{name} description",
        system_prompt="You are a test agent.",
        channels=["chat"],
        access_level=access_level,
        organization_id=org_id,
        owner_user_id=owner_user_id,
        is_active=True,
        knowledge_sources=[],
        system_tools=[],
        created_by="test@example.com",
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(agent)
    await db.flush()
    if role is not None:
        db.add(AgentRole(agent_id=agent.id, role_id=role.id, assigned_by="test@example.com"))
        await db.flush()
    return agent


async def _make_conversation(db: AsyncSession, *, user_id: UUID) -> Conversation:
    conversation = Conversation(
        id=uuid4(),
        user_id=user_id,
        agent_id=None,
        channel="chat",
        title=None,
        extra_data={},
        is_active=True,
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(conversation)
    await db.flush()
    return conversation


def _principal(user: User) -> UserPrincipal:
    return UserPrincipal(
        user_id=user.id,
        email=user.email,
        organization_id=user.organization_id,
        name=user.name or "",
        is_active=user.is_active,
        is_superuser=user.is_superuser,
        is_verified=user.is_verified,
    )


@pytest.mark.asyncio
async def test_available_agents_are_limited_to_user_access(db_session: AsyncSession):
    org_a = await _make_org(db_session, "Org A")
    org_b = await _make_org(db_session, "Org B")
    user = await _make_user(db_session, org_id=org_b.id, email_prefix="org-b-user")
    other_user = await _make_user(db_session, org_id=org_b.id, email_prefix="other-user")
    user_role = await _make_role(db_session, user)
    unassigned_role = await _make_role(db_session)

    await _make_agent(db_session, "Org B Auth Agent", org_id=org_b.id)
    await _make_agent(db_session, "Global Role Agent", org_id=None, access_level=AgentAccessLevel.ROLE_BASED, role=user_role)
    await _make_agent(db_session, "Own Private Agent", org_id=org_b.id, access_level=AgentAccessLevel.PRIVATE, owner_user_id=user.id)
    await _make_agent(db_session, "Cross Org Agent", org_id=org_a.id)
    await _make_agent(db_session, "Unassigned Role Agent", org_id=None, access_level=AgentAccessLevel.ROLE_BASED, role=unassigned_role)
    await _make_agent(db_session, "Other Private Agent", org_id=org_b.id, access_level=AgentAccessLevel.PRIVATE, owner_user_id=other_user.id)

    router = SessionBoundAgentRouter(db_session, user_id=user.id, org_id=org_b.id)

    names = {agent.name for agent in await router.get_available_agents()}

    assert names == {"Global Role Agent", "Org B Auth Agent", "Own Private Agent"}


@pytest.mark.asyncio
async def test_auto_route_cannot_select_inaccessible_agent(db_session: AsyncSession):
    org_a = await _make_org(db_session, "Org A")
    org_b = await _make_org(db_session, "Org B")
    user = await _make_user(db_session, org_id=org_b.id, email_prefix="org-b-user")

    await _make_agent(db_session, "Accessible Helpdesk", org_id=org_b.id)
    await _make_agent(db_session, "Cross Org Payroll", org_id=org_a.id)

    router = SessionBoundAgentRouter(db_session, user_id=user.id, org_id=org_b.id)
    llm_client = AsyncMock()
    llm_client.complete = AsyncMock(return_value=LLMResponse(content="Cross Org Payroll"))

    with patch("src.services.agent_router.get_llm_client", return_value=llm_client):
        routed = await router.route_message("I need help with payroll")

    assert routed is None
    prompt = llm_client.complete.call_args.kwargs["messages"][1].content
    assert "Accessible Helpdesk" in prompt
    assert "Cross Org Payroll" not in prompt


@pytest.mark.asyncio
async def test_mention_cannot_select_inaccessible_agent(db_session: AsyncSession):
    org_a = await _make_org(db_session, "Org A")
    org_b = await _make_org(db_session, "Org B")
    user = await _make_user(db_session, org_id=org_b.id, email_prefix="org-b-user")

    await _make_agent(db_session, "Cross Org Payroll", org_id=org_a.id)
    accessible = await _make_agent(db_session, "Accessible Helpdesk", org_id=org_b.id)

    router = SessionBoundAgentRouter(db_session, user_id=user.id, org_id=org_b.id)

    assert await router.parse_mention("@[Cross Org Payroll] hello") is None
    assert await router.parse_mention("@[Accessible Helpdesk] hello") == accessible


@pytest.mark.asyncio
async def test_executor_switch_agent_denies_inaccessible_agent(db_session: AsyncSession):
    org_a = await _make_org(db_session, "Org A")
    org_b = await _make_org(db_session, "Org B")
    user = await _make_user(db_session, org_id=org_b.id, email_prefix="org-b-user")
    inaccessible = await _make_agent(db_session, "Cross Org Payroll", org_id=org_a.id)
    conversation = await _make_conversation(db_session, user_id=user.id)

    executor = SessionBoundAgentExecutor(db_session)

    switch_chunk, switched_agent = await executor._switch_agent(
        conversation,
        inaccessible,
        "routed",
        user=_principal(user),
    )

    await db_session.refresh(conversation)
    assert switch_chunk is None
    assert switched_agent is None
    assert conversation.agent_id is None


@pytest.mark.asyncio
async def test_executor_switch_agent_persists_accessible_agent(db_session: AsyncSession):
    org = await _make_org(db_session, "Org")
    user = await _make_user(db_session, org_id=org.id, email_prefix="org-user")
    accessible = await _make_agent(db_session, "Accessible Helpdesk", org_id=org.id)
    conversation = await _make_conversation(db_session, user_id=user.id)

    executor = SessionBoundAgentExecutor(db_session)

    switch_chunk, switched_agent = await executor._switch_agent(
        conversation,
        accessible,
        "@mention",
        user=_principal(user),
    )

    await db_session.refresh(conversation)
    assert switched_agent == accessible
    assert switch_chunk is not None
    assert switch_chunk.agent_switch is not None
    assert switch_chunk.agent_switch.agent_id == str(accessible.id)
    assert switch_chunk.agent_switch.agent_name == "Accessible Helpdesk"
    assert conversation.agent_id == accessible.id
