"""add the administrator global-workspace proposal lifecycle

Revision ID: 20260810_builder_global
Revises: 20260810_builder_releases
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260810_builder_global"
down_revision: str = "20260810_builder_releases"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "solution_builder_projects",
        sa.Column(
            "target_kind",
            sa.String(length=24),
            nullable=False,
            server_default="solution",
        ),
    )
    op.create_check_constraint(
        "ck_solution_builder_projects_target_kind",
        "solution_builder_projects",
        "target_kind IN ('solution', 'global_repo')",
    )
    op.create_index(
        "ix_solution_builder_projects_global_target_unique",
        "solution_builder_projects",
        ["target_kind"],
        unique=True,
        postgresql_where=sa.text("target_kind = 'global_repo'"),
    )
    op.create_table(
        "solution_global_workspace_applies",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("solution_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("from_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("to_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "state",
            sa.String(length=16),
            nullable=False,
            server_default="applied",
        ),
        sa.Column("applied_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "applied_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("rolled_back_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "rolled_back_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.CheckConstraint(
            "state IN ('applied', 'superseded', 'rolled_back')",
            name="ck_solution_global_workspace_applies_state",
        ),
        sa.ForeignKeyConstraint(
            ["solution_id"], ["solutions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["from_revision_id"],
            ["solution_source_revisions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["to_revision_id"],
            ["solution_source_revisions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["applied_by"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["rolled_back_by"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_solution_global_workspace_applies_solution_id",
        "solution_global_workspace_applies",
        ["solution_id"],
    )
    op.create_index(
        "ix_solution_global_workspace_applies_solution_applied",
        "solution_global_workspace_applies",
        ["solution_id", "applied_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_solution_global_workspace_applies_solution_applied",
        table_name="solution_global_workspace_applies",
    )
    op.drop_index(
        "ix_solution_global_workspace_applies_solution_id",
        table_name="solution_global_workspace_applies",
    )
    op.drop_table("solution_global_workspace_applies")
    op.drop_index(
        "ix_solution_builder_projects_global_target_unique",
        table_name="solution_builder_projects",
    )
    op.drop_constraint(
        "ck_solution_builder_projects_target_kind",
        "solution_builder_projects",
        type_="check",
    )
    op.drop_column("solution_builder_projects", "target_kind")
