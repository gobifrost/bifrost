"""chat v2 m3: message branching + per-conversation instructions

Adds:
- messages.parent_message_id (FK to messages.id, nullable) + index
- conversations.active_leaf_message_id (FK to messages.id, nullable)
- conversations.instructions (TEXT, nullable)

Backfills:
- parent_message_id from the prior sequence row in the same conversation
- active_leaf_message_id from MAX(sequence) per conversation

Window-function ordering uses (sequence, created_at, id) as a deterministic
tiebreaker. Message.sequence is non-unique per conversation, so a tie is
theoretically possible; the extra columns make the backfill reproducible.

Revision ID: 20260429_chat_v2_m3
Revises: 20260428_chat_v2_m2
Create Date: 2026-04-29
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260429_chat_v2_m3"
down_revision = "20260428_chat_v2_m2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # messages.parent_message_id
    op.add_column(
        "messages",
        sa.Column(
            "parent_message_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_messages_parent_message_id",
        "messages",
        "messages",
        ["parent_message_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_messages_parent_message_id",
        "messages",
        ["parent_message_id"],
    )

    # conversations.active_leaf_message_id
    op.add_column(
        "conversations",
        sa.Column(
            "active_leaf_message_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_conversations_active_leaf_message_id",
        "conversations",
        "messages",
        ["active_leaf_message_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # conversations.instructions
    op.add_column(
        "conversations",
        sa.Column("instructions", sa.Text(), nullable=True),
    )

    # Backfill parent_message_id: each message's parent is the prior row by
    # (conversation_id, sequence). LAG over the conversation partition.
    op.execute(
        """
        WITH ordered AS (
            SELECT
                id,
                LAG(id) OVER (
                    PARTITION BY conversation_id
                    ORDER BY sequence, created_at, id
                ) AS prev_id
            FROM messages
        )
        UPDATE messages m
        SET parent_message_id = ordered.prev_id
        FROM ordered
        WHERE m.id = ordered.id AND ordered.prev_id IS NOT NULL;
        """
    )

    # Backfill active_leaf_message_id: MAX(sequence) message per conversation.
    op.execute(
        """
        WITH leaves AS (
            SELECT
                conversation_id,
                id AS leaf_id,
                ROW_NUMBER() OVER (
                    PARTITION BY conversation_id
                    ORDER BY sequence DESC, created_at DESC, id DESC
                ) AS rn
            FROM messages
        )
        UPDATE conversations c
        SET active_leaf_message_id = leaves.leaf_id
        FROM leaves
        WHERE leaves.conversation_id = c.id AND leaves.rn = 1;
        """
    )


def downgrade() -> None:
    op.drop_column("conversations", "instructions")
    op.drop_constraint(
        "fk_conversations_active_leaf_message_id",
        "conversations",
        type_="foreignkey",
    )
    op.drop_column("conversations", "active_leaf_message_id")
    op.drop_index("ix_messages_parent_message_id", table_name="messages")
    op.drop_constraint(
        "fk_messages_parent_message_id",
        "messages",
        type_="foreignkey",
    )
    op.drop_column("messages", "parent_message_id")
