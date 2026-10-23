"""Reusable execution workers and dispatch placement

Revision ID: 20261022_execution_workers
Revises: 20261022_work_dispatches
Create Date: 2026-10-22

execution_workers records each reusable worker incarnation (one Kubernetes Job
lifetime). work_dispatches gains a placement (broker or pool) and the pool
worker that claimed it.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261022_execution_workers"
down_revision: Union[str, None] = "20261022_work_dispatches"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "execution_workers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("form", sa.String(length=20), nullable=False, server_default="job"),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("resource_class", sa.String(length=50), nullable=False),
        sa.Column("slots", sa.Integer(), nullable=False),
        sa.Column("kubernetes_namespace", sa.String(length=63), nullable=True),
        sa.Column("kubernetes_job_name", sa.String(length=63), nullable=True, unique=True),
        sa.Column("hostname", sa.String(length=255), nullable=True),
        sa.Column("kubernetes_job_uid", sa.String(length=128), nullable=True),
        sa.Column("kubernetes_pod_uid", sa.String(length=128), nullable=True),
        sa.Column("active_runs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("runs_handled", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_claim_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_reason", sa.String(length=100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("ix_execution_workers_state", "execution_workers", ["state"])
    op.create_table(
        "execution_worker_claims",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "worker_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("execution_workers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("family", sa.String(length=50), nullable=False),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "claimed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "ix_execution_worker_claims_worker", "execution_worker_claims", ["worker_id"]
    )
    op.add_column(
        "work_dispatches",
        sa.Column("placement", sa.String(length=20), nullable=False, server_default="broker"),
    )
    op.add_column(
        "work_dispatches",
        sa.Column("assigned_worker_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "ix_work_dispatches_placement_created",
        "work_dispatches",
        ["placement", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_work_dispatches_placement_created", table_name="work_dispatches")
    op.drop_column("work_dispatches", "assigned_worker_id")
    op.drop_column("work_dispatches", "placement")
    op.drop_index("ix_execution_worker_claims_worker", table_name="execution_worker_claims")
    op.drop_table("execution_worker_claims")
    op.drop_index("ix_execution_workers_state", table_name="execution_workers")
    op.drop_table("execution_workers")
