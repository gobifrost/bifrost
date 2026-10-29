"""Pin the actor selected for each new agent run.

Revision ID: 20261017_agent_actor
Revises: 20261017_service_run_user
Create Date: 2026-10-17

Existing agent runs remain unbound because their actor cannot be reconstructed
without changing their historical authority. New rows bind the actor at
enqueue time. The foreign key is NOT VALID so the migration does not scan
agent-run history, while every new value remains checked.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "20261017_agent_actor"
down_revision: str | None = "20261017_service_run_user"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column("acting_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_agent_runs_acting_user_id",
        "agent_runs",
        "users",
        ["acting_user_id"],
        ["id"],
        ondelete="SET NULL",
        postgresql_not_valid=True,
    )
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY ix_agent_runs_acting_user_id "
            "ON agent_runs (acting_user_id) WHERE acting_user_id IS NOT NULL"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_agent_runs_acting_user_id")
    op.drop_constraint("fk_agent_runs_acting_user_id", "agent_runs", type_="foreignkey")
    op.drop_column("agent_runs", "acting_user_id")
