"""builder build jobs table

Revision ID: 20260725_build_jobs
Revises: 20260725_config_solution_id
Create Date: 2026-07-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "20260725_build_jobs"
down_revision: str = "20260725_config_solution_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "solution_build_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "solution_id",
            sa.Uuid(),
            sa.ForeignKey("solutions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "app_id",
            sa.Uuid(),
            sa.ForeignKey("applications.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "source_revision_id",
            sa.Uuid(),
            sa.ForeignKey("solution_source_revisions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "requested_by",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("toolchain_version", sa.String(length=64), nullable=False),
        sa.Column("dependency_digest", sa.String(length=64), nullable=True),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="queued"
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("log_excerpt", sa.Text(), nullable=True),
        sa.Column("output_manifest", JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_solution_build_jobs_solution_id", "solution_build_jobs", ["solution_id"]
    )
    op.create_index(
        "ix_solution_build_jobs_solution_created",
        "solution_build_jobs",
        ["solution_id", "created_at"],
    )
    op.create_index(
        "ix_solution_build_jobs_reuse_key",
        "solution_build_jobs",
        ["source_sha256", "app_id", "toolchain_version"],
    )


def downgrade() -> None:
    op.drop_table("solution_build_jobs")
