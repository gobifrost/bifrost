"""Persist integration workflow shortcuts.

Revision ID: 20261127_integration_links
Revises: 20261117_idp_access_pending
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261127_integration_links"
down_revision = "20261117_idp_access_pending"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("integrations", sa.Column("workflow_links", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False))


def downgrade():
    op.drop_column("integrations", "workflow_links")
