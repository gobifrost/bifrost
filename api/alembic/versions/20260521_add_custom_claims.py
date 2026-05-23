"""add custom_claims

Revision ID: 20260521_add_custom_claims
Revises: 20260519_add_entity_logos
Create Date: 2026-05-21
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "20260521_add_custom_claims"
down_revision = "20260519_add_entity_logos"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "custom_claims",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("type", sa.String(16), nullable=False, server_default="list"),
        sa.Column("query", JSONB, nullable=False),
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
        sa.UniqueConstraint(
            "organization_id", "name", name="uq_custom_claims_org_name"
        ),
    )
    op.create_index(
        "ix_custom_claims_organization_id", "custom_claims", ["organization_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_custom_claims_organization_id", table_name="custom_claims")
    op.drop_table("custom_claims")
