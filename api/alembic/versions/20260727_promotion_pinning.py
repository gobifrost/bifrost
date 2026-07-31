"""pin private Solution promotion requests to a deployed revision

Revision ID: 20260727_promotion_pinning
Revises: 20260727_durable_deploy_jobs
Create Date: 2026-07-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260727_promotion_pinning"
down_revision: str | None = "20260727_durable_deploy_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "solution_builder_projects",
        sa.Column("promotion_revision_id", sa.UUID(), nullable=True),
    )
    op.add_column(
        "solution_builder_projects",
        sa.Column("promotion_requested_by", sa.UUID(), nullable=True),
    )
    op.add_column(
        "solution_builder_projects",
        sa.Column(
            "promotion_requested_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_solution_builder_projects_promotion_revision_id",
        "solution_builder_projects",
        "solution_source_revisions",
        ["promotion_revision_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_solution_builder_projects_promotion_requested_by",
        "solution_builder_projects",
        "users",
        ["promotion_requested_by"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_solution_builder_projects_promotion_requested_by",
        "solution_builder_projects",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_solution_builder_projects_promotion_revision_id",
        "solution_builder_projects",
        type_="foreignkey",
    )
    op.drop_column("solution_builder_projects", "promotion_requested_at")
    op.drop_column("solution_builder_projects", "promotion_requested_by")
    op.drop_column("solution_builder_projects", "promotion_revision_id")
