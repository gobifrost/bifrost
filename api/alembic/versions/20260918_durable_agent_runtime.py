"""Durable agent runtime persistence.

Revision ID: 20260918_durable_agent_runtime
Revises: 20260918_merge_plat_prof_heads

Forward-only additive migration. Adds lease/checkpoint/snapshot/event
columns to ``agent_runs`` (backfilling ``root_run_id=id``, ``attempt=0``,
``checkpoint_sequence=0`` for existing rows) plus append-only checkpoint,
journal, tool-invocation, and fan-out join tables. Historical transcripts
are not rewritten.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260918_durable_agent_runtime"
down_revision: str | None = "20260918_merge_plat_prof_heads"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column("root_run_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column("execution_snapshot", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column("caller_context", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column(
            "checkpoint_sequence",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "agent_runs",
        sa.Column("lease_owner", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column("lease_token", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column("last_progress_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column(
            "attempt", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
    )
    op.add_column(
        "agent_runs",
        sa.Column("wake_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column("correlation", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column(
            "completion_event_pending_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "agent_runs",
        sa.Column(
            "completion_event_emitted_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "agent_runs",
        sa.Column(
            "completion_event_attempts",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "agent_runs",
        sa.Column("completion_event_last_error", sa.Text(), nullable=True),
    )

    # Backfill current rows before any non-null enforcement.
    op.execute(
        "UPDATE agent_runs SET root_run_id = id WHERE root_run_id IS NULL"
    )
    op.execute("UPDATE agent_runs SET attempt = 0 WHERE attempt IS NULL")
    op.execute(
        "UPDATE agent_runs SET checkpoint_sequence = 0 "
        "WHERE checkpoint_sequence IS NULL"
    )
    op.execute(
        "UPDATE agent_runs SET completion_event_attempts = 0 "
        "WHERE completion_event_attempts IS NULL"
    )

    op.create_foreign_key(
        "fk_agent_runs_root_run_id",
        "agent_runs",
        "agent_runs",
        ["root_run_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_agent_runs_root_run_id", "agent_runs", ["root_run_id"])
    op.create_index("ix_agent_runs_wake_at", "agent_runs", ["wake_at"])
    op.create_index(
        "ix_agent_runs_lease_expires_at", "agent_runs", ["lease_expires_at"]
    )
    op.create_index(
        "ix_agent_runs_completion_pending",
        "agent_runs",
        ["completion_event_pending_at"],
        postgresql_where=sa.text(
            "completion_event_pending_at IS NOT NULL "
            "AND completion_event_emitted_at IS NULL"
        ),
    )

    op.create_table(
        "agent_run_checkpoints",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column(
            "format_version", sa.Integer(), nullable=False, server_default="1"
        ),
        sa.Column("state", postgresql.JSONB(), nullable=False),
        sa.Column(
            "attempt", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("lease_token", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id", "sequence", name="uq_agent_run_checkpoints_run_sequence"
        ),
    )
    op.create_index(
        "ix_agent_run_checkpoints_run_id", "agent_run_checkpoints", ["run_id"]
    )

    op.create_table(
        "agent_run_journal_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column(
            "data",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "provider_invocation_id", sa.String(length=255), nullable=True
        ),
        sa.Column("checkpoint_sequence", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id", "sequence", name="uq_agent_run_journal_run_sequence"
        ),
    )
    op.create_index(
        "ix_agent_run_journal_run_id", "agent_run_journal_entries", ["run_id"]
    )
    op.create_index(
        "ix_agent_run_journal_run_kind",
        "agent_run_journal_entries",
        ["run_id", "kind"],
    )

    op.create_table(
        "agent_tool_invocations",
        sa.Column("operation_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "provider_tool_call_id", sa.String(length=255), nullable=True
        ),
        sa.Column("tool_name", sa.String(length=255), nullable=False),
        sa.Column("tool_version", sa.String(length=64), nullable=True),
        sa.Column("tool_schema", postgresql.JSONB(), nullable=True),
        sa.Column("arguments", postgresql.JSONB(), nullable=True),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "state",
            sa.String(length=20),
            nullable=False,
            server_default="planned",
        ),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("reconciliation", postgresql.JSONB(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("operation_id"),
        sa.UniqueConstraint(
            "run_id",
            "provider_tool_call_id",
            name="uq_agent_tool_invocations_run_tool_call",
        ),
        sa.CheckConstraint(
            "state IN ('planned', 'running', 'completed', 'failed', 'uncertain')",
            name="ck_agent_tool_invocations_state",
        ),
    )
    op.create_index(
        "ix_agent_tool_invocations_run_id", "agent_tool_invocations", ["run_id"]
    )
    op.create_index(
        "ix_agent_tool_invocations_run_state",
        "agent_tool_invocations",
        ["run_id", "state"],
    )

    op.create_table(
        "agent_run_joins",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "parent_run_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "provider_tool_call_id", sa.String(length=255), nullable=False
        ),
        sa.Column(
            "mode", sa.String(length=16), nullable=False, server_default="all"
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["parent_run_id"], ["agent_runs.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "parent_run_id",
            "provider_tool_call_id",
            name="uq_agent_run_joins_parent_tool_call",
        ),
        sa.CheckConstraint("mode = 'all'", name="ck_agent_run_joins_mode_v1"),
        sa.CheckConstraint(
            "status IN ('pending', 'complete', 'failed', 'cancelled')",
            name="ck_agent_run_joins_status",
        ),
    )
    op.create_index(
        "ix_agent_run_joins_parent_run_id", "agent_run_joins", ["parent_run_id"]
    )

    op.create_table(
        "agent_run_join_members",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("join_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "child_run_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "position", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("output", postgresql.JSONB(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["join_id"], ["agent_run_joins.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["child_run_id"], ["agent_runs.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "join_id", "child_run_id", name="uq_agent_run_join_members_join_child"
        ),
    )
    op.create_index(
        "ix_agent_run_join_members_join_id",
        "agent_run_join_members",
        ["join_id"],
    )
    op.create_index(
        "ix_agent_run_join_members_child_run_id",
        "agent_run_join_members",
        ["child_run_id"],
    )


def downgrade() -> None:
    op.drop_table("agent_run_join_members")
    op.drop_table("agent_run_joins")
    op.drop_table("agent_tool_invocations")
    op.drop_table("agent_run_journal_entries")
    op.drop_table("agent_run_checkpoints")
    op.drop_index("ix_agent_runs_completion_pending", table_name="agent_runs")
    op.drop_index("ix_agent_runs_lease_expires_at", table_name="agent_runs")
    op.drop_index("ix_agent_runs_wake_at", table_name="agent_runs")
    op.drop_index("ix_agent_runs_root_run_id", table_name="agent_runs")
    op.drop_constraint(
        "fk_agent_runs_root_run_id", "agent_runs", type_="foreignkey"
    )
    op.drop_column("agent_runs", "completion_event_last_error")
    op.drop_column("agent_runs", "completion_event_attempts")
    op.drop_column("agent_runs", "completion_event_emitted_at")
    op.drop_column("agent_runs", "completion_event_pending_at")
    op.drop_column("agent_runs", "correlation")
    op.drop_column("agent_runs", "wake_at")
    op.drop_column("agent_runs", "attempt")
    op.drop_column("agent_runs", "last_progress_at")
    op.drop_column("agent_runs", "lease_expires_at")
    op.drop_column("agent_runs", "lease_token")
    op.drop_column("agent_runs", "lease_owner")
    op.drop_column("agent_runs", "checkpoint_sequence")
    op.drop_column("agent_runs", "caller_context")
    op.drop_column("agent_runs", "execution_snapshot")
    op.drop_column("agent_runs", "root_run_id")
