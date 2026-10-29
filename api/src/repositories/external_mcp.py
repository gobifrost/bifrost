"""
External MCP repositories.

Database operations for ``mcp_servers`` (org-scoped, cascade — global +
per-org), ``mcp_connections`` (strict per-org only), ``mcp_connection_tools``
(scoped by connection_id), and ``user_mcp_credentials`` (scoped by user_id).

The cascade pattern on ``MCPServerRepository`` mirrors ``AgentRepository``:
platform-level templates (organization_id IS NULL) are visible to every org,
and an org may shadow them with a per-org template by inserting a non-NULL
row. ``MCPConnectionRepository`` is strict org-only — connections never have
a global fallback because they carry per-org secrets.
"""

import logging
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload, with_loader_criteria

from shared.scope_resolver import UNSET, RequestedScope
from src.models.orm.external_mcp import (
    MCPConnection,
    MCPConnectionTool,
    MCPServer,
    UserMCPCredential,
)
from src.repositories.org_scoped import OrgScopedRepository
from src.services.authorization.reach import OrgReach

logger = logging.getLogger(__name__)


class MCPServerRepository(OrgScopedRepository[MCPServer]):
    """Repository for MCP server templates with cascade scoping.

    Cascade scoping: org-specific templates > global (NULL organization_id)
    templates. Used by both platform admins (filter_type=ALL) and org admins
    (cascade via list/get).
    """

    model = MCPServer
    role_table = None  # Server templates have no role-based access

    async def list_servers(
        self,
        reach: OrgReach,
        *,
        selected_organization_id: RequestedScope = UNSET,
        active_only: bool = True,
    ) -> list[MCPServer]:
        """List templates inside the caller's SQL reach.

        Global templates are shared defaults when an organization is selected;
        explicit Global selects Global alone.  Collection reads without a
        selected scope include Global only when the permission reach includes
        at least one organization (or Global itself).
        """
        query = select(MCPServer)
        reach_filter = reach.where(MCPServer.organization_id)
        if selected_organization_id is UNSET:
            if (
                not reach.covers(None)
                and (reach.everything or reach.managed or reach.organization_ids)
            ):
                reach_filter = or_(reach_filter, MCPServer.organization_id.is_(None))
        elif selected_organization_id is None:
            reach_filter = MCPServer.organization_id.is_(None)
        else:
            if reach_filter is not None:
                reach_filter = or_(reach_filter, MCPServer.organization_id.is_(None))
        if reach_filter is not None:
            query = query.where(reach_filter)
        if selected_organization_id is not UNSET and selected_organization_id is not None:
            query = query.where(
                or_(
                    MCPServer.organization_id == selected_organization_id,
                    MCPServer.organization_id.is_(None),
                )
            )
        if active_only:
            query = query.where(MCPServer.is_active.is_(True))
        query = query.order_by(MCPServer.name)
        result = await self.session.execute(query)
        return list(result.scalars().unique().all())

    async def get_server(
        self, server_id: UUID, *, connection_reach: OrgReach | None = None
    ) -> MCPServer | None:
        """Load a template and, when supplied, only reachable connections."""
        query = select(MCPServer).where(MCPServer.id == server_id).options(
            selectinload(MCPServer.connections).selectinload(MCPConnection.tools),
        )
        if connection_reach is not None:
            connection_filter = connection_reach.where(MCPConnection.organization_id)
            if connection_filter is not None:
                query = query.options(
                    with_loader_criteria(
                        MCPConnection, connection_filter, include_aliases=True
                    )
                )
        result = await self.session.execute(query)
        return result.unique().scalar_one_or_none()


class MCPConnectionRepository(OrgScopedRepository[MCPConnection]):
    """Repository for per-org MCP connections.

    Strict org scoping — no global fallback because connections carry per-org
    secrets. ``self.org_id`` MUST be set; queries return empty for org-less
    callers (which would otherwise silently broaden access).
    """

    model = MCPConnection
    role_table = None

    async def list_connections(
        self,
        *,
        reach: OrgReach,
        server_id: UUID | None = None,
        organization_id: UUID | None = None,
    ) -> list[MCPConnection]:
        """List connections within SQL reach before ordering or shaping."""
        query = (
            select(MCPConnection)
            .options(
                joinedload(MCPConnection.server),
                selectinload(MCPConnection.tools),
                joinedload(MCPConnection.service_oauth_token),
            )
            .order_by(MCPConnection.created_at)
        )
        reach_filter = reach.where(MCPConnection.organization_id)
        if reach_filter is not None:
            query = query.where(reach_filter)
        if server_id is not None:
            query = query.where(MCPConnection.server_id == server_id)
        if organization_id is not None:
            query = query.where(MCPConnection.organization_id == organization_id)

        result = await self.session.execute(query)
        return list(result.scalars().unique().all())

    async def get_connection(self, connection_id: UUID) -> MCPConnection | None:
        """ID lookup with relationships loaded.

        Caller is responsible for org-scope verification — this method does
        not filter by ``self.org_id`` so platform admins can fetch any
        connection.
        """
        result = await self.session.execute(
            select(MCPConnection)
            .where(MCPConnection.id == connection_id)
            .options(
                joinedload(MCPConnection.server),
                selectinload(MCPConnection.tools),
                joinedload(MCPConnection.service_oauth_token),
            )
        )
        return result.unique().scalar_one_or_none()

    async def get_by_server_and_org(
        self, server_id: UUID, organization_id: UUID
    ) -> MCPConnection | None:
        """Look up the unique (server, org) connection."""
        result = await self.session.execute(
            select(MCPConnection)
            .where(MCPConnection.server_id == server_id)
            .where(MCPConnection.organization_id == organization_id)
            .options(
                joinedload(MCPConnection.server),
                selectinload(MCPConnection.tools),
                joinedload(MCPConnection.service_oauth_token),
            )
        )
        return result.unique().scalar_one_or_none()


class MCPConnectionToolRepository:
    """Repository for the per-connection tool catalog.

    Plain CRUD scoped by ``connection_id``. The unique key is
    ``(connection_id, tool_name)``; upserts on that key match the manifest
    serialization layout.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_tools(self, connection_id: UUID) -> list[MCPConnectionTool]:
        """List all tools for a connection (enabled and disabled)."""
        result = await self.session.execute(
            select(MCPConnectionTool)
            .where(MCPConnectionTool.connection_id == connection_id)
            .order_by(MCPConnectionTool.tool_name)
        )
        return list(result.scalars().all())

    async def get_tool(self, tool_id: UUID) -> MCPConnectionTool | None:
        """Look up a single tool by ID."""
        result = await self.session.execute(
            select(MCPConnectionTool).where(MCPConnectionTool.id == tool_id)
        )
        return result.scalar_one_or_none()

    async def get_by_connection_and_name(
        self, connection_id: UUID, tool_name: str
    ) -> MCPConnectionTool | None:
        """Look up a tool by its natural key."""
        result = await self.session.execute(
            select(MCPConnectionTool)
            .where(MCPConnectionTool.connection_id == connection_id)
            .where(MCPConnectionTool.tool_name == tool_name)
        )
        return result.scalar_one_or_none()


class UserMCPCredentialRepository:
    """Repository for per-user delegated MCP credentials.

    Scoped by ``user_id`` set on ``self``. Returns empty for callers without
    a user_id rather than broadening access.
    """

    def __init__(self, session: AsyncSession, user_id: UUID | None = None):
        self.session = session
        self.user_id = user_id

    async def list_credentials(self) -> list[UserMCPCredential]:
        """List all MCP credentials owned by the current user."""
        if self.user_id is None:
            return []
        result = await self.session.execute(
            select(UserMCPCredential)
            .where(UserMCPCredential.user_id == self.user_id)
            .options(
                joinedload(UserMCPCredential.connection),
                joinedload(UserMCPCredential.oauth_token),
            )
            .order_by(UserMCPCredential.consent_granted_at)
        )
        return list(result.scalars().unique().all())

    async def get_credential(
        self, credential_id: UUID
    ) -> UserMCPCredential | None:
        """Look up a credential by ID."""
        result = await self.session.execute(
            select(UserMCPCredential)
            .where(UserMCPCredential.id == credential_id)
            .options(
                joinedload(UserMCPCredential.connection),
                joinedload(UserMCPCredential.oauth_token),
            )
        )
        return result.unique().scalar_one_or_none()

    async def get_by_user_and_connection(
        self, user_id: UUID, connection_id: UUID
    ) -> UserMCPCredential | None:
        """Look up the unique (user, connection) credential row."""
        result = await self.session.execute(
            select(UserMCPCredential)
            .where(UserMCPCredential.user_id == user_id)
            .where(UserMCPCredential.connection_id == connection_id)
            .options(
                joinedload(UserMCPCredential.connection),
                joinedload(UserMCPCredential.oauth_token),
            )
        )
        return result.unique().scalar_one_or_none()
