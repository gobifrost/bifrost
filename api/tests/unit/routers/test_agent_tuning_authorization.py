"""Prompt tuning keeps the ordinary private-Agent write entitlement."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from shared.builtin_roles import USER_ROLE_ID
from src.core.principal import UserPrincipal
from src.models.enums import AgentAccessLevel
from src.models.orm.agents import Agent
from src.models.orm.organizations import Organization
from src.models.orm.users import Role, User
from src.routers import agent_tuning


async def _private_agent_owner(db, *, without_ai_execute: bool = False):
    organization = Organization(name="Contoso", created_by="test")
    db.add(organization)
    await db.flush()
    base_role_id = USER_ROLE_ID
    if without_ai_execute:
        base_role = Role(name=f"Contoso no AI {uuid4().hex}", created_by="test")
        db.add(base_role)
        await db.flush()
        base_role_id = base_role.id
    user_id = uuid4()
    user = User(
        id=user_id,
        email=f"tuning-{uuid4().hex}@contoso.example",
        organization_id=organization.id,
        base_role_id=base_role_id,
    )
    agent = Agent(
        name=f"Contoso tuning {uuid4().hex}",
        system_prompt="Handle requests carefully.",
        access_level=AgentAccessLevel.PRIVATE,
        organization_id=organization.id,
        owner_user_id=user_id,
        created_by=user.email,
    )
    db.add_all([user, agent])
    await db.flush()
    return agent, UserPrincipal(
        user_id=user.id,
        email=user.email,
        organization_id=organization.id,
        is_superuser=False,
    )


async def test_private_agent_owner_can_reach_prompt_tuning_without_agents_readwrite(db_session):
    agent, user = await _private_agent_owner(db_session)

    loaded, caller = await agent_tuning._load_agent_with_access(agent.id, db_session, user)

    assert loaded.id == agent.id
    assert caller.ctx is not None
    assert caller.ctx.user_id == user.user_id


async def test_tuning_refuses_missing_ai_execute_before_proposal_io(db_session, monkeypatch):
    agent, user = await _private_agent_owner(db_session, without_ai_execute=True)
    propose = AsyncMock(side_effect=AssertionError("proposal ran before ai.execute authorization"))
    monkeypatch.setattr(agent_tuning, "propose_consolidated_tuning", propose)

    with pytest.raises(HTTPException) as error:
        await agent_tuning.create_tuning_session(agent.id, db_session, user)

    assert error.value.status_code == 403
    propose.assert_not_awaited()
