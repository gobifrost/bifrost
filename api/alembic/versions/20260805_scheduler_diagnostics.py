"""add scheduler diagnostics and platform-job admission fields

Revision ID: 20260805_scheduler_diagnostics
Revises: 20260731_scheduler_leases, 20260805_form_captcha
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260805_scheduler_diagnostics"
down_revision: str | Sequence[str] = (
    "20260731_scheduler_leases",
    "20260805_form_captcha",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("platform_jobs", sa.Column("encrypted_payload", sa.Text(), nullable=True))
    op.add_column("platform_jobs", sa.Column("resource_lock_key", sa.String(255), nullable=True))
    op.add_column(
        "platform_jobs",
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
    )
    op.add_column("platform_jobs", sa.Column("memory_start_bytes", sa.BigInteger(), nullable=True))
    op.add_column("platform_jobs", sa.Column("memory_peak_bytes", sa.BigInteger(), nullable=True))
    op.add_column("platform_jobs", sa.Column("memory_limit_bytes", sa.BigInteger(), nullable=True))
    op.create_index(
        "ix_platform_jobs_resource_lock_key",
        "platform_jobs",
        ["resource_lock_key"],
    )
    op.drop_index("uq_platform_jobs_active_dedupe", table_name="platform_jobs")
    op.create_index(
        "uq_platform_jobs_active_dedupe",
        "platform_jobs",
        ["job_type", "dedupe_key"],
        unique=True,
        postgresql_where=sa.text(
            "dedupe_key IS NOT NULL AND "
            "status IN ('queued', 'running', 'waiting', 'cancel_requested')"
        ),
    )

    op.create_table(
        "scheduler_replicas",
        sa.Column("id", sa.String(255), primary_key=True),
        sa.Column("hostname", sa.String(255), nullable=False),
        sa.Column("pid", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("memory_current_bytes", sa.BigInteger(), nullable=True),
        sa.Column("memory_limit_bytes", sa.BigInteger(), nullable=True),
    )
    op.create_index(
        "ix_scheduler_replicas_last_heartbeat_at",
        "scheduler_replicas",
        ["last_heartbeat_at"],
    )
    op.create_table(
        "scheduler_task_states",
        sa.Column("task_id", sa.String(100), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("schedule", sa.String(100), nullable=False),
        sa.Column("execution_mode", sa.String(30), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("TRUE")),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_table(
        "scheduler_task_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("task_id", sa.String(100), nullable=False),
        sa.Column("leader_owner_id", sa.String(255), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column(
            "platform_job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("platform_jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("summary", sa.String(500), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
    )
    op.create_index("ix_scheduler_task_runs_task_id", "scheduler_task_runs", ["task_id"])
    op.create_index("ix_scheduler_task_runs_status", "scheduler_task_runs", ["status"])
    op.create_index("ix_scheduler_task_runs_platform_job_id", "scheduler_task_runs", ["platform_job_id"])
    op.create_index(
        "ix_scheduler_task_runs_task_started",
        "scheduler_task_runs",
        ["task_id", "started_at"],
    )
    op.create_table(
        "system_diagnostic_logs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("source", sa.String(30), nullable=False),
        sa.Column("level", sa.String(10), nullable=False),
        sa.Column("code", sa.String(100), nullable=False),
        sa.Column("message", sa.String(2000), nullable=False),
        sa.Column(
            "scheduler_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("scheduler_task_runs.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "platform_job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("platform_jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    for column in ("source", "level", "scheduler_run_id", "platform_job_id", "created_at"):
        op.create_index(
            f"ix_system_diagnostic_logs_{column}",
            "system_diagnostic_logs",
            [column],
        )


def downgrade() -> None:
    op.drop_table("system_diagnostic_logs")
    op.drop_table("scheduler_task_runs")
    op.drop_table("scheduler_task_states")
    op.drop_table("scheduler_replicas")
    op.drop_index("ix_platform_jobs_resource_lock_key", table_name="platform_jobs")
    op.drop_index("uq_platform_jobs_active_dedupe", table_name="platform_jobs")
    op.create_index(
        "uq_platform_jobs_active_dedupe",
        "platform_jobs",
        ["job_type", "dedupe_key"],
        unique=True,
        postgresql_where=sa.text(
            "dedupe_key IS NOT NULL AND status IN ('queued', 'running', 'cancel_requested')"
        ),
    )
    for column in (
        "memory_limit_bytes",
        "memory_peak_bytes",
        "memory_start_bytes",
        "priority",
        "resource_lock_key",
        "encrypted_payload",
    ):
        op.drop_column("platform_jobs", column)
