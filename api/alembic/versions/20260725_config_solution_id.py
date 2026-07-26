"""solution-owned config values: Config.solution_id + exact-match uniqueness

Revision ID: 20260725_config_solution_id
Revises: 20260725_builder_tables
Create Date: 2026-07-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260725_config_solution_id"
down_revision: str = "20260725_builder_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "configs",
        sa.Column(
            "solution_id",
            sa.Uuid(),
            sa.ForeignKey("solutions.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_configs_solution_key_unique",
        "configs",
        ["solution_id", "key"],
        unique=True,
        postgresql_where=sa.text("solution_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_configs_solution_key_unique", table_name="configs")
    op.drop_column("configs", "solution_id")
