"""add durable scheduler-owned platform jobs

Revision ID: 20260728_platform_jobs
Revises: 20260724_knowledge_search_tsv
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260728_platform_jobs"
down_revision: str = "20260724_knowledge_search_tsv"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "platform_jobs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("job_type", sa.String(length=100), nullable=False),
        sa.Column("payload_version", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("dedupe_key", sa.String(length=255), nullable=True),
        sa.Column("organization_id", sa.UUID(), nullable=True),
        sa.Column("requested_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("requested_by_email", sa.String(length=255), nullable=False),
        sa.Column("requested_by_name", sa.String(length=255), nullable=False),
        sa.Column("resource_type", sa.String(length=100), nullable=True),
        sa.Column("resource_id", sa.String(length=255), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("action_url", sa.String(length=500), nullable=True),
        sa.Column("notification_id", sa.UUID(), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("phase", sa.String(length=200), nullable=True),
        sa.Column("progress_current", sa.Integer(), nullable=False),
        sa.Column("progress_total", sa.Integer(), nullable=True),
        sa.Column("progress_percent", sa.Float(), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("error_retryable", sa.Boolean(), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("retry_on_runner_loss", sa.Boolean(), nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("lease_owner", sa.String(length=255), nullable=True),
        sa.Column("lease_token", sa.UUID(), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_platform_jobs_job_type", "platform_jobs", ["job_type"]
    )
    op.create_index(
        "ix_platform_jobs_organization_id",
        "platform_jobs",
        ["organization_id"],
    )
    op.create_index("ix_platform_jobs_status", "platform_jobs", ["status"])
    op.create_index(
        "ix_platform_jobs_lease_expires_at",
        "platform_jobs",
        ["lease_expires_at"],
    )
    op.create_index(
        "ix_platform_jobs_claimable",
        "platform_jobs",
        ["status", "available_at", "created_at"],
    )
    op.create_index(
        "uq_platform_jobs_active_dedupe",
        "platform_jobs",
        ["job_type", "dedupe_key"],
        unique=True,
        postgresql_where=sa.text(
            "dedupe_key IS NOT NULL AND "
            "status IN ('queued', 'running', 'cancel_requested')"
        ),
    )


def downgrade() -> None:
    op.drop_table("platform_jobs")
