"""MCP authority is current database state, separate from acting identity."""

from uuid import uuid4

import pytest

from shared.sdk_users import set_platform_admin
from src.models.orm.agent_runs import AgentRun
from src.models.orm.organizations import Organization
from src.models.orm.users import User
from src.services.authorization.enforce import allows_permission, user_reach
from src.services.mcp_server.authorization import MCPAuthorizationError, load_mcp_caller
from src.services.mcp_server.server import MCPContext, context_from_principal


async def _person(db, *, external=False):
    organization = Organization(name=f"Contoso {uuid4().hex[:8]}", created_by="test")
    db.add(organization)
    await db.flush()
    user = User(
        email=f"mcp-{uuid4().hex[:8]}@contoso.example", organization_id=organization.id,
        is_external=external,
    )
    db.add(user)
    await db.flush()
    return user


@pytest.mark.asyncio
async def test_human_mcp_context_cannot_supply_permissions_or_organization(db_session):
    user = await _person(db_session)
    foreign_org = uuid4()
    context = MCPContext(
        user_id=user.id, org_id=foreign_org, user_email="stale@contoso.example",
        is_platform_admin=True, is_provider_org=True,
    )

    caller = await load_mcp_caller(db_session, context)

    assert caller.principal.email == user.email
    assert caller.principal.organization_id == user.organization_id
    assert not caller.is_platform_admin
    assert not allows_permission(caller, "mcp.read", None)
    assert not user_reach(caller).covers(foreign_org)


@pytest.mark.asyncio
async def test_runtime_mcp_keeps_actor_facts_and_original_authority_through_bridge(db_session):
    original = await _person(db_session, external=True)
    actor = await _person(db_session)
    await set_platform_admin(db_session, actor, True, assigned_by="test")
    run = AgentRun(
        trigger_type="chat", status="running", org_id=original.organization_id,
        run_user_id=original.id, acting_user_id=actor.id,
    )
    db_session.add(run)
    await db_session.flush()
    context = MCPContext(
        user_id=actor.id, org_id=actor.organization_id, is_platform_admin=True,
        runtime_credential_kind="agent_run", runtime_credential_id=run.id,
    )

    caller = await load_mcp_caller(db_session, context)
    bridged = context_from_principal(caller.principal)
    resolved_again = await load_mcp_caller(db_session, bridged)

    assert caller.principal.user_id == actor.id
    assert caller.principal.is_superuser
    assert caller.principal.is_external is False
    assert caller.ctx.user_id == original.id and caller.ctx.is_external
    assert not caller.is_platform_admin
    assert not allows_permission(caller, "mcp.readwrite", None)
    assert bridged.runtime_credential_kind == "agent_run"
    assert bridged.runtime_credential_id == run.id
    assert resolved_again.ctx.user_id == original.id
    assert not resolved_again.is_platform_admin

    run.status = "completed"
    await db_session.flush()
    with pytest.raises(MCPAuthorizationError, match="no longer valid"):
        await load_mcp_caller(db_session, bridged)


@pytest.mark.asyncio
@pytest.mark.parametrize("exists", [False, True])
async def test_missing_or_inactive_human_context_is_refused(db_session, exists):
    user_id = uuid4()
    if exists:
        user = await _person(db_session)
        user.is_active = False
        await db_session.flush()
        user_id = user.id
    with pytest.raises(MCPAuthorizationError, match="no longer available"):
        await load_mcp_caller(db_session, MCPContext(user_id=user_id, is_platform_admin=True))
