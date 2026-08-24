"""link Global workspace source applies to release jobs

Revision ID: 20260824_global_release_jobs
Revises: 20260823_global_op_changes
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260824_global_release_jobs"
down_revision: str = "20260823_global_op_changes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "solution_global_workspace_applies",
        sa.Column("apply_job_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "solution_global_workspace_applies",
        sa.Column("rollback_job_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "ix_solution_global_workspace_applies_apply_job_id",
        "solution_global_workspace_applies",
        ["apply_job_id"],
    )
    op.create_index(
        "ix_solution_global_workspace_applies_rollback_job_id",
        "solution_global_workspace_applies",
        ["rollback_job_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_solution_global_workspace_applies_rollback_job_id",
        table_name="solution_global_workspace_applies",
    )
    op.drop_index(
        "ix_solution_global_workspace_applies_apply_job_id",
        table_name="solution_global_workspace_applies",
    )
    op.drop_column("solution_global_workspace_applies", "rollback_job_id")
    op.drop_column("solution_global_workspace_applies", "apply_job_id")
