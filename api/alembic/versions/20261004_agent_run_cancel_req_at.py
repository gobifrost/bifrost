"""Record when an agent run's cancellation was requested

Revision ID: 20261004_agent_run_cancel_req_at
Revises: 20261004_r3b_lineage
Create Date: 2026-10-04

The stale-run sweeper marks an agent run cancelled once it has been in
cancelling for CANCELLING_TIMEOUT_MINUTES. That needs the time the cancel
was requested. Runs already in cancelling get coalesce(started_at,
created_at), the closest recorded time, so they are swept on the next pass.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261004_agent_run_cancel_req_at"
down_revision: Union[str, None] = "20261004_r3b_lineage"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

BACKFILL_SQL = (
    "UPDATE agent_runs SET cancel_requested_at = coalesce(started_at, created_at) "
    "WHERE status = 'cancelling'"
)


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(BACKFILL_SQL)


def downgrade() -> None:
    op.drop_column("agent_runs", "cancel_requested_at")
