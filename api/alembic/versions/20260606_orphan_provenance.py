"""orphan provenance columns on tables + configs

Adds origin_solution_slug / origin_solution_id / orphaned_at to both ``tables``
and ``configs`` so a deleted Solution install can orphan (keep) its data-bearing
rows non-destructively, with provenance for reattach on reinstall.

Revision ID: 20260606_orphan_provenance
Revises: 20260606_solution_config_schema
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260606_orphan_provenance"
down_revision = "20260606_solution_config_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("tables", "configs"):
        op.add_column(table, sa.Column("origin_solution_slug", sa.String(length=255), nullable=True))
        op.add_column(table, sa.Column("origin_solution_id", postgresql.UUID(as_uuid=True), nullable=True))
        op.add_column(table, sa.Column("orphaned_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    for table in ("tables", "configs"):
        op.drop_column(table, "orphaned_at")
        op.drop_column(table, "origin_solution_id")
        op.drop_column(table, "origin_solution_slug")
