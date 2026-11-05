"""Persist per-handler occupancy for physical-worker capacity planning."""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20261031_worker_handler_counts"
down_revision: str | None = "20261031_app_sdk_binding"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("execution_workers", sa.Column("active_handler_counts", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("execution_workers", "active_handler_counts")
