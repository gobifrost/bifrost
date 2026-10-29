"""Fresh callers cannot retain revoked authority or adopt a runtime actor."""

from uuid import uuid4

import pytest
from sqlalchemy import delete, update

from shared.builtin_roles import USER_ROLE_ID
from src.core.constants import SYSTEM_USER_UUID
from src.core.principal import UserPrincipal
from src.models.orm.agent_runs import AgentRun
from src.models.orm.agents import Agent
from src.models.orm.organizations import Organization
from src.models.orm.users import Role, RolePermission, User, UserRole, UserRoleBoundary
from src.services.authorization.callers import CallerUnavailable, load_human_caller, refresh_caller
from src.services.authorization.enforce import Caller, allows_permission, load_caller
from src.services.authorization.runtime_auth import load_runtime_principal


async def _person(db):
    organization = Organization(name="Contoso", created_by="test")
    db.add(organization)
    await db.flush()
    user = User(email=f"caller-{uuid4().hex}@contoso.example", organization_id=organization.id, base_role_id=USER_ROLE_ID)
    db.add(user)
    await db.flush()
    return user


@pytest.mark.asyncio
async def test_refresh_observes_permission_revocation_during_a_runtime(db_session):
    original, actor = await _person(db_session), await _person(db_session)
    role = Role(name=f"Contoso Reader {uuid4().hex}", created_by="test")
    db_session.add(role)
    await db_session.flush()
    db_session.add_all([
        RolePermission(role_id=role.id, permission="mcp.readwrite"),
        UserRole(user_id=original.id, role_id=role.id, assigned_by="test"),
        UserRoleBoundary(user_id=original.id, role_id=role.id, kind="platform"),
    ])
    run = AgentRun(trigger_type="chat", status="running", org_id=original.organization_id, run_user_id=original.id, acting_user_id=actor.id)
    db_session.add(run)
    await db_session.flush()
    principal = await load_runtime_principal(db_session, {"credential_kind": "agent_run", "sub": str(run.id)})
    assert principal is not None
    old_caller = await load_caller(db_session, principal)
    assert allows_permission(old_caller, "mcp.readwrite", None)

    await db_session.execute(delete(RolePermission).where(RolePermission.role_id == role.id))
    await db_session.flush()
    current = await refresh_caller(db_session, old_caller)
    assert current.ctx.user_id == original.id
    assert current.principal.user_id == actor.id
    assert not allows_permission(current, "mcp.readwrite", None)

    # Keep the loaded row stale, as a long-lived caller's session can be when
    # another process terminalizes its run.
    await db_session.execute(
        update(AgentRun).where(AgentRun.id == run.id).values(status="completed")
        .execution_options(synchronize_session=False)
    )
    assert run.status == "running"
    with pytest.raises(CallerUnavailable, match="no longer valid"):
        await refresh_caller(db_session, old_caller)


@pytest.mark.asyncio
@pytest.mark.parametrize("locator", ["engine_execution_id", "service_id", "service_attempt_id"])
async def test_untyped_runtime_cannot_refresh_as_its_human_actor(db_session, locator):
    actor = await _person(db_session)
    principal = UserPrincipal(user_id=actor.id, email=actor.email, organization_id=actor.organization_id, **{locator: str(uuid4())})
    assert (await load_caller(db_session, principal)).ctx is None
    with pytest.raises(CallerUnavailable, match="typed runtime"):
        await refresh_caller(db_session, Caller(principal, None))


@pytest.mark.asyncio
async def test_system_account_is_never_a_human_caller(db_session):
    with pytest.raises(CallerUnavailable, match="no longer available"):
        await load_human_caller(db_session, SYSTEM_USER_UUID)


@pytest.mark.asyncio
async def test_agent_runtime_refresh_loads_parent_without_an_implicit_async_query(db_session):
    original = await _person(db_session)
    agent = Agent(
        name="Contoso Agent", system_prompt="Help with requests", created_by="test",
        organization_id=original.organization_id,
    )
    db_session.add(agent)
    await db_session.flush()
    run = AgentRun(
        trigger_type="chat", status="running", org_id=original.organization_id,
        agent_id=agent.id, run_user_id=original.id, acting_user_id=original.id,
    )
    db_session.add(run)
    await db_session.flush()
    # Force the parent out of the identity map, matching a new MCP/HTTP
    # request instead of the producer's already-loaded relationship.
    db_session.expunge(agent)
    db_session.expire(run, ["agent"])

    principal = await load_runtime_principal(db_session, {"credential_kind": "agent_run", "sub": str(run.id)})
    assert principal is not None
    assert principal.runtime_authority.credential_id == run.id
    assert principal.runtime_authority.solution_id is None
