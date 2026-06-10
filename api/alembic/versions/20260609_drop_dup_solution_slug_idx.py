"""drop duplicate solutions slug unique indexes

20260604_add_solutions created uq_solutions_slug_{org,global}; 20260605
recreated the same predicates as ix_solutions_slug_{org,global}_unique without
dropping the originals. Fresh DBs got 4 indexes, older DBs 2 — drop the uq_
pair (IF EXISTS, to converge both populations). The ix_ pair, mirrored by the
ORM, remains the canonical one.

Revision ID: 20260609_drop_dup_slug_idx
Revises: 20260609_orphan_tbl_ns
Create Date: 2026-06-09 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "20260609_drop_dup_slug_idx"
down_revision = "20260609_orphan_tbl_ns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_solutions_slug_org")
    op.execute("DROP INDEX IF EXISTS uq_solutions_slug_global")


def downgrade() -> None:
    op.create_index(
        "uq_solutions_slug_global",
        "solutions",
        ["slug"],
        unique=True,
        postgresql_where=sa.text("organization_id IS NULL"),
    )
    op.create_index(
        "uq_solutions_slug_org",
        "solutions",
        ["slug", "organization_id"],
        unique=True,
        postgresql_where=sa.text("organization_id IS NOT NULL"),
    )
