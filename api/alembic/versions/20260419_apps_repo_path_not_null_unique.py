"""apps repo_path NOT NULL + unique

Revision ID: 20260419_apps_repo_path
Revises: 20260406_audit_overhaul
Create Date: 2026-04-19

Promotes applications.repo_path to a first-class column:
- Backfills any NULL values to 'apps/{slug}' (the prior repo_prefix fallback).
- Fails if backfill would produce duplicate values (shouldn't happen since slug
  is unique, but backstop for corrupted data — operator must resolve manually).
- Alters the column to NOT NULL.
- Adds a unique index so two applications cannot claim the same source prefix.

The repo_prefix property fallback to 'apps/{slug}' is removed in the same PR;
this migration is what makes that safe.
"""

from alembic import op
import sqlalchemy as sa


revision = "20260419_apps_repo_path"
down_revision = "20260406_audit_overhaul"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Backfill NULL values using the prior convention default.
    op.execute(
        "UPDATE applications SET repo_path = 'apps/' || slug WHERE repo_path IS NULL"
    )

    # Fail fast if a duplicate slipped through (e.g. two apps with same slug
    # in corrupted data, or a manually-set duplicate repo_path).
    dup_check = op.get_bind().execute(
        sa.text(
            "SELECT repo_path, COUNT(*) AS n FROM applications "
            "GROUP BY repo_path HAVING COUNT(*) > 1"
        )
    ).fetchall()
    if dup_check:
        formatted = ", ".join(f"{row[0]!r} ({row[1]}x)" for row in dup_check)
        raise RuntimeError(
            f"Cannot enforce unique repo_path — duplicates found: {formatted}. "
            "Resolve manually before retrying this migration."
        )

    op.alter_column("applications", "repo_path", nullable=False)
    op.create_index(
        "ix_applications_repo_path_unique",
        "applications",
        ["repo_path"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_applications_repo_path_unique", table_name="applications")
    op.alter_column("applications", "repo_path", nullable=True)
