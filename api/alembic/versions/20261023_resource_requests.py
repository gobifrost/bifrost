"""Work requests memory from an executor (#769 stage 6a)

Revision ID: 20261023_resource_requests
Revises: 20261023_drop_dispatch_broker
Create Date: 2026-10-23

Every dispatch carries the memory its work is expected to add; an executor
claims work that fits its free budget and reports that budget, so the worker
controller sizes capacity by memory instead of slots. A workflow's request
is learned from its runs' peak memory growth.

Expand-only: an older API inserts dispatches without a request (the server
default applies) and an older executor ignores the new columns.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261023_resource_requests"
down_revision: Union[str, None] = "20261023_drop_dispatch_broker"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DEFAULT_REQUEST_BYTES = 64 * 1024 * 1024


def upgrade() -> None:
    op.add_column(
        "work_dispatches",
        sa.Column(
            "memory_request_bytes",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text(str(DEFAULT_REQUEST_BYTES)),
        ),
    )
    op.add_column(
        "execution_workers",
        sa.Column("memory_budget_bytes", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "execution_workers",
        sa.Column(
            "memory_reserved_bytes", sa.BigInteger(), nullable=False, server_default=sa.text("0")
        ),
    )
    op.add_column(
        "execution_workers",
        sa.Column("memory_limit_bytes", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "execution_workers",
        sa.Column("reserved_for_dispatch_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "executions",
        sa.Column("peak_memory_growth_bytes", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "workflows",
        sa.Column("learned_memory_bytes", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "workflows",
        sa.Column("learned_memory_code_hash", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "workflows",
        sa.Column("memory_request_mib", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("workflows", "memory_request_mib")
    op.drop_column("workflows", "learned_memory_code_hash")
    op.drop_column("workflows", "learned_memory_bytes")
    op.drop_column("executions", "peak_memory_growth_bytes")
    op.drop_column("execution_workers", "reserved_for_dispatch_id")
    op.drop_column("execution_workers", "memory_limit_bytes")
    op.drop_column("execution_workers", "memory_reserved_bytes")
    op.drop_column("execution_workers", "memory_budget_bytes")
    op.drop_column("work_dispatches", "memory_request_bytes")
