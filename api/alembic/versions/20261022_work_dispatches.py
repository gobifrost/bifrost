"""Durable work dispatch outbox

Revision ID: 20261022_work_dispatches
Revises: 20261022_schedule_occurrences
Create Date: 2026-10-22

work_dispatches holds accepted, not yet claimed units of domain work. A row is
inserted with its domain record and deleted by the executor's claim, so the
table stays small. The control plane republishes rows whose broker hand-off
was lost.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261022_work_dispatches"
down_revision: Union[str, None] = "20261022_schedule_occurrences"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "work_dispatches",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("family", sa.String(length=50), nullable=False),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("handler", sa.String(length=100), nullable=False),
        sa.Column("handler_version", sa.Integer(), nullable=False),
        sa.Column("queue", sa.String(length=100), nullable=False),
        sa.Column("message", postgresql.JSONB(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("publish_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.UniqueConstraint("family", "subject_id", name="uq_work_dispatches_subject"),
    )
    op.create_index(
        "ix_work_dispatches_status_created",
        "work_dispatches",
        ["status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_work_dispatches_status_created", table_name="work_dispatches")
    op.drop_table("work_dispatches")
