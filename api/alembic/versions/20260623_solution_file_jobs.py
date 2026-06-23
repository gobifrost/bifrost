"""add solution_file_jobs table

Revision ID: 20260623_solution_file_jobs
Revises: 20260623_file_solution_id
Create Date: 2026-06-23

C3: install_id is nullable with NO FK to solutions so an orphan job survives
the deletion of the Solution row it was created for.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260623_solution_file_jobs"
down_revision: str = "20260623_file_solution_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "solution_file_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        # Nullable, no FK to solutions (C3: orphan jobs must outlive the Solution row).
        sa.Column("install_id", postgresql.UUID(as_uuid=True), nullable=True),
        # Plain UUID, no FK (same reason).
        sa.Column("origin_solution_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        # Snapshotted file IDs / metadata captured at enqueue time.
        sa.Column("captured_keys", sa.JSON(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_solution_file_jobs_install_id"),
        "solution_file_jobs",
        ["install_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_solution_file_jobs_install_id"), table_name="solution_file_jobs"
    )
    op.drop_table("solution_file_jobs")
