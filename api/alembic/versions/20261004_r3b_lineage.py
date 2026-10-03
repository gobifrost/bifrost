"""Record run lineage: run user, starter and root execution

Revision ID: 20261004_r3b_lineage
Revises: 20261003_r3b_identities
Create Date: 2026-10-04

Every execution records who it runs for (run_user_id), who started the run
tree (started_by_user_id) and the tree's first execution
(root_execution_id). Agent runs record their run user. Nothing reads these
for a decision yet.

Existing rows stay NULL: their lineage can't be proven. Foreign keys are
NOT VALID, so adding them doesn't scan the executions table; new rows are
checked as usual. root_execution_id has no foreign key: retention may remove
a tree's first execution before its children.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261004_r3b_lineage"
down_revision: Union[str, None] = "20261003_r3b_identities"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_USER_COLUMNS = (
    ("executions", "run_user_id"),
    ("executions", "started_by_user_id"),
    ("agent_runs", "run_user_id"),
)


def upgrade() -> None:
    for table, column in _USER_COLUMNS:
        op.add_column(table, sa.Column(column, postgresql.UUID(as_uuid=True), nullable=True))
        op.create_foreign_key(
            f"fk_{table}_{column}",
            table,
            "users",
            [column],
            ["id"],
            ondelete="SET NULL",
            postgresql_not_valid=True,
        )
    op.add_column("executions", sa.Column("root_execution_id", postgresql.UUID(as_uuid=True), nullable=True))


def downgrade() -> None:
    op.drop_column("executions", "root_execution_id")
    for table, column in reversed(_USER_COLUMNS):
        op.drop_constraint(f"fk_{table}_{column}", table, type_="foreignkey")
        op.drop_column(table, column)
