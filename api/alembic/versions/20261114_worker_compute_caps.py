"""Persist explicit semantic compute grants for execution workers."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20261114_worker_compute_caps"
down_revision = "20261114_agent_wf_authority"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "execution_workers",
        sa.Column(
            "supported_compute",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("execution_workers", "supported_compute")
