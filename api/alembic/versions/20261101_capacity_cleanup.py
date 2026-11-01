"""Retain external cleanup eligibility until physical deletion is confirmed."""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20261101_capacity_cleanup"
down_revision: str | None = "20261031_worker_provider_binding"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("execution_workers", sa.Column("capacity_resource_deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(
        "ix_execution_workers_external_cleanup", "execution_workers", ["capacity_scope"],
        postgresql_where=sa.text(
            "form = 'external' AND state IN ('lost', 'retired') "
            "AND capacity_resource_deleted_at IS NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index("ix_execution_workers_external_cleanup", table_name="execution_workers")
    op.drop_column("execution_workers", "capacity_resource_deleted_at")
