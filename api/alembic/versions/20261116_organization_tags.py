"""Add free-form tags to organizations.

Revision ID: 20261116_organization_tags
Revises: 20261114_docs_mcp_name
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20261116_organization_tags"
down_revision = "20261114_docs_mcp_name"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column("tags", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )


def downgrade() -> None:
    op.drop_column("organizations", "tags")
