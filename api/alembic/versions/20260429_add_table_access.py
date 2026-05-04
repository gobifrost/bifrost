"""add access JSONB to tables

Revision ID: 20260429_table_access
Revises: 20260428_webhook_rate_limit
Create Date: 2026-04-29 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260429_table_access"
down_revision = "20260428_webhook_rate_limit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tables",
        sa.Column("access", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tables", "access")
