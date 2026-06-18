"""add message_artifacts table (Chat V2 sub-project 4 — Artifacts)

Files produced by a tool/skill and rendered as chat artifacts. The mirror image
of message_attachments (input): a tool returns an artifact contract, the trusted
execution layer persists the bytes to S3 under
``_artifacts/{conversation_id}/{uuid}_{filename}``, and only metadata lives here.
Download URLs are minted scoped + expiring at render time by the API. See Part C
of the agent-skill-bundles-and-capabilities design.

This revision also merges the two parallel heads that resulted from catching the
chat-v2 branch up to post-Solutions main (compaction_checkpoint +
solution_deploy_jobs), collapsing them to a single head.

Revision ID: 20260618_message_artifacts
Revises: 20260617_compaction_checkpoint, 20260617_solution_deploy_jobs
Create Date: 2026-06-18
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260618_message_artifacts"
down_revision = ("20260617_compaction_checkpoint", "20260617_solution_deploy_jobs")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "message_artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "message_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("s3_key", sa.String(length=1024), nullable=False),
        sa.Column("filename", sa.String(length=500), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("preview_kind", sa.String(length=32), nullable=True),
        sa.Column("preview_inline", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "ix_message_artifacts_message_id",
        "message_artifacts",
        ["message_id"],
    )
    op.create_index(
        "ix_message_artifacts_conversation_id",
        "message_artifacts",
        ["conversation_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_message_artifacts_conversation_id",
        table_name="message_artifacts",
    )
    op.drop_index(
        "ix_message_artifacts_message_id",
        table_name="message_artifacts",
    )
    op.drop_table("message_artifacts")
