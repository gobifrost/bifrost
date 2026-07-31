"""move Solution deploy jobs to durable staged worker execution

Revision ID: 20260727_durable_deploy_jobs
Revises: 20260727_build_plane_jobs
Create Date: 2026-07-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260727_durable_deploy_jobs"
down_revision: str | None = "20260727_build_plane_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "solution_deploy_jobs",
        sa.Column(
            "kind",
            sa.String(length=32),
            nullable=False,
            server_default="deploy",
        ),
    )
    op.add_column(
        "solution_deploy_jobs",
        sa.Column("encrypted_options", sa.Text(), nullable=True),
    )
    op.add_column(
        "solution_deploy_jobs",
        sa.Column("input_key", sa.Text(), nullable=True),
    )
    op.add_column(
        "solution_deploy_jobs",
        sa.Column("input_sha256", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "solution_deploy_jobs",
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "solution_deploy_jobs",
        sa.Column("claim_token", sa.UUID(), nullable=True),
    )
    op.add_column(
        "solution_deploy_jobs",
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "solution_deploy_jobs",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "solution_deploy_jobs",
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "solution_deploy_jobs",
        sa.Column(
            "attempt_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.create_check_constraint(
        "ck_solution_deploy_jobs_kind",
        "solution_deploy_jobs",
        "kind IN ('deploy', 'install', 'install_from_repo')",
    )
    op.create_index(
        "ix_solution_deploy_jobs_recovery",
        "solution_deploy_jobs",
        ["status", "lease_expires_at", "published_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_solution_deploy_jobs_recovery",
        table_name="solution_deploy_jobs",
    )
    op.drop_constraint(
        "ck_solution_deploy_jobs_kind",
        "solution_deploy_jobs",
        type_="check",
    )
    op.drop_column("solution_deploy_jobs", "attempt_count")
    op.drop_column("solution_deploy_jobs", "started_at")
    op.drop_column("solution_deploy_jobs", "lease_expires_at")
    op.drop_column("solution_deploy_jobs", "claimed_at")
    op.drop_column("solution_deploy_jobs", "claim_token")
    op.drop_column("solution_deploy_jobs", "published_at")
    op.drop_column("solution_deploy_jobs", "input_sha256")
    op.drop_column("solution_deploy_jobs", "input_key")
    op.drop_column("solution_deploy_jobs", "encrypted_options")
    op.drop_column("solution_deploy_jobs", "kind")
