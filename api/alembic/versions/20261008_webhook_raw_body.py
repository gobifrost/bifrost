"""Preserve accepted webhook request bytes on events.

Revision ID: 20261008_webhook_raw_body
Revises: 20261006_product_update_receipts
Create Date: 2026-10-08
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20261008_webhook_raw_body"
down_revision: Union[str, None] = "20261006_product_update_receipts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("events", sa.Column("raw_body", sa.LargeBinary(), nullable=True))


def downgrade() -> None:
    op.drop_column("events", "raw_body")
