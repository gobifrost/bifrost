"""add external execution identity to platform jobs

Revision ID: 20260807_platform_job_external
Revises: 20260807_reinstate_builder
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260807_platform_job_external"
down_revision: str = "20260807_reinstate_builder"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns(inspector: sa.Inspector, table: str) -> set[str]:
    if not inspector.has_table(table):
        return set()
    return {column["name"] for column in inspector.get_columns(table)}


def _add_column_if_missing(
    inspector: sa.Inspector,
    table: str,
    column: sa.Column,
) -> None:
    if column.name not in _columns(inspector, table):
        op.add_column(table, column)


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    _add_column_if_missing(
        inspector,
        "platform_jobs",
        sa.Column("external_provider", sa.String(length=50), nullable=True),
    )
    inspector = sa.inspect(op.get_bind())
    _add_column_if_missing(
        inspector,
        "platform_jobs",
        sa.Column("external_run_id", sa.String(length=255), nullable=True),
    )
    inspector = sa.inspect(op.get_bind())
    _add_column_if_missing(
        inspector,
        "platform_jobs",
        sa.Column("external_started_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    pass
