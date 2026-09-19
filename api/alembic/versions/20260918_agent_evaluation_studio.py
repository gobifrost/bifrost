"""Agent Evaluation Studio persistence.

Revision ID: 20260918_agent_evaluation_studio
Revises: 20260918_agent_output_contract

Forward-only additive migration. Adds suite/case/candidate/execution/
result/simulation tables. Suites/cases carry draft/published versions;
cases are immutable per (suite_id, name, version); executions are
projections over the authoritative ``agent.evaluation_suite`` PlatformJob
(active dedupe enforced by a partial unique index on ``dedupe_key``).
No existing tables are altered.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260918_agent_evaluation_studio"
down_revision: str | None = "20260918_agent_output_contract"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_evaluation_suites",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "status", sa.String(length=20), nullable=False, server_default="draft"
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "org_id", "name", "version", name="uq_eval_suites_org_name_version"
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'published', 'archived')",
            name="ck_eval_suites_status",
        ),
    )
    op.create_index("ix_eval_suites_org_id", "agent_evaluation_suites", ["org_id"])
    op.create_index(
        "ix_eval_suites_agent_id", "agent_evaluation_suites", ["agent_id"]
    )
    op.create_index("ix_eval_suites_status", "agent_evaluation_suites", ["status"])

    op.create_table(
        "agent_evaluation_cases",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("suite_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("input", postgresql.JSONB(), nullable=True),
        sa.Column(
            "fixture",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "simulator_policy",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "assertions",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "expected_tools",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "forbidden_tools",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("output_schema", postgresql.JSONB(), nullable=True),
        sa.Column("repetitions", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "scoring_policy",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "provenance",
            sa.String(length=30),
            nullable=False,
            server_default="manual",
        ),
        sa.Column(
            "provenance_run_ids",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "tags",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "accepted",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.ForeignKeyConstraint(
            ["suite_id"], ["agent_evaluation_suites.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "suite_id", "name", "version", name="uq_eval_cases_suite_name_version"
        ),
        sa.CheckConstraint(
            "provenance IN ('manual', 'generated', 'historical_inspiration')",
            name="ck_eval_cases_provenance",
        ),
    )
    op.create_index("ix_eval_cases_suite_id", "agent_evaluation_cases", ["suite_id"])
    op.create_index(
        "ix_eval_cases_suite_position",
        "agent_evaluation_cases",
        ["suite_id", "position"],
    )

    op.create_table(
        "agent_candidate_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("base_agent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("base_agent_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column(
            "overlays",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "snapshot",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("snapshot_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "evaluation_only",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.ForeignKeyConstraint(
            ["base_agent_id"], ["agents.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_eval_candidates_org_id", "agent_candidate_snapshots", ["org_id"]
    )
    op.create_index(
        "ix_eval_candidates_base_agent_id",
        "agent_candidate_snapshots",
        ["base_agent_id"],
    )
    op.create_index(
        "ix_eval_candidates_snapshot_hash",
        "agent_candidate_snapshots",
        ["snapshot_hash"],
    )

    op.create_table(
        "agent_evaluation_executions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("suite_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("suite_version", sa.Integer(), nullable=False),
        sa.Column("candidate_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("baseline_agent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "status", sa.String(length=20), nullable=False, server_default="queued"
        ),
        sa.Column("total_cases", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "completed_cases", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("passed_cases", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_cases", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("platform_job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("dedupe_key", sa.String(length=255), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["baseline_agent_id"], ["agents.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id"], ["agent_candidate_snapshots.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["platform_job_id"], ["platform_jobs.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["suite_id"], ["agent_evaluation_suites.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("platform_job_id"),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'waiting', 'succeeded', 'failed', 'cancelled')",
            name="ck_eval_executions_status",
        ),
    )
    op.create_index(
        "ix_eval_executions_suite_id", "agent_evaluation_executions", ["suite_id"]
    )
    op.create_index(
        "ix_eval_executions_status", "agent_evaluation_executions", ["status"]
    )
    op.create_index(
        "ix_eval_executions_platform_job_id",
        "agent_evaluation_executions",
        ["platform_job_id"],
    )
    op.create_index(
        "uq_eval_executions_active_dedupe",
        "agent_evaluation_executions",
        ["dedupe_key"],
        unique=True,
        postgresql_where=sa.text(
            "dedupe_key IS NOT NULL AND status IN ('queued', 'running', 'waiting')"
        ),
    )

    op.create_table(
        "agent_evaluation_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("execution_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_version", sa.Integer(), nullable=False),
        sa.Column(
            "repetition_index", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("baseline_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("candidate_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "status", sa.String(length=20), nullable=False, server_default="pending"
        ),
        sa.Column(
            "assertion_results",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("comparison", postgresql.JSONB(), nullable=True),
        sa.Column("tokens_used", sa.Integer(), nullable=True),
        sa.Column("turns_used", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.String(length=32), nullable=True),
        sa.Column("simulator_state_hash", sa.String(length=64), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.ForeignKeyConstraint(
            ["baseline_run_id"], ["agent_runs.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["candidate_run_id"], ["agent_runs.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["case_id"], ["agent_evaluation_cases.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["execution_id"],
            ["agent_evaluation_executions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "execution_id",
            "case_id",
            "repetition_index",
            name="uq_eval_results_execution_case_repetition",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'passed', 'failed', 'error')",
            name="ck_eval_results_status",
        ),
    )
    op.create_index(
        "ix_eval_results_execution_id", "agent_evaluation_results", ["execution_id"]
    )
    op.create_index(
        "ix_eval_results_case_id", "agent_evaluation_results", ["case_id"]
    )
    op.create_index(
        "ix_eval_results_status", "agent_evaluation_results", ["status"]
    )

    op.create_table(
        "agent_simulation_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_version", sa.Integer(), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("initial_state_hash", sa.String(length=64), nullable=True),
        sa.Column("final_state_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "state",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.ForeignKeyConstraint(
            ["case_id"], ["agent_evaluation_cases.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_sim_sessions_case_id", "agent_simulation_sessions", ["case_id"]
    )
    op.create_index("ix_sim_sessions_run_id", "agent_simulation_sessions", ["run_id"])

    op.create_table(
        "agent_simulation_tool_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("tool_name", sa.String(length=255), nullable=False),
        sa.Column("arguments", postgresql.JSONB(), nullable=True),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("state_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.ForeignKeyConstraint(
            ["session_id"], ["agent_simulation_sessions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "session_id", "sequence", name="uq_sim_tool_records_session_sequence"
        ),
    )
    op.create_index(
        "ix_sim_tool_records_session_id",
        "agent_simulation_tool_records",
        ["session_id"],
    )


def downgrade() -> None:
    op.drop_table("agent_simulation_tool_records")
    op.drop_table("agent_simulation_sessions")
    op.drop_table("agent_evaluation_results")
    op.drop_table("agent_evaluation_executions")
    op.drop_table("agent_candidate_snapshots")
    op.drop_table("agent_evaluation_cases")
    op.drop_table("agent_evaluation_suites")
