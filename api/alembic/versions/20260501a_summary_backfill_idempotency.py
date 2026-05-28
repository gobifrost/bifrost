"""Add processed run tracking to summary backfill jobs.

Revision ID: 20260501a_bf_idempotency
Revises: 20260524_oauth_user_cascade
Create Date: 2026-05-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260501a_bf_idempotency"
down_revision: str | None = "20260524_oauth_user_cascade"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "summary_backfill_jobs",
        sa.Column(
            "processed_run_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("summary_backfill_jobs", "processed_run_ids")
