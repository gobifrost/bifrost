"""quality usage accounting foundation

Revision ID: 20260920_quality_usage
Revises: 20260920_recorded_evaluations
Create Date: 2026-09-20 21:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260920_quality_usage"
down_revision: str | None = "20260920_recorded_evaluations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_usage_attempts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("quality_operation_type", sa.String(length=64), nullable=False),
        sa.Column("quality_operation_id", sa.UUID(), nullable=False),
        sa.Column("quality_operation_item_id", sa.String(length=255), nullable=True),
        sa.Column("usage_purpose", sa.String(length=64), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=True),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("platform_job_id", sa.UUID(), nullable=True),
        sa.Column("profile_id", sa.UUID(), nullable=True),
        sa.Column("profile_name", sa.String(length=255), nullable=True),
        sa.Column("profile_fingerprint", sa.String(length=128), nullable=True),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=128), nullable=False),
        sa.Column(
            "state",
            sa.String(length=20),
            server_default="started",
            nullable=False,
        ),
        sa.Column("unobserved_reason", sa.String(length=64), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('started', 'observed', 'unobserved')",
            name="ai_usage_attempt_state_check",
        ),
        sa.CheckConstraint(
            "(state = 'unobserved' AND unobserved_reason IS NOT NULL) "
            "OR (state != 'unobserved' AND unobserved_reason IS NULL)",
            name="ai_usage_attempt_unobserved_reason_state_check",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["platform_job_id"], ["platform_jobs.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index(
        "ix_ai_usage_attempt_operation",
        "ai_usage_attempts",
        ["quality_operation_type", "quality_operation_id"],
    )
    op.create_index(
        "ix_ai_usage_attempt_platform_job",
        "ai_usage_attempts",
        ["platform_job_id"],
    )

    op.add_column(
        "ai_usage",
        sa.Column("quality_operation_type", sa.String(length=64), nullable=True),
    )
    op.add_column("ai_usage", sa.Column("quality_operation_id", sa.UUID(), nullable=True))
    op.add_column(
        "ai_usage",
        sa.Column("quality_operation_item_id", sa.String(length=255), nullable=True),
    )
    op.add_column("ai_usage", sa.Column("usage_purpose", sa.String(length=64), nullable=True))
    op.add_column("ai_usage", sa.Column("profile_id", sa.UUID(), nullable=True))
    op.add_column("ai_usage", sa.Column("profile_name", sa.String(length=255), nullable=True))
    op.add_column(
        "ai_usage", sa.Column("profile_fingerprint", sa.String(length=128), nullable=True)
    )
    op.add_column("ai_usage", sa.Column("platform_job_id", sa.UUID(), nullable=True))
    op.add_column("ai_usage", sa.Column("usage_attempt_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "fk_ai_usage_platform_job_id_platform_jobs",
        "ai_usage",
        "platform_jobs",
        ["platform_job_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_ai_usage_usage_attempt_id_ai_usage_attempts",
        "ai_usage",
        "ai_usage_attempts",
        ["usage_attempt_id"],
        ["id"],
    )
    op.create_unique_constraint(
        "uq_ai_usage_usage_attempt_id", "ai_usage", ["usage_attempt_id"]
    )

    op.drop_constraint("ai_usage_context_check", "ai_usage", type_="check")
    op.create_check_constraint(
        "ai_usage_context_check",
        "ai_usage",
        "execution_id IS NOT NULL OR conversation_id IS NOT NULL OR agent_run_id IS NOT NULL "
        "OR (quality_operation_type IS NOT NULL AND quality_operation_id IS NOT NULL)",
    )
    op.create_check_constraint(
        "ai_usage_quality_operation_pair_check",
        "ai_usage",
        "(quality_operation_type IS NULL AND quality_operation_id IS NULL) "
        "OR (quality_operation_type IS NOT NULL AND quality_operation_id IS NOT NULL)",
    )
    op.create_check_constraint(
        "ai_usage_quality_requires_purpose_check",
        "ai_usage",
        "quality_operation_type IS NULL OR usage_purpose IS NOT NULL",
    )
    op.create_check_constraint(
        "ai_usage_purpose_requires_quality_context_check",
        "ai_usage",
        "usage_purpose IS NULL OR "
        "(quality_operation_type IS NOT NULL AND quality_operation_id IS NOT NULL)",
    )
    op.create_check_constraint(
        "ai_usage_attempt_requires_quality_context_check",
        "ai_usage",
        "usage_attempt_id IS NULL OR "
        "(quality_operation_type IS NOT NULL AND quality_operation_id IS NOT NULL "
        "AND usage_purpose IS NOT NULL)",
    )
    op.create_check_constraint(
        "ai_usage_quality_no_runtime_context_check",
        "ai_usage",
        "quality_operation_type IS NULL OR "
        "(execution_id IS NULL AND conversation_id IS NULL AND agent_run_id IS NULL)",
    )
    op.create_index(
        "ix_ai_usage_quality_operation",
        "ai_usage",
        ["quality_operation_type", "quality_operation_id", "timestamp"],
        postgresql_where=sa.text("quality_operation_type IS NOT NULL"),
    )
    op.create_index(
        "ix_ai_usage_quality_purpose",
        "ai_usage",
        ["organization_id", "usage_purpose", "provider", "model", "timestamp"],
        postgresql_where=sa.text("usage_purpose IS NOT NULL"),
    )
    op.create_index(
        "ix_ai_usage_platform_job",
        "ai_usage",
        ["platform_job_id"],
        postgresql_where=sa.text("platform_job_id IS NOT NULL"),
    )


def downgrade() -> None:
    connection = op.get_bind()
    quality_count = connection.execute(
        sa.text(
            "SELECT count(*) FROM ai_usage "
            "WHERE quality_operation_type IS NOT NULL OR usage_attempt_id IS NOT NULL"
        )
    ).scalar_one()
    attempt_count = connection.execute(
        sa.text("SELECT count(*) FROM ai_usage_attempts")
    ).scalar_one()
    if quality_count or attempt_count:
        raise RuntimeError(
            "Refusing to downgrade quality usage foundation with persisted "
            "quality accounting data; preserve or migrate those rows first."
        )

    op.drop_index("ix_ai_usage_platform_job", table_name="ai_usage")
    op.drop_index("ix_ai_usage_quality_purpose", table_name="ai_usage")
    op.drop_index("ix_ai_usage_quality_operation", table_name="ai_usage")
    op.drop_constraint(
        "ai_usage_quality_no_runtime_context_check", "ai_usage", type_="check"
    )
    op.drop_constraint(
        "ai_usage_attempt_requires_quality_context_check", "ai_usage", type_="check"
    )
    op.drop_constraint(
        "ai_usage_purpose_requires_quality_context_check", "ai_usage", type_="check"
    )
    op.drop_constraint(
        "ai_usage_quality_requires_purpose_check", "ai_usage", type_="check"
    )
    op.drop_constraint("ai_usage_quality_operation_pair_check", "ai_usage", type_="check")
    op.drop_constraint("ai_usage_context_check", "ai_usage", type_="check")
    op.create_check_constraint(
        "ai_usage_context_check",
        "ai_usage",
        "execution_id IS NOT NULL OR conversation_id IS NOT NULL OR agent_run_id IS NOT NULL",
    )
    op.drop_constraint("uq_ai_usage_usage_attempt_id", "ai_usage", type_="unique")
    op.drop_constraint(
        "fk_ai_usage_usage_attempt_id_ai_usage_attempts", "ai_usage", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_ai_usage_platform_job_id_platform_jobs", "ai_usage", type_="foreignkey"
    )
    op.drop_column("ai_usage", "usage_attempt_id")
    op.drop_column("ai_usage", "platform_job_id")
    op.drop_column("ai_usage", "profile_fingerprint")
    op.drop_column("ai_usage", "profile_name")
    op.drop_column("ai_usage", "profile_id")
    op.drop_column("ai_usage", "usage_purpose")
    op.drop_column("ai_usage", "quality_operation_item_id")
    op.drop_column("ai_usage", "quality_operation_id")
    op.drop_column("ai_usage", "quality_operation_type")

    op.drop_index("ix_ai_usage_attempt_platform_job", table_name="ai_usage_attempts")
    op.drop_index("ix_ai_usage_attempt_operation", table_name="ai_usage_attempts")
    op.drop_table("ai_usage_attempts")
