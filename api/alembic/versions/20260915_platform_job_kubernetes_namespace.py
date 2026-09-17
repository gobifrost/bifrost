"""Store Kubernetes namespace on platform jobs.

Revision ID: 20260915_platform_job_k8s_ns
Revises: 20260915_platform_job_k8s
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260915_platform_job_k8s_ns"
down_revision: str | None = "20260915_platform_job_k8s"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "platform_jobs",
        sa.Column("kubernetes_namespace", sa.String(length=63), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("platform_jobs", "kubernetes_namespace")
