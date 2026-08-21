"""allow organization-target Builder projects

Revision ID: 20260821_builder_org_target
Revises: 20260820_builder_agentless

This forward-only migration widens the builder project target vocabulary so
the Builder can persist organization-scoped workspaces with the same hidden
Solution envelope used by the rest of the builder surface.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260821_builder_org_target"
down_revision: str = "20260820_builder_agentless"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_solution_builder_projects_target_kind",
        "solution_builder_projects",
        type_="check",
    )
    op.create_check_constraint(
        "ck_solution_builder_projects_target_kind",
        "solution_builder_projects",
        "target_kind IN ('solution', 'organization', 'global_repo')",
    )
    op.create_index(
        "ix_solution_builder_projects_target_kind",
        "solution_builder_projects",
        ["target_kind"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_solution_builder_projects_target_kind",
        table_name="solution_builder_projects",
    )
    op.drop_constraint(
        "ck_solution_builder_projects_target_kind",
        "solution_builder_projects",
        type_="check",
    )
    op.create_check_constraint(
        "ck_solution_builder_projects_target_kind",
        "solution_builder_projects",
        "target_kind IN ('solution', 'global_repo')",
    )
