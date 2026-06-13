"""Solution-owned custom claims.

Custom claims can now be deployed as Solution-owned definitions. Loose claims
remain unique per org/global _repo namespace; Solution claims are unique per
install.

Revision ID: 20260613_solution_custom_claims
Revises: 20260612_solution_logo
"""

import sqlalchemy as sa
from alembic import op

revision = "20260613_solution_custom_claims"
down_revision = "20260612_solution_logo"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "custom_claims",
        sa.Column("solution_id", sa.UUID(), nullable=True),
    )
    op.alter_column("custom_claims", "organization_id", nullable=True)
    op.create_foreign_key(
        "custom_claims_solution_id_fkey",
        "custom_claims",
        "solutions",
        ["solution_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_constraint("uq_custom_claims_org_name", "custom_claims", type_="unique")
    op.create_index(
        "uq_custom_claims_org_name",
        "custom_claims",
        ["organization_id", "name"],
        unique=True,
        postgresql_where=sa.text("organization_id IS NOT NULL AND solution_id IS NULL"),
    )
    op.create_index(
        "uq_custom_claims_global_name",
        "custom_claims",
        ["name"],
        unique=True,
        postgresql_where=sa.text("organization_id IS NULL AND solution_id IS NULL"),
    )
    op.create_index(
        "uq_custom_claims_solution_name",
        "custom_claims",
        ["solution_id", "name"],
        unique=True,
        postgresql_where=sa.text("solution_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_custom_claims_solution_name", table_name="custom_claims")
    op.drop_index("uq_custom_claims_global_name", table_name="custom_claims")
    op.drop_index("uq_custom_claims_org_name", table_name="custom_claims")
    op.create_unique_constraint(
        "uq_custom_claims_org_name",
        "custom_claims",
        ["organization_id", "name"],
    )
    op.drop_constraint(
        "custom_claims_solution_id_fkey",
        "custom_claims",
        type_="foreignkey",
    )
    op.alter_column("custom_claims", "organization_id", nullable=False)
    op.drop_column("custom_claims", "solution_id")
