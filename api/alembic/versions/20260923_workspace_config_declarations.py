"""Retain required and display order on workspace configs."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260923_cfg_metadata"
down_revision: str | None = "20260921_service_logs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("configs", sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("configs", sa.Column("position", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("configs", "position")
    op.drop_column("configs", "required")
