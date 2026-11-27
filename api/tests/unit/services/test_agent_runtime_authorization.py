"""Focused policy tests for Agent and AgentRun authorization."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from shared.builtin_roles import USER_ROLE_ID
from src.core.principal import UserPrincipal
from src.models.enums import AgentAccessLevel
from src.services.agent_write_policy import can_read_agent, can_write_agent
from src.services.authorization.context import (
    AuthorizationContext,
    Boundary,
    BoundaryKind,
    RoleGrant,
)
from src.services.authorization.enforce import Caller


@pytest.mark.asyncio
@pytest.mark.parametrize("grant", ["home", "foreign", "revoked"])
async def test_chat_execution_uses_current_home_organization_grant(grant):
    from src.services.authorization.agent_runtime import (
        AgentRuntimeAuthorizationError,
        authorize_chat_execution,
    )

    home = uuid4()
    stale = _caller(
        original_user_id=uuid4(), home=home,
        permissions=frozenset({"chat.execute"}), boundary=home,
    )
    refreshed = _caller(
        original_user_id=stale.ctx.user_id, home=home,
        permissions=frozenset({"chat.execute"}) if grant != "revoked" else frozenset(),
        boundary=home if grant == "home" else uuid4(),
    )
    with patch(
        "src.services.authorization.agent_runtime.refresh_caller",
        new=AsyncMock(return_value=refreshed),
    ):
        if grant == "home":
            assert await authorize_chat_execution(None, stale) is refreshed
        else:
            with pytest.raises(AgentRuntimeAuthorizationError):
                await authorize_chat_execution(None, stale)


def _caller(
    *,
    original_user_id,
    home,
    permissions=frozenset(),
    boundary=None,
    original_external=False,
    actor_external=False,
):
    grants = () if boundary is None else (
        RoleGrant(uuid4(), permissions, (Boundary(BoundaryKind.ORGANIZATION, boundary),)),
    )
    return Caller(
        UserPrincipal(
            user_id=uuid4(),
            email="runner@contoso.example",
            organization_id=home,
            is_external=actor_external,
        ),
        AuthorizationContext(
            user_id=original_user_id,
            home_organization_id=home,
            base_role_id=USER_ROLE_ID,
            is_external=original_external,
            base_permissions=frozenset(),
            role_grants=grants,
        ),
    )


@pytest.mark.asyncio
async def test_private_agent_uses_original_runtime_user_for_ownership():
    home = uuid4()
    original_user = uuid4()
    caller = _caller(original_user_id=original_user, home=home)
    agent = SimpleNamespace(
        access_level=AgentAccessLevel.PRIVATE,
        owner_user_id=original_user,
        organization_id=home,
    )

    assert await can_read_agent(None, caller, agent)
    assert can_write_agent(caller, agent)


@pytest.mark.asyncio
async def test_other_private_agent_needs_read_all_at_its_organization():
    home, target = uuid4(), uuid4()
    caller = _caller(
        original_user_id=uuid4(),
        home=home,
        permissions=frozenset({"agents.read.all"}),
        boundary=target,
    )
    agent = SimpleNamespace(
        access_level=AgentAccessLevel.PRIVATE,
        owner_user_id=uuid4(),
        organization_id=target,
    )

    assert await can_read_agent(None, caller, agent)
    assert not can_write_agent(caller, agent)


@pytest.mark.asyncio
async def test_shared_global_agent_is_read_at_home_but_explicit_global_is_separate():
    home = uuid4()
    caller = _caller(
        original_user_id=uuid4(), home=home,
        permissions=frozenset({"agents.readbasic"}), boundary=home,
    )
    agent = SimpleNamespace(
        access_level=AgentAccessLevel.AUTHENTICATED,
        owner_user_id=None,
        organization_id=None,
    )

    assert await can_read_agent(None, caller, agent)
    assert not await can_read_agent(None, caller, agent, selected_organization_id=None)
    assert not can_write_agent(caller, agent)


def test_nonprivate_write_requires_permission_at_the_target_organization():
    home, granted, target = uuid4(), uuid4(), uuid4()
    caller = _caller(
        original_user_id=uuid4(),
        home=home,
        permissions=frozenset({"agents.readwrite"}),
        boundary=granted,
    )
    agent = SimpleNamespace(
        access_level=AgentAccessLevel.AUTHENTICATED,
        owner_user_id=None,
        organization_id=target,
    )

    assert not can_write_agent(caller, agent)


@pytest.mark.asyncio
async def test_own_private_agent_never_bypasses_original_user_reach():
    home, foreign, original_user = uuid4(), uuid4(), uuid4()
    caller = _caller(original_user_id=original_user, home=home)
    agent = SimpleNamespace(
        access_level=AgentAccessLevel.PRIVATE,
        owner_user_id=original_user,
        organization_id=foreign,
    )

    assert not await can_read_agent(None, caller, agent)
    assert not can_write_agent(caller, agent)


@pytest.mark.asyncio
async def test_authenticated_agent_uses_original_external_status_not_actor_status():
    org = uuid4()
    external_original = _caller(
        original_user_id=uuid4(),
        home=org,
        permissions=frozenset({"agents.readbasic"}),
        boundary=org,
        original_external=True,
        actor_external=False,
    )
    internal_original = _caller(
        original_user_id=uuid4(),
        home=org,
        permissions=frozenset({"agents.readbasic"}),
        boundary=org,
        original_external=False,
        actor_external=True,
    )
    agent = SimpleNamespace(
        access_level=AgentAccessLevel.AUTHENTICATED,
        owner_user_id=None,
        organization_id=org,
    )

    assert not await can_read_agent(None, external_original, agent)
    assert await can_read_agent(None, internal_original, agent)


@pytest.mark.asyncio
async def test_reauthorization_reloads_native_tool_binding_after_external_revocation(
    db_session, monkeypatch
):
    """A cached Agent cannot retain a system-tool binding after it is removed."""
    from datetime import datetime, timezone

    from sqlalchemy import delete, update
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from src.models.orm.agents import Agent
    from src.models.orm.organizations import Organization
    from src.services.authorization.agent_runtime import authorize_agent_execution

    organization_id = uuid4()
    agent_id = uuid4()
    db_session.add(
        Organization(
            id=organization_id,
            name="Fresh Agent Authority",
            domain=f"{uuid4().hex}.example.com",
            created_by="authority-test@example.com",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
    )
    db_session.add(
        Agent(
            id=agent_id,
            name="Fresh Binding Agent",
            description="freshness fixture",
            system_prompt="Be current.",
            channels=["chat"],
            access_level=AgentAccessLevel.AUTHENTICATED,
            organization_id=organization_id,
            is_active=True,
            knowledge_sources=[],
            system_tools=["execute_workflow"],
            created_by="authority-test@example.com",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
    )
    await db_session.commit()
    other_sessions = async_sessionmaker(db_session.bind, expire_on_commit=False)

    original_user = uuid4()
    caller = _caller(
        original_user_id=original_user,
        home=organization_id,
        permissions=frozenset({"agents.readbasic", "agents.execute"}),
        boundary=organization_id,
    )

    async def refresh(_db, existing):
        return existing

    monkeypatch.setattr(
        "src.services.authorization.agent_runtime.refresh_caller", refresh
    )
    try:
        _, loaded = await authorize_agent_execution(db_session, caller, agent_id)
        assert loaded.system_tools == ["execute_workflow"]

        async with other_sessions() as other_db:
            await other_db.execute(
                update(Agent)
                .where(Agent.id == agent_id)
                .values(system_tools=[])
                .execution_options(synchronize_session=False)
            )
            await other_db.commit()

        _, reauthorized = await authorize_agent_execution(db_session, caller, agent_id)
        assert reauthorized is loaded
        assert reauthorized.system_tools == []
    finally:
        await db_session.rollback()
        async with other_sessions() as cleanup_db:
            await cleanup_db.execute(delete(Agent).where(Agent.id == agent_id))
            await cleanup_db.execute(
                delete(Organization).where(Organization.id == organization_id)
            )
            await cleanup_db.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["definition", "continuation"])
async def test_typed_agent_run_reauthorization_uses_original_current_foreign_grants(
    db_session, boundary,
):
    """Revoking the original user's foreign Agent grants blocks the live run."""
    from sqlalchemy import delete, inspect

    from src.models.orm.agent_runs import AgentRun
    from src.models.orm.agents import Agent
    from src.models.orm.organizations import Organization
    from src.models.orm.users import (
        Role,
        RolePermission,
        User,
        UserRole,
        UserRoleBoundary,
    )
    from src.services.authorization.agent_runtime import (
        AgentRuntimeAuthorizationError,
        authorize_agent_execution,
        authorize_agent_run_continuation,
    )
    from src.services.authorization.enforce import load_caller
    from src.services.authorization.runtime_auth import load_runtime_principal

    home = Organization(name="Original Home", created_by="authority-test")
    foreign = Organization(name="Granted Foreign", created_by="authority-test")
    actor_home = Organization(name="Actor Home", created_by="authority-test")
    db_session.add_all([home, foreign, actor_home])
    await db_session.flush()
    original = User(
        email=f"original-{uuid4().hex}@example.test",
        name="Original User",
        organization_id=home.id,
        base_role_id=USER_ROLE_ID,
    )
    actor = User(
        email=f"actor-{uuid4().hex}@example.test",
        name="Effects Actor",
        organization_id=actor_home.id,
        base_role_id=USER_ROLE_ID,
    )
    role = Role(name=f"Foreign Agent Runner {uuid4().hex}", created_by="authority-test")
    db_session.add_all([original, actor, role])
    await db_session.flush()
    db_session.add_all(
        [
            RolePermission(role_id=role.id, permission="agents.readbasic"),
            RolePermission(role_id=role.id, permission="agents.execute"),
            UserRole(user_id=original.id, role_id=role.id, assigned_by="authority-test"),
            UserRoleBoundary(
                user_id=original.id,
                role_id=role.id,
                kind="organization",
                organization_id=foreign.id,
            ),
        ]
    )
    agent = Agent(
        name="Foreign Agent",
        description="live authority test",
        system_prompt="Only current grants execute me.",
        channels=["chat"],
        access_level=AgentAccessLevel.AUTHENTICATED,
        organization_id=foreign.id,
        is_active=True,
        knowledge_sources=[],
        system_tools=[],
        created_by="authority-test",
    )
    db_session.add(agent)
    await db_session.flush()
    run = AgentRun(
        agent_id=agent.id,
        trigger_type="event",
        status="running",
        org_id=foreign.id,
        run_user_id=original.id,
        acting_user_id=actor.id,
    )
    db_session.add(run)
    await db_session.flush()

    principal = await load_runtime_principal(
        db_session,
        {"credential_kind": "agent_run", "sub": str(run.id)},
    )
    assert principal is not None
    caller = await load_caller(db_session, principal)
    assert caller.principal.user_id == actor.id
    assert caller.ctx is not None and caller.ctx.user_id == original.id

    async def authorize():
        if boundary == "definition":
            _, authorized_agent = await authorize_agent_execution(db_session, caller, agent.id)
            assert authorized_agent.id == agent.id
        else:
            authorized = await authorize_agent_run_continuation(db_session, run.id)
            assert authorized is not None and authorized.ctx is not None
            assert authorized.principal.user_id == actor.id
            assert authorized.ctx.user_id == original.id

    db_session.expire(agent, ["tools", "delegated_agents", "mcp_connections", "roles"])
    await authorize()
    graph = {"tools", "delegated_agents", "mcp_connections"}
    if boundary == "continuation":
        assert graph <= inspect(agent).unloaded
    else:
        assert not graph.intersection(inspect(agent).unloaded)
    assert "roles" not in inspect(agent).unloaded

    await db_session.execute(delete(RolePermission).where(RolePermission.role_id == role.id))
    await db_session.flush()

    with pytest.raises(AgentRuntimeAuthorizationError):
        await authorize()


@pytest.mark.asyncio
@pytest.mark.parametrize("revoked_permission", [None, "chat.execute", "ai.execute"])
async def test_agentless_continuation_reloads_authority_for_each_boundary(
    monkeypatch, revoked_permission,
):
    from src.services.authorization import agent_runtime as auth

    home, run_id = uuid4(), uuid4()
    user_id = uuid4()
    permissions = {"chat.execute", "ai.execute"}
    current = _caller(
        original_user_id=user_id, home=home,
        permissions=frozenset(permissions), boundary=home,
    )
    after = _caller(
        original_user_id=user_id, home=home,
        permissions=frozenset(permissions - {revoked_permission}), boundary=home,
    )
    run = SimpleNamespace(status="running", trigger_type="chat", agent_id=None)
    db = SimpleNamespace(get=AsyncMock(return_value=run))
    principal = object()
    principal_load = AsyncMock(return_value=principal)
    caller_load = AsyncMock(side_effect=[current, after])
    monkeypatch.setattr(auth, "load_runtime_principal", principal_load)
    monkeypatch.setattr(auth, "load_caller", caller_load)

    assert await auth.authorize_agent_run_continuation(db, run_id) is current
    if revoked_permission is None:
        assert await auth.authorize_agent_run_continuation(db, run_id) is after
    else:
        with pytest.raises(auth.AgentRuntimeAuthorizationError):
            await auth.authorize_agent_run_continuation(db, run_id)
    assert principal_load.await_count == 2
    assert caller_load.await_count == 2
    principal_load.assert_awaited_with(
        db, {"credential_kind": "agent_run", "sub": str(run_id)},
    )
    db.get.assert_awaited_with(auth.AgentRun, run_id, populate_existing=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("unavailable", ["run", "terminal", "principal", "caller"])
async def test_continuation_fails_closed_when_current_authority_is_unavailable(
    monkeypatch, unavailable,
):
    from src.services.authorization import agent_runtime as auth

    run = None if unavailable == "run" else SimpleNamespace(
        status="completed" if unavailable == "terminal" else "running",
        trigger_type="chat", agent_id=None,
    )
    db = SimpleNamespace(get=AsyncMock(return_value=run))
    principal_load = AsyncMock(return_value=None if unavailable == "principal" else object())
    caller_load = AsyncMock(return_value=SimpleNamespace(ctx=None))
    monkeypatch.setattr(auth, "load_runtime_principal", principal_load)
    monkeypatch.setattr(auth, "load_caller", caller_load)

    assert await auth.authorize_agent_run_continuation(db, uuid4()) is None
    if unavailable in {"run", "terminal"}:
        principal_load.assert_not_awaited()
    if unavailable != "caller":
        caller_load.assert_not_awaited()
