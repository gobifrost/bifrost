"""Authorization checks for consolidated Agent tuning routes."""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.principal import UserPrincipal
from src.models.orm.agents import Agent
from src.models.orm.organizations import Organization
from src.routers.agent_tuning import _load_agent_with_access
from src.services.authorization import AuthorizationBoundary, AuthorizationContext


def _authorization(
    *,
    organization_id,
    capabilities: set[str],
    selected_organization_id=None,
) -> AuthorizationContext:
    principal = UserPrincipal(
        user_id=uuid4(),
        email="builder@example.com",
        name="Builder",
        organization_id=organization_id,
        is_superuser=False,
        is_provider_org=False,
    )
    return AuthorizationContext(
        requester=principal,
        effective_actor=principal,
        selected_boundary=(
            AuthorizationBoundary.organization(
                selected_organization_id or organization_id
            )
            if selected_organization_id is not None or organization_id is not None
            else AuthorizationBoundary.platform()
        ),
        effective_capabilities=frozenset(capabilities),
        grant_sources=(),
    )


async def _agent(db: AsyncSession, *, organization_id) -> Agent:
    agent = Agent(
        id=uuid4(),
        name=f"tunable-{uuid4().hex[:8]}",
        description="",
        system_prompt="old",
        organization_id=organization_id,
        is_active=True,
        created_by="test@example.com",
    )
    db.add(agent)
    await db.flush()
    return agent


async def _organization(db: AsyncSession):
    organization_id = uuid4()
    db.add(
        Organization(
            id=organization_id,
            name=f"tuning-org-{organization_id.hex[:8]}",
            is_active=True,
            created_by="test@example.com",
        )
    )
    await db.flush()
    return organization_id


@pytest.mark.asyncio
async def test_tuning_read_uses_capability_when_legacy_admin_false(
    db_session: AsyncSession,
) -> None:
    organization_id = await _organization(db_session)
    agent = await _agent(db_session, organization_id=organization_id)

    loaded = await _load_agent_with_access(
        agent.id,
        db_session,
        _authorization(
            organization_id=organization_id,
            capabilities={"agents.read"},
        ),
        capability="agents.read",
    )

    assert loaded.id == agent.id


@pytest.mark.asyncio
async def test_tuning_apply_requires_readwrite_not_read(
    db_session: AsyncSession,
) -> None:
    organization_id = await _organization(db_session)
    agent = await _agent(db_session, organization_id=organization_id)

    with pytest.raises(HTTPException) as exc:
        await _load_agent_with_access(
            agent.id,
            db_session,
            _authorization(
                organization_id=organization_id,
                capabilities={"agents.read"},
            ),
            capability="agents.readwrite",
        )

    assert exc.value.status_code == 403
    assert exc.value.detail == "Missing required capability: agents.readwrite"


@pytest.mark.asyncio
async def test_provider_membership_alone_does_not_authorize_agent_tuning(
    db_session: AsyncSession,
) -> None:
    organization_id = await _organization(db_session)
    agent = await _agent(db_session, organization_id=organization_id)
    principal = UserPrincipal(
        user_id=uuid4(),
        email="provider@example.com",
        name="Provider Staff",
        organization_id=uuid4(),
        is_superuser=False,
        is_provider_org=True,
    )
    authorization = AuthorizationContext(
        requester=principal,
        effective_actor=principal,
        selected_boundary=AuthorizationBoundary.organization(organization_id),
        effective_capabilities=frozenset(),
        grant_sources=(),
    )

    with pytest.raises(HTTPException) as exc:
        await _load_agent_with_access(
            agent.id,
            db_session,
            authorization,
            capability="agents.read",
        )

    assert exc.value.status_code == 403
    assert exc.value.detail == "Missing required capability: agents.read"


@pytest.mark.asyncio
async def test_global_agent_tuning_requires_platform_boundary(
    db_session: AsyncSession,
) -> None:
    agent = await _agent(db_session, organization_id=None)

    with pytest.raises(HTTPException) as exc:
        await _load_agent_with_access(
            agent.id,
            db_session,
            _authorization(
                organization_id=uuid4(),
                capabilities={"agents.read"},
            ),
            capability="agents.read",
        )

    assert exc.value.status_code == 404
