"""add resumable Builder turn checkpoints

Revision ID: 20260810_builder_checkpoints
Revises: 20260807_app_runtime_mode

This is a forward-only Builder migration after the withdrawal tombstone. Failed
or cancelled turns can retain an inert workspace/OpenCode checkpoint without
advancing the Solution's current revision.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260810_builder_checkpoints"
down_revision: str = "20260807_app_runtime_mode"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "solution_builder_turns",
        sa.Column("resume_from_turn_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "solution_builder_turns",
        sa.Column("checkpoint_sha256", sa.String(length=64), nullable=True),
    )
    op.create_foreign_key(
        "fk_solution_builder_turns_resume_from_turn_id",
        "solution_builder_turns",
        "solution_builder_turns",
        ["resume_from_turn_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_solution_builder_turns_resume_from_turn_id",
        "solution_builder_turns",
        ["resume_from_turn_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_solution_builder_turns_resume_from_turn_id",
        table_name="solution_builder_turns",
    )
    op.drop_constraint(
        "fk_solution_builder_turns_resume_from_turn_id",
        "solution_builder_turns",
        type_="foreignkey",
    )
    op.drop_column("solution_builder_turns", "checkpoint_sha256")
    op.drop_column("solution_builder_turns", "resume_from_turn_id")
