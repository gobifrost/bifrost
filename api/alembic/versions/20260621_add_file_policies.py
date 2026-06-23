"""add file metadata and policy prefix tables

Revision ID: 20260621_add_file_policies
Revises: 20260617_solution_deploy_jobs
Create Date: 2026-06-21
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "20260621_add_file_policies"
down_revision = "20260617_solution_deploy_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "file_metadata",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("location", sa.String(length=255), nullable=False),
        sa.Column("path", sa.String(length=2000), nullable=False),
        sa.Column("s3_key", sa.String(length=2500), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("created_by", UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "ix_file_metadata_organization_id",
        "file_metadata",
        ["organization_id"],
    )
    op.create_index(
        "ix_file_metadata_lookup",
        "file_metadata",
        ["organization_id", "location", "path"],
    )
    op.create_index(
        "uq_file_metadata_org_location_path",
        "file_metadata",
        ["organization_id", "location", "path"],
        unique=True,
        postgresql_where=sa.text("organization_id IS NOT NULL"),
    )
    op.create_index(
        "uq_file_metadata_global_location_path",
        "file_metadata",
        ["location", "path"],
        unique=True,
        postgresql_where=sa.text("organization_id IS NULL"),
    )

    op.create_table(
        "file_policies",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("location", sa.String(length=255), nullable=False),
        sa.Column("path", sa.String(length=2000), nullable=False),
        sa.Column("policies", JSONB, nullable=False),
        sa.Column("created_by", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "ix_file_policies_organization_id",
        "file_policies",
        ["organization_id"],
    )
    op.create_index(
        "ix_file_policies_lookup",
        "file_policies",
        ["organization_id", "location", "path"],
    )
    op.create_index(
        "uq_file_policies_org_location_path",
        "file_policies",
        ["organization_id", "location", "path"],
        unique=True,
        postgresql_where=sa.text("organization_id IS NOT NULL"),
    )
    op.create_index(
        "uq_file_policies_global_location_path",
        "file_policies",
        ["location", "path"],
        unique=True,
        postgresql_where=sa.text("organization_id IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_file_policies_global_location_path", table_name="file_policies")
    op.drop_index("uq_file_policies_org_location_path", table_name="file_policies")
    op.drop_index("ix_file_policies_lookup", table_name="file_policies")
    op.drop_index("ix_file_policies_organization_id", table_name="file_policies")
    op.drop_table("file_policies")

    op.drop_index("uq_file_metadata_global_location_path", table_name="file_metadata")
    op.drop_index("uq_file_metadata_org_location_path", table_name="file_metadata")
    op.drop_index("ix_file_metadata_lookup", table_name="file_metadata")
    op.drop_index("ix_file_metadata_organization_id", table_name="file_metadata")
    op.drop_table("file_metadata")
