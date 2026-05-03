"""
External MCP Client ORM models.

Represents external MCP servers Bifrost connects to as a client (the symmetric
counterpart to ``api/src/services/mcp_server`` which serves Bifrost's own
workflows/tools to outside clients). Four tables:

- ``mcp_servers`` — manifest-shareable server template (no secrets).
- ``mcp_connections`` — per-org instance with secrets via FK; carries the
  ``available_in_chat`` / ``available_to_autonomous`` visibility flags.
- ``mcp_connection_tools`` — per-connection tool catalog populated from the
  vendor's ``tools/list``.
- ``user_mcp_credentials`` — per-user delegated tokens, FK to
  ``oauth_tokens`` for the actual access/refresh tokens.

Critical: FKs from ``mcp_connection_tools`` and ``user_mcp_credentials`` to
``mcp_connections.id`` (and from ``mcp_connections`` to ``mcp_servers.id``)
use ``ON UPDATE CASCADE`` so the manifest-import resolver can rewrite ``id``
columns during ``ON CONFLICT`` upserts without orphaning child rows. The same
applies to FKs from ``mcp_connections.service_oauth_token_id`` and
``user_mcp_credentials.oauth_token_id`` to ``oauth_tokens.id`` — see
jackmusick/bifrost#148 for the integration-cache bug this defends against.
"""

from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.orm.base import Base

if TYPE_CHECKING:
    from src.models.orm.oauth import OAuthProvider, OAuthToken
    from src.models.orm.organizations import Organization
    from src.models.orm.users import User


class MCPServer(Base):
    """External MCP server template.

    Manifest-shareable. NO secrets stored on this row — secrets live on
    ``mcp_connections.encrypted_client_secret``. ``organization_id`` is
    nullable: ``NULL`` means the template is platform-level (visible to all
    orgs); a non-NULL value scopes the template to a single org (escape hatch
    for per-org overrides if a forcing example shows up).
    """

    __tablename__ = "mcp_servers"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    server_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    oauth_provider_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("oauth_providers.id", ondelete="RESTRICT"),
        default=None,
        nullable=True,
    )
    redirect_url: Mapped[str | None] = mapped_column(
        String(2048), default=None, nullable=True,
        comment="Deterministic redirect URL computed at server creation time"
    )
    discovery_metadata: Mapped[dict | None] = mapped_column(
        JSONB, default=None, nullable=True,
        comment="Snapshot of /.well-known/oauth-authorization-server at create time"
    )
    organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        default=None,
        nullable=True,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    connections: Mapped[list["MCPConnection"]] = relationship(
        back_populates="server",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    # Optional link to the OAuth provider that owns this server's auth
    # config (token URL, scopes, audience, oauth_flow_type). Used by the
    # auth resolver to branch on flow_type so client_credentials connections
    # don't get treated as if they had a per-user OAuth path.
    oauth_provider: Mapped["OAuthProvider | None"] = relationship(
        foreign_keys=[oauth_provider_id],
        lazy="select",
    )

    __table_args__ = (
        Index("ix_mcp_servers_name", "name"),
        Index("ix_mcp_servers_organization_id", "organization_id"),
    )


class MCPConnection(Base):
    """Per-org instance of an MCP server template.

    Carries the encrypted client secret, the visibility flags
    (``available_in_chat`` / ``available_to_autonomous``), and the optional
    shared service OAuth token used when chat falls back from a missing
    per-user credential or when an autonomous run needs a service identity.
    Per-org only — ``organization_id`` is NOT NULL. Future expansion to
    multiple connections per (server, org) is gated by the unique constraint.
    """

    __tablename__ = "mcp_connections"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    server_id: Mapped[UUID] = mapped_column(
        ForeignKey("mcp_servers.id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False,
    )
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    client_id: Mapped[str] = mapped_column(String(512), nullable=False)
    encrypted_client_secret: Mapped[str] = mapped_column(Text, nullable=False)
    server_url_override: Mapped[str | None] = mapped_column(
        String(2048), default=None, nullable=True
    )
    available_in_chat: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    available_to_autonomous: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    service_oauth_token_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("oauth_tokens.id", ondelete="SET NULL", onupdate="CASCADE"),
        default=None,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    server: Mapped["MCPServer"] = relationship(
        back_populates="connections",
        lazy="joined",
    )
    organization: Mapped["Organization"] = relationship(lazy="joined")
    tools: Mapped[list["MCPConnectionTool"]] = relationship(
        back_populates="connection",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    service_oauth_token: Mapped["OAuthToken | None"] = relationship(
        "OAuthToken",
        foreign_keys=[service_oauth_token_id],
        lazy="joined",
    )
    # Note: there's intentionally no `agents` back-relationship here. Going
    # MCPConnection → Agent would close a module-level import cycle that
    # CodeQL flags (jackmusick/bifrost). Query through agent_mcp_connections
    # directly when you need "which agents have this connection."

    __table_args__ = (
        Index("ix_mcp_connections_server_id", "server_id"),
        Index("ix_mcp_connections_organization_id", "organization_id"),
        Index(
            "ix_mcp_connections_unique_per_org",
            "server_id",
            "organization_id",
            unique=True,
        ),
    )


class MCPConnectionTool(Base):
    """Per-connection catalog row populated from the vendor's tools/list.

    A tool that disappears from the vendor is set to ``enabled = False`` with
    ``disabled_reason = "no longer published by vendor"`` rather than deleted —
    this preserves the schema for later re-enable and prevents existing agent
    bindings from silently breaking.
    """

    __tablename__ = "mcp_connection_tools"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    connection_id: Mapped[UUID] = mapped_column(
        ForeignKey("mcp_connections.id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False,
    )
    tool_name: Mapped[str] = mapped_column(String(255), nullable=False)
    tool_schema: Mapped[dict] = mapped_column(JSONB, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    disabled_reason: Mapped[str | None] = mapped_column(
        Text, default=None, nullable=True
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    connection: Mapped["MCPConnection"] = relationship(
        back_populates="tools",
    )

    __table_args__ = (
        Index("ix_mcp_connection_tools_connection_id", "connection_id"),
        Index(
            "ix_mcp_connection_tools_unique_per_connection",
            "connection_id",
            "tool_name",
            unique=True,
        ),
    )


class UserMCPCredential(Base):
    """Per-user delegated MCP credentials.

    Links a user to an MCP connection through an ``oauth_tokens`` row. The
    OAuth token row holds the access/refresh tokens; this row carries consent
    provenance (when the user granted, when the consent expires, what scopes
    they agreed to). The chat surface watches ``consent_expires_at`` to render
    "Connection expiring soon — reconnect to avoid interruption" warnings
    ahead of the actual access-token failure.

    CASCADE on all three FKs is intentional — there's no defensible state in
    which the row survives the deletion of the user, the connection, or the
    raw OAuth token.
    """

    __tablename__ = "user_mcp_credentials"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    connection_id: Mapped[UUID] = mapped_column(
        ForeignKey("mcp_connections.id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False,
    )
    oauth_token_id: Mapped[UUID] = mapped_column(
        ForeignKey("oauth_tokens.id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False,
    )
    consent_granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
    )
    consent_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, nullable=True,
        comment="Vendor-stated consent expiration (e.g. Microsoft 90-day offline_access)"
    )
    granted_scopes: Mapped[list[str]] = mapped_column(
        ARRAY(Text),
        nullable=False,
        default=list,
        server_default="{}",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    user: Mapped["User"] = relationship(lazy="joined")
    connection: Mapped["MCPConnection"] = relationship(lazy="joined")
    oauth_token: Mapped["OAuthToken"] = relationship(
        "OAuthToken",
        foreign_keys=[oauth_token_id],
        lazy="joined",
    )

    __table_args__ = (
        Index("ix_user_mcp_credentials_user_id", "user_id"),
        Index("ix_user_mcp_credentials_connection_id", "connection_id"),
        Index("ix_user_mcp_credentials_oauth_token_id", "oauth_token_id"),
        Index(
            "ix_user_mcp_credentials_unique_per_user_connection",
            "user_id",
            "connection_id",
            unique=True,
        ),
    )


class AgentMCPConnection(Base):
    """Per-agent grant for an MCP connection.

    A row here means "this agent may use the tools published by this MCP
    connection". Without a row, the agent gets zero MCP tools from that
    connection — the default for new agents is **deny**, so registering a
    write-capable connection at the org level no longer silently widens
    every agent's capabilities.

    The migration that introduces this table backfills grants for every
    existing (agent, connection) pair within the same org so the rollout
    preserves current behavior. ``granted_by`` is ``NULL`` for backfilled
    rows and the email/UUID of the granting admin for explicit grants —
    the audit log uses the distinction to tell those apart.
    """

    __tablename__ = "agent_mcp_connections"

    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"),
        primary_key=True,
    )
    connection_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "mcp_connections.id", ondelete="CASCADE", onupdate="CASCADE"
        ),
        primary_key=True,
    )
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
        nullable=False,
    )
    granted_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        default=None,
        nullable=True,
    )

    __table_args__ = (
        Index("ix_agent_mcp_connections_agent_id", "agent_id"),
        Index("ix_agent_mcp_connections_connection_id", "connection_id"),
    )
