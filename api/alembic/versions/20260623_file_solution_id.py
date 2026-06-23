"""solution_id + orphan provenance on file_metadata and file_policies

Adds solution_id (FK → solutions.id CASCADE), origin_solution_slug,
origin_solution_id, and orphaned_at columns to both tables.

Also fixes the two existing partial-unique indexes on each table to exclude
solution rows (H4 correction): the org and global predicates now carry
AND solution_id IS NULL so solution rows are not ambiguous against them.
Adds the new solution-tier unique index and a plain solution_id index.

Revision ID: 20260623_file_solution_id
Revises: 20260623_policy_rules
Create Date: 2026-06-23
"""

import sqlalchemy as sa
from alembic import op

revision = "20260623_file_solution_id"
down_revision = "20260623_policy_rules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # file_metadata                                                        #
    # ------------------------------------------------------------------ #

    # 1. Add the four new columns
    op.add_column(
        "file_metadata",
        sa.Column("solution_id", sa.Uuid(), sa.ForeignKey("solutions.id", ondelete="CASCADE"), nullable=True),
    )
    op.add_column(
        "file_metadata",
        sa.Column("origin_solution_slug", sa.String(255), nullable=True),
    )
    op.add_column(
        "file_metadata",
        sa.Column("origin_solution_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "file_metadata",
        sa.Column("orphaned_at", sa.DateTime(timezone=True), nullable=True),
    )

    # 2. H4: drop the two existing unique indexes and recreate with updated predicates
    op.drop_index("uq_file_metadata_org_location_path", table_name="file_metadata")
    op.create_index(
        "uq_file_metadata_org_location_path",
        "file_metadata",
        ["organization_id", "location", "path"],
        unique=True,
        postgresql_where=sa.text("organization_id IS NOT NULL AND solution_id IS NULL"),
    )

    op.drop_index("uq_file_metadata_global_location_path", table_name="file_metadata")
    op.create_index(
        "uq_file_metadata_global_location_path",
        "file_metadata",
        ["location", "path"],
        unique=True,
        postgresql_where=sa.text("organization_id IS NULL AND solution_id IS NULL"),
    )

    # 3. Add solution-tier unique index and plain solution_id index
    op.create_index(
        "uq_file_metadata_solution_location_path",
        "file_metadata",
        ["solution_id", "location", "path"],
        unique=True,
        postgresql_where=sa.text("solution_id IS NOT NULL"),
    )
    op.create_index("ix_file_metadata_solution_id", "file_metadata", ["solution_id"])

    # ------------------------------------------------------------------ #
    # file_policies                                                        #
    # ------------------------------------------------------------------ #

    # 1. Add the four new columns
    op.add_column(
        "file_policies",
        sa.Column("solution_id", sa.Uuid(), sa.ForeignKey("solutions.id", ondelete="CASCADE"), nullable=True),
    )
    op.add_column(
        "file_policies",
        sa.Column("origin_solution_slug", sa.String(255), nullable=True),
    )
    op.add_column(
        "file_policies",
        sa.Column("origin_solution_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "file_policies",
        sa.Column("orphaned_at", sa.DateTime(timezone=True), nullable=True),
    )

    # 2. H4: drop the two existing unique indexes and recreate with updated predicates
    op.drop_index("uq_file_policies_org_location_path", table_name="file_policies")
    op.create_index(
        "uq_file_policies_org_location_path",
        "file_policies",
        ["organization_id", "location", "path"],
        unique=True,
        postgresql_where=sa.text("organization_id IS NOT NULL AND solution_id IS NULL"),
    )

    op.drop_index("uq_file_policies_global_location_path", table_name="file_policies")
    op.create_index(
        "uq_file_policies_global_location_path",
        "file_policies",
        ["location", "path"],
        unique=True,
        postgresql_where=sa.text("organization_id IS NULL AND solution_id IS NULL"),
    )

    # 3. Add solution-tier unique index and plain solution_id index
    op.create_index(
        "uq_file_policies_solution_location_path",
        "file_policies",
        ["solution_id", "location", "path"],
        unique=True,
        postgresql_where=sa.text("solution_id IS NOT NULL"),
    )
    op.create_index("ix_file_policies_solution_id", "file_policies", ["solution_id"])


def downgrade() -> None:
    # ------------------------------------------------------------------ #
    # file_policies (reverse order)                                        #
    # ------------------------------------------------------------------ #

    op.drop_index("ix_file_policies_solution_id", table_name="file_policies")
    op.drop_index("uq_file_policies_solution_location_path", table_name="file_policies")

    # Restore original predicates (without solution_id IS NULL)
    op.drop_index("uq_file_policies_global_location_path", table_name="file_policies")
    op.create_index(
        "uq_file_policies_global_location_path",
        "file_policies",
        ["location", "path"],
        unique=True,
        postgresql_where=sa.text("organization_id IS NULL"),
    )

    op.drop_index("uq_file_policies_org_location_path", table_name="file_policies")
    op.create_index(
        "uq_file_policies_org_location_path",
        "file_policies",
        ["organization_id", "location", "path"],
        unique=True,
        postgresql_where=sa.text("organization_id IS NOT NULL"),
    )

    op.drop_column("file_policies", "orphaned_at")
    op.drop_column("file_policies", "origin_solution_id")
    op.drop_column("file_policies", "origin_solution_slug")
    op.drop_column("file_policies", "solution_id")

    # ------------------------------------------------------------------ #
    # file_metadata (reverse order)                                        #
    # ------------------------------------------------------------------ #

    op.drop_index("ix_file_metadata_solution_id", table_name="file_metadata")
    op.drop_index("uq_file_metadata_solution_location_path", table_name="file_metadata")

    # Restore original predicates (without solution_id IS NULL)
    op.drop_index("uq_file_metadata_global_location_path", table_name="file_metadata")
    op.create_index(
        "uq_file_metadata_global_location_path",
        "file_metadata",
        ["location", "path"],
        unique=True,
        postgresql_where=sa.text("organization_id IS NULL"),
    )

    op.drop_index("uq_file_metadata_org_location_path", table_name="file_metadata")
    op.create_index(
        "uq_file_metadata_org_location_path",
        "file_metadata",
        ["organization_id", "location", "path"],
        unique=True,
        postgresql_where=sa.text("organization_id IS NOT NULL"),
    )

    op.drop_column("file_metadata", "orphaned_at")
    op.drop_column("file_metadata", "origin_solution_id")
    op.drop_column("file_metadata", "origin_solution_slug")
    op.drop_column("file_metadata", "solution_id")
