"""Write-scope enforcement for the MCP update_agent tool.

Mirrors the REST rule enforced in ``routers/agents.py::update_agent``
(owner_user_id == caller AND access_level == PRIVATE for non-bypass
callers), and fixes a specific bug where a caller with no organization
skipped the org check entirely (``if context.org_id and ...`` is False
when ``context.org_id`` is falsy, silently letting the update through).
"""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.models.enums import AgentAccessLevel
from src.models.orm.agents import Agent
from src.models.orm.organizations import Organization
from src.models.orm.users import User
from src.services.mcp_server.tools import agents as mcp_agents


def _fake_tool_db(db_session):
    @asynccontextmanager
    async def _cm(_context):
        yield db_session

    return _cm


async def _org(db) -> Organization:
    row = Organization(name=f"org-{uuid4().hex[:8]}", domain=f"{uuid4().hex}.example", created_by="test")
    db.add(row)
    await db.flush()
    return row


async def _user(db, org: Organization | None, *, superuser: bool = False) -> User:
    row = User(
        email=f"{uuid4().hex}@example.com",
        name="Test User",
        is_superuser=superuser,
        is_verified=True,
        organization_id=org.id if org else None,
    )
    db.add(row)
    await db.flush()
    return row


async def _agent(
    db,
    *,
    organization_id,
    owner_user_id=None,
    access_level=AgentAccessLevel.ROLE_BASED,
) -> Agent:
    row = Agent(
        name=f"agent-{uuid4().hex[:8]}",
        system_prompt="You are a test agent.",
        organization_id=organization_id,
        owner_user_id=owner_user_id,
        access_level=access_level,
        created_by="test",
    )
    db.add(row)
    await db.flush()
    return row


def _ctx(*, user_id, org_id=None, is_platform_admin=False, is_provider_org=False):
    return SimpleNamespace(
        user_id=user_id,
        org_id=org_id,
        is_platform_admin=is_platform_admin,
        is_provider_org=is_provider_org,
    )


def _error_text(result) -> str:
    payload = result.model_dump() if hasattr(result, "model_dump") else result
    return str(payload)


@pytest.mark.asyncio
async def test_non_owner_org_member_denied(db_session, monkeypatch):
    """A caller in the same org as a role-based (non-private) agent they
    don't own may not update it via this tool — REST only allows
    non-bypass callers to edit their own PRIVATE agents."""
    monkeypatch.setattr(mcp_agents, "get_tool_db", _fake_tool_db(db_session))

    org = await _org(db_session)
    owner = await _user(db_session, org)
    agent = await _agent(
        db_session,
        organization_id=org.id,
        owner_user_id=owner.id,
        access_level=AgentAccessLevel.ROLE_BASED,
    )

    ctx = _ctx(user_id=uuid4(), org_id=org.id)
    result = await mcp_agents.update_agent(ctx, str(agent.id), name="hijacked")

    assert "own your own private agents" in _error_text(result) or "only edit your own private agents" in _error_text(result)


@pytest.mark.asyncio
async def test_owner_of_private_agent_allowed(db_session, monkeypatch):
    monkeypatch.setattr(mcp_agents, "get_tool_db", _fake_tool_db(db_session))

    org = await _org(db_session)
    owner = await _user(db_session, org)
    agent = await _agent(
        db_session,
        organization_id=org.id,
        owner_user_id=owner.id,
        access_level=AgentAccessLevel.PRIVATE,
    )

    ctx = _ctx(user_id=owner.id, org_id=org.id)
    result = await mcp_agents.update_agent(ctx, str(agent.id), name="renamed-by-owner")

    text = _error_text(result)
    assert "error" not in text.lower() or "renamed-by-owner" in text
    await db_session.refresh(agent)
    assert agent.name == "renamed-by-owner"


@pytest.mark.asyncio
async def test_caller_with_no_org_denied_for_org_owned_agent(db_session, monkeypatch):
    """Regression: the pre-fix check ``if context.org_id and ...`` skipped
    the org comparison entirely when the caller had no org, letting a
    no-org caller update any org-owned agent. A no-org caller must be
    denied, not let through."""
    monkeypatch.setattr(mcp_agents, "get_tool_db", _fake_tool_db(db_session))

    org = await _org(db_session)
    owner = await _user(db_session, org)
    agent = await _agent(
        db_session,
        organization_id=org.id,
        owner_user_id=owner.id,
        access_level=AgentAccessLevel.PRIVATE,
    )

    # The caller is not the owner and has no org — a real "no-org, non-admin"
    # principal need not be persisted as a User row (the constraint that a
    # user with no org must be a superuser is a DB invariant on real
    # accounts, not a constraint this MCP tool itself enforces).
    ctx = _ctx(user_id=uuid4(), org_id=None)
    result = await mcp_agents.update_agent(ctx, str(agent.id), name="hijacked-no-org")

    assert "only edit your own private agents" in _error_text(result)
    await db_session.refresh(agent)
    assert agent.name != "hijacked-no-org"


@pytest.mark.asyncio
async def test_platform_admin_allowed_for_global_agent(db_session, monkeypatch):
    monkeypatch.setattr(mcp_agents, "get_tool_db", _fake_tool_db(db_session))

    agent = await _agent(db_session, organization_id=None)

    ctx = _ctx(user_id=uuid4(), is_platform_admin=True)
    await mcp_agents.update_agent(ctx, str(agent.id), name="admin-renamed")

    await db_session.refresh(agent)
    assert agent.name == "admin-renamed"
