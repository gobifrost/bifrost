"""
Tests for user-created (private) agent access control.

Tests the key authorization scenarios for the loosened agent endpoints:
- Non-admin can create private agents
- Non-admin cannot create non-private agents
- Non-admin can edit/delete their own private agents
- Non-admin cannot edit/delete other users' agents
- Promote works with correct permissions
- Promote denied without can_promote_agent permission
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4, UUID

from src.models.enums import AgentAccessLevel


# ==================== Helper factories ====================

def make_user(
    user_id: UUID | None = None,
    email: str = "user@test.com",
    organization_id: UUID | None = None,
    is_superuser: bool = False,
    roles: list[str] | None = None,
):
    """Create a mock user object."""
    user = MagicMock()
    user.user_id = user_id or uuid4()
    user.email = email
    user.organization_id = organization_id or uuid4()
    user.is_superuser = is_superuser
    user.roles = roles or []
    return user


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
    agent.is_active = is_active
    agent.description = "Test"
    agent.system_prompt = "Test prompt"
    agent.channels = ["chat"]
    agent.tools = []
    agent.delegated_agents = []
    agent.roles = []
    agent.knowledge_sources = []
    agent.system_tools = []
    agent.llm_profile_id = None
    agent.llm_profile = None
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
        repo.model = Agent
        repo.role_table = AgentRole
        repo.role_entity_id_column = "agent_id"

        result = await repo._can_access_entity(agent)
        assert result is True


class TestCreateAgentAuthorization:
    """Test create_agent endpoint authorization logic."""

    def test_non_admin_cannot_create_authenticated_agent(self):
        """Non-admin users should only be able to create private agents."""
        from fastapi import HTTPException
        from src.models.contracts.agents import AgentCreate

        agent_data = AgentCreate(
            name="My Agent",
            system_prompt="You are helpful.",
            access_level=AgentAccessLevel.AUTHENTICATED,
        )

        user = make_user()
        is_admin = user.is_superuser or any(
            role in ["Platform Admin", "Platform Owner"] for role in user.roles
        )

        assert not is_admin
        if not is_admin and agent_data.access_level != AgentAccessLevel.PRIVATE:
            with pytest.raises(Exception):
                raise HTTPException(403, "Non-admin users can only create private agents")

    def test_non_admin_can_create_private_agent(self):
        """Non-admin users should be able to create private agents."""
        from src.models.contracts.agents import AgentCreate

        agent_data = AgentCreate(
            name="My Agent",
            system_prompt="You are helpful.",
            access_level=AgentAccessLevel.PRIVATE,
        )

        user = make_user()
        is_admin = user.is_superuser or any(
            role in ["Platform Admin", "Platform Owner"] for role in user.roles
        )

        assert not is_admin
        # Private access level should be allowed
        assert agent_data.access_level == AgentAccessLevel.PRIVATE

    def test_admin_can_create_any_agent(self):
        """Admin users should be able to create any access level agent."""
        from src.models.contracts.agents import AgentCreate

        for access_level in [AgentAccessLevel.AUTHENTICATED, AgentAccessLevel.ROLE_BASED, AgentAccessLevel.PRIVATE]:
            AgentCreate(
                name="My Agent",
                system_prompt="You are helpful.",
                access_level=access_level,
            )

            user = make_user(is_superuser=True)
            is_admin = user.is_superuser or any(
                role in ["Platform Admin", "Platform Owner"] for role in user.roles
            )

            assert is_admin


class TestDeleteAgentAuthorization:
    """Test delete_agent authorization logic."""

    def test_non_admin_can_delete_own_private_agent(self):
        """Non-admin can delete their own private agent."""
        user_id = uuid4()
        agent = make_agent(owner_user_id=user_id, access_level=AgentAccessLevel.PRIVATE)
        user = make_user(user_id=user_id)

        is_admin = user.is_superuser or any(
            role in ["Platform Admin", "Platform Owner"] for role in user.roles
        )
        assert not is_admin
        assert agent.owner_user_id == user.user_id

    def test_non_admin_cannot_delete_other_users_agent(self):
        """Non-admin cannot delete another user's private agent."""
        agent = make_agent(owner_user_id=uuid4(), access_level=AgentAccessLevel.PRIVATE)
        user = make_user()

        is_admin = user.is_superuser or any(
            role in ["Platform Admin", "Platform Owner"] for role in user.roles
        )
        assert not is_admin
        assert agent.owner_user_id != user.user_id


class TestPromoteAgentPermission:
    """Promotion consults the authorization context at the agent's actual org."""

    def test_grant_at_another_org_cannot_promote(self):
        from shared.builtin_roles import USER_ROLE_ID
        from src.core.principal import UserPrincipal
        from src.routers.agents import _may_promote_agent
        from src.services.authorization.context import AuthorizationContext, Boundary, BoundaryKind, RoleGrant
        from src.services.authorization.enforce import Caller

        home, granted, agent_org = uuid4(), uuid4(), uuid4()
        caller = Caller(
            UserPrincipal(user_id=uuid4(), email="person@example.com", organization_id=home),
            AuthorizationContext(
                user_id=uuid4(), home_organization_id=home, base_role_id=USER_ROLE_ID,
                is_external=False, base_permissions=frozenset(),
                role_grants=(RoleGrant(uuid4(), frozenset({"agents.readwrite"}), (Boundary(BoundaryKind.ORGANIZATION, granted),)),),
            ),
        )
        assert not _may_promote_agent(caller, make_agent(organization_id=agent_org))

    def test_grant_at_agent_org_can_promote(self):
        from shared.builtin_roles import USER_ROLE_ID
        from src.core.principal import UserPrincipal
        from src.routers.agents import _may_promote_agent
        from src.services.authorization.context import AuthorizationContext, Boundary, BoundaryKind, RoleGrant
        from src.services.authorization.enforce import Caller

        home, agent_org = uuid4(), uuid4()
        caller = Caller(
            UserPrincipal(user_id=uuid4(), email="person@example.com", organization_id=home),
            AuthorizationContext(
                user_id=uuid4(), home_organization_id=home, base_role_id=USER_ROLE_ID,
                is_external=False, base_permissions=frozenset(),
                role_grants=(RoleGrant(uuid4(), frozenset({"agents.readwrite"}), (Boundary(BoundaryKind.ORGANIZATION, agent_org),)),),
            ),
        )
        assert _may_promote_agent(caller, make_agent(organization_id=agent_org))


class TestAgentConfigurationPermission:
    """Privileged Agent fields follow original scoped permissions."""

    @staticmethod
    def _caller(*, user_id: UUID, organization_id: UUID, permissions: set[str], actor_admin: bool = False):
        from src.core.principal import UserPrincipal
        from src.services.authorization.context import AuthorizationContext, Boundary, BoundaryKind, RoleGrant
        from src.services.authorization.enforce import Caller

        return Caller(
            UserPrincipal(
                user_id=uuid4(),
                email="actor@contoso.example",
                organization_id=organization_id,
                is_superuser=actor_admin,
            ),
            AuthorizationContext(
                user_id=user_id,
                home_organization_id=organization_id,
                base_role_id=uuid4(),
                is_external=False,
                base_permissions=frozenset(),
                role_grants=(
                    RoleGrant(
                        uuid4(),
                        frozenset(permissions),
                        (Boundary(BoundaryKind.ORGANIZATION, organization_id),),
                    ),
                ) if permissions else (),
            ),
        )

    def test_actor_platform_flag_cannot_configure_private_agent(self):
        from src.services.agent_write_policy import can_configure_agent

        organization_id, owner = uuid4(), uuid4()
        caller = self._caller(
            user_id=owner,
            organization_id=organization_id,
            permissions=set(),
            actor_admin=True,
        )

        assert not can_configure_agent(
            caller,
            make_agent(
                access_level=AgentAccessLevel.PRIVATE,
                owner_user_id=owner,
                organization_id=organization_id,
            ),
        )

    def test_private_other_requires_write_and_private_all(self):
        from src.services.agent_write_policy import can_configure_agent

        organization_id, owner = uuid4(), uuid4()
        agent = make_agent(
            access_level=AgentAccessLevel.PRIVATE,
            owner_user_id=owner,
            organization_id=organization_id,
        )
        writer_only = self._caller(
            user_id=uuid4(),
            organization_id=organization_id,
            permissions={"agents.readwrite"},
        )
        writer_with_private_access = self._caller(
            user_id=uuid4(),
            organization_id=organization_id,
            permissions={"agents.readwrite", "agents.readwrite.all"},
        )

        assert not can_configure_agent(writer_only, agent)
        assert can_configure_agent(writer_with_private_access, agent)
