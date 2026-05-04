"""Per-agent MCP connection grants.

Revision ID: 20260503_agent_mcp_grants
Revises: 20260502_external_mcp
Create Date: 2026-05-03

Introduces an explicit per-agent binding to MCP connections. Defaults for
new agents are *deny* — without a row in ``agent_mcp_connections``, an
agent gets zero MCP tools from the connection regardless of the
connection's ``available_in_chat`` / ``available_to_autonomous`` flags.

Backfill: every existing (agent, connection) pair whose orgs match is
granted on upgrade so the rollout preserves the legacy "every agent in
the org auto-receives every connection's tools" behavior. Backfilled
rows use ``granted_by = NULL`` so the audit log can distinguish them
from explicit grants set by an admin via the UI/API.

Downgrade drops the table — there are no FKs pointing into it from
outside the table itself, so the rollback is straightforward.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260503_agent_mcp_grants"
down_revision = "20260502_external_mcp"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_mcp_connections",
        sa.Column(
            "agent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
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
            "granted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "granted_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint(
            "agent_id", "connection_id", name="pk_agent_mcp_connections"
        ),
    )
    op.create_index(
        "ix_agent_mcp_connections_agent_id",
        "agent_mcp_connections",
        ["agent_id"],
        unique=False,
    )
    op.create_index(
        "ix_agent_mcp_connections_connection_id",
        "agent_mcp_connections",
        ["connection_id"],
        unique=False,
    )

    # ------------------------------------------------------------------
    # Backfill: every existing agent in an org that has MCP connections
    # gets a grant for every connection in the same org. This preserves
    # the pre-feature "auto-available" behavior so upgrading prod doesn't
    # silently drop tools off in-flight agents.
    #
    # ``granted_by`` is NULL for these rows so the audit surface can tell
    # backfilled grants from explicit ones. If the same row is later set
    # via the API the explicit grantor's UUID will overwrite the NULL.
    # ------------------------------------------------------------------
    op.execute(
        """
        INSERT INTO agent_mcp_connections (
            agent_id, connection_id, granted_at, granted_by
        )
        SELECT
            a.id,
            c.id,
            NOW(),
            NULL
        FROM agents a
        JOIN mcp_connections c
          ON c.organization_id = a.organization_id
        WHERE a.organization_id IS NOT NULL
        ON CONFLICT (agent_id, connection_id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_index(
        "ix_agent_mcp_connections_connection_id",
        table_name="agent_mcp_connections",
    )
    op.drop_index(
        "ix_agent_mcp_connections_agent_id",
        table_name="agent_mcp_connections",
    )
    op.drop_table("agent_mcp_connections")
