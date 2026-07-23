"""index execution start times for dashboard aggregation

Revision ID: 20260723_exec_started_idx
Revises: 20260702_deployjob_install_null
Create Date: 2026-07-23
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260723_exec_started_idx"
down_revision: str = "20260702_deployjob_install_null"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_executions_started_at",
        "executions",
        ["started_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_executions_started_at", table_name="executions")
