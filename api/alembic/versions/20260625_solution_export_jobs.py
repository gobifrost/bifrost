"""add solution export jobs

Revision ID: 20260625_solution_export_jobs
Revises: 20260624_solution_file_locations
Create Date: 2026-06-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260625_solution_export_jobs"
down_revision: str = "20260624_solution_file_locations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "solution_export_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("solution_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("requested_by_id", sa.Uuid(), nullable=True),
        sa.Column("notification_id", sa.Uuid(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default="pending",
            nullable=False,
        ),
        sa.Column(
            "progress_percent",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("failure_message", sa.Text(), nullable=True),
        sa.Column("encrypted_options", sa.Text(), nullable=True),
        sa.Column("artifact_storage_key", sa.String(length=1024), nullable=True),
        sa.Column("artifact_filename", sa.String(length=255), nullable=True),
        sa.Column("artifact_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("artifact_sha256", sa.String(length=64), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requested_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["solution_id"], ["solutions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_solution_export_jobs_solution_id", "solution_export_jobs", ["solution_id"])
    op.create_index(
        "ix_solution_export_jobs_organization_id",
        "solution_export_jobs",
        ["organization_id"],
    )
    op.create_index(
        "ix_solution_export_jobs_requested_by_id",
        "solution_export_jobs",
        ["requested_by_id"],
    )
    op.create_index("ix_solution_export_jobs_status", "solution_export_jobs", ["status"])
    op.create_index(
        "ix_solution_export_jobs_artifact_storage_key",
        "solution_export_jobs",
        ["artifact_storage_key"],
    )
    op.create_index("ix_solution_export_jobs_expires_at", "solution_export_jobs", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_solution_export_jobs_expires_at", table_name="solution_export_jobs")
    op.drop_index(
        "ix_solution_export_jobs_artifact_storage_key",
        table_name="solution_export_jobs",
    )
    op.drop_index("ix_solution_export_jobs_status", table_name="solution_export_jobs")
    op.drop_index(
        "ix_solution_export_jobs_requested_by_id",
        table_name="solution_export_jobs",
    )
    op.drop_index(
        "ix_solution_export_jobs_organization_id",
        table_name="solution_export_jobs",
    )
    op.drop_index("ix_solution_export_jobs_solution_id", table_name="solution_export_jobs")
    op.drop_table("solution_export_jobs")
