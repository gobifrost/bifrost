"""add user_invites table

Revision ID: 20260508_user_invites
Revises: 20260506_knowledge_dim
Create Date: 2026-05-08
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260508_user_invites"
down_revision = "20260506_knowledge_dim"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_invites",
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
        sa.Column("token_hash", sa.String(128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_index(
        "uq_user_invites_user_id",
        "user_invites",
        ["user_id"],
        unique=True,
    )
    op.create_index(
        "uq_user_invites_token_hash",
        "user_invites",
        ["token_hash"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_user_invites_token_hash", table_name="user_invites")
    op.drop_index("uq_user_invites_user_id", table_name="user_invites")
    op.drop_table("user_invites")
