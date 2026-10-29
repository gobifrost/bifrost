"""
Agent Repository

Repository for Agent CRUD operations with organization scoping and role-based access.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import defer, selectinload

from src.core.org_filter import OrgFilterType
from src.models.orm.agents import Agent, AgentRole
from src.models.orm.external_mcp import AgentMCPConnection, MCPConnection
from src.repositories.org_scoped import OrgScopedRepository

if TYPE_CHECKING:
    from src.services.authorization.enforce import Caller


class AgentRepository(OrgScopedRepository[Agent]):
    """
    Agent repository using OrgScopedRepository.

    Agents use the CASCADE scoping pattern for org users:
    - Org-specific agents + global (NULL org_id) agents

    Role-based access control:
    - Agents with access_level="role_based" require user to have a role assigned
    - Agents with access_level="authenticated" are accessible to any authenticated user
    """

    model = Agent
    role_table = AgentRole
    role_entity_id_column = "agent_id"

    async def list_agents(
        self,
        active_only: bool = True,
    ) -> list[Agent]:
        """List agents with cascade scoping, role-based access, and user's private agents."""
        from sqlalchemy import or_
        from src.models.enums import AgentAccessLevel

        query = select(self.model).options(
            defer(self.model.logo_data),
            defer(self.model.logo_thumbnail_data),
            selectinload(self.model.tools),
            selectinload(self.model.delegated_agents)
            .defer(Agent.logo_data)
            .defer(Agent.logo_thumbnail_data),
            selectinload(self.model.roles),
        )

        # Build scope filter: cascade (org + global) OR user's own private agents.
        cascade_conditions = []
        if self.org_id is not None:
            cascade_conditions.append(self.model.organization_id == self.org_id)
        cascade_conditions.append(self.model.organization_id.is_(None))

        private_condition = (
            (self.model.access_level == AgentAccessLevel.PRIVATE) &
            (self.model.owner_user_id == self.user_id)
        ) if self.user_id else None

        if private_condition is not None:
            query = query.where(or_(*cascade_conditions, private_condition))
        else:
            query = query.where(or_(*cascade_conditions))

        if active_only:
            query = query.where(self.model.is_active.is_(True))

        query = query.order_by(self.model.name)

        result = await self.session.execute(query)
        entities = list(result.scalars().unique().all())

        # Filter by role access for non-superusers
        if not self.is_superuser:
            accessible = []
            for entity in entities:
                if await self._can_access_entity(entity):
                    accessible.append(entity)
            return accessible

        return entities

    async def list_all_in_scope(
        self,
        filter_type: OrgFilterType = OrgFilterType.ALL,
        active_only: bool = False,
    ) -> list[Agent]:
        """
        List all agents in scope without role-based filtering.

        Used by platform admins who bypass role checks.
        Supports all filter types:
        - ALL: No org filter, show everything
        - GLOBAL_ONLY: Only agents with org_id IS NULL
        - ORG_ONLY: Only agents in the specific org (no global fallback)
        - ORG_PLUS_GLOBAL: Agents in the org + global agents

        Args:
            filter_type: How to filter by organization scope
            active_only: If True, only return active agents

        Returns:
            List of Agent ORM objects with tools eager-loaded
        """
        query = select(self.model).options(
            defer(self.model.logo_data),
            defer(self.model.logo_thumbnail_data),
            selectinload(self.model.tools),
            selectinload(self.model.delegated_agents)
            .defer(Agent.logo_data)
            .defer(Agent.logo_thumbnail_data),
            selectinload(self.model.roles),
        )

        # Apply org filtering based on filter type
        if filter_type == OrgFilterType.ALL:
            # No org filter - show everything
            pass
        elif filter_type == OrgFilterType.GLOBAL_ONLY:
            # Only global agents (org_id IS NULL)
            query = query.where(self.model.organization_id.is_(None))
        elif filter_type == OrgFilterType.ORG_ONLY:
            # Only the specific org, NO global fallback
            if self.org_id is not None:
                query = query.where(self.model.organization_id == self.org_id)
            else:
                # Edge case: ORG_ONLY with no org_id - return nothing
                query = query.where(self.model.id.is_(None))
        elif filter_type == OrgFilterType.ORG_PLUS_GLOBAL:
            # Cascade scope: org + global
            query = self._apply_cascade_scope(query)

        if active_only:
            query = query.where(self.model.is_active.is_(True))

        query = query.order_by(self.model.name)

        result = await self.session.execute(query)
        return list(result.scalars().unique().all())

    async def get_agent(self, agent_id: UUID) -> Agent | None:
        """
        Get agent by ID with relationships loaded.

        Note: This is a raw lookup without org scoping or role checks.
        Access control should be performed at the caller level.

        Args:
            agent_id: Agent UUID

        Returns:
            Agent ORM object or None if not found
        """
        result = await self.session.execute(
            select(self.model)
            .options(
                selectinload(self.model.tools),
                selectinload(self.model.delegated_agents),
                selectinload(self.model.delegated_agents).selectinload(Agent.roles),
                selectinload(self.model.roles),
                selectinload(self.model.owner),
                selectinload(self.model.mcp_connections),
            )
            .where(self.model.id == agent_id)
        )
        return result.scalar_one_or_none()

    async def get_agent_with_access_check(self, agent_id: UUID) -> Agent | None:
        """
        Get agent by ID with access check, honoring the platform-admin bypass.

        Matches the ``OrgScopedRepository.get(id=...)`` contract:
        - Superusers get the entity regardless of its organization scope.
        - Regular users must find the entity in their own org or global scope,
          AND pass the role-based access check.

        Args:
            agent_id: Agent UUID

        Returns:
            Agent ORM object if found and accessible, None otherwise
        """
        query = (
            select(self.model)
            .options(
                selectinload(self.model.tools),
                selectinload(self.model.delegated_agents),
                selectinload(self.model.roles),
                selectinload(self.model.owner),
                selectinload(self.model.mcp_connections),
            )
            .where(self.model.id == agent_id)
        )

        # Superuser: no scoping. IDs are globally unique; trust the ID lookup.
        if self.is_superuser:
            result = await self.session.execute(query)
            return result.scalar_one_or_none()

        # Regular user: cascade scope (org-specific first, then global) + role check.
        if self.org_id is not None:
            org_query = query.where(self.model.organization_id == self.org_id)
            result = await self.session.execute(org_query)
            entity = result.scalar_one_or_none()
            if entity:
                if await self._can_access_entity(entity):
                    return entity
                return None

        global_query = query.where(self.model.organization_id.is_(None))
        result = await self.session.execute(global_query)
        entity = result.scalar_one_or_none()

        if entity and await self._can_access_entity(entity):
            return entity
        return None

    async def set_mcp_connection_grants(
        self,
        agent_id: UUID,
        connection_ids: Iterable[UUID],
        *,
        caller: Caller,
        granted_by: UUID | None,
    ) -> list[UUID]:
        """Replace the agent's full set of MCP connection grants.

        Mirrors the ``agent_tools`` delete-all-then-insert pattern: every
        existing grant for the agent is removed and the supplied
        ``connection_ids`` are re-inserted within the same transaction.
        Caller is responsible for committing.

        The caller must be allowed to write the parent Agent and must reach
        each connection's actual organization. Connections must belong to the
        Agent's organization; invalid IDs are refused before existing grants
        are changed.

        Args:
            agent_id: Agent whose grants are being replaced.
            connection_ids: New set of connection UUIDs to grant.
            caller: Original user authority for the Agent and connection.
            granted_by: Acting User UUID recorded on each new row (audit trail);
                ``None`` is allowed for system-driven syncs (manifest
                import, backfill jobs).

        Returns:
            The requested connection IDs after exact organization validation.
        """
        # Fetch the parent before changing any grants.
        agent_result = await self.session.execute(
            select(Agent).where(Agent.id == agent_id)
        )
        agent = agent_result.scalar_one_or_none()
        if agent is None:
            raise ValueError(f"Agent {agent_id} not found")

        from fastapi import HTTPException, status
        from src.services.agent_write_policy import can_configure_agent
        from src.services.authorization.enforce import user_reach

        if not can_configure_agent(caller, agent):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You don't have permission to manage agents",
            )

        connection_id_list = list(dict.fromkeys(connection_ids))  # dedup, preserve order
        connections = await self._validated_mcp_connections(agent, connection_id_list)
        for connection in connections:
            if not user_reach(caller).covers(connection.organization_id):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You don't have permission to manage agents",
                )
        return await self._replace_mcp_connection_grants(
            agent_id, connection_id_list, granted_by=granted_by
        )

    async def set_trusted_manifest_mcp_connection_grants(
        self,
        agent_id: UUID,
        connection_ids: Iterable[UUID],
        *,
        granted_by: UUID | None,
    ) -> list[UUID]:
        """Persist a deploy/import-authorized Agent connection association.

        Manifest and Solution deployment authorization happens at their parent
        operation. This narrow internal entry point intentionally accepts no
        request caller, but it retains the exact Agent/connection organization
        invariant shared with the public setter.
        """
        agent = (
            await self.session.execute(select(Agent).where(Agent.id == agent_id))
        ).scalar_one_or_none()
        if agent is None:
            raise ValueError(f"Agent {agent_id} not found")
        connection_id_list = list(dict.fromkeys(connection_ids))
        await self._validated_mcp_connections(agent, connection_id_list)
        return await self._replace_mcp_connection_grants(
            agent_id, connection_id_list, granted_by=granted_by
        )

    async def _validated_mcp_connections(
        self, agent: Agent, connection_ids: list[UUID]
    ) -> list[MCPConnection]:
        """Return exact-org connection rows or refuse before grant mutation."""
        from fastapi import HTTPException, status

        if not connection_ids:
            return []
        if agent.organization_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Global Agents cannot be granted organization MCP connections",
            )
        connections = (
            await self.session.execute(
                select(MCPConnection).where(MCPConnection.id.in_(connection_ids))
            )
        ).scalars().all()
        by_id = {connection.id: connection for connection in connections}
        if any(
            (connection := by_id.get(connection_id)) is None
            or connection.organization_id != agent.organization_id
            for connection_id in connection_ids
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="MCP connection does not belong to the Agent organization",
            )
        return [by_id[connection_id] for connection_id in connection_ids]

    async def _replace_mcp_connection_grants(
        self,
        agent_id: UUID,
        connection_ids: list[UUID],
        *,
        granted_by: UUID | None,
    ) -> list[UUID]:
        """Replace validated association rows without changing their policy."""
        await self.session.execute(
            delete(AgentMCPConnection).where(AgentMCPConnection.agent_id == agent_id)
        )
        for connection_id in connection_ids:
            self.session.add(
                AgentMCPConnection(
                    agent_id=agent_id,
                    connection_id=connection_id,
                    granted_by=granted_by,
                )
            )
        await self.session.flush()
        return connection_ids
