"""private Solution ownership: owner_user_id + visibility + partial slug uniqueness

Revision ID: 20260725_private_visibility
Revises: 20260723_exec_started_idx
Create Date: 2026-07-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260725_private_visibility"
down_revision: str = "20260723_exec_started_idx"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "solutions",
        sa.Column(
            "owner_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "solutions",
        sa.Column(
            "visibility",
            sa.String(length=16),
            nullable=False,
            server_default="shared",
        ),
    )

    # Slug uniqueness becomes visibility-aware: the existing org/global partial
    # indexes now apply only to shared installs, and private installs are unique
    # per (owner, slug) so two users in one org can each have a private "todo".
    op.drop_index("ix_solutions_slug_org_unique", table_name="solutions")
    op.drop_index("ix_solutions_slug_global_unique", table_name="solutions")
    op.create_index(
        "ix_solutions_slug_org_unique",
        "solutions",
        ["slug", "organization_id"],
        unique=True,
        postgresql_where=sa.text(
            "organization_id IS NOT NULL AND visibility = 'shared'"
        ),
    )
    op.create_index(
        "ix_solutions_slug_global_unique",
        "solutions",
        ["slug"],
        unique=True,
        postgresql_where=sa.text("organization_id IS NULL AND visibility = 'shared'"),
    )
    op.create_index(
        "ix_solutions_owner_slug_private_unique",
        "solutions",
        ["owner_user_id", "slug"],
        unique=True,
        postgresql_where=sa.text("visibility = 'private'"),
    )
    op.create_index("ix_solutions_owner_user_id", "solutions", ["owner_user_id"])


def downgrade() -> None:
    op.drop_index("ix_solutions_owner_user_id", table_name="solutions")
    op.drop_index("ix_solutions_owner_slug_private_unique", table_name="solutions")
    op.drop_index("ix_solutions_slug_global_unique", table_name="solutions")
    op.drop_index("ix_solutions_slug_org_unique", table_name="solutions")
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
    op.drop_column("solutions", "visibility")
    op.drop_column("solutions", "owner_user_id")
