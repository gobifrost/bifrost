"""Executors report memory pressure

Revision ID: 20261023_worker_memory_pressure
Revises: 20261023_dispatch_broker_columns
Create Date: 2026-10-23

An executor over its memory threshold claims no new work. It says so in its
heartbeat, so the worker controller does not count its free slots and starts
a worker Job for the work it leaves unclaimed.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261023_worker_memory_pressure"
down_revision: Union[str, None] = "20261023_dispatch_broker_columns"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "execution_workers",
        sa.Column("memory_pressured", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("execution_workers", "memory_pressured")
