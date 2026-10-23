"""Summary backfill membership on agent runs

Revision ID: 20261023_summary_dispatch
Revises: 20261023_package_changes
Create Date: 2026-10-23

agent_runs.summary_backfill_job_id records which backfill a pending summary
belongs to, so its outcome is counted however the summary is delivered or
recovered (summaries become a work-dispatch family in #769 stage 4b).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261023_summary_dispatch"
down_revision: Union[str, None] = "20261023_package_changes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column("summary_backfill_job_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_agent_runs_summary_backfill_job_id",
        "agent_runs",
        "summary_backfill_jobs",
        ["summary_backfill_job_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_agent_runs_summary_backfill_job_id",
        "agent_runs",
        ["summary_backfill_job_id"],
        postgresql_where=sa.text("summary_backfill_job_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_agent_runs_summary_backfill_job_id", table_name="agent_runs")
    op.drop_constraint("fk_agent_runs_summary_backfill_job_id", "agent_runs", type_="foreignkey")
    op.drop_column("agent_runs", "summary_backfill_job_id")
