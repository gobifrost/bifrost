"""Make provider-operation usage receipts idempotent."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20261114_ai_usage_receipts"
down_revision = "20261114_worker_compute_caps"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ai_usage",
        sa.Column("receipt_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_unique_constraint("uq_ai_usage_receipt_id", "ai_usage", ["receipt_id"])


def downgrade() -> None:
    op.drop_constraint("uq_ai_usage_receipt_id", "ai_usage", type_="unique")
    op.drop_column("ai_usage", "receipt_id")
