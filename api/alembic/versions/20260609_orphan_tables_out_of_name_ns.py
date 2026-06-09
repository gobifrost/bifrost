"""orphaned tables leave the live _repo/ name namespace

Detach-on-uninstall sets solution_id=NULL + orphaned_at, which previously
moved the row INTO the _repo/ unique name namespace — colliding with a live
same-name table (legal coexistence) and failing the uninstall. Orphaned rows
are a shadow namespace: exclude them from the live-name indexes.

Revision ID: 20260609_orphan_tbl_ns
Revises: 20260606_orphan_provenance
Create Date: 2026-06-09 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "20260609_orphan_tbl_ns"
down_revision = "20260606_orphan_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ix_tables_org_name_unique", table_name="tables")
    op.drop_index("ix_tables_global_name_unique", table_name="tables")
    op.create_index(
        "ix_tables_org_name_unique",
        "tables",
        ["organization_id", "name"],
        unique=True,
        postgresql_where=sa.text(
            "organization_id IS NOT NULL AND solution_id IS NULL AND orphaned_at IS NULL"
        ),
    )
    op.create_index(
        "ix_tables_global_name_unique",
        "tables",
        ["name"],
        unique=True,
        postgresql_where=sa.text(
            "organization_id IS NULL AND solution_id IS NULL AND orphaned_at IS NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index("ix_tables_org_name_unique", table_name="tables")
    op.drop_index("ix_tables_global_name_unique", table_name="tables")
    op.create_index(
        "ix_tables_org_name_unique",
        "tables",
        ["organization_id", "name"],
        unique=True,
        postgresql_where=sa.text("organization_id IS NOT NULL AND solution_id IS NULL"),
    )
    op.create_index(
        "ix_tables_global_name_unique",
        "tables",
        ["name"],
        unique=True,
        postgresql_where=sa.text("organization_id IS NULL AND solution_id IS NULL"),
    )
