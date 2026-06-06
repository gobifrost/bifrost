"""one Solution install per (slug, scope)

A Solution is not meant to be installed twice on the same scope (one org, or
global). Without a uniqueness constraint two installs of the same slug could
coexist in one org, and a v2 app's ``path::fn`` workflow ref (which carries no
install id) could then non-deterministically resolve a SIBLING install's
workflow (Codex #8 P1). Enforce single-install-per-scope at the DB so the
ambiguous state is unreachable by construction.

Two partial unique indexes (organization_id is nullable, and NULLs don't compare
equal in a plain unique index, so global installs need their own slug-only one):
- org installs:  UNIQUE(slug, organization_id) WHERE organization_id IS NOT NULL
- global installs: UNIQUE(slug)                WHERE organization_id IS NULL

Revision ID: 20260605_solution_unique_scope
Revises: 20260605_app_identity
Create Date: 2026-06-05 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "20260605_solution_unique_scope"
down_revision = "20260605_app_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_solutions_slug_org_unique",
        "solutions",
        ["slug", "organization_id"],
        unique=True,
        postgresql_where=sa.text("organization_id IS NOT NULL"),
    )
    op.create_index(
        "ix_solutions_slug_global_unique",
        "solutions",
        ["slug"],
        unique=True,
        postgresql_where=sa.text("organization_id IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_solutions_slug_global_unique", table_name="solutions")
    op.drop_index("ix_solutions_slug_org_unique", table_name="solutions")
