"""add private memory stores and preferences

Revision ID: 20260812_private_memory
Revises: 20260807_withdraw_builder
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260812_private_memory"
down_revision: str | None = "20260807_withdraw_builder"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "memory_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.create_table(
        "memory_stores",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
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
        sa.CheckConstraint(
            "organization_id IS NOT NULL OR user_id IS NOT NULL",
            name="ck_memory_store_has_owner",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "user_id",
            name="uq_memory_store_org_user",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index(
        "ix_memory_stores_organization_id",
        "memory_stores",
        ["organization_id"],
    )
    op.create_index("ix_memory_stores_user_id", "memory_stores", ["user_id"])
    op.create_table(
        "memory_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
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
        sa.ForeignKeyConstraint(["store_id"], ["memory_stores.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute(
        "ALTER TABLE memory_entries ADD COLUMN embedding vector NOT NULL"
    )
    op.create_index("ix_memory_entries_store_id", "memory_entries", ["store_id"])


def downgrade() -> None:
    op.drop_index("ix_memory_entries_store_id", table_name="memory_entries")
    op.drop_table("memory_entries")
    op.drop_index("ix_memory_stores_user_id", table_name="memory_stores")
    op.drop_index("ix_memory_stores_organization_id", table_name="memory_stores")
    op.drop_table("memory_stores")
    op.drop_column("users", "memory_enabled")
