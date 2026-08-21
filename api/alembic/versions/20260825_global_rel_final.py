"""track final Global release revision after manifest-regenerating operations

Revision ID: 20260825_global_rel_final
Revises: 20260824_global_release_jobs
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260825_global_rel_final"
down_revision: str = "20260824_global_release_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "solution_global_workspace_applies",
        sa.Column("released_revision_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_solution_global_workspace_applies_released_revision_id",
        "solution_global_workspace_applies",
        "solution_source_revisions",
        ["released_revision_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_solution_global_workspace_applies_released_revision_id",
        "solution_global_workspace_applies",
        type_="foreignkey",
    )
    op.drop_column("solution_global_workspace_applies", "released_revision_id")
