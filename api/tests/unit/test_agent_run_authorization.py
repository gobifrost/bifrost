"""Agent-run authorization and stream scoping regressions."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.auth import UserPrincipal
from src.core.pubsub import publish_agent_run_update
from src.models.contracts.agent_runs import AgentRunCreateRequest
from src.models.enums import AgentAccessLevel
from src.models.orm.agent_runs import AgentRun
from src.models.orm.agents import Agent, AgentRole
from src.models.orm.organizations import Organization
from src.models.orm.users import Role, User, UserRole
from src.routers.agent_runs import execute_agent_run, list_agent_runs, rerun_agent_run
from src.routers.websocket import websocket_connect


pytestmark = pytest.mark.asyncio


def _principal(
    user_id: UUID,
    org_id: UUID | None,
    *,
    is_superuser: bool = False,
) -> UserPrincipal:
    return UserPrincipal(
        user_id=user_id,
        email=f"{user_id}@example.test",
        organization_id=org_id,
        is_active=True,
        is_superuser=is_superuser,
        is_verified=True,
        roles=[],
    )


async def _make_org(db: AsyncSession, name: str) -> Organization:
    org = Organization(
        id=uuid4(),
        name=name,
        domain=f"{name.lower()}-{uuid4().hex[:8]}.example.test",
        created_by="test@example.com",
    )
    db.add(org)
    await db.flush()
    return org


async def _make_user(db: AsyncSession, org: Organization) -> User:
    user = User(
        id=uuid4(),
        email=f"user-{uuid4().hex[:8]}@example.test",
        name="Agent Run User",
        organization_id=org.id,
        is_active=True,
        is_superuser=False,
        is_verified=True,
        is_registered=True,
    )
    db.add(user)
    await db.flush()
    return user


async def _make_role_based_agent(
    db: AsyncSession,
    org: Organization,
) -> tuple[Agent, Role]:
    role = Role(
        id=uuid4(),
        name=f"agent-run-role-{uuid4().hex[:8]}",
        created_by="test@example.com",
    )
    agent = Agent(
        id=uuid4(),
        name=f"agent-run-auth-{uuid4().hex[:8]}",
        description="role-gated agent",
        system_prompt="You are a role-gated test agent.",
        channels=["chat"],
        access_level=AgentAccessLevel.ROLE_BASED,
        organization_id=org.id,
        is_active=True,
        knowledge_sources=[],
        system_tools=[],
        created_by="test@example.com",
    )
    db.add_all([role, agent])
    await db.flush()
    db.add(AgentRole(agent_id=agent.id, role_id=role.id, assigned_by="test@example.com"))
    await db.flush()
    return agent, role


async def _make_authenticated_agent(db: AsyncSession, org: Organization) -> Agent:
    agent = Agent(
        id=uuid4(),
        name=f"agent-run-authenticated-{uuid4().hex[:8]}",
        description="org-scoped authenticated agent",
        system_prompt="You are an authenticated test agent.",
        channels=["chat"],
        access_level=AgentAccessLevel.AUTHENTICATED,
        organization_id=org.id,
        is_active=True,
        knowledge_sources=[],
        system_tools=[],
        created_by="test@example.com",
    )
    db.add(agent)
    await db.flush()
    return agent


async def _make_run(db: AsyncSession, agent: Agent, org: Organization) -> AgentRun:
    run = AgentRun(
        id=uuid4(),
        agent_id=agent.id,
        trigger_type="api",
        status="completed",
        org_id=org.id,
        iterations_used=1,
        tokens_used=100,
        asked="Sensitive customer question",
        did="Sensitive customer action",
        completed_at=datetime.now(timezone.utc),
    )
    db.add(run)
    await db.flush()
    return run


async def _list_runs_for_user(db: AsyncSession, user: UserPrincipal):
    return await list_agent_runs(
        db,
        user,
        agent_id=None,
        status_filter=None,
        trigger_type=None,
        org_id=None,
        start_date=None,
        end_date=None,
        q=None,
        verdict=None,
        metadata_filter=None,
        limit=50,
        offset=0,
    )


async def test_list_agent_runs_hides_same_org_role_based_agent_without_role(
    db_session: AsyncSession,
):
    org = await _make_org(db_session, "OrgA")
    user = await _make_user(db_session, org)
    agent, _role = await _make_role_based_agent(db_session, org)
    await _make_run(db_session, agent, org)

    response = await _list_runs_for_user(db_session, _principal(user.id, org.id))

    assert response.total == 0
    assert response.items == []


async def test_rerun_agent_run_blocks_same_org_user_without_agent_role(
    db_session: AsyncSession,
):
    org = await _make_org(db_session, "OrgA")
    user = await _make_user(db_session, org)
    agent, _role = await _make_role_based_agent(db_session, org)
    run = await _make_run(db_session, agent, org)

    with pytest.raises(HTTPException) as exc_info:
        await rerun_agent_run(run.id, db_session, _principal(user.id, org.id))

    assert exc_info.value.status_code == 404


async def test_execute_agent_run_blocks_same_org_user_without_agent_role(
    db_session: AsyncSession,
):
    org = await _make_org(db_session, "OrgA")
    user = await _make_user(db_session, org)
    agent, _role = await _make_role_based_agent(db_session, org)

    request = AgentRunCreateRequest(
        agent_name=agent.name,
        input={"ticket_id": 123},
        timeout=1,
    )

    with (
        patch(
            "src.routers.agent_runs.enqueue_agent_run",
            new=AsyncMock(return_value=str(uuid4())),
        ),
        patch(
            "src.routers.agent_runs.wait_for_agent_run_result",
            new=AsyncMock(return_value={"status": "completed"}),
        ),
        pytest.raises(HTTPException) as exc_info,
    ):
        await execute_agent_run(request, db_session, _principal(user.id, org.id))

    assert exc_info.value.status_code == 404


async def test_execute_agent_run_blocks_cross_org_authenticated_agent(
    db_session: AsyncSession,
):
    owner_org = await _make_org(db_session, "OwnerOrg")
    caller_org = await _make_org(db_session, "CallerOrg")
    caller = await _make_user(db_session, caller_org)
    agent = await _make_authenticated_agent(db_session, owner_org)

    request = AgentRunCreateRequest(
        agent_name=agent.name,
        input={"ticket_id": 123},
        timeout=1,
    )

    with (
        patch(
            "src.routers.agent_runs.enqueue_agent_run",
            new=AsyncMock(return_value=str(uuid4())),
        ),
        patch(
            "src.routers.agent_runs.wait_for_agent_run_result",
            new=AsyncMock(return_value={"status": "completed"}),
        ),
        pytest.raises(HTTPException) as exc_info,
    ):
        await execute_agent_run(request, db_session, _principal(caller.id, caller_org.id))

    assert exc_info.value.status_code == 404


async def test_user_with_agent_role_can_list_agent_run(db_session: AsyncSession):
    org = await _make_org(db_session, "OrgA")
    user = await _make_user(db_session, org)
    agent, role = await _make_role_based_agent(db_session, org)
    run = await _make_run(db_session, agent, org)
    db_session.add(
        UserRole(user_id=user.id, role_id=role.id, assigned_by="test@example.com")
    )
    await db_session.flush()

    response = await _list_runs_for_user(db_session, _principal(user.id, org.id))

    assert response.total == 1
    assert response.items[0].id == run.id


async def test_publish_agent_run_update_uses_scoped_list_channels(
    db_session: AsyncSession,
):
    org = await _make_org(db_session, "OrgA")
    agent, _role = await _make_role_based_agent(db_session, org)
    run = await _make_run(db_session, agent, org)

    with patch("src.core.pubsub.manager.broadcast", new=AsyncMock()) as broadcast:
        await publish_agent_run_update(run, agent.name)

    channels = [call.args[0] for call in broadcast.await_args_list]
    assert f"agent-run:{run.id}" in channels
    assert f"agent-runs:org:{org.id}" in channels
    assert "agent-runs:all" in channels
    assert "agent-runs" not in channels


async def test_websocket_agent_runs_alias_subscribes_to_user_org_channel():
    org_id = uuid4()
    user_id = uuid4()
    websocket = SimpleNamespace(state=SimpleNamespace())
    websocket.send_json = AsyncMock()
    websocket.receive_json = AsyncMock(side_effect=WebSocketDisconnect())

    with (
        patch(
            "src.routers.websocket.get_current_user_ws",
            new=AsyncMock(return_value=_principal(user_id, org_id)),
        ),
        patch("src.routers.websocket.manager.connect", new=AsyncMock()) as connect,
        patch("src.routers.websocket.manager.disconnect"),
    ):
        await websocket_connect(cast(Any, websocket), channels=["agent-runs"])

    assert connect.await_args is not None
    subscribed_channels = connect.await_args.args[1]
    assert f"agent-runs:org:{org_id}" in subscribed_channels
    assert "agent-runs" not in subscribed_channels
