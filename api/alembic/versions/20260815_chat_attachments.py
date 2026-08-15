"""add chat message attachments

Revision ID: 20260815_chat_attachments
Revises: 20260815_optional_agent_limits
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260815_chat_attachments"
down_revision = "20260815_optional_agent_limits"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "message_attachments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "message_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("messages.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("s3_key", sa.String(length=1024), nullable=False),
        sa.Column("filename", sa.String(length=500), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "ix_message_attachments_message_id", "message_attachments", ["message_id"]
    )
    op.create_index(
        "ix_message_attachments_conversation_id",
        "message_attachments",
        ["conversation_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_message_attachments_conversation_id", table_name="message_attachments"
    )
    op.drop_index(
        "ix_message_attachments_message_id", table_name="message_attachments"
    )
    op.drop_table("message_attachments")
