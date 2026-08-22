"""Add bounded presentation thumbnails for entity logos.

Revision ID: 20260814_logo_thumbnails
Revises: 20260812_private_memory
Create Date: 2026-08-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260814_logo_thumbnails"
down_revision: str = "20260812_private_memory"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("applications", "agents", "solutions"):
        op.add_column(table, sa.Column("logo_thumbnail_data", sa.LargeBinary(), nullable=True))
        op.add_column(
            table,
            sa.Column("logo_thumbnail_content_type", sa.String(length=50), nullable=True),
        )
        op.add_column(
            table,
            sa.Column("logo_thumbnail_version", sa.String(length=64), nullable=True),
        )


def downgrade() -> None:
    for table in ("solutions", "agents", "applications"):
        op.drop_column(table, "logo_thumbnail_version")
        op.drop_column(table, "logo_thumbnail_content_type")
        op.drop_column(table, "logo_thumbnail_data")
