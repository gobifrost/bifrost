"""restore event_source_id NOT NULL and add event_type to event_sources for topics

Revision ID: 20260521_topic_sources
Revises: 20260521_rename_int_topic
Create Date: 2026-05-21

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260521_topic_sources"
down_revision: Union[str, Sequence[str]] = "20260521_rename_int_topic"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Safeguard: if any events with NULL event_source_id exist, fail loudly.
    conn = op.get_bind()
    null_count = conn.execute(
        sa.text("SELECT COUNT(*) FROM events WHERE event_source_id IS NULL")
    ).scalar()
    if null_count and null_count > 0:
        raise RuntimeError(
            f"{null_count} events with NULL event_source_id exist; cannot revert nullability"
        )

    # Restore NOT NULL on event_source_id
    op.alter_column(
        "events",
        "event_source_id",
        existing_type=sa.dialects.postgresql.UUID(as_uuid=True),
        nullable=False,
    )
    op.alter_column(
        "event_subscriptions",
        "event_source_id",
        existing_type=sa.dialects.postgresql.UUID(as_uuid=True),
        nullable=False,
    )

    # Add event_type column to event_sources (used to identify topic sources)
    op.add_column(
        "event_sources",
        sa.Column("event_type", sa.String(100), nullable=True),
    )
    op.create_index(
        "ix_event_sources_event_type",
        "event_sources",
        ["event_type"],
    )

    # Add organization_id to events (stamped at emit time for topic events)
    op.add_column(
        "events",
        sa.Column(
            "organization_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_events_organization_id",
        "events",
        ["organization_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_events_organization_id", table_name="events")
    op.drop_column("events", "organization_id")

    op.drop_index("ix_event_sources_event_type", table_name="event_sources")
    op.drop_column("event_sources", "event_type")

    op.alter_column(
        "events",
        "event_source_id",
        existing_type=sa.dialects.postgresql.UUID(as_uuid=True),
        nullable=True,
    )
    op.alter_column(
        "event_subscriptions",
        "event_source_id",
        existing_type=sa.dialects.postgresql.UUID(as_uuid=True),
        nullable=True,
    )
