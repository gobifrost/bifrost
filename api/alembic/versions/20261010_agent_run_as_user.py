"""Record whose identity an agent run acts as

Revision ID: 20261010_agent_run_as_user
Revises: 20261009_graph_permission_names
Create Date: 2026-10-10

An agent run started with Run As records the user its tools act as in
run_as_user_id. NULL means the tools act as the caller, as before.

The foreign key is NOT VALID, so adding it doesn't scan agent_runs; new rows
are checked as usual. A partial index (built CONCURRENTLY, rows with a value
only) keeps deleting a user — ON DELETE SET NULL — from scanning agent_runs.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261010_agent_run_as_user"
down_revision: Union[str, None] = "20261009_graph_permission_names"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("agent_runs", sa.Column("run_as_user_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_agent_runs_run_as_user_id",
        "agent_runs",
        "users",
        ["run_as_user_id"],
        ["id"],
        ondelete="SET NULL",
        postgresql_not_valid=True,
    )
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY ix_agent_runs_run_as_user_id "
            "ON agent_runs (run_as_user_id) WHERE run_as_user_id IS NOT NULL"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_agent_runs_run_as_user_id")
    op.drop_constraint("fk_agent_runs_run_as_user_id", "agent_runs", type_="foreignkey")
    op.drop_column("agent_runs", "run_as_user_id")
