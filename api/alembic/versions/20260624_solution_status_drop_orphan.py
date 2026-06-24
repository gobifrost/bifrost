"""solution status + drop orphan provenance columns

Adds Solution.status (active|inactive, server_default active).
Drops origin_solution_slug / origin_solution_id / orphaned_at from
tables, configs, file_metadata, file_policies.

These columns were added by:
  - 20260606_orphan_provenance.py          (tables + configs)
  - 20260609_orphan_tbl_ns.py              (tables namespace tweak)
  - 20260623_file_solution_id.py           (file_metadata + file_policies)

A forward DROP is cleaner than rewriting history; the test stack runs all
migrations forward.

Revision ID: 20260624_solution_status_drop_orphan
Revises: 20260623_solution_file_jobs
Create Date: 2026-06-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260624_sol_status"
down_revision: str = "20260623_solution_file_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "solutions",
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
    )
    for table in ("tables", "configs", "file_metadata", "file_policies"):
        op.drop_column(table, "origin_solution_slug")
        op.drop_column(table, "origin_solution_id")
        op.drop_column(table, "orphaned_at")


def downgrade() -> None:
    for table in ("tables", "configs", "file_metadata", "file_policies"):
        op.add_column(
            table, sa.Column("orphaned_at", sa.DateTime(timezone=True), nullable=True)
        )
        op.add_column(table, sa.Column("origin_solution_id", sa.Uuid(), nullable=True))
        op.add_column(
            table,
            sa.Column("origin_solution_slug", sa.String(length=255), nullable=True),
        )
    op.drop_column("solutions", "status")
