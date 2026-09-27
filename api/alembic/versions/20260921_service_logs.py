"""Service trailing logs (supervised workflow services).

Slice 4 of the supervised-services plan
(docs/plans/2026-09-21-services-completion-handoff.md §2.3): bounded
trailing persistence for @service output. The owning worker's claim loop
drains each attempt's Redis stream (bifrost:service-logs:{attempt_id})
into this table and trims each service to its newest ~2000 rows; the
service-wide timeline reads Postgres (GET /api/services/{id}/logs).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260921_service_logs"
down_revision: str | None = "20260920_service_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "service_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "service_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("service_definitions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "attempt_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("service_attempts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("level", sa.String(20), nullable=False, server_default="INFO"),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "ix_service_logs_service_time",
        "service_logs",
        ["service_id", "timestamp", "id"],
    )
    op.create_index(
        "ix_service_logs_attempt_id",
        "service_logs",
        ["attempt_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_service_logs_attempt_id", table_name="service_logs")
    op.drop_index("ix_service_logs_service_time", table_name="service_logs")
    op.drop_table("service_logs")
