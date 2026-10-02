"""Unified source search: solution_file_index, optional pg_trgm, MCP tool rename.

Revision ID: 20261001_unified_file_search
Revises: 20261001_graph_client_state_enc
Create Date: 2026-10-01

pg_trgm is an accelerator only. Source search issues the same ILIKE/LIKE
either way, so a host that cannot create the extension keeps identical results
and simply scans sequentially.
"""
import logging

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261001_unified_file_search"
down_revision = "20261001_graph_client_state_enc"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")


def upgrade() -> None:
    op.create_table(
        "solution_file_index",
        sa.Column(
            "solution_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("solutions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("path", sa.String(1000), primary_key=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
    )
    op.execute(
        "UPDATE agents SET system_tools = "
        "array_replace(system_tools, 'search_content', 'bifrost_file_search') "
        "WHERE 'search_content' = ANY(system_tools)"
    )
    bind = op.get_bind()
    try:
        with bind.begin_nested():
            bind.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    except sa.exc.DBAPIError as exc:
        logger.warning(
            "pg_trgm unavailable (%s); source search works without the trigram "
            "index but scans sequentially",
            exc.orig,
        )
        return
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_file_index_content_trgm "
        "ON file_index USING gin (content gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_solution_file_index_content_trgm "
        "ON solution_file_index USING gin (content gin_trgm_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_file_index_content_trgm")
    op.drop_table("solution_file_index")
    op.execute(
        "UPDATE agents SET system_tools = "
        "array_replace(system_tools, 'bifrost_file_search', 'search_content') "
        "WHERE 'bifrost_file_search' = ANY(system_tools)"
    )
