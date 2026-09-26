"""Track the active attempt age for retried event deliveries.

Revision ID: 20260825_delivery_attempt
Revises: 20260912_app_sdk_provenance
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260825_delivery_attempt"
down_revision: str | Sequence[str] = "20260912_app_sdk_provenance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "event_deliveries",
        sa.Column("attempt_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_event_deliveries_active_attempt_age",
        "event_deliveries",
        [sa.text("COALESCE(attempt_started_at, created_at)")],
        postgresql_where=sa.text("status IN ('pending', 'queued')"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_event_deliveries_active_attempt_age",
        table_name="event_deliveries",
    )
    op.drop_column("event_deliveries", "attempt_started_at")
