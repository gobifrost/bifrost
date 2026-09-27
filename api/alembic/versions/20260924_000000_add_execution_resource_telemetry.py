"""add execution resource telemetry columns

Revision ID: 20260924_exec_resource
Revises: 20260923_cfg_metadata
Create Date: 2026-09-24

Adds peak_cpu_cores and peak_process_rss_bytes to executions for the
workflow resource report. Historical rows remain null.
"""

from alembic import op
import sqlalchemy as sa

revision = "20260924_exec_resource"
down_revision = "20260923_cfg_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("executions", sa.Column("peak_cpu_cores", sa.Float(), nullable=True))
    op.add_column(
        "executions",
        sa.Column("peak_process_rss_bytes", sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("executions", "peak_process_rss_bytes")
    op.drop_column("executions", "peak_cpu_cores")
