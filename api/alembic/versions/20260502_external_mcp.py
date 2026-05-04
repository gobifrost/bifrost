"""External MCP client tables.

Revision ID: 20260502_external_mcp
Revises: 20260504_backfill_table_access
Create Date: 2026-05-02

Creates four tables for the external MCP client feature:

- ``mcp_servers``: manifest-shareable server template (no secrets)
- ``mcp_connections``: per-org instance with ``encrypted_client_secret`` and
  the ``available_in_chat`` / ``available_to_autonomous`` visibility flags
- ``mcp_connection_tools``: per-connection catalog from vendor's tools/list
- ``user_mcp_credentials``: per-user delegated tokens with consent metadata

FKs from child rows to ``mcp_connections.id`` and ``mcp_servers.id`` use
``ON UPDATE CASCADE`` so manifest-import upserts can rewrite parent IDs
without orphaning children. Same for ``oauth_tokens.id`` references —
manifest sync may need to rewrite those too (jackmusick/bifrost#148).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260502_external_mcp"
down_revision = "20260504_backfill_table_access"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # mcp_servers — server template, manifest-shareable, no secrets
    # ------------------------------------------------------------------
    op.create_table(
        "mcp_servers",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("server_url", sa.String(2048), nullable=False),
        sa.Column(
            "oauth_provider_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("oauth_providers.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("redirect_url", sa.String(2048), nullable=True),
        sa.Column(
            "discovery_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.UniqueConstraint("name", name="uq_mcp_servers_name"),
    )
    op.create_index(
        "ix_mcp_servers_name", "mcp_servers", ["name"], unique=False
    )
    op.create_index(
        "ix_mcp_servers_organization_id",
        "mcp_servers",
        ["organization_id"],
        unique=False,
    )

    # ------------------------------------------------------------------
    # mcp_connections — per-org instance with secrets
    # ------------------------------------------------------------------
    op.create_table(
        "mcp_connections",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "server_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "mcp_servers.id",
                ondelete="CASCADE",
                onupdate="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("client_id", sa.String(512), nullable=False),
        sa.Column("encrypted_client_secret", sa.Text(), nullable=False),
        sa.Column("server_url_override", sa.String(2048), nullable=True),
        sa.Column(
            "available_in_chat",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "available_to_autonomous",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "service_oauth_token_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "oauth_tokens.id",
                ondelete="SET NULL",
                onupdate="CASCADE",
            ),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "ix_mcp_connections_server_id",
        "mcp_connections",
        ["server_id"],
        unique=False,
    )
    op.create_index(
        "ix_mcp_connections_organization_id",
        "mcp_connections",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        "ix_mcp_connections_unique_per_org",
        "mcp_connections",
        ["server_id", "organization_id"],
        unique=True,
    )

    # ------------------------------------------------------------------
    # mcp_connection_tools — per-connection catalog
    # ------------------------------------------------------------------
    op.create_table(
        "mcp_connection_tools",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "connection_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "mcp_connections.id",
                ondelete="CASCADE",
                onupdate="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column("tool_name", sa.String(255), nullable=False),
        sa.Column(
            "tool_schema",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column("disabled_reason", sa.Text(), nullable=True),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "ix_mcp_connection_tools_connection_id",
        "mcp_connection_tools",
        ["connection_id"],
        unique=False,
    )
    op.create_index(
        "ix_mcp_connection_tools_unique_per_connection",
        "mcp_connection_tools",
        ["connection_id", "tool_name"],
        unique=True,
    )

    # ------------------------------------------------------------------
    # user_mcp_credentials — per-user delegated tokens
    # ------------------------------------------------------------------
    op.create_table(
        "user_mcp_credentials",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "connection_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "mcp_connections.id",
                ondelete="CASCADE",
                onupdate="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column(
            "oauth_token_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "oauth_tokens.id",
                ondelete="CASCADE",
                onupdate="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column(
            "consent_granted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "consent_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "granted_scopes",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "ix_user_mcp_credentials_user_id",
        "user_mcp_credentials",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_user_mcp_credentials_connection_id",
        "user_mcp_credentials",
        ["connection_id"],
        unique=False,
    )
    op.create_index(
        "ix_user_mcp_credentials_oauth_token_id",
        "user_mcp_credentials",
        ["oauth_token_id"],
        unique=False,
    )
    op.create_index(
        "ix_user_mcp_credentials_unique_per_user_connection",
        "user_mcp_credentials",
        ["user_id", "connection_id"],
        unique=True,
    )


def downgrade() -> None:
    # Drop in reverse FK dependency order: leaf rows first, parents last.
    op.drop_index(
        "ix_user_mcp_credentials_unique_per_user_connection",
        table_name="user_mcp_credentials",
    )
    op.drop_index(
        "ix_user_mcp_credentials_oauth_token_id",
        table_name="user_mcp_credentials",
    )
    op.drop_index(
        "ix_user_mcp_credentials_connection_id",
        table_name="user_mcp_credentials",
    )
    op.drop_index(
        "ix_user_mcp_credentials_user_id", table_name="user_mcp_credentials"
    )
    op.drop_table("user_mcp_credentials")

    op.drop_index(
        "ix_mcp_connection_tools_unique_per_connection",
        table_name="mcp_connection_tools",
    )
    op.drop_index(
        "ix_mcp_connection_tools_connection_id",
        table_name="mcp_connection_tools",
    )
    op.drop_table("mcp_connection_tools")

    op.drop_index(
        "ix_mcp_connections_unique_per_org", table_name="mcp_connections"
    )
    op.drop_index(
        "ix_mcp_connections_organization_id", table_name="mcp_connections"
    )
    op.drop_index("ix_mcp_connections_server_id", table_name="mcp_connections")
    op.drop_table("mcp_connections")

    op.drop_index("ix_mcp_servers_organization_id", table_name="mcp_servers")
    op.drop_index("ix_mcp_servers_name", table_name="mcp_servers")
    op.drop_table("mcp_servers")
