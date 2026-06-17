"""add compaction checkpoint columns to conversations (Chat V2 M5 — Lossless Compaction)

Lossless compaction summarizes older turns *in the model's working context only*;
the message rows themselves are never modified or deleted (§4.1). The summary is
persisted on the conversation as a checkpoint so it survives between turns and the
manual "Compact older turns" button has lasting effect:

- ``compaction_summary``           — the ``[Conversation history summary]`` text.
- ``compaction_through_sequence``  — messages with ``sequence <= this`` are folded
                                     into the summary in working context; later
                                     messages stay verbatim.
- ``compaction_original_tokens``   — estimated token weight of the folded span,
                                     for the "Compacted N turns (~X tokens)" feedback.

All three are NULL on conversations that have never been compacted.

Revision ID: 20260617_compaction_checkpoint
Revises: 20260617_message_attachments
Create Date: 2026-06-17
"""
from alembic import op
import sqlalchemy as sa

revision = "20260617_compaction_checkpoint"
down_revision = "20260617_message_attachments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("compaction_summary", sa.Text(), nullable=True),
    )
    op.add_column(
        "conversations",
        sa.Column("compaction_through_sequence", sa.Integer(), nullable=True),
    )
    op.add_column(
        "conversations",
        sa.Column("compaction_original_tokens", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("conversations", "compaction_original_tokens")
    op.drop_column("conversations", "compaction_through_sequence")
    op.drop_column("conversations", "compaction_summary")
