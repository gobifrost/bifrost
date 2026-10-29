"""E2E: agent-run visibility is own-runs-only, not org-wide.

A non-bypass caller may list and read only the agent runs THEY started —
never another user's run, even in the same org. Regression coverage for
the fix to ``agent_run_visibility_conditions``, which previously let any
org member see every other org member's non-delegation runs.
"""

from datetime import datetime, timezone
from typing import AsyncGenerator
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.enums import AgentAccessLevel
from src.core.principal import UserPrincipal
from src.models.orm.agent_runs import AgentRun
from src.models.orm.agents import Agent
from src.services.authorization.context import (
    AuthorizationContext,
    Boundary,
    BoundaryKind,
    RoleGrant,
)
from src.services.authorization.enforce import Caller
from src.services.execution.agent_run_access import agent_run_visibility_conditions

pytestmark = pytest.mark.e2e


def _principal(user) -> UserPrincipal:
    """Adapt an E2E fixture user to the verified principal callers require."""
    assert user.user_id is not None
    return UserPrincipal(
        user_id=user.user_id,
        email=user.email,
        name=user.name,
        organization_id=user.organization_id,
        is_superuser=user.is_superuser,
    )


@pytest_asyncio.fixture
async def alice_and_bob_runs(
    db_session: AsyncSession,
    org1: dict,
    alice_user,
    bob_user,
) -> AsyncGenerator[dict, None]:
    agent = Agent(
        id=uuid4(),
        name=f"Visibility Test Agent {uuid4().hex[:8]}",
        description="agent-run-visibility-test",
        system_prompt="test",
        channels=["chat"],
        access_level=AgentAccessLevel.AUTHENTICATED,
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

    alice_run = AgentRun(
        id=uuid4(),
        agent_id=agent.id,
        trigger_type="api",
        status="completed",
        org_id=UUID(org1["id"]),
        caller_user_id=str(alice_user.user_id),
    )
    bob_run = AgentRun(
        id=uuid4(),
        agent_id=agent.id,
        trigger_type="api",
        status="completed",
        org_id=UUID(org1["id"]),
        caller_user_id=str(bob_user.user_id),
    )
    db_session.add(alice_run)
    db_session.add(bob_run)
    await db_session.commit()

    try:
        yield {"agent": agent, "alice_run": alice_run, "bob_run": bob_run}
    finally:
        await db_session.execute(
            delete(AgentRun).where(AgentRun.id.in_([alice_run.id, bob_run.id]))
        )
        await db_session.execute(delete(Agent).where(Agent.id == agent.id))
        await db_session.commit()


def test_user_cannot_get_another_users_run_in_same_org(
    e2e_client, alice_user, bob_user, alice_and_bob_runs
):
    """Alice cannot fetch Bob's run by id, even though they share an org."""
    response = e2e_client.get(
        f"/api/agent-runs/{alice_and_bob_runs['bob_run'].id}",
        headers=alice_user.headers,
    )
    assert response.status_code == 404, response.text


def test_user_sees_own_run(e2e_client, alice_user, alice_and_bob_runs):
    response = e2e_client.get(
        f"/api/agent-runs/{alice_and_bob_runs['alice_run'].id}",
        headers=alice_user.headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["id"] == str(alice_and_bob_runs["alice_run"].id)


def test_user_cannot_list_another_users_run_in_same_org(
    e2e_client, alice_user, alice_and_bob_runs
):
    """Alice's list of runs for this agent includes her own run, never Bob's."""
    response = e2e_client.get(
        "/api/agent-runs",
        params={"agent_id": str(alice_and_bob_runs["agent"].id)},
        headers=alice_user.headers,
    )
    assert response.status_code == 200, response.text
    run_ids = {item["id"] for item in response.json()["items"]}
    assert str(alice_and_bob_runs["alice_run"].id) in run_ids
    assert str(alice_and_bob_runs["bob_run"].id) not in run_ids


def test_platform_admin_sees_both_runs(
    e2e_client, platform_admin, alice_and_bob_runs
):
    response = e2e_client.get(
        "/api/agent-runs",
        params={"agent_id": str(alice_and_bob_runs["agent"].id)},
        headers=platform_admin.headers,
    )
    assert response.status_code == 200, response.text
    run_ids = {item["id"] for item in response.json()["items"]}
    assert str(alice_and_bob_runs["alice_run"].id) in run_ids
    assert str(alice_and_bob_runs["bob_run"].id) in run_ids


@pytest.mark.asyncio
async def test_unrestricted_read_all_sees_another_users_run(
    db_session, alice_user, alice_and_bob_runs
):
    """An unrestricted Read All branch is SQL ``true``, not own-only."""
    from shared.builtin_roles import (
        PLATFORM_ADMIN_ROLE_ID,
        USER_ROLE_ID,
        WILDCARD_PERMISSION,
    )

    caller = Caller(
        _principal(alice_user),
        AuthorizationContext(
            user_id=alice_user.user_id,
            home_organization_id=alice_and_bob_runs["alice_run"].org_id,
            base_role_id=USER_ROLE_ID,
            is_external=False,
            base_permissions=frozenset(),
            role_grants=(
                RoleGrant(
                    PLATFORM_ADMIN_ROLE_ID,
                    frozenset({WILDCARD_PERMISSION}),
                    (Boundary(BoundaryKind.PLATFORM),),
                ),
            ),
        ),
    )
    rows = (
        await db_session.execute(
            select(AgentRun.id).where(*agent_run_visibility_conditions(caller))
        )
    ).scalars().all()
    assert alice_and_bob_runs["bob_run"].id in rows


@pytest.mark.asyncio
async def test_read_all_never_widens_original_user_reach(
    db_session, org1, alice_user, bob_user, alice_and_bob_runs
):
    """A role's Read All grant remains confined to its original boundary."""
    from shared.builtin_roles import USER_ROLE_ID
    from src.models.orm.organizations import Organization

    foreign_org = Organization(name=f"Fabrikam-{uuid4().hex[:8]}", created_by="test")
    db_session.add(foreign_org)
    await db_session.flush()
    foreign_run = AgentRun(
        id=uuid4(),
        agent_id=alice_and_bob_runs["agent"].id,
        trigger_type="api",
        status="completed",
        org_id=foreign_org.id,
        caller_user_id=str(bob_user.user_id),
    )
    db_session.add(foreign_run)
    await db_session.flush()

    org_id = UUID(org1["id"])
    caller = Caller(
        _principal(alice_user),
        AuthorizationContext(
            user_id=alice_user.user_id,
            home_organization_id=org_id,
            base_role_id=USER_ROLE_ID,
            is_external=False,
            base_permissions=frozenset(),
            role_grants=(
                RoleGrant(
                    uuid4(),
                    frozenset({"agentruns.read.all"}),
                    (Boundary(BoundaryKind.ORGANIZATION, org_id),),
                ),
            ),
        ),
    )
    rows = (
        await db_session.execute(
            select(AgentRun.id).where(*agent_run_visibility_conditions(caller))
        )
    ).scalars().all()
    assert alice_and_bob_runs["bob_run"].id in rows
    assert foreign_run.id not in rows
