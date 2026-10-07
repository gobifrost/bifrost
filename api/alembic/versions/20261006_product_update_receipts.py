"""Persist Product Updates presentation receipts by admin and entry UUID.

Revision ID: 20261006_product_update_receipts
Revises: 20261006_run_retention
Create Date: 2026-10-06
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20261006_product_update_receipts"
down_revision: Union[str, None] = "20261006_run_retention"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "product_update_receipts",
        sa.Column("admin_id", sa.Uuid(), nullable=False),
        sa.Column("entry_id", sa.Uuid(), nullable=False),
        sa.Column(
            "acknowledged_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.ForeignKeyConstraint(["admin_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("admin_id", "entry_id"),
    )


def downgrade() -> None:
    op.drop_table("product_update_receipts")
