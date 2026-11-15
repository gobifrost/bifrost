"""Retain durable executor attribution for PlatformJob attempts."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20261114_job_executor_attr"
down_revision = "20261114_ai_usage_receipts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("platform_jobs", sa.Column("executor_attempt", sa.Integer(), nullable=True))
    op.add_column(
        "platform_jobs",
        sa.Column("executor_worker_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "platform_jobs",
        sa.Column("executor_worker_form", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "platform_jobs",
        sa.Column("executor_capacity_scope", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "platform_jobs",
        sa.Column("executor_capacity_resource_uid", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "platform_jobs",
        sa.Column("executor_compute_capability", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "platform_jobs",
        sa.Column("executor_stage_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "platform_jobs",
        sa.Column("executor_input_sha256", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("platform_jobs", "executor_input_sha256")
    op.drop_column("platform_jobs", "executor_stage_id")
    op.drop_column("platform_jobs", "executor_compute_capability")
    op.drop_column("platform_jobs", "executor_capacity_resource_uid")
    op.drop_column("platform_jobs", "executor_capacity_scope")
    op.drop_column("platform_jobs", "executor_worker_form")
    op.drop_column("platform_jobs", "executor_worker_id")
    op.drop_column("platform_jobs", "executor_attempt")
