"""Fence remote launcher control sessions on the execution-worker row.

Revision ID: 20261025_remote_worker_ownership
Revises: 20261024_isolated_pod_request
Create Date: 2026-10-25

The remote launcher is a physical process and an API replica owns its control
session only through a database-clock lease.  The fields intentionally live on
the canonical worker incarnation, rather than creating a second worker or
transport table.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20261025_remote_worker_ownership"
down_revision: Union[str, None] = "20261024_isolated_pod_request"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("execution_workers", sa.Column("remote_launcher_id", sa.Uuid(), nullable=True))
    op.add_column("execution_workers", sa.Column("remote_owner_id", sa.Uuid(), nullable=True))
    op.add_column(
        "execution_workers",
        sa.Column("remote_session_generation", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "execution_workers",
        sa.Column("remote_owner_lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "execution_workers", sa.Column("remote_auth_nonce_digest", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "execution_workers",
        sa.Column("remote_hello_sequence", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.add_column(
        "execution_workers",
        sa.Column(
            "supported_handlers",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text(
                "'[\"workflow.execute\", \"agent.run\", \"agent.summarize\", "
                "\"agent.summarize_backfill\"]'::jsonb"
            ),
        ),
    )
    op.create_unique_constraint(
        "uq_execution_workers_remote_launcher_id", "execution_workers", ["remote_launcher_id"]
    )
    op.create_index(
        "ix_execution_workers_remote_owner_lease",
        "execution_workers",
        ["remote_owner_lease_expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_execution_workers_remote_owner_lease", table_name="execution_workers")
    op.drop_constraint(
        "uq_execution_workers_remote_launcher_id", "execution_workers", type_="unique"
    )
    op.drop_column("execution_workers", "supported_handlers")
    op.drop_column("execution_workers", "remote_hello_sequence")
    op.drop_column("execution_workers", "remote_auth_nonce_digest")
    op.drop_column("execution_workers", "remote_owner_lease_expires_at")
    op.drop_column("execution_workers", "remote_session_generation")
    op.drop_column("execution_workers", "remote_owner_id")
    op.drop_column("execution_workers", "remote_launcher_id")
