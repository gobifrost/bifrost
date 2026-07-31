"""Make build jobs claimable before application deploy.

Revision ID: 20260727_build_plane_jobs
Revises: 20260727_agent_bundle_path
Create Date: 2026-07-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260727_build_plane_jobs"
down_revision: str | None = "20260727_agent_bundle_path"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Build jobs are committed and executed before a source deploy creates the
    # deterministic Application id. Keeping this FK makes the coordinator wait
    # on a row the deploy cannot commit until the build completes.
    op.drop_constraint(
        "solution_build_jobs_app_id_fkey",
        "solution_build_jobs",
        type_="foreignkey",
    )
    op.add_column(
        "solution_build_jobs",
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "solution_build_jobs",
        sa.Column("last_progress_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("solution_build_jobs", "last_progress_at")
    op.drop_column("solution_build_jobs", "claimed_at")
    op.create_foreign_key(
        "solution_build_jobs_app_id_fkey",
        "solution_build_jobs",
        "applications",
        ["app_id"],
        ["id"],
        ondelete="SET NULL",
    )
