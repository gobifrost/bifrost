"""Stop requiring the broker columns on work dispatches

Revision ID: 20261023_dispatch_broker_columns
Revises: 20261023_summary_dispatch
Create Date: 2026-10-23

RabbitMQ is retired (#769 stage 5b): dispatches are only claimed from
PostgreSQL, so ``queue``, ``status``, ``publish_count`` and
``last_published_at`` are no longer written. Older API pods still write them
during a rolling upgrade, so this release only relaxes them; the release
after drops them. New rows default to pool placement.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261023_dispatch_broker_columns"
down_revision: Union[str, None] = "20261023_summary_dispatch"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("work_dispatches", "queue", existing_type=sa.String(length=100), nullable=True)
    op.alter_column(
        "work_dispatches",
        "status",
        existing_type=sa.String(length=20),
        existing_nullable=False,
        server_default="pending",
    )
    op.alter_column(
        "work_dispatches",
        "placement",
        existing_type=sa.String(length=20),
        existing_nullable=False,
        server_default="pool",
    )


def downgrade() -> None:
    op.alter_column(
        "work_dispatches",
        "placement",
        existing_type=sa.String(length=20),
        existing_nullable=False,
        server_default="broker",
    )
    op.alter_column(
        "work_dispatches",
        "status",
        existing_type=sa.String(length=20),
        existing_nullable=False,
        server_default=None,
    )
    op.execute("UPDATE work_dispatches SET queue = family WHERE queue IS NULL")
    op.alter_column("work_dispatches", "queue", existing_type=sa.String(length=100), nullable=False)
