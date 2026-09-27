"""drop_local_runner

Drops the local-runner (browser-mediated CLI session) schema: the
``cli_sessions`` table and the ``executions.session_id`` /
``executions.is_local_execution`` columns. The feature has been unreachable
since the CLI-session web UI was deleted in bd4483e9a (2026-06-05); see
docs/plans (local-runner-removal-plan.md, not checked in) for the removal
rationale.

Existing execution rows are kept as history — this migration only drops the
now-permanently-dead columns, it does not delete any ``executions`` rows.

Downgrade recreates the ``cli_sessions`` table and the two ``executions``
columns (schema only) — any session/local-execution data that existed before
the upgrade is NOT restored.

Revision ID: 20260927_drop_local_runner
Revises: 20260927_r1b_mcp_names_b2
Create Date: 2026-09-27 00:00:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260927_drop_local_runner"
down_revision: Union[str, None] = "20260927_r1b_mcp_names_b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop executions.session_id (FK + index) first — cli_sessions is referenced by it.
    op.drop_constraint("fk_executions_session_id", "executions", type_="foreignkey")
    op.drop_index("idx_executions_session_id", table_name="executions")
    op.drop_column("executions", "session_id")

    # Drop executions.is_local_execution (+ index).
    op.drop_index("ix_executions_is_local_execution", table_name="executions")
    op.drop_column("executions", "is_local_execution")

    # Drop cli_sessions table.
    op.drop_index("idx_cli_sessions_user_id", table_name="cli_sessions")
    op.drop_table("cli_sessions")


def downgrade() -> None:
    # Recreate cli_sessions table (schema only — no data restored).
    op.create_table(
        "cli_sessions",
        sa.Column("id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("workflows", sa.JSON(), nullable=False),
        sa.Column("selected_workflow", sa.Text(), nullable=True),
        sa.Column("params", sa.JSON(), nullable=True),
        sa.Column("pending", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("idx_cli_sessions_user_id", "cli_sessions", ["user_id"])

    # Recreate executions.is_local_execution (+ index).
    op.add_column(
        "executions",
        sa.Column("is_local_execution", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.create_index("ix_executions_is_local_execution", "executions", ["is_local_execution"])

    # Recreate executions.session_id (+ FK + index).
    op.add_column("executions", sa.Column("session_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "fk_executions_session_id",
        "executions",
        "cli_sessions",
        ["session_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("idx_executions_session_id", "executions", ["session_id"])
