"""Schedule occurrence identity and processor scan cursor

Revision ID: 20261022_schedule_occurrences
Revises: 20261019_durable_impersonation
Create Date: 2026-10-22

Trigger leadership can move between control replicas (scheduler or API).
events.occurrence_key gives each fired schedule occurrence a unique identity so
overlapping leaders cannot fire it twice; existing rows stay NULL and are
outside the partial unique index. scheduler_task_states.cursor_at records the
last completed schedule scan so a successor leader fires an occurrence missed
during failover.

The unique index is built CONCURRENTLY so events stays writable, and without IF
NOT EXISTS so a leftover INVALID index from an interrupted build fails loudly.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261022_schedule_occurrences"
down_revision: Union[str, None] = "20261019_durable_impersonation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "events",
        sa.Column("occurrence_key", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "scheduler_task_states",
        sa.Column("cursor_at", sa.DateTime(timezone=True), nullable=True),
    )
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE UNIQUE INDEX CONCURRENTLY ux_events_occurrence_key "
            "ON events (occurrence_key) WHERE occurrence_key IS NOT NULL"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ux_events_occurrence_key")
    op.drop_column("scheduler_task_states", "cursor_at")
    op.drop_column("events", "occurrence_key")
