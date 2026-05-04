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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from src.core.org_filter import OrgFilterType
from src.models.orm.external_mcp import (
    MCPConnection,
    MCPConnectionTool,
    MCPServer,
    UserMCPCredential,
)
from src.repositories.org_scoped import OrgScopedRepository

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
        active_only: bool = True,
    ) -> list[MCPServer]:
        """List MCP servers visible in the current scope.

        Cascade: org-specific (if org_id set) + global (NULL org_id).
        Eager-loads ``connections`` so callers can inspect per-org instances
        without N+1 queries.
        """
        query = select(MCPServer).options(selectinload(MCPServer.connections))
        query = self._apply_cascade_scope(query)
        if active_only:
            query = query.where(MCPServer.is_active.is_(True))
        query = query.order_by(MCPServer.name)
        result = await self.session.execute(query)
        return list(result.scalars().unique().all())

    async def list_all_in_scope(
        self,
        filter_type: OrgFilterType = OrgFilterType.ALL,
        active_only: bool = False,
    ) -> list[MCPServer]:
        """List all MCP servers in scope without role-based filtering.

        Used by platform admins. Mirrors ``AgentRepository.list_all_in_scope``.
        """
        query = select(MCPServer).options(selectinload(MCPServer.connections))

        if filter_type == OrgFilterType.ALL:
            pass
        elif filter_type == OrgFilterType.GLOBAL_ONLY:
            query = query.where(MCPServer.organization_id.is_(None))
        elif filter_type == OrgFilterType.ORG_ONLY:
            if self.org_id is not None:
                query = query.where(MCPServer.organization_id == self.org_id)
            else:
                query = query.where(MCPServer.id.is_(None))
        elif filter_type == OrgFilterType.ORG_PLUS_GLOBAL:
            query = self._apply_cascade_scope(query)

        if active_only:
            query = query.where(MCPServer.is_active.is_(True))

        query = query.order_by(MCPServer.name)
        result = await self.session.execute(query)
        return list(result.scalars().unique().all())

    async def get_server(self, server_id: UUID) -> MCPServer | None:
        """Direct ID lookup, eager-loading connections + tools."""
        result = await self.session.execute(
            select(MCPServer)
            .where(MCPServer.id == server_id)
            .options(
                selectinload(MCPServer.connections).selectinload(MCPConnection.tools),
            )
        )
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
        self, server_id: UUID | None = None
    ) -> list[MCPConnection]:
        """List connections in the current org, optionally filtered by server."""
        if self.org_id is None:
            return []

        query = (
            select(MCPConnection)
            .where(MCPConnection.organization_id == self.org_id)
            .options(
                joinedload(MCPConnection.server),
                selectinload(MCPConnection.tools),
                joinedload(MCPConnection.service_oauth_token),
            )
            .order_by(MCPConnection.created_at)
        )
        if server_id is not None:
            query = query.where(MCPConnection.server_id == server_id)

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
