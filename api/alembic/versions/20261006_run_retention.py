"""Run retention: daily rollup table, ai_usage outlives its run, paging indexes

Revision ID: 20261006_run_retention
Revises: 20261005_audit_retention
Create Date: 2026-10-06

workflow_run_daily holds one row per (day, organization, workflow name and id,
status) for finished workflow runs that run retention has deleted, so run
counts, resource peaks and AI cost keep their history. organization_id and workflow_id
carry no foreign keys: history must outlive the organization and the workflow.
The unique key is NULLS NOT DISTINCT so rows with no organization or workflow
still collapse into one.

ai_usage.execution_id and ai_usage.agent_run_id lose their ON DELETE CASCADE
foreign keys and stay as plain id stamps, so AI cost rows survive the deletion
of their run. ai_usage.workflow_id and ai_usage.agent_id are added; the
retention job stamps them from the run just before deleting it. The
conversation_id foreign key is unchanged.

execution_metrics_daily.total_ai_* are dropped: nothing ever wrote them.

ix_executions_completed_id and ix_agent_runs_completed_id page the retention
job through finished runs in (completed_at, id) order. They are built
CONCURRENTLY so the large tables stay writable, and without IF NOT EXISTS so
a leftover INVALID index from an interrupted build fails loudly instead of
being kept.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261006_run_retention"
down_revision: Union[str, None] = "20261005_audit_retention"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

AI_ROLLUP_COLUMNS = (
    ("total_ai_input_tokens", sa.BigInteger()),
    ("total_ai_output_tokens", sa.BigInteger()),
    ("total_ai_cost", sa.Numeric(12, 4)),
    ("total_ai_calls", sa.Integer()),
)


def upgrade() -> None:
    op.create_table(
        "workflow_run_daily",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("workflow_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("workflow_name", sa.String(255), nullable=False),
        sa.Column("status", postgresql.ENUM(name="execution_status", create_type=False), nullable=False),
        sa.Column("run_count", sa.Integer(), nullable=False),
        sa.Column("total_duration_ms", sa.BigInteger(), nullable=False),
        sa.Column("total_cpu_seconds", sa.Float(), nullable=False),
        sa.Column("max_peak_cpu_cores", sa.Float(), nullable=True),
        sa.Column("max_peak_process_rss_bytes", sa.BigInteger(), nullable=True),
        sa.Column("max_peak_memory_bytes", sa.BigInteger(), nullable=True),
        sa.Column("total_ai_cost", sa.Numeric(16, 8), nullable=False, server_default=sa.text("0")),
        sa.Column("total_ai_calls", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.UniqueConstraint(
            "day",
            "organization_id",
            "workflow_id",
            "workflow_name",
            "status",
            name="uq_workflow_run_daily_key",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index("ix_workflow_run_daily_day", "workflow_run_daily", ["day"])

    # Dropping a foreign key removes its RI triggers on the referenced executions
    # and agent_runs tables (gigabytes each) and takes ACCESS EXCLUSIVE locks on
    # them. Fail fast instead of queueing behind long reads; the init container
    # retries the migration.
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.drop_constraint("ai_usage_execution_id_fkey", "ai_usage", type_="foreignkey")
    op.drop_constraint("ai_usage_agent_run_id_fkey", "ai_usage", type_="foreignkey")
    op.add_column("ai_usage", sa.Column("workflow_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("ai_usage", sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=True))

    for name, _ in AI_ROLLUP_COLUMNS:
        op.drop_column("execution_metrics_daily", name)

    with op.get_context().autocommit_block():
        op.execute("CREATE INDEX CONCURRENTLY ix_executions_completed_id ON executions (completed_at, id)")
        op.execute("CREATE INDEX CONCURRENTLY ix_agent_runs_completed_id ON agent_runs (completed_at, id)")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_agent_runs_completed_id")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_executions_completed_id")

    for name, column_type in AI_ROLLUP_COLUMNS:
        op.add_column("execution_metrics_daily", sa.Column(name, column_type, nullable=True))

    op.drop_column("ai_usage", "agent_id")
    op.drop_column("ai_usage", "workflow_id")

    # Downgrade drops usage for runs that retention already deleted, as the
    # old ON DELETE CASCADE foreign keys would have.
    op.execute(
        "DELETE FROM ai_usage "
        "WHERE (execution_id IS NOT NULL AND execution_id NOT IN (SELECT id FROM executions)) "
        "OR (agent_run_id IS NOT NULL AND agent_run_id NOT IN (SELECT id FROM agent_runs))"
    )
    op.create_foreign_key(
        "ai_usage_execution_id_fkey", "ai_usage", "executions", ["execution_id"], ["id"], ondelete="CASCADE"
    )
    op.create_foreign_key(
        "ai_usage_agent_run_id_fkey", "ai_usage", "agent_runs", ["agent_run_id"], ["id"], ondelete="CASCADE"
    )

    op.drop_index("ix_workflow_run_daily_day", table_name="workflow_run_daily")
    op.drop_table("workflow_run_daily")
