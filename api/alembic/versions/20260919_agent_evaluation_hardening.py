"""Harden durable Studio execution snapshots and simulation operations."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260919_eval_hardening"
down_revision: str | None = "20260918_agent_evaluation_studio"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("agent_simulation_sessions", "case_id", nullable=True)
    op.add_column("agent_evaluation_executions", sa.Column("baseline_snapshot", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")))
    op.add_column("agent_evaluation_executions", sa.Column("candidate_snapshot", postgresql.JSONB(), nullable=True))
    op.add_column("agent_evaluation_executions", sa.Column("case_definitions", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")))
    op.add_column("agent_simulation_sessions", sa.Column("execution_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("agent_simulation_sessions", sa.Column("result_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("agent_simulation_sessions", sa.Column("side", sa.String(length=16), nullable=True))
    op.add_column("agent_simulation_sessions", sa.Column("root_run_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("agent_simulation_sessions", sa.Column("fixture", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")))
    op.add_column("agent_simulation_sessions", sa.Column("tool_schemas", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")))
    op.create_foreign_key("fk_sim_sessions_execution", "agent_simulation_sessions", "agent_evaluation_executions", ["execution_id"], ["id"], ondelete="CASCADE")
    op.create_foreign_key("fk_sim_sessions_result", "agent_simulation_sessions", "agent_evaluation_results", ["result_id"], ["id"], ondelete="CASCADE")
    op.create_foreign_key("fk_sim_sessions_root_run", "agent_simulation_sessions", "agent_runs", ["root_run_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_sim_sessions_root_run_id", "agent_simulation_sessions", ["root_run_id"])
    op.create_unique_constraint("uq_sim_sessions_result_side", "agent_simulation_sessions", ["result_id", "side"])
    op.add_column("agent_simulation_tool_records", sa.Column("operation_id", sa.String(length=255), nullable=True))
    op.create_unique_constraint("uq_sim_tool_records_operation", "agent_simulation_tool_records", ["session_id", "operation_id"])


def downgrade() -> None:
    op.alter_column("agent_simulation_sessions", "case_id", nullable=False)
    op.drop_constraint("uq_sim_tool_records_operation", "agent_simulation_tool_records", type_="unique")
    op.drop_column("agent_simulation_tool_records", "operation_id")
    op.drop_constraint("uq_sim_sessions_result_side", "agent_simulation_sessions", type_="unique")
    op.drop_index("ix_sim_sessions_root_run_id", table_name="agent_simulation_sessions")
    op.drop_constraint("fk_sim_sessions_root_run", "agent_simulation_sessions", type_="foreignkey")
    op.drop_constraint("fk_sim_sessions_result", "agent_simulation_sessions", type_="foreignkey")
    op.drop_constraint("fk_sim_sessions_execution", "agent_simulation_sessions", type_="foreignkey")
    for column in ("tool_schemas", "fixture", "root_run_id", "side", "result_id", "execution_id"):
        op.drop_column("agent_simulation_sessions", column)
    for column in ("case_definitions", "candidate_snapshot", "baseline_snapshot"):
        op.drop_column("agent_evaluation_executions", column)
