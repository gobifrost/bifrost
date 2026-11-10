"""Add durable execution lanes for reserved Fast Path workers.

Existing installations are deliberately backfilled to normal worker/dispatch
lanes and automatic workflow policy, preserving their scheduler behaviour.
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20261109_reserved_fast_path"
down_revision: str | None = "20261109_merge_calibration"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workflows",
        sa.Column("execution_lane", sa.String(length=20), nullable=False, server_default="auto"),
    )
    op.add_column(
        "executions",
        sa.Column("execution_lane", sa.String(length=20), nullable=False, server_default="normal"),
    )
    op.add_column(
        "work_dispatches",
        sa.Column("execution_lane", sa.String(length=20), nullable=False, server_default="normal"),
    )
    op.add_column(
        "execution_workers",
        sa.Column("execution_lane", sa.String(length=20), nullable=False, server_default="normal"),
    )
    op.create_index(
        "ix_work_dispatches_lane_created", "work_dispatches", ["execution_lane", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_work_dispatches_lane_created", table_name="work_dispatches")
    op.drop_column("execution_workers", "execution_lane")
    op.drop_column("work_dispatches", "execution_lane")
    op.drop_column("executions", "execution_lane")
    op.drop_column("workflows", "execution_lane")
