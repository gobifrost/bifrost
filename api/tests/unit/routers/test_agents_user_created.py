"""
Tests for user-created (private) agent access control.

Tests the private-Agent resource gate layered after boundary capabilities.
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4, UUID

from fastapi import HTTPException
from src.core.principal import UserPrincipal
from src.models.enums import AgentAccessLevel
from src.services.authorization import AuthorizationBoundary, AuthorizationContext


# ==================== Helper factories ====================

def make_agent(
    agent_id: UUID | None = None,
    name: str = "Test Agent",
    access_level: AgentAccessLevel = AgentAccessLevel.PRIVATE,
    owner_user_id: UUID | None = None,
    organization_id: UUID | None = None,
    is_active: bool = True,
):
    """Create a mock agent ORM object."""
    agent = MagicMock()
    agent.id = agent_id or uuid4()
    agent.name = name
    agent.access_level = access_level
    agent.owner_user_id = owner_user_id
    agent.organization_id = organization_id
    agent.solution_id = None
    agent.is_active = is_active
    agent.description = "Test"
    agent.system_prompt = "Test prompt"
    agent.channels = ["chat"]
    agent.tools = []
    agent.delegated_agents = []
    agent.roles = []
    agent.knowledge_sources = []
    agent.system_tools = []
    agent.llm_model = None
    agent.llm_max_tokens = None
    agent.created_by = "admin@test.com"
    agent.created_at = datetime.now(timezone.utc)
    agent.updated_at = datetime.now(timezone.utc)
    agent.owner = None
    return agent


# ==================== Access Control Tests ====================


class TestPrivateAgentAccessLevel:
    """Test _can_access_entity with private access level."""

    @pytest.mark.asyncio
    async def test_private_agent_accessible_by_owner(self):
        """Private agent is accessible by its owner."""
        from src.repositories.org_scoped import OrgScopedRepository
        from src.models.orm.agents import Agent, AgentRole

        user_id = uuid4()
        agent = make_agent(owner_user_id=user_id, access_level=AgentAccessLevel.PRIVATE)

        repo = OrgScopedRepository.__new__(OrgScopedRepository)
        repo.session = AsyncMock()
        repo.org_id = uuid4()
        repo.user_id = user_id
        repo.is_superuser = False
        repo.is_external = False
        repo.model = Agent
        repo.role_table = AgentRole
        repo.role_entity_id_column = "agent_id"

        result = await repo._can_access_entity(agent)
        assert result is True

    @pytest.mark.asyncio
    async def test_private_agent_not_accessible_by_other_user(self):
        """Private agent is not accessible by a different user."""
        from src.repositories.org_scoped import OrgScopedRepository
        from src.models.orm.agents import Agent, AgentRole

        owner_id = uuid4()
        other_user_id = uuid4()
        agent = make_agent(owner_user_id=owner_id, access_level=AgentAccessLevel.PRIVATE)

        repo = OrgScopedRepository.__new__(OrgScopedRepository)
        repo.session = AsyncMock()
        repo.org_id = uuid4()
        repo.user_id = other_user_id
        repo.is_superuser = False
        repo.is_external = False
        repo.model = Agent
        repo.role_table = AgentRole
        repo.role_entity_id_column = "agent_id"

        result = await repo._can_access_entity(agent)
        assert result is False

    @pytest.mark.asyncio
    async def test_private_agent_accessible_by_superuser(self):
        """Private agents are accessible by superusers."""
        from src.repositories.org_scoped import OrgScopedRepository
        from src.models.orm.agents import Agent, AgentRole

        agent = make_agent(
            owner_user_id=uuid4(),
            access_level=AgentAccessLevel.PRIVATE,
        )

        repo = OrgScopedRepository.__new__(OrgScopedRepository)
        repo.session = AsyncMock()
        repo.org_id = uuid4()
        repo.user_id = uuid4()
        repo.is_superuser = True
        repo.is_external = False
        repo.model = Agent
        repo.role_table = AgentRole
        repo.role_entity_id_column = "agent_id"

        result = await repo._can_access_entity(agent)
        assert result is True


def _authorization(
    *,
    user_id: UUID,
    organization_id: UUID,
    capabilities: set[str],
    boundary: AuthorizationBoundary | None = None,
) -> AuthorizationContext:
    principal = UserPrincipal(
        user_id=user_id,
        email="user@test.com",
        organization_id=organization_id,
    )
    return AuthorizationContext(
        requester=principal,
        effective_actor=principal,
        selected_boundary=boundary or AuthorizationBoundary.organization(organization_id),
        effective_capabilities=frozenset(capabilities),
        grant_sources=(),
    )


class TestAgentMutationAuthorization:
    def test_owner_with_capability_can_edit_private_agent(self):
        from src.routers.agents import _require_agent_mutation

        user_id = uuid4()
        organization_id = uuid4()
        authorization = _authorization(
            user_id=user_id,
            organization_id=organization_id,
            capabilities={"agents.readwrite"},
        )
        agent = make_agent(
            owner_user_id=user_id,
            organization_id=organization_id,
        )

        _require_agent_mutation(authorization, agent)

    def test_other_users_private_agent_is_denied(self):
        from src.routers.agents import _require_agent_mutation

        organization_id = uuid4()
        authorization = _authorization(
            user_id=uuid4(),
            organization_id=organization_id,
            capabilities={"agents.readwrite"},
        )
        agent = make_agent(
            owner_user_id=uuid4(),
            organization_id=organization_id,
        )

        with pytest.raises(HTTPException) as exc:
            _require_agent_mutation(authorization, agent)
        assert exc.value.status_code == 403

    def test_wrong_organization_boundary_is_denied(self):
        from src.routers.agents import _require_agent_mutation

        user_id = uuid4()
        authorization = _authorization(
            user_id=user_id,
            organization_id=uuid4(),
            capabilities={"agents.readwrite"},
        )
        agent = make_agent(
            owner_user_id=user_id,
            organization_id=uuid4(),
        )

        with pytest.raises(HTTPException) as exc:
            _require_agent_mutation(authorization, agent)
        assert exc.value.status_code == 409


class TestAgentRepositoryAuthorizationBridge:
    def test_exact_boundary_repository_uses_selected_org_without_bypass(self):
        from src.routers.agents import _agent_repository

        organization_id = uuid4()
        authorization = _authorization(
            user_id=uuid4(),
            organization_id=organization_id,
            capabilities={"agents.read"},
        )

        repo = _agent_repository(AsyncMock(), authorization)

        assert repo.org_id == organization_id
        assert repo.bypass_resource_roles is False

    def test_platform_superuser_repository_uses_global_with_bypass(self):
        from shared.authorization_scopes import PLATFORM_SUPERUSER_SCOPE
        from src.routers.agents import _agent_repository

        authorization = _authorization(
            user_id=uuid4(),
            organization_id=uuid4(),
            capabilities={PLATFORM_SUPERUSER_SCOPE},
            boundary=AuthorizationBoundary.platform(),
        )

        repo = _agent_repository(AsyncMock(), authorization)

        assert repo.org_id is None
        assert repo.bypass_resource_roles is True
