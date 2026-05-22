"""Add logo_data and logo_content_type to applications and agents.

Revision ID: 20260519_add_entity_logos
Revises: 20260516_per_token_status
Create Date: 2026-05-19

Stores uploaded logo images inline in the database as bytea alongside a
content-type string (e.g. "image/png", "image/svg+xml"). Both columns are
nullable — absence means no logo has been uploaded.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260519_add_entity_logos"
down_revision = "20260516_per_token_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "applications",
        sa.Column("logo_data", sa.LargeBinary(), nullable=True),
    )
    op.add_column(
        "applications",
        sa.Column("logo_content_type", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "agents",
        sa.Column("logo_data", sa.LargeBinary(), nullable=True),
    )
    op.add_column(
        "agents",
        sa.Column("logo_content_type", sa.String(length=50), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agents", "logo_content_type")
    op.drop_column("agents", "logo_data")
    op.drop_column("applications", "logo_content_type")
    op.drop_column("applications", "logo_data")
