"""per-install app identity for solution-managed apps

The same Solution can be installed multiple times (criterion 9). An app-bearing
Solution installed for two orgs would deploy two Application rows with the SAME
slug + repo_path — which the GLOBAL unique indexes on slug and repo_path
reject, so the second install fails.

Fix: make the global uniqueness apply only to ad-hoc ``_repo/`` apps
(``solution_id IS NULL``) and scope solution-managed apps per-install:

- ``_repo/`` apps: UNIQUE(slug) and UNIQUE(repo_path) WHERE solution_id IS NULL
  (unchanged behavior for non-solution apps).
- solution apps: UNIQUE(solution_id, slug) and UNIQUE(solution_id, repo_path)
  — each install has its own namespace, so two installs of one Solution don't
  collide.

Revision ID: 20260605_app_identity
Revises: 20260604_add_app_model
Create Date: 2026-06-05 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "20260605_app_identity"
down_revision = "20260604_add_app_model"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the global unique indexes (slug + repo_path).
    op.drop_index("ix_applications_slug_unique", table_name="applications")
    op.drop_index("ix_applications_repo_path_unique", table_name="applications")

    # _repo/ apps keep global uniqueness (partial: only solution_id IS NULL).
    op.create_index(
        "ix_applications_slug_unique",
        "applications",
        ["slug"],
        unique=True,
        postgresql_where=sa.text("solution_id IS NULL"),
    )
    op.create_index(
        "ix_applications_repo_path_unique",
        "applications",
        ["repo_path"],
        unique=True,
        postgresql_where=sa.text("solution_id IS NULL"),
    )

    # solution-managed apps: unique per install (solution_id).
    op.create_index(
        "ix_applications_solution_slug_unique",
        "applications",
        ["solution_id", "slug"],
        unique=True,
        postgresql_where=sa.text("solution_id IS NOT NULL"),
    )
    op.create_index(
        "ix_applications_solution_repo_path_unique",
        "applications",
        ["solution_id", "repo_path"],
        unique=True,
        postgresql_where=sa.text("solution_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_applications_solution_repo_path_unique", table_name="applications")
    op.drop_index("ix_applications_solution_slug_unique", table_name="applications")
    op.drop_index("ix_applications_repo_path_unique", table_name="applications")
    op.drop_index("ix_applications_slug_unique", table_name="applications")
    # Restore the global unique indexes.
    op.create_index(
        "ix_applications_slug_unique", "applications", ["slug"], unique=True
    )
    op.create_index(
        "ix_applications_repo_path_unique", "applications", ["repo_path"], unique=True
    )
