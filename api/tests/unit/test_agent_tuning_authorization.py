"""Authorization checks for the consolidated agent tuning router."""
from __future__ import annotations

from uuid import UUID, uuid4
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from src.core.auth import UserPrincipal
from src.models.enums import AgentAccessLevel
from src.models.orm.agents import Agent
from src.routers.agent_tuning import _load_agent_with_access


def _principal(
    user_id: UUID,
    org_id: UUID | None,
    *,
    is_superuser: bool = False,
    roles: list[str] | None = None,
) -> UserPrincipal:
    return UserPrincipal(
        user_id=user_id,
        email=f"{user_id}@example.test",
        organization_id=org_id,
        is_active=True,
        is_superuser=is_superuser,
        is_verified=True,
        roles=roles or [],
    )


class _AgentResult:
    def __init__(self, agent: Agent | None):
        self._agent = agent

    def scalar_one_or_none(self) -> Agent | None:
        return self._agent


def _db_returning(agent: Agent | None):
    db = AsyncMock()
    db.execute.return_value = _AgentResult(agent)
    return db


def _agent(
    *,
    access_level: AgentAccessLevel,
    owner_user_id: UUID | None,
    organization_id: UUID | None,
) -> Agent:
    return Agent(
        id=uuid4(),
        name=f"tuning-auth-agent-{uuid4().hex[:8]}",
        description="agent tuning authorization regression fixture",
        system_prompt="You are a test agent.",
        channels=["chat"],
        access_level=access_level,
        organization_id=organization_id,
        owner_user_id=owner_user_id,
        is_active=True,
        knowledge_sources=[],
        system_tools=[],
        created_by="test@example.com",
    )


@pytest.mark.asyncio
async def test_regular_user_can_tune_own_private_agent():
    org_id = uuid4()
    user_id = uuid4()
    agent = _agent(
        access_level=AgentAccessLevel.PRIVATE,
        owner_user_id=user_id,
        organization_id=org_id,
    )

    loaded = await _load_agent_with_access(
        agent.id,
        _db_returning(agent),
        _principal(user_id, org_id),
    )

    assert loaded.id == agent.id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "access_level",
    [AgentAccessLevel.AUTHENTICATED, AgentAccessLevel.ROLE_BASED],
)
async def test_regular_user_cannot_tune_shared_agent(access_level):
    org_id = uuid4()
    user_id = uuid4()
    agent = _agent(
        access_level=access_level,
        owner_user_id=None,
        organization_id=org_id,
    )

    with pytest.raises(HTTPException) as exc_info:
        await _load_agent_with_access(
            agent.id,
            _db_returning(agent),
            _principal(user_id, org_id),
        )

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_regular_user_cannot_tune_another_users_private_agent():
    org_id = uuid4()
    owner_user_id = uuid4()
    caller_user_id = uuid4()
    agent = _agent(
        access_level=AgentAccessLevel.PRIVATE,
        owner_user_id=owner_user_id,
        organization_id=org_id,
    )

    with pytest.raises(HTTPException) as exc_info:
        await _load_agent_with_access(
            agent.id,
            _db_returning(agent),
            _principal(caller_user_id, org_id),
        )

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_platform_admin_can_tune_shared_agent():
    org_id = uuid4()
    admin_id = uuid4()
    agent = _agent(
        access_level=AgentAccessLevel.AUTHENTICATED,
        owner_user_id=None,
        organization_id=org_id,
    )

    loaded = await _load_agent_with_access(
        agent.id,
        _db_returning(agent),
        _principal(admin_id, None, is_superuser=True),
    )

    assert loaded.id == agent.id


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["Platform Admin", "Platform Owner"])
async def test_role_based_admin_can_tune_shared_agent(role: str):
    org_id = uuid4()
    admin_id = uuid4()
    agent = _agent(
        access_level=AgentAccessLevel.AUTHENTICATED,
        owner_user_id=None,
        organization_id=org_id,
    )

    loaded = await _load_agent_with_access(
        agent.id,
        _db_returning(agent),
        _principal(admin_id, org_id, roles=[role]),
    )

    assert loaded.id == agent.id


@pytest.mark.asyncio
async def test_missing_agent_returns_404():
    with pytest.raises(HTTPException) as exc_info:
        await _load_agent_with_access(
            uuid4(),
            _db_returning(None),
            _principal(uuid4(), uuid4()),
        )

    assert exc_info.value.status_code == 404
