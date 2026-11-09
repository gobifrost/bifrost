"""Retain qualified startup memory measurements on worker incarnations."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261108_worker_calibration"
down_revision = "20261107_running_run_lineage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("execution_workers", sa.Column("memory_calibration", postgresql.JSONB(), nullable=True))
    op.create_index(
        "ix_execution_workers_calibration", "execution_workers",
        ["form", sa.text("(memory_calibration['environment'])"), "memory_limit_bytes", sa.text("created_at DESC")],
        postgresql_where=sa.text("memory_calibration IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_execution_workers_calibration", table_name="execution_workers")
    op.drop_column("execution_workers", "memory_calibration")
