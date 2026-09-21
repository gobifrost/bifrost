"""recorded evaluations

Revision ID: 20260920_recorded_evaluations
Revises: 20260919_matrix_cells
Create Date: 2026-09-20 18:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260920_recorded_evaluations"
down_revision: Union[str, None] = "20260919_matrix_cells"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_recorded_evaluations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=True),
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("requested_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("requested_by_email", sa.String(length=255), nullable=False),
        sa.Column("platform_job_id", sa.UUID(), nullable=False),
        sa.Column(
            "frozen_input",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("aggregate", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["platform_job_id"], ["platform_jobs.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("platform_job_id"),
    )
    op.create_index(
        "ix_agent_recorded_evaluations_agent_id",
        "agent_recorded_evaluations",
        ["agent_id"],
    )
    op.create_index(
        "ix_agent_recorded_evaluations_org_id",
        "agent_recorded_evaluations",
        ["org_id"],
    )
    op.create_index(
        "ix_agent_recorded_evaluations_requester",
        "agent_recorded_evaluations",
        ["requested_by_user_id"],
    )
    op.create_table(
        "agent_recorded_evaluation_results",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("evaluation_id", sa.UUID(), nullable=False),
        sa.Column("case_id", sa.UUID(), nullable=False),
        sa.Column("case_version", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("applicability", sa.String(length=20), nullable=False),
        sa.Column("applicability_source", sa.String(length=30), nullable=False),
        sa.Column("outcome", sa.String(length=40), nullable=False),
        sa.Column("complete", sa.Boolean(), nullable=False),
        sa.Column(
            "assertion_outcomes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "counts",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "evidence_refs",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "limitations",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["evaluation_id"], ["agent_recorded_evaluations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "evaluation_id", "case_id", "run_id", name="uq_recorded_eval_result_pair"
        ),
    )
    op.create_index(
        "ix_recorded_eval_results_evaluation_id",
        "agent_recorded_evaluation_results",
        ["evaluation_id"],
    )
    op.create_index(
        "ix_recorded_eval_results_run_id",
        "agent_recorded_evaluation_results",
        ["run_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_recorded_eval_results_run_id",
        table_name="agent_recorded_evaluation_results",
    )
    op.drop_index(
        "ix_recorded_eval_results_evaluation_id",
        table_name="agent_recorded_evaluation_results",
    )
    op.drop_table("agent_recorded_evaluation_results")
    op.drop_index(
        "ix_agent_recorded_evaluations_requester",
        table_name="agent_recorded_evaluations",
    )
    op.drop_index(
        "ix_agent_recorded_evaluations_org_id",
        table_name="agent_recorded_evaluations",
    )
    op.drop_index(
        "ix_agent_recorded_evaluations_agent_id",
        table_name="agent_recorded_evaluations",
    )
    op.drop_table("agent_recorded_evaluations")
