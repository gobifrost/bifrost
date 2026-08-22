"""add canonical artifact identity

Revision ID: 20260816_artifact_identity
Revises: 20260815_ai_usage_cache_cost
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260816_artifact_identity"
down_revision = "20260815_ai_usage_cache_cost"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("s3_key", sa.String(length=1024), nullable=False, unique=True),
        sa.Column("filename", sa.String(length=500), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "ix_artifacts_created_by_user_id", "artifacts", ["created_by_user_id"]
    )
    op.create_index("ix_artifacts_organization_id", "artifacts", ["organization_id"])
    op.create_index("ix_artifacts_created_at", "artifacts", ["created_at"])

    op.add_column(
        "message_attachments",
        sa.Column("artifact_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.execute(
        """
        INSERT INTO artifacts (
            id,
            organization_id,
            created_by_user_id,
            s3_key,
            filename,
            content_type,
            size_bytes,
            created_at
        )
        SELECT
            attachment.id,
            users.organization_id,
            conversation.user_id,
            attachment.s3_key,
            attachment.filename,
            attachment.content_type,
            attachment.size_bytes,
            attachment.created_at
        FROM message_attachments AS attachment
        JOIN conversations AS conversation ON conversation.id = attachment.conversation_id
        JOIN users ON users.id = conversation.user_id
        """
    )
    op.execute("UPDATE message_attachments SET artifact_id = id")
    op.alter_column("message_attachments", "artifact_id", nullable=False)
    op.create_foreign_key(
        "fk_message_attachments_artifact_id",
        "message_attachments",
        "artifacts",
        ["artifact_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_message_attachments_artifact_id",
        "message_attachments",
        ["artifact_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_message_attachments_artifact_id",
        table_name="message_attachments",
    )
    op.drop_constraint(
        "fk_message_attachments_artifact_id",
        "message_attachments",
        type_="foreignkey",
    )
    op.drop_column("message_attachments", "artifact_id")
    op.drop_index("ix_artifacts_created_at", table_name="artifacts")
    op.drop_index("ix_artifacts_organization_id", table_name="artifacts")
    op.drop_index("ix_artifacts_created_by_user_id", table_name="artifacts")
    op.drop_table("artifacts")
