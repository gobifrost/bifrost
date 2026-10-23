"""Drop the broker columns from work dispatches

Revision ID: 20261023_drop_dispatch_broker
Revises: 20261023_legacy_run_lineage
Create Date: 2026-10-23

RabbitMQ is retired (#769 stage 5b) and every dispatch is claimed from
PostgreSQL. No release ever wrote ``queue``, ``status``, ``publish_count``,
``last_published_at`` or ``placement`` (only dev builds did), so they are
dropped without a relaxed release in between. The claim and the reconciler
order unclaimed work by ``created_at``, which keeps an index of its own.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261023_drop_dispatch_broker"
down_revision: Union[str, None] = "20261023_legacy_run_lineage"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index("ix_work_dispatches_status_created", table_name="work_dispatches")
    op.drop_index("ix_work_dispatches_placement_created", table_name="work_dispatches")
    op.drop_column("work_dispatches", "queue")
    op.drop_column("work_dispatches", "status")
    op.drop_column("work_dispatches", "publish_count")
    op.drop_column("work_dispatches", "last_published_at")
    op.drop_column("work_dispatches", "placement")
    op.create_index("ix_work_dispatches_created", "work_dispatches", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_work_dispatches_created", table_name="work_dispatches")
    op.add_column(
        "work_dispatches",
        sa.Column("placement", sa.String(length=20), nullable=True, server_default="pool"),
    )
    op.add_column(
        "work_dispatches",
        sa.Column("last_published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "work_dispatches",
        sa.Column("publish_count", sa.Integer(), nullable=True, server_default="0"),
    )
    op.add_column(
        "work_dispatches",
        sa.Column("status", sa.String(length=20), nullable=True, server_default="pending"),
    )
    op.add_column("work_dispatches", sa.Column("queue", sa.String(length=100), nullable=True))
    op.create_index(
        "ix_work_dispatches_placement_created",
        "work_dispatches",
        ["placement", "created_at"],
    )
    op.create_index(
        "ix_work_dispatches_status_created",
        "work_dispatches",
        ["status", "created_at"],
    )
