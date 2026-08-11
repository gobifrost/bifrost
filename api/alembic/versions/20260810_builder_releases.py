"""publish Builder revisions as separate shared Solution releases

Revision ID: 20260810_builder_releases
Revises: 20260810_builder_checkpoints

This forward-only migration preserves private Builder Solutions as source
projects. Each approved target scope receives a separate shared install that
can be updated by later pinned releases.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260810_builder_releases"
down_revision: str = "20260810_builder_checkpoints"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "solution_builder_releases",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_solution_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("published_solution_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_organization_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("published_revision_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "runtime_mode",
            sa.String(length=16),
            nullable=False,
            server_default="isolated",
        ),
        sa.Column("approved_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "published_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
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
        sa.CheckConstraint(
            "source_solution_id <> published_solution_id",
            name="ck_solution_builder_releases_distinct_solutions",
        ),
        sa.CheckConstraint(
            "runtime_mode IN ('isolated', 'trusted')",
            name="ck_solution_builder_releases_runtime_mode",
        ),
        sa.ForeignKeyConstraint(
            ["source_solution_id"],
            ["solutions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["published_solution_id"],
            ["solutions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["target_organization_id"],
            ["organizations.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["published_revision_id"],
            ["solution_source_revisions.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["approved_by"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("published_solution_id"),
    )
    op.create_index(
        "ix_solution_builder_releases_source_solution_id",
        "solution_builder_releases",
        ["source_solution_id"],
    )
    op.create_index(
        "ix_solution_builder_releases_target_organization_id",
        "solution_builder_releases",
        ["target_organization_id"],
    )
    op.create_index(
        "ix_solution_builder_releases_source_org_unique",
        "solution_builder_releases",
        ["source_solution_id", "target_organization_id"],
        unique=True,
        postgresql_where=sa.text("target_organization_id IS NOT NULL"),
    )
    op.create_index(
        "ix_solution_builder_releases_source_global_unique",
        "solution_builder_releases",
        ["source_solution_id"],
        unique=True,
        postgresql_where=sa.text("target_organization_id IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_solution_builder_releases_source_global_unique",
        table_name="solution_builder_releases",
    )
    op.drop_index(
        "ix_solution_builder_releases_source_org_unique",
        table_name="solution_builder_releases",
    )
    op.drop_index(
        "ix_solution_builder_releases_target_organization_id",
        table_name="solution_builder_releases",
    )
    op.drop_index(
        "ix_solution_builder_releases_source_solution_id",
        table_name="solution_builder_releases",
    )
    op.drop_table("solution_builder_releases")
