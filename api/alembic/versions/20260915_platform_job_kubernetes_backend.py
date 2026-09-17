"""Add Kubernetes backend metadata to platform jobs.

Revision ID: 20260915_platform_job_k8s
Revises: 20260916_solution_inbound
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260915_platform_job_k8s"
down_revision: str | None = "20260916_solution_inbound"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "platform_jobs",
        sa.Column(
            "execution_backend",
            sa.String(length=30),
            server_default="local",
            nullable=False,
        ),
    )
    op.add_column(
        "platform_jobs",
        sa.Column("kubernetes_job_name", sa.String(length=253), nullable=True),
    )
    op.add_column(
        "platform_jobs",
        sa.Column("kubernetes_job_uid", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "platform_jobs",
        sa.Column("kubernetes_pod_uid", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "platform_jobs",
        sa.Column(
            "kubernetes_launch_started_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "platform_jobs",
        sa.Column("runner_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.drop_index("ix_platform_jobs_claimable", table_name="platform_jobs")
    op.create_index(
        "ix_platform_jobs_claimable",
        "platform_jobs",
        ["execution_backend", "status", "available_at", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_platform_jobs_claimable", table_name="platform_jobs")
    op.create_index(
        "ix_platform_jobs_claimable",
        "platform_jobs",
        ["status", "available_at", "created_at"],
        unique=False,
    )
    op.drop_column("platform_jobs", "runner_started_at")
    op.drop_column("platform_jobs", "kubernetes_launch_started_at")
    op.drop_column("platform_jobs", "kubernetes_pod_uid")
    op.drop_column("platform_jobs", "kubernetes_job_uid")
    op.drop_column("platform_jobs", "kubernetes_job_name")
    op.drop_column("platform_jobs", "execution_backend")
