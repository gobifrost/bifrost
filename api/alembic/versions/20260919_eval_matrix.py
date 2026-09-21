"""Saved multi-profile evaluation matrixes.

Revision ID: 20260919_eval_matrix
Revises: 20260919_case_finding_link
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260919_eval_matrix"
down_revision: str | None = "20260919_case_finding_link"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_evaluation_matrixes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("suite_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("suite_version", sa.Integer(), nullable=False),
        sa.Column(
            "candidate_ids",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "profile_ids",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("repetitions_override", sa.Integer(), nullable=True),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.ForeignKeyConstraint(
            ["suite_id"], ["agent_evaluation_suites.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_eval_matrixes_suite_id", "agent_evaluation_matrixes", ["suite_id"]
    )
    op.create_index(
        "ix_eval_matrixes_org_id", "agent_evaluation_matrixes", ["org_id"]
    )
    op.add_column(
        "agent_evaluation_executions",
        sa.Column("matrix_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_eval_executions_matrix_id",
        "agent_evaluation_executions",
        "agent_evaluation_matrixes",
        ["matrix_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_eval_executions_matrix_id",
        "agent_evaluation_executions",
        ["matrix_id"],
    )
    op.add_column(
        "agent_evaluation_executions",
        sa.Column("profile_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_eval_executions_profile_id",
        "agent_evaluation_executions",
        "ai_model_profiles",
        ["profile_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_eval_executions_profile_id",
        "agent_evaluation_executions",
        type_="foreignkey",
    )
    op.drop_column("agent_evaluation_executions", "profile_id")
    op.drop_index(
        "ix_eval_executions_matrix_id", table_name="agent_evaluation_executions"
    )
    op.drop_constraint(
        "fk_eval_executions_matrix_id",
        "agent_evaluation_executions",
        type_="foreignkey",
    )
    op.drop_column("agent_evaluation_executions", "matrix_id")
    op.drop_index(
        "ix_eval_matrixes_org_id", table_name="agent_evaluation_matrixes"
    )
    op.drop_index(
        "ix_eval_matrixes_suite_id", table_name="agent_evaluation_matrixes"
    )
    op.drop_table("agent_evaluation_matrixes")
