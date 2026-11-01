"""Bind externally provisioned workers to their capacity provider resource."""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20261031_worker_provider_binding"
down_revision: str | None = "20261031_worker_handler_counts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("execution_workers", sa.Column("capacity_scope", sa.String(128), nullable=True))
    op.add_column("execution_workers", sa.Column("capacity_resource_uid", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("execution_workers", "capacity_resource_uid")
    op.drop_column("execution_workers", "capacity_scope")
